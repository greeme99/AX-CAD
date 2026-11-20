"""Photo-to-3D: Multi-angle & Sequential Photo Real-Scale 3D Reconstruction + Antigravity SDK."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

MAX_IMAGES = 36
MAX_IMAGE_BYTES = 15 * 1024 * 1024  # 15 MB per photo

PhotoMode = Literal["ORTHO_MULTI", "TURNTABLE", "FREE_ANGLE"]
OutputMode = Literal["HYBRID", "PARAMETRIC", "VISUAL_HULL"]
ViewLabel = Literal["FRONT", "BACK", "LEFT", "RIGHT", "SIDE", "TOP", "BOTTOM", "ANGLE"]


class Photo3dError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


@dataclass(frozen=True)
class ReferenceScale:
    """Real-world scale anchor (mm): either overall target length or 2-point pixel calibration."""

    reference_mm: float
    axis: Literal["X", "Y", "Z"] = "X"
    p1_px: tuple[float, float] | None = None
    p2_px: tuple[float, float] | None = None


@dataclass(frozen=True)
class PhotoInput:
    """Validated input photo with optional view label or turntable azimuth angle."""

    data: bytes
    filename: str
    view_label: ViewLabel = "ANGLE"
    angle_deg: float | None = None


@dataclass
class SilhouetteSummary:
    view_label: str
    angle_deg: float
    image_w: int
    image_h: int
    bbox_px: tuple[int, int, int, int]  # (xmin, ymin, xmax, ymax)
    width_px: float
    height_px: float
    fill_ratio: float
    circularity: float
    hole_count: int


@dataclass
class ReconstructionResult:
    mode: PhotoMode
    output_mode: OutputMode
    resolved_output: Literal["PARAMETRIC", "VISUAL_HULL"]
    bbox_mm: tuple[float, float, float]  # (W_x, D_y, H_z)
    volume_mm3: float
    surface_area_mm2: float
    mm_per_px: float
    brep_bytes: bytes
    feature_plan: dict[str, Any]
    ai_used: bool = False
    ai_provider: str = "local-geometry"
    view_summaries: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "output_mode": self.output_mode,
            "resolved_output": self.resolved_output,
            "bbox_mm": [round(v, 3) for v in self.bbox_mm],
            "volume_mm3": round(self.volume_mm3, 3),
            "surface_area_mm2": round(self.surface_area_mm2, 3),
            "mm_per_px": round(self.mm_per_px, 6),
            "feature_plan": self.feature_plan,
            "ai_used": self.ai_used,
            "ai_provider": self.ai_provider,
            "view_summaries": self.view_summaries,
            "warnings": self.warnings,
        }
