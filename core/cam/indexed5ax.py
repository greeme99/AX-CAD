"""3+2 Indexed 5-Axis Positioning: Tilted Workplane (G68.2 / CYCLE800 / PLANE SPATIAL) & Holder Collision (CAM C7)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

from core.cam.mill3d import HeightMap
from core.cam.mill25d import ToolpathSegment
from core.cam.tools import CamError, check_finite

TiltController = Literal["FANUC_MILL", "SIEMENS_840D", "HEIDENHAIN_TNC"]


@dataclass(frozen=True)
class TiltedWorkplane:
    origin: tuple[float, float, float]
    normal: tuple[float, float, float]
    rot_matrix: np.ndarray  # (3, 3) maps local frame -> machine frame; col 2 == normal
    a_deg: float  # rotation about X (deg)
    b_deg: float  # rotation about Y (deg)
    c_deg: float  # rotation about Z (deg)

    def tilt_header(self, controller: TiltController) -> list[str]:
        ox, oy, oz = self.origin
        if controller == "SIEMENS_840D":
            return [
                f'CYCLE800(1,"TABLE",100000,57,{ox:.3f},{oy:.3f},{oz:.3f},{self.a_deg:.3f},{self.b_deg:.3f},{self.c_deg:.3f},0,0,0,-1,100,1)'
            ]
        if controller == "HEIDENHAIN_TNC":
            return [
                f"PLANE SPATIAL SPA{self.a_deg:+.3f} SPB{self.b_deg:+.3f} SPC{self.c_deg:+.3f} TURN FMAX SEQ- TABLE ROT"
            ]
        # Fanuc G68.2 + G53.1 (Euler ZYX I=C, J=B, K=A)
        return [
            f"G68.2 X{ox:.3f} Y{oy:.3f} Z{oz:.3f} I{self.c_deg:.3f} J{self.b_deg:.3f} K{self.a_deg:.3f}",
            "G53.1",
        ]

    @staticmethod
    def tilt_footer(controller: TiltController) -> list[str]:
        if controller == "SIEMENS_840D":
            return ["CYCLE800()"]
        if controller == "HEIDENHAIN_TNC":
            return ["PLANE RESET STAY"]
        return ["G69"]


def tilted_workplane(
    normal: tuple[float, float, float],
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> TiltedWorkplane:
    """Compute orthonormal local workplane with +Z along `normal` and XYZ Euler angles (A, B, C) in degrees.

    Convention: R = Rz(C) * Ry(B) * Rx(A), so R @ [0, 0, 1] == unit(normal).
    """
    nx, ny, nz = (check_finite(v, "normal") for v in normal)
    ox, oy, oz = (check_finite(v, "origin") for v in origin)
    nlen = math.sqrt(nx * nx + ny * ny + nz * nz)
    if nlen <= 1e-9:
        raise CamError("CAM_INVALID_PARAM", "tilted workplane normal cannot be zero vector")
    ux, uy, uz = nx / nlen, ny / nlen, nz / nlen

    # Solve Rz(C) * Ry(B) * Rx(A) * [0, 0, 1]^T = [ux, uy, uz]^T with C = atan2(uy, ux) and A = 0:
    # Rz(C) * Ry(B) * [0, 0, 1]^T = [cos(C)*sin(B), sin(C)*sin(B), cos(B)]^T
    b_rad = math.acos(max(-1.0, min(1.0, uz)))
    horiz = math.hypot(ux, uy)
    c_rad = math.atan2(uy, ux) if horiz > 1e-9 else 0.0
    a_rad = 0.0

    ca, sa = math.cos(a_rad), math.sin(a_rad)
    cb, sb = math.cos(b_rad), math.sin(b_rad)
    cc, sc = math.cos(c_rad), math.sin(c_rad)

    rx = np.array([[1.0, 0.0, 0.0], [0.0, ca, -sa], [0.0, sa, ca]], dtype=np.float64)
    ry = np.array([[cb, 0.0, sb], [0.0, 1.0, 0.0], [-sb, 0.0, cb]], dtype=np.float64)
    rz = np.array([[cc, -sc, 0.0], [sc, cc, 0.0], [0.0, 0.0, 1.0]], dtype=np.float64)
    rot = rz @ ry @ rx

    return TiltedWorkplane(
        origin=(ox, oy, oz),
        normal=(ux, uy, uz),
        rot_matrix=rot,
        a_deg=math.degrees(a_rad),
        b_deg=math.degrees(b_rad),
        c_deg=math.degrees(c_rad),
    )


def check_holder_collision(
    segments: list[ToolpathSegment],
    hmap: HeightMap,
    *,
    flute_length_mm: float = 25.0,
    holder_radius_mm: float = 18.0,
) -> list[dict[str, float]]:
    """Check whether the tool holder (at z_tip + flute_length_mm, radius holder_radius_mm) collides with the workpiece height map."""
    fl = check_finite(flute_length_mm, "flute_length_mm", positive=True)
    hr = check_finite(holder_radius_mm, "holder_radius_mm", positive=True)
    collisions: list[dict[str, float]] = []

    for idx, seg in enumerate(segments):
        ex, ey, ez = seg.end
        holder_z = ez + fl
        ix0 = max(0, int(np.searchsorted(hmap.xs, ex - hr, side="left")))
        ix1 = min(hmap.xs.size - 1, int(np.searchsorted(hmap.xs, ex + hr, side="right")))
        iy0 = max(0, int(np.searchsorted(hmap.ys, ey - hr, side="left")))
        iy1 = min(hmap.ys.size - 1, int(np.searchsorted(hmap.ys, ey + hr, side="right")))
        if ix0 > ix1 or iy0 > iy1:
            continue
        sub_x = hmap.xs[ix0 : ix1 + 1][:, None] - ex
        sub_y = hmap.ys[iy0 : iy1 + 1][None, :] - ey
        mask = (sub_x * sub_x + sub_y * sub_y) <= hr * hr
        if np.any(mask):
            max_part_z = float(hmap.z[ix0 : ix1 + 1, iy0 : iy1 + 1][mask].max())
            if max_part_z > holder_z + 1e-3:
                collisions.append(
                    {
                        "segment_index": float(idx),
                        "x": round(ex, 4),
                        "y": round(ey, 4),
                        "holder_z": round(holder_z, 4),
                        "workpiece_z": round(max_part_z, 4),
                        "penetration_mm": round(max_part_z - holder_z, 4),
                    }
                )
    return collisions
