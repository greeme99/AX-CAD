"""Feature builders: Extrude (S5), Revolve and Boolean (S6)."""

import math
from typing import Any

from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism, BRepPrimAPI_MakeRevol
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Vec
from OCP.ShapeFix import ShapeFix_Shape
from OCP.TopAbs import TopAbs_EDGE, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape

from core.geometry.errors import GeomError
from core.geometry.metrics import count, measure
from core.geometry.serialize import from_brep, to_brep
from core.geometry.sketch import profile_face

MAX_DISTANCE_MM = 1e6
DIRECTIONS = {"+Z": 1.0, "-Z": -1.0}
CROSS_TOL = 1e-6  # mm, signed distance to the revolve axis
EDGE_SAMPLES = 32
BOOLEAN_OPS = {"FUSE": BRepAlgoAPI_Fuse, "CUT": BRepAlgoAPI_Cut, "COMMON": BRepAlgoAPI_Common}


def extrude(geoms: list[dict[str, Any]], distance: float, direction: str = "+Z") -> TopoDS_Shape:
    if (
        not (math.isfinite(distance) and 0 < distance <= MAX_DISTANCE_MM)
        or direction not in DIRECTIONS
    ):
        raise GeomError("GEOM_INVALID_PARAM", "distance must be in (0, 1e6] mm, direction +Z/-Z")
    face = profile_face(geoms)
    shape = BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, DIRECTIONS[direction] * distance)).Shape()
    if shape.IsNull() or not BRepCheck_Analyzer(shape).IsValid():
        raise GeomError("GEOM_INVALID_RESULT", "result shape is invalid")
    return shape


def extrude_job(
    geoms: list[dict[str, Any]], distance: float, direction: str
) -> tuple[bytes, dict[str, Any]]:
    """Worker entry point: (BREP bytes, metrics)."""
    shape = extrude(geoms, distance, direction)
    m = measure(shape)
    if m.pop("solids") != 1:
        raise GeomError("GEOM_INVALID_RESULT", "extrude did not produce exactly one solid")
    return to_brep(shape), m


def _valid(shape: TopoDS_Shape | None) -> bool:
    return shape is not None and not shape.IsNull() and BRepCheck_Analyzer(shape).IsValid()


def _crosses_axis(face: TopoDS_Shape, p: tuple[float, float], d: tuple[float, float]) -> bool:
    lo = hi = 0.0
    ex = TopExp_Explorer(face, TopAbs_EDGE)
    while ex.More():
        c = BRepAdaptor_Curve(TopoDS.Edge(ex.Current()))
        a, b = c.FirstParameter(), c.LastParameter()
        for i in range(EDGE_SAMPLES + 1):
            q = c.Value(a + (b - a) * i / EDGE_SAMPLES)
            s = d[0] * (q.Y() - p[1]) - d[1] * (q.X() - p[0])  # d is unit -> signed distance
            lo, hi = min(lo, s), max(hi, s)
        ex.Next()
    # ponytail: sampled, so a thin sliver crossing between samples can slip; BRepCheck still guards the result
    return lo < -CROSS_TOL and hi > CROSS_TOL


def revolve(
    geoms: list[dict[str, Any]],
    axis_point: tuple[float, float],
    axis_dir: tuple[float, float],
    angle_deg: float,
) -> TopoDS_Shape:
    vals = (*axis_point, *axis_dir, angle_deg)
    n = math.hypot(*axis_dir)
    if not all(math.isfinite(v) for v in vals) or n == 0 or not 0 < angle_deg <= 360:
        raise GeomError("GEOM_INVALID_PARAM", "axis must be non-zero, angle in (0, 360]")
    d = (axis_dir[0] / n, axis_dir[1] / n)
    face = profile_face(geoms)
    if _crosses_axis(face, axis_point, d):
        raise GeomError("GEOM_INVALID_PARAM", "profile crosses axis")
    ax = gp_Ax1(gp_Pnt(axis_point[0], axis_point[1], 0.0), gp_Dir(d[0], d[1], 0.0))
    shape = BRepPrimAPI_MakeRevol(face, ax, math.radians(angle_deg)).Shape()
    if not _valid(shape):
        raise GeomError("GEOM_INVALID_RESULT", "result shape is invalid")
    return shape


def _fix(shape: TopoDS_Shape) -> TopoDS_Shape:
    fix = ShapeFix_Shape(shape)
    fix.Perform()
    return fix.Shape()


def _run(op: str, a: TopoDS_Shape, b: TopoDS_Shape) -> TopoDS_Shape | None:
    # OCCT 8 binding has no HasErrors(): judge by IsDone() + exception (ADR-06)
    try:
        algo = BOOLEAN_OPS[op](a, b)
        return algo.Shape() if algo.IsDone() else None
    except Exception:  # noqa: BLE001 - Standard_Failure surfaces as a Python exception
        return None


def boolean(op: str, target: TopoDS_Shape, tool: TopoDS_Shape) -> TopoDS_Shape:
    if op not in BOOLEAN_OPS:
        raise GeomError("GEOM_INVALID_PARAM", "op must be FUSE, CUT or COMMON")
    res = _run(op, target, tool)
    if not _valid(res):  # retry once on ShapeFix-ed inputs
        res = _run(op, _fix(target), _fix(tool))
    if res is None or res.IsNull():
        raise GeomError("GEOM_BOOLEAN_FAILED", f"{op} failed")
    if count(res, TopAbs_SOLID) == 0 or measure(res)["volume_mm3"] <= 1e-9:
        raise GeomError("GEOM_EMPTY_RESULT", f"{op} produced no solid")
    res = _fix(res)
    if not _valid(res):
        raise GeomError("GEOM_BOOLEAN_FAILED", f"{op} result is invalid")
    return res


def _job(shape: TopoDS_Shape) -> tuple[bytes, dict[str, Any]]:
    m = measure(shape)
    if m.pop("solids") < 1:
        raise GeomError("GEOM_INVALID_RESULT", "no solid produced")
    return to_brep(shape), m


def revolve_job(
    geoms: list[dict[str, Any]],
    axis_point: tuple[float, float],
    axis_dir: tuple[float, float],
    angle_deg: float,
) -> tuple[bytes, dict[str, Any]]:
    return _job(revolve(geoms, axis_point, axis_dir, angle_deg))


def boolean_job(op: str, target: bytes, tool: bytes) -> tuple[bytes, dict[str, Any]]:
    return _job(boolean(op, from_brep(target), from_brep(tool)))
