"""CAM C9: Simultaneous 5-Axis Swarf / Multi-Axis Tool-Axis Interpolation & RTCP Post.

Supports:
  - Ruled-surface Swarf 5-axis toolpath (bottom & top rail curves)
  - Multi-axis surface-following toolpath with Lead/Tilt angles
  - Inverse kinematics (A/C trunnion table) with round-trip accuracy <= 1e-9
  - Gimbal-lock (pole singularity) detection & C-axis continuous unwrapping
  - RTCP G-code output: Fanuc G43.4, Siemens 840D TRAORI, Heidenhain M128
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from core.cam.mill25d import ToolpathResult, ToolpathSegment
from core.cam.tools import CamError, CamTool


@dataclass(frozen=True)
class FiveAxisPose:
    """Single 5-axis tool tip + unit tool axis + rotary angles (A, C in degrees)."""

    x: float
    y: float
    z: float
    i: float
    j: float
    k: float
    a_deg: float
    c_deg: float
    feed: float
    singularity_warning: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "x": round(self.x, 4),
            "y": round(self.y, 4),
            "z": round(self.z, 4),
            "i": round(self.i, 6),
            "j": round(self.j, 6),
            "k": round(self.k, 6),
            "a_deg": round(self.a_deg, 4),
            "c_deg": round(self.c_deg, 4),
            "feed": round(self.feed, 2),
            "singularity_warning": self.singularity_warning,
        }


@dataclass
class FiveAxisResult:
    poses: list[FiveAxisPose] = field(default_factory=list)
    toolpath: ToolpathResult = field(default_factory=ToolpathResult)
    singularity_count: int = 0
    max_angular_step_deg: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "pose_count": len(self.poses),
            "singularity_count": self.singularity_count,
            "max_angular_step_deg": round(self.max_angular_step_deg, 4),
            "toolpath": self.toolpath.to_dict(),
            "poses": [p.to_dict() for p in self.poses],
        }


def _normalize(v: tuple[float, float, float]) -> tuple[float, float, float]:
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if n <= 1e-12:
        raise CamError("CAM_INVALID_PARAM", "Tool axis vector cannot be zero")
    return v[0] / n, v[1] / n, v[2] / n


def tool_axis_to_ac(
    axis: tuple[float, float, float],
    *,
    prev_c_deg: float = 0.0,
    pole_tol_deg: float = 1.0,
) -> tuple[float, float, bool]:
    """Inverse kinematics for A/C trunnion table (tool axis in workpiece coords -> A, C deg).

    Forward kinematics convention:
        i = sin(A) * sin(C)
        j = -sin(A) * cos(C)
        k = cos(A)
    Returns (a_deg, c_deg, is_near_singularity).
    """
    i, j, k = _normalize(axis)
    k_clamped = max(-1.0, min(1.0, k))
    a_rad = math.acos(k_clamped)
    a_deg = math.degrees(a_rad)
    near_pole = a_deg < pole_tol_deg or abs(180.0 - a_deg) < pole_tol_deg
    if math.sin(a_rad) < 1e-9:
        return a_deg, prev_c_deg, True
    c_raw = math.degrees(math.atan2(i, -j))
    # Unwrap C continuously relative to prev_c_deg to avoid 360-degree jumps
    diff = (c_raw - prev_c_deg + 180.0) % 360.0 - 180.0
    c_deg = prev_c_deg + diff
    return a_deg, c_deg, near_pole


def ac_to_tool_axis(a_deg: float, c_deg: float) -> tuple[float, float, float]:
    """Forward kinematics for A/C trunnion table (A, C deg -> unit tool axis (i, j, k))."""
    a = math.radians(a_deg)
    c = math.radians(c_deg)
    return (math.sin(a) * math.sin(c), -math.sin(a) * math.cos(c), math.cos(a))


def apply_lead_tilt(
    normal: tuple[float, float, float],
    tangent: tuple[float, float, float],
    lead_deg: float = 0.0,
    tilt_deg: float = 0.0,
) -> tuple[float, float, float]:
    """Tilt surface normal by lead angle (along feed tangent) and tilt angle (sideways)."""
    n = np.array(_normalize(normal), dtype=float)
    t_raw = np.array(tangent, dtype=float)
    t_proj = t_raw - float(np.dot(t_raw, n)) * n
    t_norm = float(np.linalg.norm(t_proj))
    if t_norm <= 1e-12:
        return float(n[0]), float(n[1]), float(n[2])
    t = t_proj / t_norm
    b = np.cross(n, t)
    b_norm = float(np.linalg.norm(b))
    if b_norm <= 1e-12:
        return float(n[0]), float(n[1]), float(n[2])
    b = b / b_norm

    lead = math.radians(lead_deg)
    tilt = math.radians(tilt_deg)
    # Rotate normal toward tangent by lead, then toward binormal by tilt
    v = math.cos(lead) * n + math.sin(lead) * t
    v = math.cos(tilt) * v + math.sin(tilt) * b
    return _normalize((float(v[0]), float(v[1]), float(v[2])))


def swarf_5ax(
    bottom_curve: list[tuple[float, float, float]],
    top_curve: list[tuple[float, float, float]],
    tool: CamTool,
    *,
    safe_z: float = 30.0,
    lead_deg: float = 0.0,
    tilt_deg: float = 0.0,
) -> FiveAxisResult:
    """Generate simultaneous 5-axis Swarf toolpath along paired bottom/top rail curves."""
    if len(bottom_curve) < 2 or len(bottom_curve) != len(top_curve):
        raise CamError(
            "CAM_INVALID_PARAM",
            "bottom_curve and top_curve must have identical length >= 2",
        )

    poses: list[FiveAxisPose] = []
    segs: list[ToolpathSegment] = []
    prev_c = 0.0
    sing_count = 0
    max_ang_step = 0.0
    cut_len = 0.0
    rapid_len = 0.0

    # Approach rapid to first point
    p0 = bottom_curve[0]
    start_safe = (p0[0], p0[1], safe_z)
    segs.append(ToolpathSegment(kind="RAPID", start=start_safe, end=p0))
    rapid_len += abs(safe_z - p0[2])

    for idx in range(len(bottom_curve)):
        pb = bottom_curve[idx]
        pt = top_curve[idx]
        raw_axis = (pt[0] - pb[0], pt[1] - pb[1], pt[2] - pb[2])
        if idx + 1 < len(bottom_curve):
            nxt = bottom_curve[idx + 1]
            tang = (nxt[0] - pb[0], nxt[1] - pb[1], nxt[2] - pb[2])
        else:
            prv = bottom_curve[idx - 1]
            tang = (pb[0] - prv[0], pb[1] - prv[1], pb[2] - prv[2])

        axis = apply_lead_tilt(raw_axis, tang, lead_deg=lead_deg, tilt_deg=tilt_deg)
        a_deg, c_deg, is_sing = tool_axis_to_ac(axis, prev_c_deg=prev_c)
        if is_sing:
            sing_count += 1
        if poses:
            da = abs(a_deg - poses[-1].a_deg)
            dc = abs(c_deg - poses[-1].c_deg)
            max_ang_step = max(max_ang_step, math.hypot(da, dc))
            seg_len = math.dist((poses[-1].x, poses[-1].y, poses[-1].z), pb)
            cut_len += seg_len
            segs.append(
                ToolpathSegment(
                    kind="CUT",
                    start=(poses[-1].x, poses[-1].y, poses[-1].z),
                    end=pb,
                    feed=tool.feed_xy_mm_min,
                )
            )
        prev_c = c_deg
        poses.append(
            FiveAxisPose(
                x=pb[0],
                y=pb[1],
                z=pb[2],
                i=axis[0],
                j=axis[1],
                k=axis[2],
                a_deg=a_deg,
                c_deg=c_deg,
                feed=tool.feed_xy_mm_min,
                singularity_warning=is_sing,
            )
        )

    # Retract rapid
    pend = bottom_curve[-1]
    end_safe = (pend[0], pend[1], safe_z)
    segs.append(ToolpathSegment(kind="RAPID", start=pend, end=end_safe))
    rapid_len += abs(safe_z - pend[2])

    cycle_s = (cut_len / max(tool.feed_xy_mm_min, 1.0)) * 60.0 + (rapid_len / 10000.0) * 60.0
    tp = ToolpathResult(
        segments=segs,
        cut_length_mm=round(cut_len, 4),
        plunge_length_mm=0.0,
        rapid_length_mm=round(rapid_len, 4),
        cycle_time_s=round(cycle_s, 4),
        z_passes=1,
    )
    return FiveAxisResult(
        poses=poses,
        toolpath=tp,
        singularity_count=sing_count,
        max_angular_step_deg=max_ang_step,
    )


def multiaxis_surface_5ax(
    points: list[tuple[float, float, float]],
    normals: list[tuple[float, float, float]],
    tool: CamTool,
    *,
    lead_deg: float = 5.0,
    tilt_deg: float = 0.0,
    safe_z: float = 30.0,
) -> FiveAxisResult:
    """Generate simultaneous 5-axis surface-following toolpath with Lead/Tilt angles."""
    if len(points) < 2 or len(points) != len(normals):
        raise CamError("CAM_INVALID_PARAM", "points and normals must have identical length >= 2")
    top_curve = [
        (p[0] + n[0] * 10.0, p[1] + n[1] * 10.0, p[2] + n[2] * 10.0)
        for p, n in zip(points, normals, strict=True)
    ]
    return swarf_5ax(
        points,
        top_curve,
        tool,
        safe_z=safe_z,
        lead_deg=lead_deg,
        tilt_deg=tilt_deg,
    )


def post_5ax_rtcp(
    result: FiveAxisResult,
    tool: CamTool,
    *,
    controller: str = "FANUC_MILL",
    program_name: str = "O5001",
    safe_z: float = 30.0,
) -> str:
    """Emit simultaneous 5-axis RTCP G-code (Fanuc G43.4 / Siemens TRAORI / Heidenhain M128)."""
    ctrl = controller.upper()
    lines: list[str] = []
    if ctrl == "SIEMENS_840D":
        lines.extend(
            [
                f"; {program_name} SIMULTANEOUS 5AX RTCP",
                "G54 G90 G71",
                f"T1 D1 M6 ; {tool.tool_code}",
                f"S{tool.spindle_rpm} M3",
                "TRAORI",
            ]
        )
    elif ctrl == "HEIDENHAIN_TNC":
        lines.extend(
            [
                f"BEGIN PGM {program_name} MM",
                f"TOOL CALL 1 Z S{tool.spindle_rpm}",
                "M3",
                "M128",
            ]
        )
    else:
        lines.extend(
            [
                "%",
                f"{program_name} (SIMULTANEOUS 5AX RTCP)",
                "G90 G21 G17 G40 G80",
                f"T01 M06 ({tool.tool_code})",
                f"S{tool.spindle_rpm} M03",
                f"G43.4 H01 Z{safe_z:.3f}",
            ]
        )

    for idx, p in enumerate(result.poses):
        if ctrl == "HEIDENHAIN_TNC":
            if idx == 0:
                lines.append(
                    f"L X{p.x:.3f} Y{p.y:.3f} Z{safe_z:.3f} A{p.a_deg:.3f} C{p.c_deg:.3f} FMAX"
                )
            lines.append(
                f"L X{p.x:.3f} Y{p.y:.3f} Z{p.z:.3f} A{p.a_deg:.3f} C{p.c_deg:.3f} F{p.feed:.0f}"
            )
        else:
            if idx == 0:
                lines.append(
                    f"G00 X{p.x:.3f} Y{p.y:.3f} Z{safe_z:.3f} A{p.a_deg:.3f} C{p.c_deg:.3f}"
                )
            lines.append(
                f"G01 X{p.x:.3f} Y{p.y:.3f} Z{p.z:.3f} A{p.a_deg:.3f} C{p.c_deg:.3f} F{p.feed:.1f}"
            )

    if ctrl == "SIEMENS_840D":
        lines.extend([f"G00 Z{safe_z:.3f}", "TRAFOOF", "M5", "M30"])
    elif ctrl == "HEIDENHAIN_TNC":
        lines.extend([f"L Z{safe_z:.3f} FMAX", "M129", "M5", f"END PGM {program_name} MM"])
    else:
        lines.extend([f"G00 Z{safe_z:.3f}", "G49", "M05", "M30", "%"])
    return "\n".join(lines) + "\n"
