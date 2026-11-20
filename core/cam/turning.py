"""2-Axis CNC Turning (Lathe): OD/ID roughing, finishing, grooving, and G76 threading (CAM C6)."""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any, Literal

from core.cam.mill25d import RAPID_MM_MIN, ToolpathResult, ToolpathSegment, _append_seg
from core.cam.tools import CamError, check_finite

ProfilePoint = tuple[float, float]  # (z_mm, radius_mm)


@dataclass
class TurningResult:
    toolpath: ToolpathResult
    css_m_min: float
    max_rpm: int
    feed_mm_rev: float
    thread_cycle: dict[str, float] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _validate_profile(profile_zr: list[ProfilePoint]) -> list[ProfilePoint]:
    if len(profile_zr) < 2:
        raise CamError("CAM_INVALID_PARAM", "turning profile requires at least 2 (z, r) points")
    out: list[ProfilePoint] = []
    for z, r in profile_zr:
        out.append((check_finite(z, "z"), check_finite(r, "r", non_negative=True)))
    return out


def _interp_radius(profile: list[ProfilePoint], z: float) -> float:
    """Piecewise-linear radius r(z) for a profile sorted descending in Z (chuck at Z<0, face at Z=0)."""
    pts = sorted(profile, key=lambda p: p[0])
    if z <= pts[0][0]:
        return pts[0][1]
    if z >= pts[-1][0]:
        return pts[-1][1]
    for (z0, r0), (z1, r1) in itertools.pairwise(pts):
        if z0 <= z <= z1:
            if abs(z1 - z0) <= 1e-9:
                return max(r0, r1)
            t = (z - z0) / (z1 - z0)
            return r0 + t * (r1 - r0)
    return pts[-1][1]


def _lathe_finalize(
    segments: list[ToolpathSegment],
    feed_mm_min: float,
    css_m_min: float,
    max_rpm: int,
    feed_mm_rev: float,
    z_passes: int,
    thread_cycle: dict[str, float] | None = None,
) -> TurningResult:
    cut_len = sum(s.length_mm for s in segments if s.kind != "RAPID")
    rapid_len = sum(s.length_mm for s in segments if s.kind == "RAPID")
    cycle_time_s = (cut_len / feed_mm_min + rapid_len / RAPID_MM_MIN) * 60.0
    tp = ToolpathResult(
        segments=segments,
        cut_length_mm=cut_len,
        plunge_length_mm=0.0,
        rapid_length_mm=rapid_len,
        cycle_time_s=cycle_time_s,
        z_passes=z_passes,
    )
    return TurningResult(
        toolpath=tp,
        css_m_min=css_m_min,
        max_rpm=max_rpm,
        feed_mm_rev=feed_mm_rev,
        thread_cycle=thread_cycle,
    )


def turn_rough(
    profile_zr: list[ProfilePoint],
    *,
    stock_radius_mm: float,
    doc_mm: float = 2.0,
    finish_allowance_mm: float = 0.3,
    feed_mm_rev: float = 0.25,
    css_m_min: float = 180.0,
    max_rpm: int = 3000,
    mode: Literal["OD", "ID"] = "OD",
    clearance_mm: float = 2.0,
) -> TurningResult:
    """Multi-pass OD/ID rough turning along Z. Coordinates stored as (X=diameter, Y=0, Z=axial)."""
    pts = _validate_profile(profile_zr)
    r_stock = check_finite(stock_radius_mm, "stock_radius_mm", positive=True)
    doc = check_finite(doc_mm, "doc_mm", positive=True)
    allow = check_finite(finish_allowance_mm, "finish_allowance_mm", non_negative=True)
    f_rev = check_finite(feed_mm_rev, "feed_mm_rev", positive=True)
    css = check_finite(css_m_min, "css_m_min", positive=True)
    clr = check_finite(clearance_mm, "clearance_mm", positive=True)

    zs = [p[0] for p in pts]
    z_min, z_max = min(zs), max(zs)
    r_min = min(p[1] for p in pts)
    r_max = max(p[1] for p in pts)

    # Approximate feed in mm/min at nominal radius for cycle time
    nom_rpm = min(max_rpm, max(100, int((css * 1000.0) / (math.pi * max(2.0 * r_stock, 1.0)))))
    feed_mm_min = f_rev * nom_rpm

    segments: list[ToolpathSegment] = []
    if mode == "OD":
        target_r = r_min + allow
        if r_stock <= target_r + 1e-6:
            raise CamError(
                "CAM_INVALID_PARAM", "stock_radius_mm must exceed profile min radius + allowance"
            )
        n_passes = math.ceil((r_stock - target_r) / doc - 1e-9)
        cur = (2.0 * (r_stock + clr), 0.0, z_max + clr)
        for i in range(1, n_passes + 1):
            r_cut = max(target_r, r_stock - i * doc)
            # Find leftmost Z where profile radius + allowance <= r_cut
            z_end = z_min
            for z_sample in [z_max - k * (z_max - z_min) / 100.0 for k in range(101)]:
                if _interp_radius(pts, z_sample) + allow > r_cut + 1e-6:
                    z_end = z_sample
                    break
            x_d = 2.0 * r_cut
            x_ret = 2.0 * (r_cut + clr)
            _append_seg(segments, ToolpathSegment("RAPID", cur, (x_d, 0.0, z_max + clr)))
            _append_seg(
                segments,
                ToolpathSegment("CUT", (x_d, 0.0, z_max + clr), (x_d, 0.0, z_end), feed=f_rev),
            )
            _append_seg(
                segments,
                ToolpathSegment(
                    "CUT", (x_d, 0.0, z_end), (x_ret, 0.0, z_end + clr * 0.5), feed=f_rev
                ),
            )
            _append_seg(
                segments,
                ToolpathSegment(
                    "RAPID", (x_ret, 0.0, z_end + clr * 0.5), (x_ret, 0.0, z_max + clr)
                ),
            )
            cur = (x_ret, 0.0, z_max + clr)
    else:
        # ID boring: expand from stock bore radius outward to r_max - allow
        target_r = r_max - allow
        if target_r <= r_stock + 1e-6:
            raise CamError("CAM_INVALID_PARAM", "ID target radius must exceed stock bore radius")
        n_passes = math.ceil((target_r - r_stock) / doc - 1e-9)
        cur = (2.0 * max(0.0, r_stock - clr), 0.0, z_max + clr)
        for i in range(1, n_passes + 1):
            r_cut = min(target_r, r_stock + i * doc)
            x_d = 2.0 * r_cut
            x_ret = 2.0 * max(0.0, r_cut - clr)
            _append_seg(segments, ToolpathSegment("RAPID", cur, (x_d, 0.0, z_max + clr)))
            _append_seg(
                segments,
                ToolpathSegment("CUT", (x_d, 0.0, z_max + clr), (x_d, 0.0, z_min), feed=f_rev),
            )
            _append_seg(
                segments, ToolpathSegment("RAPID", (x_d, 0.0, z_min), (x_ret, 0.0, z_max + clr))
            )
            cur = (x_ret, 0.0, z_max + clr)

    return _lathe_finalize(segments, feed_mm_min, css, max_rpm, f_rev, n_passes)


def turn_finish(
    profile_zr: list[ProfilePoint],
    *,
    feed_mm_rev: float = 0.12,
    css_m_min: float = 220.0,
    max_rpm: int = 3500,
    clearance_mm: float = 2.0,
) -> TurningResult:
    """Single-pass finish turning following the exact (Z, X=2*r) profile."""
    pts = _validate_profile(profile_zr)
    f_rev = check_finite(feed_mm_rev, "feed_mm_rev", positive=True)
    css = check_finite(css_m_min, "css_m_min", positive=True)
    clr = check_finite(clearance_mm, "clearance_mm", positive=True)

    ordered = sorted(pts, key=lambda p: -p[0])  # start from front face (largest Z) toward chuck
    r_max = max(p[1] for p in ordered)
    nom_rpm = min(max_rpm, max(100, int((css * 1000.0) / (math.pi * max(2.0 * r_max, 1.0)))))
    feed_mm_min = f_rev * nom_rpm

    segments: list[ToolpathSegment] = []
    start = (2.0 * (ordered[0][1] + clr), 0.0, ordered[0][0] + clr)
    p0 = (2.0 * ordered[0][1], 0.0, ordered[0][0])
    _append_seg(segments, ToolpathSegment("RAPID", start, p0))
    for a, b in itertools.pairwise(ordered):
        _append_seg(
            segments,
            ToolpathSegment("CUT", (2.0 * a[1], 0.0, a[0]), (2.0 * b[1], 0.0, b[0]), feed=f_rev),
        )
    last = (2.0 * ordered[-1][1], 0.0, ordered[-1][0])
    _append_seg(
        segments, ToolpathSegment("RAPID", last, (2.0 * (r_max + clr), 0.0, ordered[0][0] + clr))
    )
    return _lathe_finalize(segments, feed_mm_min, css, max_rpm, f_rev, 1)


def turn_groove(
    *,
    z_start: float,
    z_end: float,
    outer_radius_mm: float,
    bottom_radius_mm: float,
    tool_width_mm: float = 3.0,
    feed_mm_rev: float = 0.08,
    css_m_min: float = 120.0,
    max_rpm: int = 2000,
    clearance_mm: float = 2.0,
) -> TurningResult:
    """OD plunge grooving with stepover across [z_end, z_start]."""
    zs = check_finite(z_start, "z_start")
    ze = check_finite(z_end, "z_end")
    r_out = check_finite(outer_radius_mm, "outer_radius_mm", positive=True)
    r_bot = check_finite(bottom_radius_mm, "bottom_radius_mm", non_negative=True)
    tw = check_finite(tool_width_mm, "tool_width_mm", positive=True)
    f_rev = check_finite(feed_mm_rev, "feed_mm_rev", positive=True)
    css = check_finite(css_m_min, "css_m_min", positive=True)
    clr = check_finite(clearance_mm, "clearance_mm", positive=True)
    if r_out <= r_bot:
        raise CamError("CAM_INVALID_PARAM", "outer_radius_mm must exceed bottom_radius_mm")

    width = abs(zs - ze)
    step = max(0.5, tw * 0.8)
    n_plunges = max(1, math.ceil(max(0.0, width - tw) / step) + 1)
    z_hi, z_lo = max(zs, ze), min(zs, ze)

    segments: list[ToolpathSegment] = []
    cur = (2.0 * (r_out + clr), 0.0, z_hi)
    for i in range(n_plunges):
        zc = max(z_lo + 0.5 * tw, z_hi - 0.5 * tw - i * step)
        _append_seg(segments, ToolpathSegment("RAPID", cur, (2.0 * (r_out + clr), 0.0, zc)))
        _append_seg(
            segments,
            ToolpathSegment(
                "CUT", (2.0 * (r_out + clr), 0.0, zc), (2.0 * r_bot, 0.0, zc), feed=f_rev
            ),
        )
        _append_seg(
            segments,
            ToolpathSegment("RAPID", (2.0 * r_bot, 0.0, zc), (2.0 * (r_out + clr), 0.0, zc)),
        )
        cur = (2.0 * (r_out + clr), 0.0, zc)

    nom_rpm = min(max_rpm, max(100, int((css * 1000.0) / (math.pi * 2.0 * r_out))))
    return _lathe_finalize(segments, f_rev * nom_rpm, css, max_rpm, f_rev, n_plunges)


def turn_thread(
    *,
    z_start: float,
    z_end: float,
    major_diameter_mm: float,
    pitch_mm: float,
    passes: int = 6,
    spindle_rpm: int = 800,
    clearance_mm: float = 2.0,
) -> TurningResult:
    """Metric ISO external threading (G76 cycle) with multi-pass radial infeed."""
    zs = check_finite(z_start, "z_start")
    ze = check_finite(z_end, "z_end")
    maj_d = check_finite(major_diameter_mm, "major_diameter_mm", positive=True)
    pitch = check_finite(pitch_mm, "pitch_mm", positive=True)
    clr = check_finite(clearance_mm, "clearance_mm", positive=True)
    if passes < 1 or passes > 50:
        raise CamError("CAM_INVALID_PARAM", f"thread passes must be in 1..50 (got {passes})")

    thread_depth = 0.6134 * pitch  # ISO 60-degree metric thread depth
    minor_d = maj_d - 2.0 * thread_depth
    if minor_d <= 0:
        raise CamError("CAM_INVALID_PARAM", "pitch too large for major_diameter_mm")

    segments: list[ToolpathSegment] = []
    x_clr = maj_d + 2.0 * clr
    cur = (x_clr, 0.0, zs + clr)
    for i in range(1, passes + 1):
        # Constant-area infeed schedule: d_i = depth * sqrt(i / passes)
        d_i = thread_depth * math.sqrt(i / passes)
        x_cut = maj_d - 2.0 * d_i
        _append_seg(segments, ToolpathSegment("RAPID", cur, (x_cut, 0.0, zs + clr)))
        _append_seg(
            segments, ToolpathSegment("CUT", (x_cut, 0.0, zs + clr), (x_cut, 0.0, ze), feed=pitch)
        )
        _append_seg(segments, ToolpathSegment("RAPID", (x_cut, 0.0, ze), (x_clr, 0.0, zs + clr)))
        cur = (x_clr, 0.0, zs + clr)

    cycle = {
        "major_d": round(maj_d, 4),
        "minor_d": round(minor_d, 4),
        "depth_mm": round(thread_depth, 4),
        "pitch_mm": round(pitch, 4),
        "z_start": round(zs, 4),
        "z_end": round(ze, 4),
        "passes": float(passes),
    }
    return _lathe_finalize(segments, pitch * spindle_rpm, 100.0, spindle_rpm, pitch, passes, cycle)
