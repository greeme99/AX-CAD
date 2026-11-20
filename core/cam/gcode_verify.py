"""CAM C10: G-Code Reverse-Parser & Machine Limit / Stock Verifier.

Parses Fanuc / Siemens 840D / Heidenhain TNC / Lathe NC programs back into motion segments,
validates machine travel envelopes, spindle/feed ceilings, modal consistency, and optionally
runs Dexel stock-removal verification against the parsed toolpath.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from core.cam.mill25d import ToolpathSegment
from core.cam.stock_sim import simulate_stock_removal

_WORD_RE = re.compile(r"([A-Z])([-+]?\d+(?:\.\d+)?)")


@dataclass(frozen=True)
class MachineLimits:
    x_min: float = -500.0
    x_max: float = 500.0
    y_min: float = -400.0
    y_max: float = 400.0
    z_min: float = -300.0
    z_max: float = 300.0
    a_min: float = -120.0
    a_max: float = 120.0
    max_rpm: int = 24000
    max_feed_mm_min: float = 15000.0


@dataclass
class GCodeVerifyResult:
    ok: bool
    line_count: int
    segment_count: int
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    min_xyz: tuple[float, float, float] = (0.0, 0.0, 0.0)
    max_xyz: tuple[float, float, float] = (0.0, 0.0, 0.0)
    max_rpm_seen: float = 0.0
    max_feed_seen: float = 0.0
    rtcp_active_blocks: int = 0
    stock_sim: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "line_count": self.line_count,
            "segment_count": self.segment_count,
            "errors": self.errors,
            "warnings": self.warnings,
            "min_xyz": [round(v, 4) for v in self.min_xyz],
            "max_xyz": [round(v, 4) for v in self.max_xyz],
            "max_rpm_seen": self.max_rpm_seen,
            "max_feed_seen": self.max_feed_seen,
            "rtcp_active_blocks": self.rtcp_active_blocks,
            "stock_sim": self.stock_sim,
        }


def _strip_comments(line: str) -> str:
    # Remove (...) and ;... comments
    s = re.sub(r"\([^)]*\)", "", line)
    if ";" in s:
        s = s.split(";", 1)[0]
    return s.strip().upper()


def verify_gcode(
    gcode: str,
    *,
    limits: MachineLimits | None = None,
    blank_bbox: tuple[float, float, float, float, float, float] | None = None,
    tool_diameter_mm: float = 10.0,
    grid_step_mm: float = 2.0,
) -> GCodeVerifyResult:
    """Parse G-code program, verify machine limits & modal state, and optionally verify stock."""
    lim = limits or MachineLimits()
    lines = gcode.splitlines()
    errors: list[str] = []
    warnings: list[str] = []
    segs: list[ToolpathSegment] = []

    motion_mode: Literal["RAPID", "CUT", "PLUNGE", "ARC_CW", "ARC_CCW"] = "RAPID"
    abs_mode = True
    spindle_on = False
    rtcp_on = False
    rtcp_blocks = 0

    cx = cy = cz = ca = 0.0
    feed = 0.0
    max_rpm_seen = 0.0
    max_feed_seen = 0.0

    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []

    for lineno, raw in enumerate(lines, start=1):
        clean = _strip_comments(raw)
        if not clean or clean == "%" or clean.startswith("O") and len(clean.split()) == 1:
            continue

        if "TRAORI" in clean or "G43.4" in clean or "M128" in clean:
            rtcp_on = True
        if "TRAFOOF" in clean or "G49" in clean or "M129" in clean:
            rtcp_on = False

        # Heidenhain L-block support
        is_hh_line = clean.startswith("L ")
        if is_hh_line:
            motion_mode = "RAPID" if "FMAX" in clean else "CUT"

        words = _WORD_RE.findall(clean)
        word_map: dict[str, list[float]] = {}
        for k, v in words:
            word_map.setdefault(k, []).append(float(v))

        for g in word_map.get("G", []):
            gi = round(g, 1)
            if gi == 0.0:
                motion_mode = "RAPID"
            elif gi == 1.0:
                motion_mode = "CUT"
            elif gi == 2.0:
                motion_mode = "ARC_CW"
            elif gi == 3.0:
                motion_mode = "ARC_CCW"
            elif gi == 90.0:
                abs_mode = True
            elif gi == 91.0:
                abs_mode = False

        for m in word_map.get("M", []):
            mi = round(m)
            if mi in (3, 4):
                spindle_on = True
            elif mi == 5:
                spindle_on = False

        if "S" in word_map:
            rpm = word_map["S"][-1]
            max_rpm_seen = max(max_rpm_seen, rpm)
            if rpm > lim.max_rpm:
                errors.append(f"Line {lineno}: spindle RPM {rpm:.0f} exceeds max {lim.max_rpm}")

        if "F" in word_map:
            feed = word_map["F"][-1]
            max_feed_seen = max(max_feed_seen, feed)
            if feed > lim.max_feed_mm_min:
                errors.append(
                    f"Line {lineno}: feed {feed:.1f} exceeds max {lim.max_feed_mm_min:.1f}"
                )

        has_axis = any(ax in word_map for ax in ("X", "Y", "Z", "A", "C"))
        if has_axis:
            nx = (
                (word_map["X"][-1] if abs_mode else cx + word_map["X"][-1])
                if "X" in word_map
                else cx
            )
            ny = (
                (word_map["Y"][-1] if abs_mode else cy + word_map["Y"][-1])
                if "Y" in word_map
                else cy
            )
            nz = (
                (word_map["Z"][-1] if abs_mode else cz + word_map["Z"][-1])
                if "Z" in word_map
                else cz
            )
            na = (
                (word_map["A"][-1] if abs_mode else ca + word_map["A"][-1])
                if "A" in word_map
                else ca
            )

            if not (lim.x_min <= nx <= lim.x_max):
                errors.append(f"Line {lineno}: X={nx:.3f} outside [{lim.x_min}, {lim.x_max}]")
            if not (lim.y_min <= ny <= lim.y_max):
                errors.append(f"Line {lineno}: Y={ny:.3f} outside [{lim.y_min}, {lim.y_max}]")
            if not (lim.z_min <= nz <= lim.z_max):
                errors.append(f"Line {lineno}: Z={nz:.3f} outside [{lim.z_min}, {lim.z_max}]")
            if not (lim.a_min <= na <= lim.a_max):
                errors.append(f"Line {lineno}: A={na:.3f} outside [{lim.a_min}, {lim.a_max}]")

            if motion_mode != "RAPID" and not spindle_on:
                warnings.append(f"Line {lineno}: cutting move while spindle is OFF")
            if motion_mode != "RAPID" and feed <= 0.0:
                warnings.append(f"Line {lineno}: cutting move with F=0")

            if (nx, ny, nz) != (cx, cy, cz):
                segs.append(
                    ToolpathSegment(
                        kind=motion_mode,
                        start=(cx, cy, cz),
                        end=(nx, ny, nz),
                        feed=feed if motion_mode != "RAPID" else None,
                    )
                )
            if rtcp_on:
                rtcp_blocks += 1

            cx, cy, cz, ca = nx, ny, nz, na
            xs.append(cx)
            ys.append(cy)
            zs.append(cz)

    min_xyz = (min(xs), min(ys), min(zs)) if xs else (0.0, 0.0, 0.0)
    max_xyz = (max(xs), max(ys), max(zs)) if xs else (0.0, 0.0, 0.0)

    stock_dict: dict[str, Any] | None = None
    if blank_bbox is not None and segs:
        sim = simulate_stock_removal(
            segs,
            tool_diameter_mm=tool_diameter_mm,
            blank_bbox=blank_bbox,
            grid_step_mm=grid_step_mm,
        )
        stock_dict = sim.to_dict()
        if sim.rapid_collision_count > 0:
            errors.append(
                f"Stock verification detected {sim.rapid_collision_count} rapid collision(s)"
            )

    return GCodeVerifyResult(
        ok=len(errors) == 0,
        line_count=len(lines),
        segment_count=len(segs),
        errors=errors,
        warnings=warnings,
        min_xyz=min_xyz,
        max_xyz=max_xyz,
        max_rpm_seen=max_rpm_seen,
        max_feed_seen=max_feed_seen,
        rtcp_active_blocks=rtcp_blocks,
        stock_sim=stock_dict,
    )
