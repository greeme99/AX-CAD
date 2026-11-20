"""2.5D/3D Height-Field (Dexel) Stock Removal, Rapid-Collision & Gouge Verification (CAM C8)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from core.cam.mill3d import MAX_GRID_PTS, HeightMap
from core.cam.mill25d import ToolpathSegment
from core.cam.tools import CamError, check_finite


@dataclass
class StockSimResult:
    initial_volume_mm3: float
    removed_volume_mm3: float
    remaining_volume_mm3: float
    rapid_collision_count: int
    gouge_count: int
    max_gouge_mm: float
    grid_shape: tuple[int, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "initial_volume_mm3": round(self.initial_volume_mm3, 3),
            "removed_volume_mm3": round(self.removed_volume_mm3, 3),
            "remaining_volume_mm3": round(self.remaining_volume_mm3, 3),
            "rapid_collision_count": self.rapid_collision_count,
            "gouge_count": self.gouge_count,
            "max_gouge_mm": round(self.max_gouge_mm, 4),
            "grid_shape": list(self.grid_shape),
        }


def simulate_stock_removal(
    segments: list[ToolpathSegment],
    *,
    tool_diameter_mm: float,
    blank_bbox: tuple[float, float, float, float, float, float],
    grid_step_mm: float = 2.0,
    is_ball_nose: bool = False,
    target_height_map: HeightMap | None = None,
    gouge_tol_mm: float = 0.05,
) -> StockSimResult:
    """Sweep toolpath segments across a dexel Z-height stock grid to compute removed volume, rapid collisions, and target gouges."""
    diam = check_finite(tool_diameter_mm, "tool_diameter_mm", positive=True)
    step = check_finite(grid_step_mm, "grid_step_mm", positive=True)
    xmin, ymin, zmin, xmax, ymax, zmax = (check_finite(v, "blank_bbox") for v in blank_bbox)
    if xmax <= xmin or ymax <= ymin or zmax <= zmin:
        raise CamError("CAM_INVALID_PARAM", "blank_bbox must have positive extents")

    nx = max(2, math.ceil((xmax - xmin) / step) + 1)
    ny = max(2, math.ceil((ymax - ymin) / step) + 1)
    if nx * ny > MAX_GRID_PTS:
        raise CamError(
            "CAM_LIMIT_EXCEEDED", f"stock simulation grid ({nx * ny}) exceeds {MAX_GRID_PTS}"
        )

    xs = np.linspace(xmin, xmax, nx, dtype=np.float64)
    ys = np.linspace(ymin, ymax, ny, dtype=np.float64)
    dx = (xmax - xmin) / (nx - 1)
    dy = (ymax - ymin) / (ny - 1)
    cell_area = ((xmax - xmin) * (ymax - ymin)) / (nx * ny)

    stock_z = np.full((nx, ny), zmax, dtype=np.float64)
    initial_vol = float(np.sum(np.maximum(0.0, stock_z - zmin)) * cell_area)

    radius = 0.5 * diam
    r2_max = radius * radius
    rapid_collisions = 0

    for seg in segments:
        sx, sy, sz = seg.start
        ex, ey, ez = seg.end
        seg_len = math.sqrt((ex - sx) ** 2 + (ey - sy) ** 2 + (ez - sz) ** 2)
        n_samples = max(2, math.ceil(seg_len / max(0.5 * step, 0.25)) + 1)
        ts = np.linspace(0.0, 1.0, n_samples)

        if seg.kind == "RAPID":
            # Pure vertical retract upward from already-cut surface is safe
            if ez > sz and abs(ex - sx) <= 1e-6 and abs(ey - sy) <= 1e-6:
                continue
            collided = False
            for t in ts[1:]:
                px = sx + t * (ex - sx)
                py = sy + t * (ey - sy)
                pz = sz + t * (ez - sz)
                if (
                    px < xmin - radius
                    or px > xmax + radius
                    or py < ymin - radius
                    or py > ymax + radius
                ):
                    continue
                ix = min(nx - 1, max(0, round((px - xmin) / dx)))
                iy = min(ny - 1, max(0, round((py - ymin) / dy)))
                if pz < stock_z[ix, iy] - 1e-3:
                    collided = True
                    break
            if collided:
                rapid_collisions += 1
            continue

        for t in ts:
            px = sx + t * (ex - sx)
            py = sy + t * (ey - sy)
            pz = sz + t * (ez - sz)
            ix0 = max(0, math.floor((px - radius - xmin) / dx))
            ix1 = min(nx - 1, math.ceil((px + radius - xmin) / dx))
            iy0 = max(0, math.floor((py - radius - ymin) / dy))
            iy1 = min(ny - 1, math.ceil((py + radius - ymin) / dy))
            if ix0 > ix1 or iy0 > iy1:
                continue
            gx = xs[ix0 : ix1 + 1][:, None] - px
            gy = ys[iy0 : iy1 + 1][None, :] - py
            r2 = gx * gx + gy * gy
            mask = r2 <= r2_max + 1e-9
            if not np.any(mask):
                continue
            sub = stock_z[ix0 : ix1 + 1, iy0 : iy1 + 1]
            if is_ball_nose:
                cut_z = pz + radius - np.sqrt(np.maximum(0.0, r2_max - r2))
                sub[mask] = np.minimum(sub[mask], np.maximum(zmin, cut_z[mask]))
            else:
                sub[mask] = np.minimum(sub[mask], max(zmin, pz))

    remaining_vol = float(np.sum(np.maximum(0.0, stock_z - zmin)) * cell_area)
    removed_vol = max(0.0, initial_vol - remaining_vol)

    gouge_count = 0
    max_gouge = 0.0
    if target_height_map is not None and target_height_map.z.shape == stock_z.shape:
        diff = target_height_map.z - stock_z
        gouges = diff[diff > gouge_tol_mm]
        gouge_count = int(gouges.size)
        if gouge_count > 0:
            max_gouge = float(gouges.max())

    return StockSimResult(
        initial_volume_mm3=initial_vol,
        removed_volume_mm3=removed_vol,
        remaining_volume_mm3=remaining_vol,
        rapid_collision_count=rapid_collisions,
        gouge_count=gouge_count,
        max_gouge_mm=max_gouge,
        grid_shape=(nx, ny),
    )
