"""2.5D milling toolpath generation: contour, pocket, drill (plan.md §24 C1)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

from core.cam.tools import CamError, CamTool, check_finite

MAX_SEGMENTS = 500_000
MAX_Z_PASSES = 500
RAPID_MM_MIN = 5000.0

Point2D = tuple[float, float]
Point3D = tuple[float, float, float]
SegKind = Literal["RAPID", "CUT", "PLUNGE", "ARC_CW", "ARC_CCW"]


@dataclass(frozen=True)
class ToolpathSegment:
    kind: SegKind
    start: Point3D
    end: Point3D
    center: Point2D | None = None
    feed: float | None = None

    @property
    def length_mm(self) -> float:
        dx = self.end[0] - self.start[0]
        dy = self.end[1] - self.start[1]
        dz = self.end[2] - self.start[2]
        if self.kind in ("ARC_CW", "ARC_CCW") and self.center is not None:
            cx, cy = self.center
            r = math.hypot(self.start[0] - cx, self.start[1] - cy)
            if r <= 1e-12:
                return 0.0
            a0 = math.atan2(self.start[1] - cy, self.start[0] - cx)
            a1 = math.atan2(self.end[1] - cy, self.end[0] - cx)
            da = a1 - a0
            if self.kind == "ARC_CCW":
                while da <= 0:
                    da += math.tau
            else:
                while da >= 0:
                    da -= math.tau
            arc_len = abs(da) * r
            return math.hypot(arc_len, dz)
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind,
            "start": [round(self.start[0], 6), round(self.start[1], 6), round(self.start[2], 6)],
            "end": [round(self.end[0], 6), round(self.end[1], 6), round(self.end[2], 6)],
        }
        if self.center is not None:
            out["center"] = [round(self.center[0], 6), round(self.center[1], 6)]
        if self.feed is not None:
            out["feed"] = round(self.feed, 3)
        return out


@dataclass
class ToolpathResult:
    segments: list[ToolpathSegment] = field(default_factory=list)
    cut_length_mm: float = 0.0
    plunge_length_mm: float = 0.0
    rapid_length_mm: float = 0.0
    cycle_time_s: float = 0.0
    z_passes: int = 0
    drill_cycles: list[dict[str, float]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_count": len(self.segments),
            "cut_length_mm": round(self.cut_length_mm, 4),
            "plunge_length_mm": round(self.plunge_length_mm, 4),
            "rapid_length_mm": round(self.rapid_length_mm, 4),
            "cycle_time_s": round(self.cycle_time_s, 4),
            "z_passes": self.z_passes,
            "drill_cycles": self.drill_cycles,
            "segments": [s.to_dict() for s in self.segments],
        }


def _clean_loop(loop: list[Point2D]) -> list[Point2D]:
    """Validate and remove consecutive duplicate points and closing duplicate."""
    if len(loop) < 3:
        raise CamError("CAM_INVALID_PARAM", "polygon loop requires at least 3 vertices")
    pts: list[Point2D] = []
    for p in loop:
        x = check_finite(p[0], "x")
        y = check_finite(p[1], "y")
        if not pts or math.hypot(x - pts[-1][0], y - pts[-1][1]) > 1e-9:
            pts.append((x, y))
    if len(pts) > 1 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) <= 1e-9:
        pts.pop()
    if len(pts) < 3:
        raise CamError("CAM_INVALID_PARAM", "degenerate polygon loop (< 3 distinct vertices)")
    return pts


def signed_area(loop: list[Point2D]) -> float:
    area = 0.0
    n = len(loop)
    for i in range(n):
        x0, y0 = loop[i]
        x1, y1 = loop[(i + 1) % n]
        area += x0 * y1 - x1 * y0
    return 0.5 * area


def ensure_ccw(loop: list[Point2D]) -> list[Point2D]:
    return list(loop) if signed_area(loop) >= 0 else list(reversed(loop))


def _bbox(loop: list[Point2D]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in loop]
    ys = [p[1] for p in loop]
    return min(xs), min(ys), max(xs), max(ys)


def _is_axis_aligned_rect(loop: list[Point2D]) -> tuple[float, float, float, float] | None:
    if len(loop) != 4:
        return None
    x0, y0, x1, y1 = _bbox(loop)
    if x1 - x0 <= 1e-9 or y1 - y0 <= 1e-9:
        return None
    corners = {(x0, y0), (x1, y0), (x1, y1), (x0, y1)}
    for p in loop:
        if not any(math.hypot(p[0] - c[0], p[1] - c[1]) <= 1e-6 for c in corners):
            return None
    return (x0, y0, x1, y1)


def offset_polygon(loop: list[Point2D], delta: float) -> list[Point2D] | None:
    """Offset a CCW polygon outward (delta > 0) or inward (delta < 0).

    Returns None when inward offset collapses the polygon.
    """
    pts = ensure_ccw(_clean_loop(loop))
    if abs(delta) <= 1e-12:
        return pts

    rect = _is_axis_aligned_rect(pts)
    if rect is not None:
        x0, y0, x1, y1 = rect
        nx0, ny0, nx1, ny1 = x0 - delta, y0 - delta, x1 + delta, y1 + delta
        if nx1 - nx0 <= 1e-6 or ny1 - ny0 <= 1e-6:
            return None
        return [(nx0, ny0), (nx1, ny0), (nx1, ny1), (nx0, ny1)]

    n = len(pts)
    # Build offset edge lines (right-hand outward normal for CCW loop)
    lines: list[tuple[Point2D, Point2D]] = []
    for i in range(n):
        p0 = pts[i]
        p1 = pts[(i + 1) % n]
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        seg_len = math.hypot(dx, dy)
        if seg_len <= 1e-12:
            continue
        nx, ny = dy / seg_len, -dx / seg_len  # outward unit normal for CCW
        lines.append(((p0[0] + nx * delta, p0[1] + ny * delta), (dx / seg_len, dy / seg_len)))

    if len(lines) < 3:
        return None

    out: list[Point2D] = []
    m = len(lines)
    for i in range(m):
        p0, d0 = lines[i - 1]
        p1, d1 = lines[i]
        det = d0[0] * d1[1] - d0[1] * d1[0]
        if abs(det) <= 1e-9:
            out.append(p1)
        else:
            rx, ry = p1[0] - p0[0], p1[1] - p0[1]
            t = (rx * d1[1] - ry * d1[0]) / det
            out.append((p0[0] + t * d0[0], p0[1] + t * d0[1]))

    # Reject collapsed/inverted inward polygons
    area = signed_area(out)
    if area <= 1e-6:
        return None
    if delta < 0 and area >= signed_area(pts) - 1e-6:
        return None
    return out


def point_in_polygon(pt: Point2D, poly: list[Point2D]) -> bool:
    """Ray-casting point-in-polygon test."""
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        if (y0 > y) != (y1 > y):
            x_int = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
            if x < x_int:
                inside = not inside
    return inside


def _z_levels(depth: float, stepdown: float, top_z: float) -> list[float]:
    d = check_finite(depth, "depth", positive=True)
    sd = check_finite(stepdown, "stepdown", positive=True)
    tz = check_finite(top_z, "top_z")
    n_passes = math.ceil(d / sd - 1e-9)
    if n_passes > MAX_Z_PASSES:
        raise CamError(
            "CAM_LIMIT_EXCEEDED", f"z passes ({n_passes}) exceed MAX_Z_PASSES ({MAX_Z_PASSES})"
        )
    levels: list[float] = []
    for i in range(1, n_passes + 1):
        z = tz - min(i * sd, d)
        levels.append(z)
    return levels


def _finalize(
    segments: list[ToolpathSegment],
    tool: CamTool,
    z_passes: int,
    drill_cycles: list[dict[str, float]] | None = None,
) -> ToolpathResult:
    if len(segments) > MAX_SEGMENTS:
        raise CamError(
            "CAM_LIMIT_EXCEEDED",
            f"segment count ({len(segments)}) exceeds MAX_SEGMENTS ({MAX_SEGMENTS})",
        )
    cut_len = 0.0
    plunge_len = 0.0
    rapid_len = 0.0
    for seg in segments:
        ln = seg.length_mm
        if seg.kind == "RAPID":
            rapid_len += ln
        elif seg.kind == "PLUNGE":
            plunge_len += ln
        else:
            cut_len += ln
    cycle_time_s = (
        cut_len / tool.feed_xy_mm_min + plunge_len / tool.feed_z_mm_min + rapid_len / RAPID_MM_MIN
    ) * 60.0
    return ToolpathResult(
        segments=segments,
        cut_length_mm=cut_len,
        plunge_length_mm=plunge_len,
        rapid_length_mm=rapid_len,
        cycle_time_s=cycle_time_s,
        z_passes=z_passes,
        drill_cycles=drill_cycles or [],
    )


def _append_seg(segments: list[ToolpathSegment], seg: ToolpathSegment) -> None:
    if len(segments) >= MAX_SEGMENTS:
        raise CamError("CAM_LIMIT_EXCEEDED", f"segment count exceeds MAX_SEGMENTS ({MAX_SEGMENTS})")
    segments.append(seg)


def contour(
    loops: list[list[Point2D]],
    tool: CamTool,
    depth: float,
    stepdown: float | None = None,
    *,
    side: Literal["OUTSIDE", "INSIDE", "ON"] = "OUTSIDE",
    lead_in_r: float = 0.0,
    safe_z: float = 20.0,
    top_z: float = 0.0,
) -> ToolpathResult:
    """Generate 2.5D contour toolpath with +/-R offset, multi-Z passes, and optional arc lead-in/out."""
    if not loops:
        raise CamError("CAM_INVALID_PARAM", "contour requires at least one loop")
    if side not in ("OUTSIDE", "INSIDE", "ON"):
        raise CamError("CAM_INVALID_PARAM", f"invalid contour side: {side}")
    sd = tool.stepdown_mm if stepdown is None else stepdown
    lr = check_finite(lead_in_r, "lead_in_r", non_negative=True)
    sz = check_finite(safe_z, "safe_z")
    levels = _z_levels(depth, sd, top_z)
    delta = tool.radius_mm if side == "OUTSIDE" else (-tool.radius_mm if side == "INSIDE" else 0.0)

    segments: list[ToolpathSegment] = []
    cur: Point3D = (0.0, 0.0, sz)

    for raw_loop in loops:
        path = offset_polygon(raw_loop, delta)
        if path is None:
            raise CamError("CAM_TOOL_TOO_LARGE", "tool radius collapses contour loop")
        # Compute tangent/normal at start vertex for optional arc lead-in/out
        p0, p1 = path[0], path[1]
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        elen = math.hypot(dx, dy) or 1.0
        tx, ty = dx / elen, dy / elen
        # Outward normal for OUTSIDE, inward normal for INSIDE
        sign = 1.0 if side == "OUTSIDE" else -1.0
        nx, ny = sign * (dy / elen), sign * (-dx / elen)

        for z in levels:
            if lr > 1e-6:
                cx, cy = p0[0] + nx * lr, p0[1] + ny * lr
                entry = (cx - tx * lr, cy - ty * lr)
                _append_seg(segments, ToolpathSegment("RAPID", cur, (entry[0], entry[1], sz)))
                _append_seg(
                    segments,
                    ToolpathSegment(
                        "PLUNGE",
                        (entry[0], entry[1], sz),
                        (entry[0], entry[1], z),
                        feed=tool.feed_z_mm_min,
                    ),
                )
                arc_kind: SegKind = "ARC_CCW" if side == "INSIDE" else "ARC_CW"
                _append_seg(
                    segments,
                    ToolpathSegment(
                        arc_kind,
                        (entry[0], entry[1], z),
                        (p0[0], p0[1], z),
                        center=(cx, cy),
                        feed=tool.feed_xy_mm_min,
                    ),
                )
            else:
                _append_seg(segments, ToolpathSegment("RAPID", cur, (p0[0], p0[1], sz)))
                _append_seg(
                    segments,
                    ToolpathSegment(
                        "PLUNGE", (p0[0], p0[1], sz), (p0[0], p0[1], z), feed=tool.feed_z_mm_min
                    ),
                )
            # Traverse closed loop
            for i in range(len(path)):
                a = path[i]
                b = path[(i + 1) % len(path)]
                _append_seg(
                    segments,
                    ToolpathSegment(
                        "CUT", (a[0], a[1], z), (b[0], b[1], z), feed=tool.feed_xy_mm_min
                    ),
                )
            if lr > 1e-6:
                cx, cy = p0[0] + nx * lr, p0[1] + ny * lr
                exit_pt = (cx + tx * lr, cy + ty * lr)
                arc_kind = "ARC_CCW" if side == "INSIDE" else "ARC_CW"
                _append_seg(
                    segments,
                    ToolpathSegment(
                        arc_kind,
                        (p0[0], p0[1], z),
                        (exit_pt[0], exit_pt[1], z),
                        center=(cx, cy),
                        feed=tool.feed_xy_mm_min,
                    ),
                )
                end_xy = exit_pt
            else:
                end_xy = p0
            _append_seg(
                segments,
                ToolpathSegment("RAPID", (end_xy[0], end_xy[1], z), (end_xy[0], end_xy[1], sz)),
            )
            cur = (end_xy[0], end_xy[1], sz)

    return _finalize(segments, tool, len(levels))


def pocket(
    outer: list[Point2D],
    islands: list[list[Point2D]] | None,
    tool: CamTool,
    depth: float,
    stepdown: float | None = None,
    stepover: float | None = None,
    *,
    safe_z: float = 20.0,
    top_z: float = 0.0,
) -> ToolpathResult:
    """Generate contour-parallel inward-offset 2.5D pocket toolpath with zero uncut islands."""
    sd = tool.stepdown_mm if stepdown is None else stepdown
    so_ratio = tool.stepover_ratio if stepover is None else stepover
    check_finite(so_ratio, "stepover", positive=True)
    if not (0.05 <= so_ratio <= 0.95):
        raise CamError("CAM_INVALID_PARAM", f"stepover must be in [0.05, 0.95] (got {so_ratio})")
    sz = check_finite(safe_z, "safe_z")
    levels = _z_levels(depth, sd, top_z)
    clean_outer = ensure_ccw(_clean_loop(outer))
    clean_islands = [ensure_ccw(_clean_loop(isl)) for isl in (islands or [])]

    step_mm = tool.diameter_mm * so_ratio
    # Precompute inward contour-parallel rings + optional centerline clearing pass
    rings: list[list[Point2D]] = []
    center_pass: tuple[Point2D, Point2D] | None = None

    rect = _is_axis_aligned_rect(clean_outer)
    offset_dist = tool.radius_mm
    max_rings = 2000
    for _ in range(max_rings):
        ring = offset_polygon(clean_outer, -offset_dist)
        if ring is None:
            # Check if a centerline pass is needed for axis-aligned rectangle (e.g., 100x60 Ø10 step 5 -> y=30)
            if rect is not None and not clean_islands:
                x0, y0, x1, y1 = rect
                nx0, ny0, nx1, ny1 = (
                    x0 + offset_dist,
                    y0 + offset_dist,
                    x1 - offset_dist,
                    y1 - offset_dist,
                )
                w = nx1 - nx0
                h = ny1 - ny0
                # If exactly one dimension collapsed (0 width or 0 height) while the other is > 0
                if abs(h) <= 1e-6 and w > 1e-6:
                    cy = 0.5 * (y0 + y1)
                    center_pass = ((nx0, cy), (nx1, cy))
                elif abs(w) <= 1e-6 and h > 1e-6:
                    cx = 0.5 * (x0 + x1)
                    center_pass = ((cx, ny0), (cx, ny1))
            break
        rings.append(ring)
        offset_dist += step_mm

    if not rings:
        raise CamError("CAM_TOOL_TOO_LARGE", "tool diameter exceeds pocket boundary")

    # Precompute island keep-out rings (offset outward by tool radius)
    island_rings: list[list[Point2D]] = []
    for isl in clean_islands:
        keepout = offset_polygon(isl, tool.radius_mm)
        if keepout is not None:
            island_rings.append(keepout)

    segments: list[ToolpathSegment] = []
    cur: Point3D = (0.0, 0.0, sz)

    for z in levels:
        first_pt = rings[0][0]
        _append_seg(segments, ToolpathSegment("RAPID", cur, (first_pt[0], first_pt[1], sz)))
        _append_seg(
            segments,
            ToolpathSegment(
                "PLUNGE",
                (first_pt[0], first_pt[1], sz),
                (first_pt[0], first_pt[1], z),
                feed=tool.feed_z_mm_min,
            ),
        )
        pos_xy = first_pt
        for r_idx, ring in enumerate(rings):
            start_xy = ring[0]
            if r_idx > 0:
                _append_seg(
                    segments,
                    ToolpathSegment(
                        "CUT",
                        (pos_xy[0], pos_xy[1], z),
                        (start_xy[0], start_xy[1], z),
                        feed=tool.feed_xy_mm_min,
                    ),
                )
            for i in range(len(ring)):
                a = ring[i]
                b = ring[(i + 1) % len(ring)]
                # Skip segments whose midpoint lies strictly inside an island keepout
                mid = (0.5 * (a[0] + b[0]), 0.5 * (a[1] + b[1]))
                if any(point_in_polygon(mid, ko) for ko in island_rings):
                    _append_seg(
                        segments, ToolpathSegment("RAPID", (a[0], a[1], z), (a[0], a[1], sz))
                    )
                    _append_seg(
                        segments, ToolpathSegment("RAPID", (a[0], a[1], sz), (b[0], b[1], sz))
                    )
                    _append_seg(
                        segments,
                        ToolpathSegment(
                            "PLUNGE", (b[0], b[1], sz), (b[0], b[1], z), feed=tool.feed_z_mm_min
                        ),
                    )
                else:
                    _append_seg(
                        segments,
                        ToolpathSegment(
                            "CUT", (a[0], a[1], z), (b[0], b[1], z), feed=tool.feed_xy_mm_min
                        ),
                    )
            pos_xy = ring[0]

        if center_pass is not None:
            c0, c1 = center_pass
            _append_seg(
                segments,
                ToolpathSegment(
                    "CUT", (pos_xy[0], pos_xy[1], z), (c0[0], c0[1], z), feed=tool.feed_xy_mm_min
                ),
            )
            _append_seg(
                segments,
                ToolpathSegment(
                    "CUT", (c0[0], c0[1], z), (c1[0], c1[1], z), feed=tool.feed_xy_mm_min
                ),
            )
            pos_xy = c1

        # Finish around island boundaries
        for ko in island_rings:
            kp0 = ko[0]
            _append_seg(
                segments,
                ToolpathSegment("RAPID", (pos_xy[0], pos_xy[1], z), (pos_xy[0], pos_xy[1], sz)),
            )
            _append_seg(
                segments, ToolpathSegment("RAPID", (pos_xy[0], pos_xy[1], sz), (kp0[0], kp0[1], sz))
            )
            _append_seg(
                segments,
                ToolpathSegment(
                    "PLUNGE", (kp0[0], kp0[1], sz), (kp0[0], kp0[1], z), feed=tool.feed_z_mm_min
                ),
            )
            for i in range(len(ko)):
                a = ko[i]
                b = ko[(i + 1) % len(ko)]
                _append_seg(
                    segments,
                    ToolpathSegment(
                        "CUT", (a[0], a[1], z), (b[0], b[1], z), feed=tool.feed_xy_mm_min
                    ),
                )
            pos_xy = kp0

        _append_seg(
            segments,
            ToolpathSegment("RAPID", (pos_xy[0], pos_xy[1], z), (pos_xy[0], pos_xy[1], sz)),
        )
        cur = (pos_xy[0], pos_xy[1], sz)

    return _finalize(segments, tool, len(levels))


def _nearest_neighbor_order(points: list[Point2D]) -> list[Point2D]:
    remaining = list(points)
    ordered: list[Point2D] = []
    cur: Point2D = (0.0, 0.0)
    while remaining:
        best_idx = min(
            range(len(remaining)),
            key=lambda i: math.hypot(remaining[i][0] - cur[0], remaining[i][1] - cur[1]),
        )
        cur = remaining.pop(best_idx)
        ordered.append(cur)
    return ordered


def drill(
    points: list[Point2D],
    tool: CamTool,
    depth: float,
    *,
    peck_mm: float | None = None,
    safe_z: float = 20.0,
    retract_z: float = 2.0,
    top_z: float = 0.0,
) -> ToolpathResult:
    """Generate G81/G83 drilling toolpath with nearest-neighbor travel optimization."""
    if not points:
        raise CamError("CAM_INVALID_PARAM", "drill requires at least one point")
    d = check_finite(depth, "depth", positive=True)
    sz = check_finite(safe_z, "safe_z")
    rz = check_finite(retract_z, "retract_z")
    tz = check_finite(top_z, "top_z")
    pk = None if peck_mm is None else check_finite(peck_mm, "peck_mm", positive=True)
    if pk is not None and math.ceil(d / pk) > MAX_Z_PASSES:
        raise CamError("CAM_LIMIT_EXCEEDED", "peck count exceeds MAX_Z_PASSES")

    clean_pts = [(check_finite(p[0], "x"), check_finite(p[1], "y")) for p in points]
    ordered = _nearest_neighbor_order(clean_pts)

    segments: list[ToolpathSegment] = []
    cycles: list[dict[str, float]] = []
    cur: Point3D = (0.0, 0.0, sz)
    z_bot = tz - d

    for x, y in ordered:
        _append_seg(segments, ToolpathSegment("RAPID", cur, (x, y, sz)))
        _append_seg(segments, ToolpathSegment("RAPID", (x, y, sz), (x, y, tz + rz)))
        if pk is None:
            _append_seg(
                segments,
                ToolpathSegment("PLUNGE", (x, y, tz + rz), (x, y, z_bot), feed=tool.feed_z_mm_min),
            )
            _append_seg(segments, ToolpathSegment("RAPID", (x, y, z_bot), (x, y, sz)))
        else:
            z_cur = tz
            while z_cur > z_bot + 1e-9:
                z_next = max(z_bot, z_cur - pk)
                _append_seg(
                    segments,
                    ToolpathSegment(
                        "PLUNGE", (x, y, tz + rz), (x, y, z_next), feed=tool.feed_z_mm_min
                    ),
                )
                _append_seg(segments, ToolpathSegment("RAPID", (x, y, z_next), (x, y, tz + rz)))
                z_cur = z_next
            _append_seg(segments, ToolpathSegment("RAPID", (x, y, tz + rz), (x, y, sz)))
        cycles.append(
            {
                "x": round(x, 6),
                "y": round(y, 6),
                "z": round(z_bot, 6),
                "r": round(tz + rz, 6),
                "q": 0.0 if pk is None else round(pk, 6),
                "feed": round(tool.feed_z_mm_min, 3),
            }
        )
        cur = (x, y, sz)

    return _finalize(segments, tool, 1 if pk is None else math.ceil(d / pk), cycles)
