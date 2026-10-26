"""Track 8 P2-6: rebuild FreeCAD exercise parts with AX-CAD and compare volumes.

    uv run python -m tests.freecad_examples "<dir>/Page_064_Exercise3D-05_byHannu.fcstd" ...

Reads a .FCStd (a zip: Document.xml + BREP files) without FreeCAD: the sketches of the PartDesign
Body (placement, lines, circles, arcs; construction geometry skipped) and its Pad / Pocket /
Revolution features, then rebuilds them with the same kernel jobs the AX-CAD API uses (pad, pocket,
revolve, Boolean fuse). The reference is the Body's own stored BREP. Nothing from the file is kept;
the FreeCAD-org Examples stay outside the repository.
"""

import math
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

from OCP.BRep import BRep_Builder
from OCP.BRepTools import BRepTools
from OCP.TopoDS import TopoDS_Shape

from core.geometry.features import boolean_job, revolve_job
from core.geometry.metrics import measure
from core.geometry.pad import pad_job, pocket_job
from core.geometry.serialize import from_brep

Obj = dict[str, ET.Element]


def _props(o: ET.Element) -> Obj:
    return {p.get("name", ""): p for p in o.iter("Property")}


def _rot(q: list[float], v: tuple[float, float, float]) -> list[float]:
    """Rotate v by the unit quaternion (x, y, z, w)."""
    x, y, z, w = q
    vx, vy, vz = v
    tx, ty, tz = 2 * (y * vz - z * vy), 2 * (z * vx - x * vz), 2 * (x * vy - y * vx)
    return [
        vx + w * tx + (y * tz - z * ty),
        vy + w * ty + (z * tx - x * tz),
        vz + w * tz + (x * ty - y * tx),
    ]


def _plane(p: Obj) -> dict[str, list[float]]:
    pl = p["Placement"].find("PropertyPlacement")
    assert pl is not None
    q = [float(pl.get(k, "0")) for k in ("Q0", "Q1", "Q2", "Q3")]
    origin = [float(pl.get(k, "0")) for k in ("Px", "Py", "Pz")]
    return {"origin": origin, "normal": _rot(q, (0, 0, 1)), "xdir": _rot(q, (1, 0, 0))}


def _construction(g: ET.Element) -> bool:
    c = g.find("Construction")
    if c is not None:
        return c.get("value") == "1"
    ext = g.find(".//GeometryExtension[@type='Sketcher::SketchGeometryExtension']")
    return ext is not None and ext.get("geometryModeFlags", "0").endswith("1")


def _entities(p: Obj) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for g in p["Geometry"].iter("Geometry"):
        if _construction(g):
            continue
        kind, e = g.get("type"), list(g)[-1]
        f = {k: float(v) for k, v in e.attrib.items() if k not in ("type",)}
        if kind == "Part::GeomLineSegment":
            out.append(
                {"type": "LINE", "start": [f["StartX"], f["StartY"]], "end": [f["EndX"], f["EndY"]]}
            )
        elif kind == "Part::GeomCircle":
            out.append(
                {"type": "CIRCLE", "center": [f["CenterX"], f["CenterY"]], "radius": f["Radius"]}
            )
        elif kind == "Part::GeomArcOfCircle":
            a0, a1 = math.degrees(f["StartAngle"]), math.degrees(f["EndAngle"])
            if f.get("NormalZ", 1) < 0:  # clockwise in the sketch: same arc read the other way
                a0, a1 = -a1, -a0
            out.append(
                {
                    "type": "ARC",
                    "center": [f["CenterX"], f["CenterY"]],
                    "radius": f["Radius"],
                    "start_angle": a0 % 360,
                    "end_angle": a1 % 360,
                }
            )
        elif kind != "Part::GeomPoint":  # points only place other geometry
            raise ValueError(f"unsupported sketch geometry {kind}")
    return out


def _shape(z: zipfile.ZipFile, p: Obj) -> TopoDS_Shape:
    part = p["Shape"].find("Part")
    assert part is not None
    with tempfile.NamedTemporaryFile(suffix=".brp") as t:
        t.write(z.read(part.get("file", "")))
        t.flush()
        s = TopoDS_Shape()
        BRepTools.Read_s(s, t.name, BRep_Builder())
    return s


def _val(p: Obj, name: str, default: str = "0") -> str:
    e = p.get(name)
    child = None if e is None else next(iter(e), None)
    return default if child is None else child.get("value", default)


def rebuild(path: str) -> tuple[float, float]:
    """(FreeCAD volume, AX-CAD volume) of the file's PartDesign Body."""
    z = zipfile.ZipFile(path)
    root = ET.fromstring(z.read("Document.xml"))
    types = {o.get("name"): o.get("type") for o in root.iter("Object") if o.get("type")}
    data = {
        o.get("name"): _props(o) for o in root.iter("Object") if o.find("Properties") is not None
    }
    (body,) = [n for n, t in types.items() if t == "PartDesign::Body"]
    group = [link.get("value") for link in data[body]["Group"].iter("Link")]
    brep: bytes | None = None
    for name in group:
        t, p = types[name], data[name]
        if t not in ("PartDesign::Pad", "PartDesign::Pocket", "PartDesign::Revolution"):
            if t.startswith("PartDesign::") and t not in ("PartDesign::Plane", "PartDesign::Line"):
                raise ValueError(f"unsupported feature {t}")
            continue
        sk = data[p["Profile"].find("LinkSub").get("value")]  # type: ignore[union-attr]
        geoms, plane = _entities(sk), _plane(sk)
        reversed_ = _val(p, "Reversed") == "true"
        if t == "PartDesign::Revolution":
            axis = p["ReferenceAxis"].find(".//Sub")
            sub = "" if axis is None else axis.get("value", "")
            d = {"V_Axis": (0.0, 1.0), "H_Axis": (1.0, 0.0)}[sub]
            new, _ = revolve_job(geoms, (0.0, 0.0), d, float(_val(p, "Angle")), plane)
            brep = new if brep is None else boolean_job("FUSE", brep, new)[0]
            continue
        kind = int(_val(p, "Type"))
        mid = _val(p, "Midplane") == "true"
        pocket = t == "PartDesign::Pocket"
        mode = {0: "SYMMETRIC" if mid else "LENGTH", 1: "THROUGH_ALL", 4: "TWO_SIDED"}[kind]
        # FreeCAD pads along the sketch normal and pockets against it; Reversed flips either
        up = (not pocket) != reversed_
        params = {
            "mode": mode,
            "direction": "+Z" if up else "-Z",
            "distance": float(_val(p, "Length")),
            "distance2": float(_val(p, "Length2")),
        }
        if pocket:
            assert brep is not None
            if (
                mode == "THROUGH_ALL" and mid
            ):  # through all, both sides: a symmetric cut longer than the body
                b = measure(from_brep(brep))["bbox"]
                reach = (
                    math.dist(b["min"], b["max"])
                    + max(map(abs, [*b["min"], *b["max"]]))
                    + math.dist(plane["origin"], [0, 0, 0])
                )
                params |= {"mode": "SYMMETRIC", "distance": 4 * reach}
            brep = pocket_job(geoms, params, plane, brep)[0]
        else:
            new, _ = pad_job(geoms, params, plane)
            brep = new if brep is None else boolean_job("FUSE", brep, new)[0]
    assert brep is not None
    return measure(_shape(z, data[body]))["volume_mm3"], measure(from_brep(brep))["volume_mm3"]


if __name__ == "__main__":
    for f in sys.argv[1:]:
        try:
            fc, ax = rebuild(f)
            print(f"{Path(f).name}\tFreeCAD {fc:.4f}\tAX-CAD {ax:.4f}\trel {abs(ax - fc) / fc:.2e}")
        except Exception as e:  # noqa: BLE001 - report and go on to the next file
            print(f"{Path(f).name}\tNOT REBUILT: {type(e).__name__}: {e}")
