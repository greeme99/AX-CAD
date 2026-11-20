"""G-code post-processors: FANUC_MILL & GENERIC_LASER (plan.md §24 C3)."""

from __future__ import annotations

import math
import re
from typing import Any

from core.cam.mill25d import ToolpathResult
from core.cam.tools import MAX_FEED_MM_MIN, MAX_RPM, MIN_RPM, CamError, CamTool, check_finite

SAFETY_HEADER = "(AX-CAD SIMULATION/DRAFT ONLY - VERIFY ON DRY-RUN BEFORE MACHINING)"
_FORBIDDEN_COMMENT_RE = re.compile(r"[\x00-\x1f\x7f()]")
_PROG_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")


def sanitize_comment(text: str, field_name: str = "comment") -> str:
    """Reject control characters (CR/LF/NUL/etc.) and parentheses to prevent G-code injection."""
    if not isinstance(text, str):
        raise CamError("CAM_INVALID_PARAM", f"{field_name} must be a string")
    if _FORBIDDEN_COMMENT_RE.search(text):
        raise CamError(
            "CAM_INVALID_PARAM",
            f"{field_name} contains forbidden control characters or parentheses",
        )
    return text.strip()


def validate_program_name(name: str) -> str:
    if not isinstance(name, str) or not _PROG_NAME_RE.match(name):
        raise CamError(
            "CAM_INVALID_PARAM",
            "program_name must match ^[A-Za-z0-9_.-]{1,32}$ (no spaces or control characters)",
        )
    return name


def _fmt(v: float) -> str:
    val = check_finite(v, "coord")
    return f"{val:.3f}"


def _tool_number(tool_code: str) -> int:
    digits = re.sub(r"[^0-9]", "", tool_code)
    if not digits:
        return 1
    num = int(digits)
    return max(1, min(99, num))


def post_fanuc_mill(
    operations: list[tuple[CamTool, ToolpathResult, str]],
    *,
    program_name: str = "O0001",
    comment: str = "",
) -> str:
    """Emit Fanuc-compatible 2.5D milling G-code with mandatory safety header & injection guards."""
    prog = validate_program_name(program_name)
    safe_comment = sanitize_comment(comment, "comment") if comment else ""

    lines: list[str] = [
        "%",
        prog,
        SAFETY_HEADER,
    ]
    if safe_comment:
        lines.append(f"({safe_comment})")
    lines.append("G90 G17 G21 G40 G49 G80")

    for idx, (tool, tp, op_label) in enumerate(operations, start=1):
        label = sanitize_comment(op_label, "op_label")
        if not (MIN_RPM <= tool.spindle_rpm <= MAX_RPM):
            raise CamError("CAM_INVALID_PARAM", f"spindle_rpm out of bounds: {tool.spindle_rpm}")
        if not (0 < tool.feed_xy_mm_min <= MAX_FEED_MM_MIN):
            raise CamError("CAM_INVALID_PARAM", f"feed_xy out of bounds: {tool.feed_xy_mm_min}")
        if not (0 < tool.feed_z_mm_min <= MAX_FEED_MM_MIN):
            raise CamError("CAM_INVALID_PARAM", f"feed_z out of bounds: {tool.feed_z_mm_min}")

        tnum = _tool_number(tool.tool_code)
        lines.append(f"(OP {idx}: {label} - {tool.tool_code})")
        lines.append(f"T{tnum:02d} M06")
        lines.append(f"S{tool.spindle_rpm} M03")

        if tp.drill_cycles:
            first = tp.drill_cycles[0]
            q_val = first.get("q", 0.0)
            cycle = "G83" if q_val > 0 else "G81"
            q_str = f" Q{_fmt(q_val)}" if q_val > 0 else ""
            lines.append(
                f"{cycle} G98 X{_fmt(first['x'])} Y{_fmt(first['y'])} "
                f"Z{_fmt(first['z'])} R{_fmt(first['r'])}{q_str} F{_fmt(first['feed'])}"
            )
            for cyc in tp.drill_cycles[1:]:
                lines.append(f"X{_fmt(cyc['x'])} Y{_fmt(cyc['y'])}")
            lines.append("G80")
        else:
            for seg in tp.segments:
                ex, ey, ez = seg.end
                if seg.kind == "RAPID":
                    lines.append(f"G00 X{_fmt(ex)} Y{_fmt(ey)} Z{_fmt(ez)}")
                elif seg.kind in ("CUT", "PLUNGE"):
                    f = seg.feed if seg.feed is not None else tool.feed_xy_mm_min
                    lines.append(f"G01 X{_fmt(ex)} Y{_fmt(ey)} Z{_fmt(ez)} F{_fmt(f)}")
                elif seg.kind in ("ARC_CW", "ARC_CCW") and seg.center is not None:
                    gcode = "G02" if seg.kind == "ARC_CW" else "G03"
                    i_off = seg.center[0] - seg.start[0]
                    j_off = seg.center[1] - seg.start[1]
                    f = seg.feed if seg.feed is not None else tool.feed_xy_mm_min
                    lines.append(
                        f"{gcode} X{_fmt(ex)} Y{_fmt(ey)} Z{_fmt(ez)} "
                        f"I{_fmt(i_off)} J{_fmt(j_off)} F{_fmt(f)}"
                    )

    lines.append("M05")
    lines.append("M30")
    lines.append("%")
    return "\n".join(lines) + "\n"


def post_generic_laser(
    operations: list[tuple[CamTool, ToolpathResult, str]],
    *,
    program_name: str = "O0001",
    comment: str = "",
) -> str:
    """Emit generic 2D laser G-code (M03 S<power> / M05) with safety header & injection guards."""
    prog = validate_program_name(program_name)
    safe_comment = sanitize_comment(comment, "comment") if comment else ""

    lines: list[str] = [
        "%",
        prog,
        SAFETY_HEADER,
    ]
    if safe_comment:
        lines.append(f"({safe_comment})")
    lines.append("G90 G21")

    beam_on = False
    for idx, (tool, tp, op_label) in enumerate(operations, start=1):
        label = sanitize_comment(op_label, "op_label")
        power = tool.laser_power_pct if tool.laser_power_pct is not None else 80.0
        power_i = round(check_finite(power, "laser_power_pct", positive=True) * 10)  # 0..1000 scale
        lines.append(f"(LASER OP {idx}: {label})")

        for seg in tp.segments:
            ex, ey, _ = seg.end
            if seg.kind == "RAPID":
                if beam_on:
                    lines.append("M05")
                    beam_on = False
                lines.append(f"G00 X{_fmt(ex)} Y{_fmt(ey)}")
            else:
                if not beam_on:
                    lines.append(f"M03 S{power_i}")
                    beam_on = True
                f = seg.feed if seg.feed is not None else tool.feed_xy_mm_min
                if seg.kind in ("ARC_CW", "ARC_CCW") and seg.center is not None:
                    gcode = "G02" if seg.kind == "ARC_CW" else "G03"
                    i_off = seg.center[0] - seg.start[0]
                    j_off = seg.center[1] - seg.start[1]
                    lines.append(
                        f"{gcode} X{_fmt(ex)} Y{_fmt(ey)} I{_fmt(i_off)} J{_fmt(j_off)} F{_fmt(f)}"
                    )
                else:
                    lines.append(f"G01 X{_fmt(ex)} Y{_fmt(ey)} F{_fmt(f)}")

    if beam_on:
        lines.append("M05")
    lines.append("M30")
    lines.append("%")
    return "\n".join(lines) + "\n"


def validate_toolpath_json(payload: dict[str, Any]) -> None:
    """Ensure toolpath payload contains only finite numbers."""
    for seg in payload.get("segments", []):
        for key in ("start", "end"):
            for v in seg.get(key, ()):
                if not isinstance(v, (int, float)) or not math.isfinite(float(v)):
                    raise CamError("CAM_INVALID_PARAM", f"non-finite value in segment {key}")


def post_siemens_840d(
    operations: list[tuple[CamTool, ToolpathResult, str]],
    *,
    program_name: str = "AXCAD_PART",
    comment: str = "",
    tilt_header: list[str] | None = None,
    tilt_footer: list[str] | None = None,
) -> str:
    """Emit Siemens Sinumerik 840D milling G-code with safety header, CYCLE800 tilt, and injection guards."""
    prog = validate_program_name(program_name)
    safe_comment = sanitize_comment(comment, "comment") if comment else ""
    lines: list[str] = [
        f"; {prog}.MPF",
        f"; {SAFETY_HEADER.strip('()')}",
    ]
    if safe_comment:
        lines.append(f"; {safe_comment}")
    lines.append("G90 G17 G71 G40 G64")
    if tilt_header:
        lines.extend(tilt_header)

    for idx, (tool, tp, op_label) in enumerate(operations, start=1):
        label = sanitize_comment(op_label, "op_label")
        lines.append(f"; OP {idx}: {label}")
        lines.append(f'T="{tool.tool_code}" D1 M6')
        lines.append(f"S{tool.spindle_rpm} M3")
        for seg in tp.segments:
            ex, ey, ez = seg.end
            if seg.kind == "RAPID":
                lines.append(f"G0 X{_fmt(ex)} Y{_fmt(ey)} Z{_fmt(ez)}")
            else:
                f = seg.feed if seg.feed is not None else tool.feed_xy_mm_min
                lines.append(f"G1 X{_fmt(ex)} Y{_fmt(ey)} Z{_fmt(ez)} F{_fmt(f)}")

    if tilt_footer:
        lines.extend(tilt_footer)
    lines.append("M5")
    lines.append("M30")
    return "\n".join(lines) + "\n"


def post_heidenhain_tnc(
    operations: list[tuple[CamTool, ToolpathResult, str]],
    *,
    program_name: str = "AXCAD_PART",
    comment: str = "",
    tilt_header: list[str] | None = None,
    tilt_footer: list[str] | None = None,
) -> str:
    """Emit Heidenhain TNC conversational NC program with safety header, PLANE SPATIAL, and injection guards."""
    prog = validate_program_name(program_name)
    safe_comment = sanitize_comment(comment, "comment") if comment else ""
    raw_lines: list[str] = [
        f"BEGIN PGM {prog} MM",
        f"; {SAFETY_HEADER.strip('()')}",
    ]
    if safe_comment:
        raw_lines.append(f"; {safe_comment}")
    if tilt_header:
        raw_lines.extend(tilt_header)

    for idx, (tool, tp, op_label) in enumerate(operations, start=1):
        label = sanitize_comment(op_label, "op_label")
        tnum = _tool_number(tool.tool_code)
        raw_lines.append(f"; OP {idx}: {label}")
        raw_lines.append(f"TOOL CALL {tnum} Z S{tool.spindle_rpm}")
        raw_lines.append("M3")
        for seg in tp.segments:
            ex, ey, ez = seg.end
            if seg.kind == "RAPID":
                raw_lines.append(f"L X{_fmt(ex)} Y{_fmt(ey)} Z{_fmt(ez)} FMAX")
            else:
                f = seg.feed if seg.feed is not None else tool.feed_xy_mm_min
                raw_lines.append(f"L X{_fmt(ex)} Y{_fmt(ey)} Z{_fmt(ez)} F{round(f)}")

    if tilt_footer:
        raw_lines.extend(tilt_footer)
    raw_lines.append("M5")
    raw_lines.append(f"END PGM {prog} MM")
    return "\n".join(f"{i} {ln}" for i, ln in enumerate(raw_lines)) + "\n"


def post_fanuc_lathe(
    operations: list[tuple[CamTool, ToolpathResult, str, dict[str, Any] | None]],
    *,
    program_name: str = "O2001",
    comment: str = "",
    css_m_min: float = 180.0,
    max_rpm: int = 3000,
) -> str:
    """Emit Fanuc 2-axis CNC lathe G-code (G99/G50/G96/G00/G01/G76) with safety header & injection guards."""
    prog = validate_program_name(program_name)
    safe_comment = sanitize_comment(comment, "comment") if comment else ""
    css = round(check_finite(css_m_min, "css_m_min", positive=True))
    lines: list[str] = [
        "%",
        prog,
        SAFETY_HEADER,
    ]
    if safe_comment:
        lines.append(f"({safe_comment})")
    lines.append("G99 G21 G40 G80")
    lines.append(f"G50 S{max_rpm}")
    lines.append(f"G96 S{css} M03")

    for idx, (tool, tp, op_label, thread_cycle) in enumerate(operations, start=1):
        label = sanitize_comment(op_label, "op_label")
        tnum = _tool_number(tool.tool_code)
        lines.append(f"(LATHE OP {idx}: {label})")
        lines.append(f"T{tnum:02d}{tnum:02d}")
        if thread_cycle:
            depth_um = round(thread_cycle["depth_mm"] * 1000.0)
            lines.append("G76 P010060 Q100 R0.05")
            lines.append(
                f"G76 X{_fmt(thread_cycle['minor_d'])} Z{_fmt(thread_cycle['z_end'])} "
                f"P{depth_um} Q200 F{_fmt(thread_cycle['pitch_mm'])}"
            )
        else:
            for seg in tp.segments:
                ex, _, ez = seg.end
                if seg.kind == "RAPID":
                    lines.append(f"G00 X{_fmt(ex)} Z{_fmt(ez)}")
                else:
                    f = seg.feed if seg.feed is not None else 0.2
                    lines.append(f"G01 X{_fmt(ex)} Z{_fmt(ez)} F{_fmt(f)}")

    lines.append("M05")
    lines.append("M30")
    lines.append("%")
    return "\n".join(lines) + "\n"
