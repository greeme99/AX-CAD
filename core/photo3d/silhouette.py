"""Foreground silhouette extraction & pixel-to-mm scale calibration (Pillow + NumPy + SciPy)."""

from __future__ import annotations

import io
import math

import numpy as np
from PIL import Image
from scipy import ndimage

from . import MAX_IMAGE_BYTES, Photo3dError, ReferenceScale, SilhouetteSummary

_MAX_ANALYSIS_DIM = 512


def validate_image_bytes(data: bytes, filename: str = "photo") -> Image.Image:
    """Validate magic bytes & size and return an RGB Pillow Image."""
    if not data:
        raise Photo3dError("PHOTO3D_EMPTY", f"이미지 파일이 비어 있습니다: {filename}")
    if len(data) > MAX_IMAGE_BYTES:
        raise Photo3dError("PHOTO3D_TOO_LARGE", f"이미지 크기가 15MB를 초과합니다: {filename}", 413)
    # Strict magic byte check: PNG, JPEG, WEBP
    is_png = data.startswith(b"\x89PNG\r\n\x1a\n")
    is_jpg = data.startswith(b"\xff\xd8\xff")
    is_webp = len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if not (is_png or is_jpg or is_webp):
        raise Photo3dError(
            "PHOTO3D_INVALID_FORMAT",
            f"PNG, JPEG, WEBP 이미지 파일만 지원합니다: {filename}",
        )
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        return img.convert("RGB")
    except Exception as exc:
        raise Photo3dError(
            "PHOTO3D_DECODE_FAILED", f"이미지를 읽을 수 없습니다: {filename}"
        ) from exc


def extract_silhouette(
    img: Image.Image,
    *,
    view_label: str = "ANGLE",
    angle_deg: float = 0.0,
) -> tuple[np.ndarray, SilhouetteSummary]:
    """Segment foreground object silhouette from a photo using border-color background estimation.

    Returns (mask_bool (H, W) resized to <= 512px, SilhouetteSummary in original pixel coords).
    """
    orig_w, orig_h = img.size
    if orig_w < 8 or orig_h < 8:
        raise Photo3dError("PHOTO3D_TOO_SMALL", "이미지 해상도가 너무 작습니다 (최소 8x8 px)")

    # Resize for fast & consistent morphological analysis
    scale = min(1.0, _MAX_ANALYSIS_DIM / float(max(orig_w, orig_h)))
    work = (
        img.resize((max(8, round(orig_w * scale)), max(8, round(orig_h * scale))))
        if scale < 1.0
        else img
    )
    arr = np.asarray(work, dtype=np.float32)  # (H, W, 3)
    _h, _w, _ = arr.shape

    # Estimate background RGB from 4 image borders
    border = np.concatenate(
        [arr[0, :, :], arr[-1, :, :], arr[:, 0, :], arr[:, -1, :]],
        axis=0,
    )
    bg_rgb = np.median(border, axis=0)
    dist = np.linalg.norm(arr - bg_rgb[None, None, :], axis=2)

    # Adaptive threshold: Otsu-like separation on color distance from background
    d_max = float(np.max(dist))
    if d_max < 12.0:
        raise Photo3dError(
            "PHOTO3D_NO_OBJECT",
            f"배경과 물체를 구분할 수 없습니다 ({view_label}). 배경 대비가 뚜렷한 사진을 올려주세요.",
        )
    bg_noise = float(np.percentile(np.linalg.norm(border - bg_rgb[None, :], axis=1), 95))
    thresh = max(25.0, min(d_max * 0.45, bg_noise + 22.0))
    raw_mask = dist > thresh

    # Morphological cleanup & keep largest connected foreground component
    clean = ndimage.binary_closing(raw_mask, structure=np.ones((3, 3), dtype=bool))
    clean = ndimage.binary_opening(clean, structure=np.ones((3, 3), dtype=bool))
    labeled, num = ndimage.label(clean)
    if num == 0:
        raise Photo3dError("PHOTO3D_NO_OBJECT", f"물체 윤곽을 찾지 못했습니다 ({view_label})")

    counts = np.bincount(labeled.ravel())
    counts[0] = 0
    best_id = int(np.argmax(counts))
    fg = labeled == best_id
    filled = ndimage.binary_fill_holes(fg)

    # Count internal holes (through-holes visible in silhouette)
    holes = filled & (~fg)
    _, hole_num = ndimage.label(holes)
    # Filter tiny 1-2px hole noise
    real_holes = 0
    if hole_num > 0:
        hl, _ = ndimage.label(holes)
        for hid in range(1, hole_num + 1):
            if int(np.sum(hl == hid)) >= max(9, int(fg.size * 0.0004)):
                real_holes += 1

    ys, xs = np.nonzero(filled)
    if ys.size < 16:
        raise Photo3dError("PHOTO3D_NO_OBJECT", f"물체 영역이 너무 작습니다 ({view_label})")

    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    bw = max(1, x1 - x0 + 1)
    bh = max(1, y1 - y0 + 1)
    area = float(np.sum(filled))
    fill_ratio = area / float(bw * bh)

    # Perimeter via eroded boundary pixels -> circularity = 4*pi*A / P^2
    eroded = ndimage.binary_erosion(filled)
    perim = float(np.sum(filled & (~eroded)))
    circularity = (4.0 * math.pi * area) / max(perim * perim, 1.0)

    inv = 1.0 / scale
    orig_bbox = (
        round(x0 * inv),
        round(y0 * inv),
        round((x1 + 1) * inv),
        round((y1 + 1) * inv),
    )
    summary = SilhouetteSummary(
        view_label=view_label,
        angle_deg=angle_deg,
        image_w=orig_w,
        image_h=orig_h,
        bbox_px=orig_bbox,
        width_px=float(orig_bbox[2] - orig_bbox[0]),
        height_px=float(orig_bbox[3] - orig_bbox[1]),
        fill_ratio=round(fill_ratio, 4),
        circularity=round(min(1.2, circularity), 4),
        hole_count=real_holes,
    )
    return filled, summary


def compute_mm_per_pixel(scale: ReferenceScale, primary: SilhouetteSummary) -> float:
    """Compute exact mm/pixel ratio from either 2-point pixel picks or overall reference dimension."""
    if not math.isfinite(scale.reference_mm) or scale.reference_mm <= 0 or scale.reference_mm > 1e6:
        raise Photo3dError(
            "PHOTO3D_INVALID_SCALE", "기준 치수(mm)는 0보다 크고 1,000,000 이하여야 합니다"
        )

    if scale.p1_px is not None and scale.p2_px is not None:
        dx = float(scale.p2_px[0]) - float(scale.p1_px[0])
        dy = float(scale.p2_px[1]) - float(scale.p1_px[1])
        px_dist = math.hypot(dx, dy)
        if px_dist < 2.0:
            raise Photo3dError(
                "PHOTO3D_INVALID_SCALE", "기준 치수 두 점이 너무 가깝습니다 (최소 2px 이상)"
            )
        return float(scale.reference_mm) / px_dist

    # Overall reference length on chosen axis (X -> silhouette width, Z -> silhouette height)
    ref_px = primary.height_px if scale.axis == "Z" else primary.width_px
    if ref_px < 2.0:
        raise Photo3dError(
            "PHOTO3D_INVALID_SCALE", "물체 기준 폭이 너무 작아 축척을 계산할 수 없습니다"
        )
    return float(scale.reference_mm) / ref_px
