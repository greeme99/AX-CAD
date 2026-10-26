"""Machining features on an existing body, modelled on FreeCAD PartDesign (Fillet, Chamfer, Hole,
LinearPattern/PolarPattern, Mirrored, Thickness, Draft), reimplemented on OCP. Units mm / degrees.

Edges and faces are chosen by rules relative to Z (the extrude direction of our sketches) instead of
by clicking: VERTICAL = the outline corners of a plate, TOP/BOTTOM = the edges or face at max/min Z."""

import math
from typing import Any

from OCP.BRep import BRep_Builder
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer, BRepFilletAPI_MakeFillet
from OCP.BRepOffset import BRepOffset_Skin
from OCP.BRepOffsetAPI import (
    BRepOffsetAPI_DraftAngle,
    BRepOffsetAPI_MakeOffsetShape,
    BRepOffsetAPI_MakeThickSolid,
)
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder
from OCP.collections import List_TopoDS_Shape
from OCP.GeomAbs import GeomAbs_Arc, GeomAbs_Cylinder, GeomAbs_Intersection, GeomAbs_Plane
from OCP.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pln, gp_Pnt, gp_Trsf, gp_Vec
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SOLID
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Compound, TopoDS_Shape

from core.geometry import holes_std
from core.geometry.errors import GeomError
from core.geometry.features import _fix, _valid
from core.geometry.metrics import count, measure
from core.geometry.refs import _items, face_edges, resolve
from core.geometry.serialize import from_brep, to_brep

EDGE_SETS = ("ALL", "VERTICAL", "TOP", "BOTTOM")
FACE_SETS = ("TOP", "BOTTOM", "BOTH", "NONE")  # faces a shell leaves open
JOINS = {"ARC": GeomAbs_Arc, "INTERSECTION": GeomAbs_Intersection}
MAX_DRAFT_DEG = 45
PLANES = {"YZ": (1.0, 0.0, 0.0), "ZX": (0.0, 1.0, 0.0), "XY": (0.0, 0.0, 1.0)}
SAMPLES = 8
TOL = 1e-6
MAX_HOLES = 500
MAX_COPIES = 100
MAX_PATTERN_FACES = 200_000  # faces of the body x copies: bounds kernel time, memory and the mesh
MAX_REACH_MM = 1e6  # how far a pattern may spread
MIN_STEP_MM = 1e-3  # nearly coincident copies make booleans crawl
MIN_POLAR_STEP_DEG = 0.1


def _fail(msg: str) -> GeomError:
    return GeomError("GEOM_FEATURE_FAILED", msg)


def _shapes(items: list[TopoDS_Shape]) -> List_TopoDS_Shape:
    out = List_TopoDS_Shape()
    for s in items:
        out.Append(s)
    return out


def _compound(shapes: list[TopoDS_Shape]) -> TopoDS_Compound:
    comp, b = TopoDS_Compound(), BRep_Builder()
    b.MakeCompound(comp)
    for s in shapes:
        b.Add(comp, s)
    return comp


def _zrange(shape: TopoDS_Shape) -> tuple[float, float]:
    b = measure(shape)["bbox"]
    return b["min"][2], b["max"][2]


def _edge_points(edge: TopoDS_Shape) -> list[tuple[float, float, float]]:
    c = BRepAdaptor_Curve(TopoDS.Edge(edge))
    a, b = c.FirstParameter(), c.LastParameter()
    pts = [c.Value(a + (b - a) * i / SAMPLES) for i in range(SAMPLES + 1)]
    return [(p.X(), p.Y(), p.Z()) for p in pts]


def edges(shape: TopoDS_Shape, which: str) -> list[TopoDS_Shape]:
    if which not in EDGE_SETS:
        raise GeomError("GEOM_INVALID_PARAM", f"edges must be one of {', '.join(EDGE_SETS)}")
    lo, hi = _zrange(shape)
    tol = max(TOL, 1e-9 * (hi - lo))
    out = []
    for e in _items(shape, TopAbs_EDGE):
        pts = _edge_points(e)
        x0, y0, _ = pts[0]
        zs = [p[2] for p in pts]
        if (
            which == "ALL"
            or (which == "TOP" and all(abs(z - hi) <= tol for z in zs))
            or (which == "BOTTOM" and all(abs(z - lo) <= tol for z in zs))
            or (
                which == "VERTICAL"
                and max(zs) - min(zs) > tol
                and all(abs(p[0] - x0) <= tol and abs(p[1] - y0) <= tol for p in pts)
            )
        ):
            out.append(e)
    if not out:
        raise _fail(f"no {which} edges on this body")
    return out


def _done(res: TopoDS_Shape | None, what: str) -> TopoDS_Shape:
    if res is None or res.IsNull() or count(res, TopAbs_SOLID) == 0:
        raise _fail(f"{what} failed")
    if not _valid(res):
        res = _fix(res)
        if not _valid(res):
            raise _fail(f"{what} result is invalid")
    return res


def _size(v: float, what: str) -> None:
    if not (math.isfinite(v) and 0 < v <= 1e6):
        raise GeomError("GEOM_INVALID_PARAM", f"{what} must be in (0, 1e6] mm")


def picked(shape: TopoDS_Shape, p: dict[str, Any]) -> list[TopoDS_Shape]:
    """The edges a fillet or chamfer works on: picked edges, all edges of picked faces, or a rule."""
    if p.get("edge_refs"):
        return resolve(shape, p["edge_refs"], "EDGE")
    if p.get("face_refs"):
        return face_edges(resolve(shape, p["face_refs"], "FACE"))
    return edges(shape, p["edges"])


def fillet(shape: TopoDS_Shape, radius: float, which: str | list[TopoDS_Shape]) -> TopoDS_Shape:
    _size(radius, "radius")
    mk = BRepFilletAPI_MakeFillet(shape)
    for e in edges(shape, which) if isinstance(which, str) else which:
        mk.Add(radius, TopoDS.Edge(e))
    try:
        mk.Build()
        res = mk.Shape() if mk.IsDone() else None
    except Exception:  # noqa: BLE001 - Standard_Failure: radius too large for the edges
        res = None
    return _done(res, "fillet (radius too large for these edges?)")


def chamfer(shape: TopoDS_Shape, distance: float, which: str | list[TopoDS_Shape]) -> TopoDS_Shape:
    _size(distance, "distance")
    mk = BRepFilletAPI_MakeChamfer(shape)
    for e in edges(shape, which) if isinstance(which, str) else which:
        mk.Add(distance, TopoDS.Edge(e))
    try:
        mk.Build()
        res = mk.Shape() if mk.IsDone() else None
    except Exception:  # noqa: BLE001
        res = None
    return _done(res, "chamfer (distance too large for these edges?)")


def _frame(shape: TopoDS_Shape, plane: dict[str, Any] | None) -> tuple[gp_Pnt, gp_Dir, gp_Dir]:
    """Origin, normal (drilling goes against it) and x axis of the hole positions; default = the
    XY plane at the body's top."""
    if plane is None:
        return gp_Pnt(0, 0, _zrange(shape)[1]), gp_Dir(0, 0, 1), gp_Dir(1, 0, 0)
    n, x = gp_Dir(*plane["normal"]), gp_Dir(*plane["xdir"])
    if abs(n.Dot(x)) > 1e-6:
        raise GeomError("GEOM_INVALID_PARAM", "the hole plane's x axis must be at right angles")
    return gp_Pnt(*plane["origin"]), n, x


def _hole_tools(
    at: gp_Pnt, n: gp_Dir, d: float, length: float, head: dict[str, Any] | None
) -> list[TopoDS_Shape]:
    """Drill (and counterbore / 90 deg countersink) starting 1 mm above `at`, going against n."""
    down = n.Reversed()
    top = at.Translated(gp_Vec(n).Multiplied(1.0))
    tools = [BRepPrimAPI_MakeCylinder(gp_Ax2(top, down), d / 2, 1.0 + length).Shape()]
    if head and head["kind"] == "COUNTERBORE":
        tools.append(
            BRepPrimAPI_MakeCylinder(
                gp_Ax2(top, down), head["diameter"] / 2, 1.0 + head["depth"]
            ).Shape()
        )
    elif head and head["kind"] == "COUNTERSINK":
        r = head["diameter"] / 2
        tools.append(BRepPrimAPI_MakeCylinder(gp_Ax2(top, down), r, 1.0).Shape())
        tools.append(BRepPrimAPI_MakeCone(gp_Ax2(at, down), r, d / 2, r - d / 2).Shape())
    return tools


def holes(
    shape: TopoDS_Shape,
    positions: list[tuple[float, float]],
    diameter: float,
    depth: float | None,
    plane: dict[str, Any] | None = None,
    head: dict[str, Any] | None = None,
) -> tuple[TopoDS_Shape, list[list[float]], list[float]]:
    """Holes at 2D positions on a plane, drilled against its normal (default: XY positions drilled
    down from the top); depth None = through all. Returns the body, the hole centres on the plane
    and the drilling direction."""
    _size(diameter, "diameter")
    if depth is not None:
        _size(depth, "depth")
    positions = list(dict.fromkeys((round(x, 6), round(y, 6)) for x, y in positions))  # no repeats
    if not 1 <= len(positions) <= MAX_HOLES:
        raise GeomError("GEOM_INVALID_PARAM", f"1..{MAX_HOLES} hole positions")
    o, n, x = _frame(shape, plane)
    y = n.Crossed(x)
    lo, hi = measure(shape)["bbox"]["min"], measure(shape)["bbox"]["max"]
    corners = [
        gp_Pnt(a, b, c) for a in (lo[0], hi[0]) for b in (lo[1], hi[1]) for c in (lo[2], hi[2])
    ]
    centers = [
        o.Translated(gp_Vec(x).Multiplied(px) + gp_Vec(y).Multiplied(py)) for px, py in positions
    ]
    tools = []
    for c in centers:
        through = 1.0 + max(c.Distance(k) for k in corners)  # past the body, whatever the plane
        tools += _hole_tools(c, n, diameter, through if depth is None else depth, head)
    try:
        # tools as a list (not one compound): overlapping holes are cut correctly
        algo = BRepAlgoAPI_Cut()
        algo.SetArguments(_shapes([shape]))
        algo.SetTools(_shapes(tools))
        algo.Build()
        res = algo.Shape() if algo.IsDone() else None
    except Exception:  # noqa: BLE001
        res = None
    res = _done(res, "hole")
    if measure(res)["volume_mm3"] >= measure(shape)["volume_mm3"] - TOL:
        raise _fail("holes miss the body: check the positions")
    pts = [[round(c.X(), 6), round(c.Y(), 6), round(c.Z(), 6)] for c in centers]
    return res, pts, [-n.X(), -n.Y(), -n.Z()]


def _fuse_all(parts: list[TopoDS_Shape], what: str) -> TopoDS_Shape:
    try:  # one general fuse of all copies, not copies-1 pairwise ones
        algo = BRepAlgoAPI_Fuse()
        algo.SetArguments(_shapes(parts[:1]))
        algo.SetTools(_shapes(parts[1:]))
        algo.Build()
        res = algo.Shape() if algo.IsDone() else None
    except Exception:  # noqa: BLE001
        res = None
    return _done(res, what)


def _moved(shape: TopoDS_Shape, t: gp_Trsf) -> TopoDS_Shape:
    return shape.Moved(TopLoc_Location(t))  # shares the geometry: no copy per instance


def _check_size(shape: TopoDS_Shape, copies: int) -> None:
    if count(shape, TopAbs_FACE) * copies > MAX_PATTERN_FACES:
        raise GeomError("GEOM_INVALID_PARAM", "pattern too large: fewer copies or a simpler body")


def linear_pattern(
    shape: TopoDS_Shape, step: tuple[float, float, float], copies: int
) -> TopoDS_Shape:
    """`copies` bodies in total, each `step` further along."""
    if not 2 <= copies <= MAX_COPIES or not all(math.isfinite(v) for v in step):
        raise GeomError("GEOM_INVALID_PARAM", f"copies 2..{MAX_COPIES}, finite step")
    d = math.hypot(*step)
    if d < MIN_STEP_MM or d * (copies - 1) > MAX_REACH_MM:
        raise GeomError(
            "GEOM_INVALID_PARAM", "step must be >= 0.001 mm and the pattern within 1e6 mm"
        )
    _check_size(shape, copies)
    parts = [shape]
    for i in range(1, copies):
        t = gp_Trsf()
        t.SetTranslation(gp_Vec(step[0] * i, step[1] * i, step[2] * i))
        parts.append(_moved(shape, t))
    return _fuse_all(parts, "linear pattern")


def polar_pattern(
    shape: TopoDS_Shape, center: tuple[float, float], copies: int, angle_deg: float = 360.0
) -> TopoDS_Shape:
    """`copies` bodies spread over angle_deg about the Z axis through center (360 = full circle)."""
    if not 2 <= copies <= MAX_COPIES or not (math.isfinite(angle_deg) and 0 < angle_deg <= 360):
        raise GeomError("GEOM_INVALID_PARAM", f"copies 2..{MAX_COPIES}, angle in (0, 360]")
    step = angle_deg / (copies if angle_deg == 360 else copies - 1)
    if step < MIN_POLAR_STEP_DEG:
        raise GeomError("GEOM_INVALID_PARAM", "copies must be at least 0.1 degree apart")
    _check_size(shape, copies)
    axis = gp_Ax1(gp_Pnt(center[0], center[1], 0), gp_Dir(0, 0, 1))
    parts = [shape]
    for i in range(1, copies):
        t = gp_Trsf()
        t.SetRotation(axis, math.radians(step * i))
        parts.append(_moved(shape, t))
    return _fuse_all(parts, "polar pattern")


def mirror(shape: TopoDS_Shape, plane: str, offset: float) -> TopoDS_Shape:
    """Original plus its mirror image across the plane (YZ: x = offset, ZX: y = offset, XY: z = offset)."""
    if plane not in PLANES or not math.isfinite(offset):
        raise GeomError("GEOM_INVALID_PARAM", "plane must be YZ, ZX or XY")
    n = PLANES[plane]
    t = gp_Trsf()
    t.SetMirror(gp_Ax2(gp_Pnt(n[0] * offset, n[1] * offset, n[2] * offset), gp_Dir(*n)))
    # a mirror flips orientation, which a shared location cannot carry: real copy here
    return _fuse_all([shape, BRepBuilderAPI_Transform(shape, t, True).Shape()], "mirror")


def _flat_faces(shape: TopoDS_Shape, which: str) -> List_TopoDS_Shape:
    """The flat faces lying at max Z (TOP), min Z (BOTTOM) or both."""
    lo, hi = _zrange(shape)
    zs = {"TOP": [hi], "BOTTOM": [lo], "BOTH": [lo, hi], "NONE": []}[which]
    faces = List_TopoDS_Shape()
    for f in _items(shape, TopAbs_FACE):
        s = BRepAdaptor_Surface(TopoDS.Face(f))
        flat = (
            s.GetType() == GeomAbs_Plane and abs(abs(s.Plane().Axis().Direction().Z()) - 1) <= 1e-9
        )
        if flat and any(abs(s.Plane().Location().Z() - z) <= TOL for z in zs):
            faces.Append(f)
    if which != "NONE" and faces.Size() == 0:
        raise _fail(f"no flat {which} face to open")
    return faces


def shell(
    shape: TopoDS_Shape,
    thickness: float,
    open_face: str,
    outward: bool = False,
    join: str = "ARC",
    picked_faces: list[TopoDS_Shape] | None = None,
) -> TopoDS_Shape:
    """FreeCAD Thickness: walls of `thickness` grown inward (or outward), with the flat TOP, BOTTOM,
    BOTH or NONE of those faces left open, or the faces picked in the 3D view. Join ARC rounds the
    outside corners, INTERSECTION keeps them sharp."""
    _size(thickness, "thickness")
    if open_face not in FACE_SETS or join not in JOINS:
        raise GeomError(
            "GEOM_INVALID_PARAM", "open face TOP/BOTTOM/BOTH/NONE, join ARC/INTERSECTION"
        )
    offset = thickness if outward else -thickness
    try:
        if (
            open_face == "NONE" and not picked_faces
        ):  # a closed hollow: the offset skin minus (or around) the body
            mk = BRepOffsetAPI_MakeOffsetShape()
            mk.PerformByJoin(shape, offset, 1e-3, BRepOffset_Skin, False, False, JOINS[join])
            skin = mk.Shape() if mk.IsDone() else None
            res = (
                None
                if skin is None
                else BRepAlgoAPI_Cut(skin, shape).Shape()
                if outward
                else BRepAlgoAPI_Cut(shape, skin).Shape()
            )
        else:
            thick = BRepOffsetAPI_MakeThickSolid()
            faces = _shapes(picked_faces) if picked_faces else _flat_faces(shape, open_face)
            thick.MakeThickSolidByJoin(
                shape, faces, offset, 1e-3, BRepOffset_Skin, False, False, JOINS[join]
            )
            res = thick.Shape() if thick.IsDone() else None
    except GeomError:
        raise
    except Exception:  # noqa: BLE001
        res = None
    res = _done(res, "shell (wall too thick?)")
    before, after = measure(shape)["volume_mm3"], measure(res)["volume_mm3"]
    if abs(after - before) <= TOL or (not outward and after >= before):
        raise _fail("shell changed nothing")
    return res


def draft(
    shape: TopoDS_Shape,
    angle_deg: float,
    pull: str,
    neutral: str,
    picked_faces: list[TopoDS_Shape] | None = None,
) -> TopoDS_Shape:
    """FreeCAD Draft: tilt every side face (flat faces along Z, cylinders on a Z axis), or only the
    picked ones, by the angle about the TOP or BOTTOM plane; positive = the body narrows along the
    pull direction."""
    if (
        not (0 < abs(angle_deg) <= MAX_DRAFT_DEG)
        or pull not in ("+Z", "-Z")
        or neutral not in ("TOP", "BOTTOM")
    ):
        raise GeomError(
            "GEOM_INVALID_PARAM", "angle in (0, 45] degrees, pull +Z/-Z, neutral TOP/BOTTOM"
        )
    lo, hi = _zrange(shape)
    plane = gp_Pln(gp_Pnt(0, 0, hi if neutral == "TOP" else lo), gp_Dir(0, 0, 1))
    mk = BRepOffsetAPI_DraftAngle(shape)
    added = 0
    for f in picked_faces or _items(shape, TopAbs_FACE):
        s = BRepAdaptor_Surface(TopoDS.Face(f))
        side = (s.GetType() == GeomAbs_Plane and abs(s.Plane().Axis().Direction().Z()) <= 1e-9) or (
            s.GetType() == GeomAbs_Cylinder
            and abs(abs(s.Cylinder().Axis().Direction().Z()) - 1) <= 1e-9
        )
        if not side:
            continue
        mk.Add(
            TopoDS.Face(f), gp_Dir(0, 0, 1 if pull == "+Z" else -1), math.radians(angle_deg), plane
        )
        if not mk.AddDone():
            raise _fail("draft cannot tilt one of the side faces (angle too steep?)")
        added += 1
    if not added:
        raise _fail("no side faces along Z to draft")
    try:
        mk.Build()
        res = mk.Shape() if mk.IsDone() else None
    except Exception:  # noqa: BLE001
        res = None
    return _done(res, "draft (angle too steep for this body?)")


# --- worker jobs: BREP bytes in, (BREP bytes, metrics) out ---


def _out(shape: TopoDS_Shape) -> tuple[bytes, dict[str, Any]]:
    m = measure(shape)
    m.pop("solids")
    return to_brep(shape), m


def hole_job(s: TopoDS_Shape, p: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    """Plain holes, or the hole wizard's spec; the metrics carry what a quote needs (count, sizes,
    the thread for the "tap" process) and the centres for the cosmetic thread in the 3D view."""
    try:
        std = holes_std.resolve(p["spec"]) if p.get("spec") else {}
    except holes_std.SpecError as e:
        raise GeomError("GEOM_INVALID_PARAM", str(e)) from None
    dia = std.get("diameter") or p["diameter"]
    res, centers, axis = holes(
        s, [tuple(q) for q in p["positions"]], dia, p["depth"], p.get("plane"), std.get("head")
    )
    brep, m = _out(res)
    m["holes"] = {
        **std,
        "diameter": dia,
        "depth": p["depth"],
        "count": len(centers),
        "centers": centers,
        "axis": axis,
    }
    if "thread" in std:
        thread_len = p["spec"].get("thread_depth") or p["depth"]
        m["holes"]["thread"] = {**std["thread"], "length": thread_len}  # None = through
    return brep, m


def _faces(shape: TopoDS_Shape, p: dict[str, Any]) -> list[TopoDS_Shape] | None:
    return resolve(shape, p["face_refs"], "FACE") if p.get("face_refs") else None


def dressup_job(kind: str, body: bytes, p: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    s = from_brep(body)
    if kind == "FILLET":
        return _out(fillet(s, p["radius"], picked(s, p)))
    if kind == "CHAMFER":
        return _out(chamfer(s, p["distance"], picked(s, p)))
    if kind == "HOLE":
        return hole_job(s, p)
    if kind == "PATTERN":
        if p["kind"] == "LINEAR":
            return _out(linear_pattern(s, tuple(p["step"]), p["copies"]))
        return _out(polar_pattern(s, tuple(p["center"]), p["copies"], p["angle_deg"]))
    if kind == "MIRROR":
        return _out(mirror(s, p["plane"], p["offset"]))
    if kind == "SHELL":
        return _out(
            shell(
                s,
                p["thickness"],
                p["open_face"],
                p.get("outward", False),
                p.get("join") or "ARC",
                _faces(s, p),
            )
        )
    if kind == "DRAFT":
        return _out(draft(s, p["angle_deg"], p["pull"], p["neutral"], _faces(s, p)))
    raise GeomError("GEOM_INVALID_PARAM", "unknown feature")
