"""S6: NURBS curve & surface creation — loft/sweep/boundary/fill/extrude/revolve/offset.

This module creates *sheet* bodies (open shells) and NURBS curves.  Solid creation
from sheet bodies (sewing, thicken) is in S7.  All coordinate units are mm.
"""

from __future__ import annotations

import math
from typing import Any

from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeWire,
)
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepFill import BRepFill_Filling
from OCP.BRepGProp import BRepGProp
from OCP.BRepOffsetAPI import (
    BRepOffsetAPI_MakePipe,
    BRepOffsetAPI_ThruSections,
)
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism, BRepPrimAPI_MakeRevol
from OCP.collections import Array1_double, Array1_gp_Pnt, Array1_int, HArray1_gp_Pnt
from OCP.Geom import Geom_BSplineCurve
from OCP.GeomAbs import GeomAbs_C0, GeomAbs_C1, GeomAbs_C2
from OCP.GeomAPI import GeomAPI_Interpolate
from OCP.GeomFill import GeomFill_Trihedron
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Vec
from OCP.GProp import GProp_GProps
from OCP.ShapeFix import ShapeFix_Shape
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape, TopoDS_Wire

from core.geometry.errors import GeomError
from core.geometry.features import Plane, place
from core.geometry.metrics import count
from core.geometry.serialize import to_brep
from core.geometry.sketch import path_wire

# ── limits (§23.7) ──────────────────────────────────────────────────────────
MAX_CURVE_POINTS = 2_000
MAX_DEGREE = 9
MAX_BOUNDARY_EDGES = 12
MAX_FILL_CONSTRAINTS = 12
MAX_SECTIONS = 20
MAX_OFFSET_MM = 1e6

# ── continuity map ──────────────────────────────────────────────────────────
_CONT = {"G0": GeomAbs_C0, "G1": GeomAbs_C1, "G2": GeomAbs_C2}


def _gp(p: list[float] | tuple[float, ...]) -> gp_Pnt:
    return gp_Pnt(float(p[0]), float(p[1]), float(p[2]))


def _vec(v: list[float] | tuple[float, ...]) -> gp_Vec:
    return gp_Vec(float(v[0]), float(v[1]), float(v[2]))


def _dir(v: list[float] | tuple[float, ...]) -> gp_Dir:
    return gp_Dir(float(v[0]), float(v[1]), float(v[2]))


def _fix(shape: TopoDS_Shape) -> TopoDS_Shape:
    f = ShapeFix_Shape(shape)
    f.Perform()
    return f.Shape()


def _check(shape: TopoDS_Shape | None, msg: str) -> TopoDS_Shape:
    if shape is None or shape.IsNull():
        raise GeomError("GEOM_FEATURE_FAILED", msg)
    if not BRepCheck_Analyzer(shape).IsValid():
        shape = _fix(shape)
        if not BRepCheck_Analyzer(shape).IsValid():
            raise GeomError("GEOM_FEATURE_FAILED", msg)
    return shape


# ── sheet body metrics ──────────────────────────────────────────────────────
def sheet_measure(shape: TopoDS_Shape) -> dict[str, Any]:
    """Metrics for a sheet body (open shell) — no volume, report surface area + bbox."""
    sp = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, sp)
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    lo, hi = box.CornerMin(), box.CornerMax()
    n_faces = count(shape, TopAbs_FACE)
    n_solids = count(shape, TopAbs_SOLID)
    return {
        "surface_area_mm2": sp.Mass(),
        "bbox": {
            "min": [lo.X(), lo.Y(), lo.Z()],
            "max": [hi.X(), hi.Y(), hi.Z()],
            "size": [hi.X() - lo.X(), hi.Y() - lo.Y(), hi.Z() - lo.Z()],
        },
        "faces": n_faces,
        "solids": n_solids,
        "kind": "solid" if n_solids > 0 else "sheet",
    }


# ═══════════════════════════════════════════════════════════════════════════
# 1. NURBS CURVE
# ═══════════════════════════════════════════════════════════════════════════

def nurbs_curve_interpolate(
    points: list[list[float]],
    degree: int = 3,
    periodic: bool = False,
) -> Geom_BSplineCurve:
    """Interpolating B-spline through 3D points (G2-smooth by default)."""
    n = len(points)
    if n < 2 or n > MAX_CURVE_POINTS:
        raise GeomError("GEOM_INVALID_PARAM", f"need 2..{MAX_CURVE_POINTS} points")
    if not 1 <= degree <= MAX_DEGREE:
        raise GeomError("GEOM_INVALID_PARAM", f"degree must be 1..{MAX_DEGREE}")
    arr = HArray1_gp_Pnt(1, n)
    for i, p in enumerate(points, 1):
        arr.SetValue(i, _gp(p))
    interp = GeomAPI_Interpolate(arr, periodic, 1e-6)
    interp.Perform()
    if not interp.IsDone():
        raise GeomError("GEOM_FEATURE_FAILED", "curve interpolation failed")
    return interp.Curve()


def nurbs_curve_control(
    poles: list[list[float]],
    degree: int = 3,
    weights: list[float] | None = None,
) -> Geom_BSplineCurve:
    """B-spline from control points (and optional weights) with uniform knots."""
    n = len(poles)
    if n < 2 or n > MAX_CURVE_POINTS:
        raise GeomError("GEOM_INVALID_PARAM", f"need 2..{MAX_CURVE_POINTS} poles")
    if not 1 <= degree <= MAX_DEGREE:
        raise GeomError("GEOM_INVALID_PARAM", f"degree must be 1..{MAX_DEGREE}")
    if degree >= n:
        raise GeomError("GEOM_INVALID_PARAM", "degree must be < number of poles")
    gp_poles = Array1_gp_Pnt(1, n)
    for i, p in enumerate(poles, 1):
        gp_poles.SetValue(i, _gp(p))
    # uniform clamped knot vector
    n_knots = n - degree + 1
    knots = Array1_double(1, n_knots)
    mults = Array1_int(1, n_knots)
    for i in range(1, n_knots + 1):
        knots.SetValue(i, float(i - 1) / (n_knots - 1))
        mults.SetValue(i, degree + 1 if (i == 1 or i == n_knots) else 1)
    if weights is not None:
        if len(weights) != n:
            raise GeomError("GEOM_INVALID_PARAM", "weights must match poles count")
        gp_w = Array1_double(1, n)
        for i, w in enumerate(weights, 1):
            gp_w.SetValue(i, float(w))
        return Geom_BSplineCurve(gp_poles, gp_w, knots, mults, degree)
    return Geom_BSplineCurve(gp_poles, knots, mults, degree)


def curve_edge(curve: Geom_BSplineCurve) -> TopoDS_Shape:
    """Wrap a Geom_BSplineCurve into a TopoDS_Edge."""
    mk = BRepBuilderAPI_MakeEdge(curve)
    if not mk.IsDone():
        raise GeomError("GEOM_FEATURE_FAILED", "edge from curve failed")
    return mk.Edge()


def curve_wire(curve: Geom_BSplineCurve) -> TopoDS_Wire:
    """Wrap a curve into a single-edge wire."""
    mk = BRepBuilderAPI_MakeWire(TopoDS.Edge(curve_edge(curve)))
    if not mk.IsDone():
        raise GeomError("GEOM_FEATURE_FAILED", "wire from curve failed")
    return mk.Wire()


# ═══════════════════════════════════════════════════════════════════════════
# 2. SURFACE LOFT (open sections → sheet body)
# ═══════════════════════════════════════════════════════════════════════════

def surface_loft(
    sections: list[tuple[list[dict[str, Any]], Plane]],
    ruled: bool = False,
    solid: bool = False,
) -> TopoDS_Shape:
    """Loft through open or closed wire sections → sheet or solid body."""
    if not 2 <= len(sections) <= MAX_SECTIONS:
        raise GeomError("GEOM_INVALID_PARAM", f"need 2..{MAX_SECTIONS} sections")
    mk = BRepOffsetAPI_ThruSections(solid, ruled)
    for geoms, plane in sections:
        wire = TopoDS.Wire(place(path_wire(geoms), plane))
        mk.AddWire(wire)
    try:
        mk.Build()
        shape = mk.Shape() if mk.IsDone() else None
    except Exception:  # noqa: BLE001
        shape = None
    return _check(shape, "surface loft failed (do sections cross each other?)")


# ═══════════════════════════════════════════════════════════════════════════
# 3. SURFACE SWEEP (open section along spine → sheet body)
# ═══════════════════════════════════════════════════════════════════════════

def surface_sweep(
    profile: list[dict[str, Any]],
    profile_plane: Plane,
    spine: list[dict[str, Any]],
    spine_plane: Plane,
    frenet: bool = False,
) -> TopoDS_Shape:
    """Sweep an open profile along a spine wire → sheet body."""
    wire = TopoDS.Wire(place(path_wire(profile), profile_plane))
    path = TopoDS.Wire(place(path_wire(spine), spine_plane))
    mode = (
        GeomFill_Trihedron.GeomFill_IsFrenet
        if frenet
        else GeomFill_Trihedron.GeomFill_IsCorrectedFrenet
    )
    try:
        shape = BRepOffsetAPI_MakePipe(path, wire, mode, False).Shape()
    except Exception:  # noqa: BLE001
        shape = None
    return _check(shape, "surface sweep failed")


# ═══════════════════════════════════════════════════════════════════════════
# 4. BOUNDARY SURFACE (3–4 edge curves → face)
# ═══════════════════════════════════════════════════════════════════════════

def surface_boundary(
    edges: list[Geom_BSplineCurve],
    continuity: str = "G0",
) -> TopoDS_Shape:
    """Create a surface bounded by 3 or 4 NURBS edge curves.

    Uses BRepFill_Filling for flexibility (supports G0/G1/G2 constraints).
    """
    n = len(edges)
    if n < 3 or n > 4:
        raise GeomError("GEOM_INVALID_PARAM", "boundary surface needs 3 or 4 edges")
    cont = _CONT.get(continuity)
    if cont is None:
        raise GeomError("GEOM_INVALID_PARAM", f"continuity must be one of {list(_CONT)}")

    fill = BRepFill_Filling()
    for crv in edges:
        edge = TopoDS.Edge(curve_edge(crv))
        fill.Add(edge, cont)
    try:
        fill.Build()
        shape = fill.Face() if fill.IsDone() else None
    except Exception:  # noqa: BLE001
        shape = None
    return _check(shape, "boundary surface failed (edges may not form a closed loop)")


# ═══════════════════════════════════════════════════════════════════════════
# 5. FILL SURFACE (N-sided hole, BRepFill_Filling)
# ═══════════════════════════════════════════════════════════════════════════

def surface_fill(
    edges: list[Geom_BSplineCurve],
    continuity: str = "G0",
    points: list[list[float]] | None = None,
) -> TopoDS_Shape:
    """Fill an N-sided hole bounded by curves, optionally passing through interior points."""
    n = len(edges)
    if n < 2 or n > MAX_FILL_CONSTRAINTS:
        raise GeomError("GEOM_INVALID_PARAM", f"fill needs 2..{MAX_FILL_CONSTRAINTS} edges")
    cont = _CONT.get(continuity)
    if cont is None:
        raise GeomError("GEOM_INVALID_PARAM", f"continuity must be one of {list(_CONT)}")

    fill = BRepFill_Filling()
    for crv in edges:
        edge = TopoDS.Edge(curve_edge(crv))
        fill.Add(edge, cont)
    if points:
        for p in points:
            fill.Add(_gp(p))
    try:
        fill.Build()
        shape = fill.Face() if fill.IsDone() else None
    except Exception:  # noqa: BLE001
        shape = None
    return _check(shape, "fill surface failed")


# ═══════════════════════════════════════════════════════════════════════════
# 6. OPEN-SECTION EXTRUDE (wire → shell)
# ═══════════════════════════════════════════════════════════════════════════

def surface_extrude(
    geoms: list[dict[str, Any]],
    distance: float,
    direction: str = "+Z",
) -> TopoDS_Shape:
    """Extrude an open profile wire into a sheet body."""
    from core.geometry.features import DIRECTIONS, MAX_DISTANCE_MM

    if not (math.isfinite(distance) and 0 < distance <= MAX_DISTANCE_MM) or direction not in DIRECTIONS:
        raise GeomError("GEOM_INVALID_PARAM", "distance must be in (0, 1e6] mm, direction +Z/-Z")
    wire = path_wire(geoms)
    shape = BRepPrimAPI_MakePrism(wire, gp_Vec(0, 0, DIRECTIONS[direction] * distance)).Shape()
    return _check(shape, "surface extrude failed")


# ═══════════════════════════════════════════════════════════════════════════
# 7. OPEN-SECTION REVOLVE (wire → shell)
# ═══════════════════════════════════════════════════════════════════════════

def surface_revolve(
    geoms: list[dict[str, Any]],
    axis_point: tuple[float, float],
    axis_dir: tuple[float, float],
    angle_deg: float,
) -> TopoDS_Shape:
    """Revolve an open profile wire around an axis → sheet body."""
    n = math.hypot(*axis_dir)
    if n == 0 or not 0 < angle_deg <= 360:
        raise GeomError("GEOM_INVALID_PARAM", "axis must be non-zero, angle in (0, 360]")
    d = (axis_dir[0] / n, axis_dir[1] / n)
    wire = path_wire(geoms)
    ax = gp_Ax1(gp_Pnt(axis_point[0], axis_point[1], 0.0), gp_Dir(d[0], d[1], 0.0))
    shape = BRepPrimAPI_MakeRevol(wire, ax, math.radians(angle_deg)).Shape()
    return _check(shape, "surface revolve failed")


# ═══════════════════════════════════════════════════════════════════════════
# 8. OFFSET SURFACE
# ═══════════════════════════════════════════════════════════════════════════

def surface_offset(
    face_shape: TopoDS_Shape,
    offset: float,
) -> TopoDS_Shape:
    """Offset a face by a distance (positive = along face normal)."""
    if not math.isfinite(offset) or abs(offset) > MAX_OFFSET_MM or offset == 0:
        raise GeomError("GEOM_INVALID_PARAM", f"offset must be non-zero and ≤ {MAX_OFFSET_MM} mm")
    from OCP.BRepOffset import BRepOffset_MakeSimpleOffset

    ex = TopExp_Explorer(face_shape, TopAbs_FACE)
    if not ex.More():
        raise GeomError("GEOM_INVALID_PARAM", "input has no face to offset")
    try:
        mk = BRepOffset_MakeSimpleOffset(face_shape, offset)
        mk.Perform()
        shape = mk.GetResultShape() if mk.IsDone() else None
    except Exception:  # noqa: BLE001
        shape = None
    return _check(shape, "surface offset failed")


# ═══════════════════════════════════════════════════════════════════════════
# JOB ENTRY POINTS (run in kernel worker)
# ═══════════════════════════════════════════════════════════════════════════

def _surf_job(shape: TopoDS_Shape) -> tuple[bytes, dict[str, Any]]:
    """Standard job return for a surface feature."""
    m = sheet_measure(shape)
    return to_brep(shape), m


def surface_loft_job(
    sections: list[tuple[list[dict[str, Any]], Plane]],
    ruled: bool = False,
    solid: bool = False,
) -> tuple[bytes, dict[str, Any]]:
    return _surf_job(surface_loft(sections, ruled, solid))


def surface_sweep_job(
    profile: list[dict[str, Any]],
    profile_plane: Plane,
    spine: list[dict[str, Any]],
    spine_plane: Plane,
    frenet: bool = False,
) -> tuple[bytes, dict[str, Any]]:
    return _surf_job(surface_sweep(profile, profile_plane, spine, spine_plane, frenet))


def surface_extrude_job(
    geoms: list[dict[str, Any]],
    distance: float,
    direction: str,
    plane: Plane | None = None,
) -> tuple[bytes, dict[str, Any]]:
    shape = surface_extrude(geoms, distance, direction)
    if plane is not None:
        shape = place(shape, plane)
    return _surf_job(shape)


def surface_revolve_job(
    geoms: list[dict[str, Any]],
    axis_point: tuple[float, float],
    axis_dir: tuple[float, float],
    angle_deg: float,
    plane: Plane | None = None,
) -> tuple[bytes, dict[str, Any]]:
    shape = surface_revolve(geoms, axis_point, axis_dir, angle_deg)
    if plane is not None:
        shape = place(shape, plane)
    return _surf_job(shape)


def surface_offset_job(
    face_brep: bytes,
    offset: float,
) -> tuple[bytes, dict[str, Any]]:
    from core.geometry.serialize import from_brep

    shape = from_brep(face_brep)
    result = surface_offset(shape, offset)
    return _surf_job(result)


def surface_boundary_job(
    edge_point_sets: list[list[list[float]]],
    continuity: str = "G0",
) -> tuple[bytes, dict[str, Any]]:
    """Build boundary surface from edge point sets (each set → interpolated curve)."""
    curves = [nurbs_curve_interpolate(pts) for pts in edge_point_sets]
    shape = surface_boundary(curves, continuity)
    return _surf_job(shape)


def surface_fill_job(
    edge_point_sets: list[list[list[float]]],
    continuity: str = "G0",
    interior_points: list[list[float]] | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """Build fill surface from edge point sets (each → interpolated curve) + optional interior pts."""
    curves = [nurbs_curve_interpolate(pts) for pts in edge_point_sets]
    shape = surface_fill(curves, continuity, interior_points)
    return _surf_job(shape)
