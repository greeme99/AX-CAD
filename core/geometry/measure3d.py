"""Track 8 P2-5: 3D measurement on a body (FreeCAD Measure): picked edges and faces, by signature.

One pick: its length or area (and radius of a circle / cylinder). Two picks: the shortest distance
between them and, when both have a direction (line, circle axis, plane normal, cylinder axis), the
angle between those directions (0-90 deg). Always: the body's volume, area and centre of mass.
"""

import math
from typing import Any

from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

from core.geometry.errors import GeomError
from core.geometry.refs import edge_sig, face_sig, resolve
from core.geometry.serialize import from_brep

MAX_PICKS = 10


def _angle(a: list[float], b: list[float]) -> float:
    cos = abs(sum(x * y for x, y in zip(a, b, strict=True))) / (math.hypot(*a) * math.hypot(*b))
    return math.degrees(math.acos(min(1.0, cos)))


def measure_job(brep: bytes, picks: list[dict[str, Any]]) -> dict[str, Any]:
    if len(picks) > MAX_PICKS:
        raise GeomError("GEOM_INVALID_PARAM", f"at most {MAX_PICKS} picks")
    shape = from_brep(brep)
    shapes = [resolve(shape, [p["sig"]], p["what"])[0] for p in picks]
    items = []
    for p, s in zip(picks, shapes, strict=True):
        sig = edge_sig(s) if p["what"] == "EDGE" else face_sig(s)
        items.append({k: sig[k] for k in ("kind", "length", "area", "radius", "dir") if k in sig})
    out: dict[str, Any] = {"items": items}
    if len(shapes) == 2:
        dist = BRepExtrema_DistShapeShape(shapes[0], shapes[1])
        if not dist.IsDone():
            raise GeomError("GEOM_FEATURE_FAILED", "distance could not be computed")
        out["distance"] = dist.Value()
        a, b = items[0].get("dir"), items[1].get("dir")
        if a and b:
            out["angle_deg"] = _angle(a, b)
    vp, sp = GProp_GProps(), GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, vp)
    BRepGProp.SurfaceProperties_s(shape, sp)
    c = vp.CentreOfMass()
    out["body"] = {
        "volume_mm3": vp.Mass(),
        "surface_area_mm2": sp.Mass(),
        "center": [c.X(), c.Y(), c.Z()],
    }
    return out
