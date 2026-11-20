"""CAM tool & cutting-condition master validation (plan.md §24 C1)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

TOOL_TYPES = frozenset(
    {"ENDMILL_FLAT", "ENDMILL_BALL", "DRILL", "CHAMFER", "FACEMILL", "LASER_NOZZLE"}
)
MIN_RPM = 100
MAX_RPM = 60_000
MAX_FEED_MM_MIN = 100_000.0
MIN_STEPOVER = 0.05
MAX_STEPOVER = 0.95


class CamError(Exception):
    """Domain error raised by CAM toolpath, recognition, post-processor, or laser modules."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def check_finite(
    value: float, name: str, *, positive: bool = False, non_negative: bool = False
) -> float:
    """Validate that a numeric parameter is finite (not NaN/Inf) and within sign bounds."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise CamError("CAM_INVALID_PARAM", f"{name} must be a number")
    val = float(value)
    if not math.isfinite(val):
        raise CamError("CAM_INVALID_PARAM", f"{name} must be finite (got {value})")
    if positive and val <= 0.0:
        raise CamError("CAM_INVALID_PARAM", f"{name} must be > 0 (got {val})")
    if non_negative and val < 0.0:
        raise CamError("CAM_INVALID_PARAM", f"{name} must be >= 0 (got {val})")
    return val


@dataclass(frozen=True)
class CamTool:
    tool_code: str
    tool_type: str
    diameter_mm: float
    flutes: int = 2
    material_group: str = "STEEL"
    spindle_rpm: int = 6000
    feed_xy_mm_min: float = 600.0
    feed_z_mm_min: float = 200.0
    stepdown_mm: float = 2.0
    stepover_ratio: float = 0.5
    laser_power_pct: float | None = None

    def __post_init__(self) -> None:
        if not self.tool_code or len(self.tool_code) > 32:
            raise CamError("CAM_INVALID_PARAM", "tool_code must be 1..32 chars")
        if self.tool_type not in TOOL_TYPES:
            raise CamError("CAM_INVALID_PARAM", f"unsupported tool_type: {self.tool_type}")
        d = check_finite(self.diameter_mm, "diameter_mm", positive=True)
        if d > 500.0:
            raise CamError("CAM_INVALID_PARAM", f"diameter_mm out of range: {d}")
        if not isinstance(self.flutes, int) or self.flutes < 1 or self.flutes > 32:
            raise CamError("CAM_INVALID_PARAM", f"flutes out of range: {self.flutes}")
        if not isinstance(self.spindle_rpm, int) or not (MIN_RPM <= self.spindle_rpm <= MAX_RPM):
            raise CamError(
                "CAM_INVALID_PARAM",
                f"spindle_rpm must be in [{MIN_RPM}, {MAX_RPM}] (got {self.spindle_rpm})",
            )
        fxy = check_finite(self.feed_xy_mm_min, "feed_xy_mm_min", positive=True)
        fz = check_finite(self.feed_z_mm_min, "feed_z_mm_min", positive=True)
        if fxy > MAX_FEED_MM_MIN or fz > MAX_FEED_MM_MIN:
            raise CamError("CAM_INVALID_PARAM", "feed rate exceeds safety ceiling")
        check_finite(self.stepdown_mm, "stepdown_mm", positive=True)
        so = check_finite(self.stepover_ratio, "stepover_ratio", positive=True)
        if not (MIN_STEPOVER <= so <= MAX_STEPOVER):
            raise CamError(
                "CAM_INVALID_PARAM",
                f"stepover_ratio must be in [{MIN_STEPOVER}, {MAX_STEPOVER}] (got {so})",
            )
        if self.laser_power_pct is not None:
            pw = check_finite(self.laser_power_pct, "laser_power_pct", positive=True)
            if pw > 100.0:
                raise CamError("CAM_INVALID_PARAM", f"laser_power_pct must be <= 100 (got {pw})")

    @property
    def radius_mm(self) -> float:
        return self.diameter_mm * 0.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_code": self.tool_code,
            "tool_type": self.tool_type,
            "diameter_mm": self.diameter_mm,
            "flutes": self.flutes,
            "material_group": self.material_group,
            "spindle_rpm": self.spindle_rpm,
            "feed_xy_mm_min": self.feed_xy_mm_min,
            "feed_z_mm_min": self.feed_z_mm_min,
            "stepdown_mm": self.stepdown_mm,
            "stepover_ratio": self.stepover_ratio,
            "laser_power_pct": self.laser_power_pct,
        }


DEFAULT_TOOLS: tuple[CamTool, ...] = (
    CamTool(
        tool_code="T01",
        tool_type="ENDMILL_FLAT",
        diameter_mm=10.0,
        flutes=4,
        material_group="STEEL",
        spindle_rpm=4500,
        feed_xy_mm_min=600.0,
        feed_z_mm_min=200.0,
        stepdown_mm=2.0,
        stepover_ratio=0.5,
    ),
    CamTool(
        tool_code="T02",
        tool_type="ENDMILL_FLAT",
        diameter_mm=6.0,
        flutes=4,
        material_group="STEEL",
        spindle_rpm=6000,
        feed_xy_mm_min=500.0,
        feed_z_mm_min=180.0,
        stepdown_mm=1.5,
        stepover_ratio=0.5,
    ),
    CamTool(
        tool_code="T03",
        tool_type="DRILL",
        diameter_mm=8.0,
        flutes=2,
        material_group="STEEL",
        spindle_rpm=2200,
        feed_xy_mm_min=300.0,
        feed_z_mm_min=150.0,
        stepdown_mm=4.0,
        stepover_ratio=0.5,
    ),
    CamTool(
        tool_code="T90",
        tool_type="LASER_NOZZLE",
        diameter_mm=0.2,
        flutes=1,
        material_group="SHEET",
        spindle_rpm=100,
        feed_xy_mm_min=3000.0,
        feed_z_mm_min=1000.0,
        stepdown_mm=1.0,
        stepover_ratio=0.5,
        laser_power_pct=85.0,
    ),
)
