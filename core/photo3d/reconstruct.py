"""Hybrid Photo-to-3D Reconstruction Engine (Parametric B-Rep + Multi-View Visual Hull).

Supports:
  1. ORTHO_MULTI (2-6 orthographic photos: FRONT/SIDE/TOP) -> calibrated B-Rep primitive or
     extruded/intersected solid with editable feature plan.
  2. TURNTABLE (4-36 sequential circular photos) -> 3D Visual Hull voxel carving across all
     azimuth angles, exported as watertight layered OCCT 8.0 B-Rep solid.
  3. FREE_ANGLE (1+ arbitrary photos) -> local silhouette/ratio inference + optional
     Antigravity SDK (`google-antigravity`) AI parametric shape reasoning.
"""

from __future__ import annotations

import math
from typing import Any, Literal

import numpy as np
from OCP.Bnd import Bnd_Box
from OCP.BRep import BRep_Builder
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
from OCP.BRepGProp import BRepGProp
from OCP.BRepPrimAPI import (
    BRepPrimAPI_MakeBox,
    BRepPrimAPI_MakeCone,
    BRepPrimAPI_MakeCylinder,
    BRepPrimAPI_MakePrism,
    BRepPrimAPI_MakeSphere,
)
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Vec
from OCP.GProp import GProp_GProps
from OCP.TopoDS import TopoDS_Compound, TopoDS_Shape
from scipy import ndimage

from core.geometry.serialize import to_brep
from core.underlay.trace import _moore_trace, _rdp

from . import (
    MAX_IMAGES,
    OutputMode,
    Photo3dError,
    PhotoInput,
    PhotoMode,
    ReconstructionResult,
    ReferenceScale,
    SilhouetteSummary,
)
from .antigravity_adapter import infer_parametric_plan_with_antigravity
from .silhouette import compute_mm_per_pixel, extract_silhouette, validate_image_bytes


def _shape_metrics(shape: TopoDS_Shape) -> tuple[tuple[float, float, float], float, float]:
    """Compute (bbox_dims_mm (W, D, H), volume_mm3, surface_area_mm2) via OCCT 8.0."""
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    pmin = box.CornerMin()
    pmax = box.CornerMax()
    dims = (
        max(0.0, float(pmax.X() - pmin.X())),
        max(0.0, float(pmax.Y() - pmin.Y())),
        max(0.0, float(pmax.Z() - pmin.Z())),
    )
    vprops = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, vprops)
    sprops = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, sprops)
    return dims, abs(float(vprops.Mass())), abs(float(sprops.Mass()))


def _build_parametric_shape(plan: dict[str, Any]) -> TopoDS_Shape:
    """Create an exact OCCT 8.0 B-Rep primitive solid from a validated parametric plan."""
    kind = str(plan.get("kind", "BOX")).upper()
    w = max(1.0, float(plan.get("width_mm", 50.0)))
    d = max(1.0, float(plan.get("depth_mm", 50.0)))
    h = max(1.0, float(plan.get("height_mm", 50.0)))
    r = max(0.5, float(plan.get("radius_mm", 0.5 * min(w, d))))

    if kind == "CYLINDER":
        ax = gp_Ax2(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0))
        return BRepPrimAPI_MakeCylinder(ax, r, h).Shape()
    if kind == "CONE":
        r2 = max(0.0, min(r * 0.95, float(plan.get("top_radius_mm", r * 0.4))))
        ax = gp_Ax2(gp_Pnt(0.0, 0.0, 0.0), gp_Dir(0.0, 0.0, 1.0))
        return BRepPrimAPI_MakeCone(ax, r, r2, h).Shape()
    if kind == "SPHERE":
        return BRepPrimAPI_MakeSphere(gp_Pnt(0.0, 0.0, r), r).Shape()
    # Default: BOX centered in XY, resting on Z=0
    return BRepPrimAPI_MakeBox(gp_Pnt(-0.5 * w, -0.5 * d, 0.0), w, d, h).Shape()


def _heuristic_parametric_plan(
    summaries: list[SilhouetteSummary],
    dims_mm: tuple[float, float, float],
) -> dict[str, Any]:
    """Deterministic local shape classification from multi-view circularity & fill ratios."""
    w, d, h = dims_mm
    top_views = [s for s in summaries if s.view_label in ("TOP", "BOTTOM")]
    side_views = [s for s in summaries if s.view_label not in ("TOP", "BOTTOM")]

    top_circ = max((s.circularity for s in top_views), default=0.0)
    side_fill = (
        float(np.mean([s.fill_ratio for s in side_views]))
        if side_views
        else summaries[0].fill_ratio
    )
    max_circ = max(s.circularity for s in summaries)

    # Sphere: high circularity across views & roughly equal W/D/H
    aspect_max = max(w, d, h) / max(1e-6, min(w, d, h))
    if (
        max_circ >= 0.84
        and len(summaries) >= 2
        and all(s.circularity >= 0.80 for s in summaries)
        and aspect_max <= 1.25
    ):
        r = (w + d + h) / 6.0
        return {
            "kind": "SPHERE",
            "width_mm": round(2 * r, 3),
            "depth_mm": round(2 * r, 3),
            "height_mm": round(2 * r, 3),
            "radius_mm": round(r, 3),
            "confidence": 0.88,
            "notes": "다각도 원형 실루엣 기반 구(Sphere) 자동 인식",
        }

    # Cylinder: circular top view OR near-equal W/D with rectangular side fill
    if top_circ >= 0.82 or (
        len(summaries) >= 2
        and abs(w - d) / max(w, d, 1.0) <= 0.12
        and side_fill >= 0.82
        and max_circ >= 0.82
    ):
        r = 0.25 * (w + d)
        return {
            "kind": "CYLINDER",
            "width_mm": round(2 * r, 3),
            "depth_mm": round(2 * r, 3),
            "height_mm": round(h, 3),
            "radius_mm": round(r, 3),
            "confidence": 0.86,
            "notes": "평면 원형/측면 직사각형 실루엣 기반 원통(Cylinder) 자동 인식",
        }

    # Cone: tapered side silhouette (fill_ratio ~ 0.45..0.72)
    if 0.42 <= side_fill <= 0.72 and top_circ >= 0.78:
        r = 0.25 * (w + d)
        return {
            "kind": "CONE",
            "width_mm": round(2 * r, 3),
            "depth_mm": round(2 * r, 3),
            "height_mm": round(h, 3),
            "radius_mm": round(r, 3),
            "top_radius_mm": round(r * 0.2, 3),
            "confidence": 0.80,
            "notes": "테이퍼 측면 실루엣 기반 원뿔(Cone) 자동 인식",
        }

    return {
        "kind": "BOX",
        "width_mm": round(w, 3),
        "depth_mm": round(d, 3),
        "height_mm": round(h, 3),
        "radius_mm": round(0.5 * min(w, d), 3),
        "confidence": 0.85,
        "notes": "다각도 외곽 실루엣 기반 직육면체(Box) 파라메트릭 생성",
    }


def _reconstruct_visual_hull_brep(
    masks: list[np.ndarray],
    angles_deg: list[float],
    target_dims_mm: tuple[float, float, float],
    *,
    grid_n: int = 24,
) -> TopoDS_Shape:
    """Carve a 3D Visual Hull voxel volume from sequential turntable silhouettes and build a watertight
    layered OCCT 8.0 B-Rep compound (24 Z-slices extruded from simplified 2D contours)."""
    w_mm, d_mm, h_mm = (max(1.0, float(v)) for v in target_dims_mm)
    nx = ny = nz = max(12, min(40, int(grid_n)))

    # Normalize each mask to its tight foreground crop so camera centering jitter cancels out
    cropped_masks: list[np.ndarray] = []
    for m in masks:
        ys, xs = np.nonzero(m)
        if ys.size == 0:
            continue
        cropped_masks.append(m[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1])

    if not cropped_masks:
        raise Photo3dError("PHOTO3D_NO_OBJECT", "Visual Hull 복원에 사용할 유효 실루엣이 없습니다")

    # Voxel centers in normalized [-0.5, 0.5] x [-0.5, 0.5] x [0, 1]
    xs_v = np.linspace(-0.5, 0.5, nx, dtype=np.float32)
    ys_v = np.linspace(-0.5, 0.5, ny, dtype=np.float32)
    zs_v = np.linspace(0.0, 1.0, nz, dtype=np.float32)
    zz, yy, xx = np.meshgrid(zs_v, ys_v, xs_v, indexing="ij")  # (nz, ny, nx)

    occ = np.ones((nz, ny, nx), dtype=bool)
    for cm, ang in zip(cropped_masks, angles_deg, strict=True):
        ch, cw = cm.shape
        rad = math.radians(ang)
        # Orthographic turntable projection: horizontal u = x*cos(a) + y*sin(a), vertical v = z
        u = xx * math.cos(rad) + yy * math.sin(rad)  # in [-0.707, 0.707], object spans [-0.5, 0.5]
        # Map u in [-0.5, 0.5] -> col in [0, cw-1], z in [0, 1] -> row in [ch-1, 0]
        col = np.rint((u + 0.5) * (cw - 1)).astype(np.int32)
        row = np.rint((1.0 - zz) * (ch - 1)).astype(np.int32)
        in_bounds = (col >= 0) & (col < cw) & (row >= 0) & (row < ch)
        col_c = np.clip(col, 0, cw - 1)
        row_c = np.clip(row, 0, ch - 1)
        hit = in_bounds & cm[row_c, col_c]
        occ &= hit

    if not np.any(occ):
        # Fallback to bounding box if silhouettes were misaligned
        return BRepPrimAPI_MakeBox(gp_Pnt(-0.5 * w_mm, -0.5 * d_mm, 0.0), w_mm, d_mm, h_mm).Shape()

    # Build layered OCCT B-Rep prisms for each occupied Z-slice
    builder = BRep_Builder()
    comp = TopoDS_Compound()
    builder.MakeCompound(comp)

    dz = h_mm / float(nz)
    sx = w_mm / float(nx)
    sy = d_mm / float(ny)
    made_any = False

    for iz in range(nz):
        sl = occ[iz]
        if not np.any(sl):
            continue
        sl_filled = ndimage.binary_fill_holes(sl)
        rs, cs = np.nonzero(sl_filled)
        if rs.size < 3:
            continue
        # Trace outer boundary of this Z-slice
        contour_rc = _moore_trace(sl_filled, int(rs[0]), int(cs[0]), max_steps=4000)
        if len(contour_rc) < 3:
            continue
        pts_xy = [
            ((c + 0.5) * sx - 0.5 * w_mm, (r + 0.5) * sy - 0.5 * d_mm) for (r, c) in contour_rc
        ]
        simp = _rdp(pts_xy + [pts_xy[0]], max(sx, sy) * 0.45)
        if len(simp) >= 2 and math.hypot(simp[0][0] - simp[-1][0], simp[0][1] - simp[-1][1]) < 1e-4:
            simp = simp[:-1]
        if len(simp) < 3:
            continue

        z0 = iz * dz
        poly = BRepBuilderAPI_MakePolygon()
        for px, py in simp:
            poly.Add(gp_Pnt(px, py, z0))
        poly.Close()
        if not poly.IsDone():
            continue
        face = BRepBuilderAPI_MakeFace(poly.Wire(), True)
        if not face.IsDone():
            continue
        prism = BRepPrimAPI_MakePrism(face.Face(), gp_Vec(0.0, 0.0, dz)).Shape()
        builder.Add(comp, prism)
        made_any = True

    if not made_any:
        return BRepPrimAPI_MakeBox(gp_Pnt(-0.5 * w_mm, -0.5 * d_mm, 0.0), w_mm, d_mm, h_mm).Shape()
    return comp


def reconstruct_from_photos(
    photos: list[PhotoInput],
    scale: ReferenceScale,
    *,
    mode: PhotoMode = "ORTHO_MULTI",
    output_mode: OutputMode = "HYBRID",
    use_ai: bool = False,
) -> ReconstructionResult:
    """Reconstruct a real-scale 3D CAD solid (OCCT 8.0 B-Rep) + feature plan from multi-angle/sequential photos."""
    if not photos:
        raise Photo3dError("PHOTO3D_NO_IMAGES", "최소 1장 이상의 사진을 업로드하세요")
    if len(photos) > MAX_IMAGES:
        raise Photo3dError("PHOTO3D_TOO_MANY", f"최대 {MAX_IMAGES}장까지 업로드할 수 있습니다")

    masks: list[np.ndarray] = []
    summaries: list[SilhouetteSummary] = []
    angles: list[float] = []
    n = len(photos)

    for idx, ph in enumerate(photos):
        img = validate_image_bytes(ph.data, ph.filename)
        if ph.angle_deg is not None:
            ang = float(ph.angle_deg)
        elif mode == "TURNTABLE":
            ang = (360.0 * idx) / float(n)
        elif ph.view_label in ("SIDE", "RIGHT"):
            ang = 90.0
        elif ph.view_label == "BACK":
            ang = 180.0
        elif ph.view_label == "LEFT":
            ang = 270.0
        else:
            ang = 0.0
        mask, summ = extract_silhouette(img, view_label=ph.view_label, angle_deg=ang)
        masks.append(mask)
        summaries.append(summ)
        angles.append(ang)

    # 1. Calibrate mm/px from primary (FRONT or first) silhouette
    front = next((s for s in summaries if s.view_label == "FRONT"), summaries[0])
    mm_per_px = compute_mm_per_pixel(scale, front)

    # Estimate real-world 3D bounding box (W_x, D_y, H_z) in mm
    side = next((s for s in summaries if s.view_label in ("SIDE", "RIGHT", "LEFT")), None)
    top = next((s for s in summaries if s.view_label in ("TOP", "BOTTOM")), None)

    w_mm = front.width_px * mm_per_px
    h_mm = front.height_px * mm_per_px
    if side is not None:
        d_mm = side.width_px * mm_per_px
    elif top is not None:
        d_mm = top.height_px * mm_per_px
    elif mode == "TURNTABLE" and len(summaries) >= 4:
        # Take orthogonal view closest to 90 deg for depth
        orth = min(summaries, key=lambda s: abs((s.angle_deg % 180.0) - 90.0))
        d_mm = orth.width_px * mm_per_px
    else:
        d_mm = w_mm  # single-photo default depth = width (overridable by AI)

    est_dims = (max(1.0, w_mm), max(1.0, d_mm), max(1.0, h_mm))
    warnings: list[str] = []

    # 2. Optional Antigravity SDK AI shape reasoning (opt-in only)
    ai_plan: dict[str, Any] | None = None
    ai_used = False
    ai_provider = "local-geometry"
    if use_ai:
        ai_plan, ai_provider, ai_warn = infer_parametric_plan_with_antigravity(
            summaries, est_dims, mode=mode
        )
        if ai_warn:
            warnings.append(ai_warn)
        if ai_plan is not None:
            ai_used = True

    # 3. Choose resolved output representation (PARAMETRIC vs VISUAL_HULL)
    resolved: Literal["PARAMETRIC", "VISUAL_HULL"]
    if output_mode == "VISUAL_HULL":
        resolved = "VISUAL_HULL"
    elif output_mode == "PARAMETRIC":
        resolved = "PARAMETRIC"
    else:
        # HYBRID: turntable >= 4 photos -> real-scale Visual Hull; ortho/single -> editable Parametric B-Rep
        resolved = "VISUAL_HULL" if (mode == "TURNTABLE" and len(masks) >= 4) else "PARAMETRIC"

    feature_plan = (
        ai_plan if ai_plan is not None else _heuristic_parametric_plan(summaries, est_dims)
    )

    if resolved == "VISUAL_HULL":
        shape = _reconstruct_visual_hull_brep(masks, angles, est_dims)
        feature_plan = {
            **feature_plan,
            "kind": "VISUAL_HULL",
            "photo_count": len(masks),
            "angles_deg": [round(a, 1) for a in angles],
        }
    else:
        shape = _build_parametric_shape(feature_plan)

    bbox_mm, vol_mm3, area_mm2 = _shape_metrics(shape)
    brep_bytes = to_brep(shape)

    return ReconstructionResult(
        mode=mode,
        output_mode=output_mode,
        resolved_output=resolved,
        bbox_mm=bbox_mm,
        volume_mm3=vol_mm3,
        surface_area_mm2=area_mm2,
        mm_per_px=mm_per_px,
        brep_bytes=brep_bytes,
        feature_plan=feature_plan,
        ai_used=ai_used,
        ai_provider=ai_provider,
        view_summaries=[
            {
                "view_label": s.view_label,
                "angle_deg": round(s.angle_deg, 1),
                "width_px": s.width_px,
                "height_px": s.height_px,
                "fill_ratio": s.fill_ratio,
                "circularity": s.circularity,
                "hole_count": s.hole_count,
            }
            for s in summaries
        ],
        warnings=warnings,
    )
