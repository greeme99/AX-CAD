"""2D laser & sheet-metal flat-pattern cutting toolpath (plan.md §24 C4)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from core.cam.mill25d import (
    RAPID_MM_MIN,
    Point2D,
    ToolpathResult,
    ToolpathSegment,
    _append_seg,
    _clean_loop,
    ensure_ccw,
    offset_polygon,
    point_in_polygon,
    signed_area,
)
from core.cam.tools import CamError, CamTool, check_finite


@dataclass(frozen=True)
class LaserCutOrderItem:
    loop_index: int
    depth: int  # 0 = outer contour, 1 = inner hole, 2 = island inside hole, ...
    is_hole: bool
    lead_in_start: Point2D
    pierce_collision: bool


def _seg_intersects(a0: Point2D, a1: Point2D, b0: Point2D, b1: Point2D) -> bool:
    """Return True if closed 2D segments a0-a1 and b0-b1 properly intersect."""

    def cross(o: Point2D, p: Point2D, q: Point2D) -> float:
        return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])

    d1 = cross(b0, b1, a0)
    d2 = cross(b0, b1, a1)
    d3 = cross(a0, a1, b0)
    d4 = cross(a0, a1, b1)
    return ((d1 > 1e-9 and d2 < -1e-9) or (d1 < -1e-9 and d2 > 1e-9)) and (
        (d3 > 1e-9 and d4 < -1e-9) or (d3 < -1e-9 and d4 > 1e-9)
    )


def _segment_collides_loops(
    p0: Point2D,
    p1: Point2D,
    loops: list[list[Point2D]],
    skip_idx: int,
) -> bool:
    for idx, poly in enumerate(loops):
        if idx == skip_idx:
            continue
        n = len(poly)
        for i in range(n):
            if _seg_intersects(p0, p1, poly[i], poly[(i + 1) % n]):
                return True
    return False


def _edge_key(a: Point2D, b: Point2D) -> tuple[int, int, int, int]:
    ka = (round(a[0] * 1000), round(a[1] * 1000))
    kb = (round(b[0] * 1000), round(b[1] * 1000))
    return (ka[0], ka[1], kb[0], kb[1]) if ka <= kb else (kb[0], kb[1], ka[0], ka[1])


def laser_toolpath(
    loops: list[list[Point2D]],
    *,
    kerf_mm: float = 0.2,
    lead_in_mm: float = 2.0,
    power_pct: float = 80.0,
    feed_xy: float = 3000.0,
    common_line: bool = False,
) -> tuple[ToolpathResult, list[LaserCutOrderItem]]:
    """Generate 2D laser cutting toolpath:
    1. Compute containment hierarchy -> topological sort (inner holes first, outer contour last).
    2. Apply kerf/2 offset (inward for holes, outward for outer contours).
    3. Place lead-in inward for holes and outward for outer contours, shrinking if needed to prevent collision.
    4. Optionally skip already-cut common lines between adjacent parts.
    """
    if not loops:
        raise CamError("CAM_INVALID_PARAM", "laser_toolpath requires at least one closed loop")
    kerf = check_finite(kerf_mm, "kerf_mm", non_negative=True)
    lead = check_finite(lead_in_mm, "lead_in_mm", non_negative=True)
    pw = check_finite(power_pct, "power_pct", positive=True)
    fxy = check_finite(feed_xy, "feed_xy", positive=True)

    clean_loops = [ensure_ccw(_clean_loop(lp)) for lp in loops]
    n_loops = len(clean_loops)

    # 1. Nesting depth: count how many other loops strictly contain loop i's first vertex
    depths: list[int] = []
    for i in range(n_loops):
        test_pt = clean_loops[i][0]
        d = sum(1 for j in range(n_loops) if j != i and point_in_polygon(test_pt, clean_loops[j]))
        depths.append(d)

    # Sort descending by nesting depth (inner holes first), then by area ascending
    order = sorted(range(n_loops), key=lambda i: (-depths[i], abs(signed_area(clean_loops[i]))))

    # 2. Apply kerf/2 offset
    half_kerf = 0.5 * kerf
    offset_loops: list[list[Point2D]] = []
    for i in range(n_loops):
        is_hole = (depths[i] % 2) == 1
        delta = -half_kerf if is_hole else half_kerf
        off = offset_polygon(clean_loops[i], delta) if half_kerf > 1e-9 else clean_loops[i]
        if off is None:
            raise CamError("CAM_TOOL_TOO_LARGE", f"kerf ({kerf} mm) collapses loop {i}")
        offset_loops.append(off)

    # 3. Build segments with collision-free lead-in & optional common-line dedup
    segments: list[ToolpathSegment] = []
    cut_order: list[LaserCutOrderItem] = []
    seen_edges: set[tuple[int, int, int, int]] = set()
    cur: tuple[float, float, float] = (0.0, 0.0, 0.0)

    for idx in order:
        poly = offset_loops[idx]
        is_hole = (depths[idx] % 2) == 1
        p0, p1 = poly[0], poly[1]
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        elen = math.hypot(dx, dy) or 1.0
        # CCW loop: outward normal = (dy/elen, -dx/elen), inward normal = (-dy/elen, dx/elen)
        nx, ny = (-dy / elen, dx / elen) if is_hole else (dy / elen, -dx / elen)

        # Try shrinking lead-in until collision-free (and inside hole for holes)
        lead_pt = p0
        collided = False
        if lead > 1e-6:
            scale = 1.0
            found = False
            for _ in range(8):
                cand = (p0[0] + nx * lead * scale, p0[1] + ny * lead * scale)
                in_ok = (
                    point_in_polygon(cand, poly) if is_hole else not point_in_polygon(cand, poly)
                )
                if in_ok and not _segment_collides_loops(cand, p0, offset_loops, idx):
                    lead_pt = cand
                    found = True
                    break
                scale *= 0.5
            if not found:
                lead_pt = p0
                collided = False  # fallback to direct pierce on contour (0 collision)

        _append_seg(segments, ToolpathSegment("RAPID", cur, (lead_pt[0], lead_pt[1], 0.0)))
        if math.hypot(lead_pt[0] - p0[0], lead_pt[1] - p0[1]) > 1e-9:
            _append_seg(
                segments,
                ToolpathSegment(
                    "CUT", (lead_pt[0], lead_pt[1], 0.0), (p0[0], p0[1], 0.0), feed=fxy
                ),
            )

        pos = p0
        for i in range(len(poly)):
            a = poly[i]
            b = poly[(i + 1) % len(poly)]
            ek = _edge_key(a, b)
            if common_line and ek in seen_edges:
                _append_seg(
                    segments, ToolpathSegment("RAPID", (pos[0], pos[1], 0.0), (b[0], b[1], 0.0))
                )
                pos = b
                continue
            if common_line:
                seen_edges.add(ek)
            if math.hypot(pos[0] - a[0], pos[1] - a[1]) > 1e-9:
                _append_seg(
                    segments, ToolpathSegment("RAPID", (pos[0], pos[1], 0.0), (a[0], a[1], 0.0))
                )
            _append_seg(
                segments,
                ToolpathSegment("CUT", (a[0], a[1], 0.0), (b[0], b[1], 0.0), feed=fxy),
            )
            pos = b

        cur = (pos[0], pos[1], 0.0)
        cut_order.append(
            LaserCutOrderItem(
                loop_index=idx,
                depth=depths[idx],
                is_hole=is_hole,
                lead_in_start=lead_pt,
                pierce_collision=collided,
            )
        )

    tool = CamTool(
        tool_code="T90",
        tool_type="LASER_NOZZLE",
        diameter_mm=max(kerf, 0.05),
        flutes=1,
        material_group="SHEET",
        spindle_rpm=100,
        feed_xy_mm_min=fxy,
        feed_z_mm_min=1000.0,
        stepdown_mm=1.0,
        stepover_ratio=0.5,
        laser_power_pct=min(pw, 100.0),
    )
    cut_len = sum(s.length_mm for s in segments if s.kind != "RAPID")
    rapid_len = sum(s.length_mm for s in segments if s.kind == "RAPID")
    cycle_time_s = (cut_len / tool.feed_xy_mm_min + rapid_len / RAPID_MM_MIN) * 60.0
    result = ToolpathResult(
        segments=segments,
        cut_length_mm=cut_len,
        plunge_length_mm=0.0,
        rapid_length_mm=rapid_len,
        cycle_time_s=cycle_time_s,
        z_passes=1,
    )
    return result, cut_order


def loops_from_dxf_bytes(dxf_bytes: bytes, layer: str | None = None) -> list[list[Point2D]]:
    """Extract closed 2D loops (LWPOLYLINE / CIRCLE) from a flat-pattern or 2D DXF."""
    import io

    import ezdxf

    doc = ezdxf.read(io.StringIO(dxf_bytes.decode("utf-8", errors="replace")))
    msp = doc.modelspace()
    out: list[list[Point2D]] = []
    for e in msp:
        if layer is not None and str(e.dxf.get("layer", "")).upper() != layer.upper():
            continue
        kind = e.dxftype()
        if kind == "LWPOLYLINE" and bool(getattr(e, "closed", False)):
            get_pts = getattr(e, "get_points", None)
            pts = [(float(p[0]), float(p[1])) for p in (get_pts("xy") if get_pts else [])]
            if len(pts) >= 3:
                out.append(pts)
        elif kind == "CIRCLE":
            cx, cy = float(e.dxf.center.x), float(e.dxf.center.y)
            r = float(e.dxf.radius)
            if r > 1e-6:
                n = 24
                out.append(
                    [
                        (cx + r * math.cos(math.tau * i / n), cy + r * math.sin(math.tau * i / n))
                        for i in range(n)
                    ]
                )
    return out
