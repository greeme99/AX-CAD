"""S7: Surface editing & solidification — extend, trim, split, knit (sew), thicken, face replace.

Operates on *sheet* bodies (open shells) created by S6.  Knit (sewing) and thicken
convert sheets to solids.  All coordinate units are mm.
"""

from __future__ import annotations

import math
from typing import Any

from OCP.BRep import BRep_Tool
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakeSolid,
    BRepBuilderAPI_Sewing,
)
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeThickSolid
from OCP.BRepTools import BRepTools
from OCP.gp import gp_Dir, gp_Pln, gp_Pnt, gp_Vec
from OCP.GProp import GProp_GProps
from OCP.ShapeFix import ShapeFix_Shape
from OCP.TopAbs import TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape

from core.geometry.errors import GeomError
from core.geometry.serialize import from_brep, to_brep
from core.geometry.surface import sheet_measure

# ── limits ──────────────────────────────────────────────────────────────────
MAX_EXTEND_MM = 1e6
MAX_THICKEN_MM = 1e4
MAX_SEW_FACES = 500
SEW_TOLERANCE = 1e-3  # mm


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


def _count(shape: TopoDS_Shape, kind: int) -> int:
    n = 0
    ex = TopExp_Explorer(shape, kind)
    while ex.More():
        n += 1
        ex.Next()
    return n


def _volume(shape: TopoDS_Shape) -> float:
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return props.Mass()


def _surface_area(shape: TopoDS_Shape) -> float:
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, props)
    return props.Mass()


# ═══════════════════════════════════════════════════════════════════════════
# 1. SURFACE EXTEND
# ═══════════════════════════════════════════════════════════════════════════


def surface_extend(
    face_shape: TopoDS_Shape,
    distance: float,
    mode: str = "natural",
) -> TopoDS_Shape:
    """Extend a one-face sheet past all four of its sides, on its own surface (still a sheet)."""
    if not math.isfinite(distance) or distance <= 0 or distance > MAX_EXTEND_MM:
        raise GeomError("GEOM_INVALID_PARAM", f"distance must be in (0, {MAX_EXTEND_MM}]")
    if mode not in ("natural", "linear"):
        raise GeomError("GEOM_INVALID_PARAM", "mode must be 'natural' or 'linear'")
    if _count(face_shape, TopAbs_FACE) != 1:
        raise GeomError("GEOM_INVALID_PARAM", "extend takes a sheet of exactly one face")
    face = TopoDS.Face(TopExp_Explorer(face_shape, TopAbs_FACE).Current())
    surf = BRep_Tool.Surface_s(face)
    # ponytail: widens the UV range of the face's own surface, so a plane, cylinder, cone,
    # extrusion or revolution continues exactly. A NURBS patch ends at its poles and a trimmed
    # outline is lost -> both refused. Upgrade: GeomLib.ExtendSurfByLength (OCP cannot call it).
    u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
    whole = BRepBuilderAPI_MakeFace(surf, u0, u1, v0, v1, 1e-6).Face()
    if abs(_surface_area(whole) - _surface_area(face)) > 1e-6 * max(1.0, _surface_area(face)):
        raise GeomError("GEOM_INVALID_PARAM", "a trimmed face cannot be extended")
    p, du, dv = gp_Pnt(), gp_Vec(), gp_Vec()
    surf.D1((u0 + u1) / 2, (v0 + v1) / 2, p, du, dv)
    if du.Magnitude() < 1e-10 or dv.Magnitude() < 1e-10:
        raise GeomError("GEOM_FEATURE_FAILED", "degenerate face")
    eu, ev = distance / du.Magnitude(), distance / dv.Magnitude()
    a0, a1, b0, b1 = surf.Bounds()
    lo_u, hi_u = max(u0 - eu, a0), min(u1 + eu, a1)
    lo_v, hi_v = max(v0 - ev, b0), min(v1 + ev, b1)
    if surf.IsUPeriodic():
        hi_u = min(hi_u, lo_u + surf.UPeriod())
    if surf.IsVPeriodic():
        hi_v = min(hi_v, lo_v + surf.VPeriod())
    if (lo_u, hi_u, lo_v, hi_v) == (u0, u1, v0, v1):
        raise GeomError("GEOM_INVALID_PARAM", "this surface cannot be extended (NURBS patch)")
    out = BRepBuilderAPI_MakeFace(surf, lo_u, hi_u, lo_v, hi_v, 1e-6).Face()
    out.Orientation(face.Orientation())
    return _check(out, "surface extend failed")


# ═══════════════════════════════════════════════════════════════════════════
# 2. SURFACE TRIM (by plane)
# ═══════════════════════════════════════════════════════════════════════════


def surface_trim(
    face_shape: TopoDS_Shape,
    plane_origin: list[float],
    plane_normal: list[float],
    keep_side: str = "positive",
) -> TopoDS_Shape:
    """Trim (cut) a surface body by an infinite plane.

    keep_side: 'positive' keeps the half on the normal side, 'negative' the other.
    Uses Boolean Common with a half-space solid.
    """
    if keep_side not in ("positive", "negative"):
        raise GeomError("GEOM_INVALID_PARAM", "keep_side must be 'positive' or 'negative'")

    nx, ny, nz = [float(x) for x in plane_normal]
    n = math.sqrt(nx * nx + ny * ny + nz * nz)
    if n < 1e-10:
        raise GeomError("GEOM_INVALID_PARAM", "plane normal must be non-zero")
    nx, ny, nz = nx / n, ny / n, nz / n

    if keep_side == "negative":
        nx, ny, nz = -nx, -ny, -nz

    pln = gp_Pln(
        gp_Pnt(float(plane_origin[0]), float(plane_origin[1]), float(plane_origin[2])),
        gp_Dir(nx, ny, nz),
    )

    # Create a half-space solid on the positive side of the plane
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeHalfSpace

    face_mk = BRepBuilderAPI_MakeFace(pln, -1e6, 1e6, -1e6, 1e6)
    if not face_mk.IsDone():
        raise GeomError("GEOM_FEATURE_FAILED", "trim plane creation failed")
    plane_face = face_mk.Face()

    # Reference point on the keep side
    ref_pt = gp_Pnt(
        float(plane_origin[0]) + nx * 1.0,
        float(plane_origin[1]) + ny * 1.0,
        float(plane_origin[2]) + nz * 1.0,
    )

    try:
        hs = BRepPrimAPI_MakeHalfSpace(plane_face, ref_pt)
        half_solid = hs.Solid()
    except Exception:  # noqa: BLE001
        raise GeomError("GEOM_FEATURE_FAILED", "half-space creation failed") from None

    # Boolean common: keep the part of the surface inside the half-space
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common

    try:
        common = BRepAlgoAPI_Common(face_shape, half_solid)
        common.Build()
        shape = common.Shape() if common.IsDone() else None
    except Exception:  # noqa: BLE001
        shape = None

    return _check(shape, "surface trim failed")


# ═══════════════════════════════════════════════════════════════════════════
# 3. SURFACE SPLIT (by plane → two halves)
# ═══════════════════════════════════════════════════════════════════════════


def surface_split(
    face_shape: TopoDS_Shape,
    plane_origin: list[float],
    plane_normal: list[float],
) -> tuple[TopoDS_Shape, TopoDS_Shape]:
    """Split a surface body into two halves by an infinite plane.

    Returns (positive_half, negative_half).
    """
    pos = surface_trim(face_shape, plane_origin, plane_normal, "positive")
    neg = surface_trim(face_shape, plane_origin, plane_normal, "negative")
    return pos, neg


# ═══════════════════════════════════════════════════════════════════════════
# 4. SURFACE KNIT (Sewing → Solid if closed)
# ═══════════════════════════════════════════════════════════════════════════


def surface_knit(
    shapes: list[TopoDS_Shape],
    tolerance: float = SEW_TOLERANCE,
) -> TopoDS_Shape:
    """Sew faces/shells together.  If the result is a closed shell, promote to solid.

    § 23.5 verification: 6 faces of a box → solid with volume = theory.
    """
    if len(shapes) < 1 or len(shapes) > MAX_SEW_FACES:
        raise GeomError("GEOM_INVALID_PARAM", f"need 1..{MAX_SEW_FACES} shapes to sew")
    if not (0 < tolerance <= 1.0):
        raise GeomError("GEOM_INVALID_PARAM", "tolerance must be in (0, 1.0] mm")

    sewing = BRepBuilderAPI_Sewing(tolerance)
    for s in shapes:
        sewing.Add(s)
    sewing.Perform()
    sewn = sewing.SewedShape()

    if sewn is None or sewn.IsNull():
        raise GeomError("GEOM_FEATURE_FAILED", "sewing produced no result")

    sewn = _fix(sewn)

    # Try to promote closed shell → solid
    n_solids = _count(sewn, TopAbs_SOLID)
    if n_solids > 0:
        return _check(sewn, "sewing produced invalid solid")

    # Check if any shell is closed → make solid
    ex = TopExp_Explorer(sewn, TopAbs_SHELL)
    while ex.More():
        shell = TopoDS.Shell(ex.Current())
        if shell.Closed():
            try:
                mk = BRepBuilderAPI_MakeSolid(shell)
                if mk.IsDone():
                    solid = mk.Solid()
                    solid = _fix(solid)
                    if BRepCheck_Analyzer(solid).IsValid():
                        return solid
            except Exception:  # noqa: BLE001, S110
                pass  # shell promotion is best-effort; fall through to return sewn
        ex.Next()

    return _check(sewn, "sewing failed")


# ═══════════════════════════════════════════════════════════════════════════
# 5. SURFACE THICKEN (sheet → solid by offset)
# ═══════════════════════════════════════════════════════════════════════════


def surface_thicken(
    face_shape: TopoDS_Shape,
    thickness: float,
) -> TopoDS_Shape:
    """Thicken a sheet body (face/shell) into a solid by offsetting.

    § 23.5 verification: thickness volume = surface_area × |t|.
    """
    if not math.isfinite(thickness) or thickness == 0 or abs(thickness) > MAX_THICKEN_MM:
        raise GeomError(
            "GEOM_INVALID_PARAM", f"thickness must be non-zero and ≤ {MAX_THICKEN_MM} mm"
        )

    try:
        mk = BRepOffsetAPI_MakeThickSolid()
        mk.MakeThickSolidBySimple(face_shape, thickness)
        mk.Build()
        shape = mk.Shape() if mk.IsDone() else None
    except Exception:  # noqa: BLE001
        shape = None

    return _check(shape, "surface thicken failed")


# ═══════════════════════════════════════════════════════════════════════════
# 6. FACE REPLACE
# ═══════════════════════════════════════════════════════════════════════════


def surface_replace(
    body_shape: TopoDS_Shape,
    old_face_index: int,
    new_face_shape: TopoDS_Shape,
) -> TopoDS_Shape:
    """Replace a face in a body with a new face shape.

    old_face_index: 0-based index of the face to replace (ordered by TopExp_Explorer).
    new_face_shape: the replacement face/shell.
    """
    if old_face_index < 0:
        raise GeomError("GEOM_INVALID_PARAM", "face index must be >= 0")

    # Collect all faces
    faces: list[TopoDS_Shape] = []
    ex = TopExp_Explorer(body_shape, TopAbs_FACE)
    while ex.More():
        faces.append(ex.Current())
        ex.Next()

    if old_face_index >= len(faces):
        raise GeomError(
            "GEOM_INVALID_PARAM",
            f"face index {old_face_index} out of range (have {len(faces)} faces)",
        )

    # Sew all faces except the old one, plus the new one
    sewing = BRepBuilderAPI_Sewing(SEW_TOLERANCE)
    for i, f in enumerate(faces):
        if i != old_face_index:
            sewing.Add(f)
    # Add replacement
    sewing.Add(new_face_shape)
    sewing.Perform()
    result = sewing.SewedShape()

    if result is None or result.IsNull():
        raise GeomError("GEOM_FEATURE_FAILED", "face replace sewing failed")

    result = _fix(result)

    # Try solid promotion if closed
    n_solids = _count(result, TopAbs_SOLID)
    if n_solids > 0:
        return _check(result, "face replace produced invalid solid")

    ex_shell = TopExp_Explorer(result, TopAbs_SHELL)
    while ex_shell.More():
        shell = TopoDS.Shell(ex_shell.Current())
        if shell.Closed():
            try:
                mk = BRepBuilderAPI_MakeSolid(shell)
                if mk.IsDone():
                    return _check(_fix(mk.Solid()), "face replace solid promotion failed")
            except Exception:  # noqa: BLE001, S110
                pass  # solid promotion is best-effort
        ex_shell.Next()

    return _check(result, "face replace failed")


# ═══════════════════════════════════════════════════════════════════════════
# METRICS
# ═══════════════════════════════════════════════════════════════════════════


def solid_measure(shape: TopoDS_Shape) -> dict[str, Any]:
    """Extended metrics that include volume (for knit/thicken results that may be solid)."""
    m = sheet_measure(shape)
    if m["kind"] == "solid":
        m["volume_mm3"] = abs(_volume(shape))
    return m


# ═══════════════════════════════════════════════════════════════════════════
# JOB ENTRY POINTS (run in kernel worker)
# ═══════════════════════════════════════════════════════════════════════════


def _edit_job(shape: TopoDS_Shape) -> tuple[bytes, dict[str, Any]]:
    """Standard job return for a surface edit feature."""
    m = solid_measure(shape)
    return to_brep(shape), m


def surface_extend_job(
    face_brep: bytes,
    distance: float,
    mode: str = "natural",
) -> tuple[bytes, dict[str, Any]]:
    shape = from_brep(face_brep)
    result = surface_extend(shape, distance, mode)
    return _edit_job(result)


def surface_trim_job(
    face_brep: bytes,
    plane_origin: list[float],
    plane_normal: list[float],
    keep_side: str = "positive",
) -> tuple[bytes, dict[str, Any]]:
    shape = from_brep(face_brep)
    result = surface_trim(shape, plane_origin, plane_normal, keep_side)
    return _edit_job(result)


def surface_split_job(
    face_brep: bytes,
    plane_origin: list[float],
    plane_normal: list[float],
) -> tuple[bytes, dict[str, Any]]:
    """Split returns the positive half as the primary result (negative available via re-split)."""
    shape = from_brep(face_brep)
    pos, _neg = surface_split(shape, plane_origin, plane_normal)
    return _edit_job(pos)


def surface_knit_job(
    brep_list: list[bytes],
    tolerance: float = SEW_TOLERANCE,
) -> tuple[bytes, dict[str, Any]]:
    shapes = [from_brep(b) for b in brep_list]
    result = surface_knit(shapes, tolerance)
    return _edit_job(result)


def surface_thicken_job(
    face_brep: bytes,
    thickness: float,
) -> tuple[bytes, dict[str, Any]]:
    shape = from_brep(face_brep)
    result = surface_thicken(shape, thickness)
    return _edit_job(result)


def surface_replace_job(
    body_brep: bytes,
    new_face_brep: bytes,
    face_index: int,
) -> tuple[bytes, dict[str, Any]]:
    body = from_brep(body_brep)
    new_face = from_brep(new_face_brep)
    result = surface_replace(body, face_index, new_face)
    return _edit_job(result)
