"""3-Axis Freeform Surface Machining: Z-level roughing, waterline & constant-scallop finishing (CAM C5)."""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from OCP.Bnd import Bnd_Box
from OCP.BRep import BRep_Tool
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Shape

from core.cam.mill25d import (
    MAX_Z_PASSES,
    ToolpathResult,
    ToolpathSegment,
    _append_seg,
    _finalize,
)
from core.cam.tools import CamError, CamTool, check_finite

MAX_GRID_PTS = 200_000


@dataclass
class HeightMap:
    xs: np.ndarray  # (Nx,)
    ys: np.ndarray  # (Ny,)
    z: np.ndarray  # (Nx, Ny)
    z_min: float
    z_max: float

    @property
    def step_x(self) -> float:
        return float(self.xs[1] - self.xs[0]) if self.xs.size > 1 else 1.0

    @property
    def step_y(self) -> float:
        return float(self.ys[1] - self.ys[0]) if self.ys.size > 1 else 1.0


def scallop_stepover(tool_radius_mm: float, target_scallop_mm: float) -> float:
    """Exact ball-nose constant-scallop stepover: s = 2 * sqrt(2*R*h - h^2)."""
    r = check_finite(tool_radius_mm, "tool_radius_mm", positive=True)
    h = check_finite(target_scallop_mm, "target_scallop_mm", positive=True)
    if h >= r:
        raise CamError("CAM_INVALID_PARAM", f"target_scallop_mm ({h}) must be < tool radius ({r})")
    return 2.0 * math.sqrt(2.0 * r * h - h * h)


def sample_height_map(
    *,
    shape: TopoDS_Shape | None = None,
    surface_fn: Callable[[np.ndarray, np.ndarray], np.ndarray] | None = None,
    bbox: tuple[float, float, float, float, float, float] | None = None,
    grid_step_mm: float = 2.0,
) -> HeightMap:
    """Sample a 3D B-Rep solid or analytic z(x, y) surface onto a regular XY height grid."""
    step = check_finite(grid_step_mm, "grid_step_mm", positive=True)
    if shape is not None:
        box = Bnd_Box()
        BRepBndLib.Add_s(shape, box)
        pmin, pmax = box.CornerMin(), box.CornerMax()
        xmin, ymin, zmin = float(pmin.X()), float(pmin.Y()), float(pmin.Z())
        xmax, ymax, _zmax = float(pmax.X()), float(pmax.Y()), float(pmax.Z())
    elif bbox is not None:
        xmin, ymin, zmin, xmax, ymax, _zmax = (check_finite(v, "bbox") for v in bbox)
    else:
        raise CamError("CAM_INVALID_PARAM", "either shape or bbox+surface_fn must be provided")

    nx = max(2, math.ceil((xmax - xmin) / step) + 1)
    ny = max(2, math.ceil((ymax - ymin) / step) + 1)
    if nx * ny > MAX_GRID_PTS:
        raise CamError(
            "CAM_LIMIT_EXCEEDED",
            f"height-map grid ({nx}x{ny}={nx * ny}) exceeds MAX_GRID_PTS ({MAX_GRID_PTS})",
        )

    xs = np.linspace(xmin, xmax, nx, dtype=np.float64)
    ys = np.linspace(ymin, ymax, ny, dtype=np.float64)

    if surface_fn is not None:
        gx, gy = np.meshgrid(xs, ys, indexing="ij")
        z_grid = np.asarray(surface_fn(gx, gy), dtype=np.float64)
        return HeightMap(
            xs=xs, ys=ys, z=z_grid, z_min=float(z_grid.min()), z_max=float(z_grid.max())
        )

    assert shape is not None
    BRepMesh_IncrementalMesh(shape, max(0.05, step * 0.25), False, 0.3, True)
    z_grid = np.full((nx, ny), zmin, dtype=np.float64)
    dx = (xmax - xmin) / (nx - 1)
    dy = (ymax - ymin) / (ny - 1)

    fexp = TopExp_Explorer(shape, TopAbs_FACE)
    while fexp.More():
        face = TopoDS.Face(fexp.Current())
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is not None:
            trsf = loc.Transformation()
            n_nodes = tri.NbNodes()
            pts = np.empty((n_nodes, 3), dtype=np.float64)
            for i in range(1, n_nodes + 1):
                p = tri.Node(i).Transformed(trsf)
                pts[i - 1] = (p.X(), p.Y(), p.Z())
            for t in range(1, tri.NbTriangles() + 1):
                n1, n2, n3 = tri.Triangle(t).Get()
                p0, p1, p2 = pts[n1 - 1], pts[n2 - 1], pts[n3 - 1]
                tx0, tx1 = min(p0[0], p1[0], p2[0]), max(p0[0], p1[0], p2[0])
                ty0, ty1 = min(p0[1], p1[1], p2[1]), max(p0[1], p1[1], p2[1])
                ix0 = max(0, math.floor((tx0 - xmin) / dx))
                ix1 = min(nx - 1, math.ceil((tx1 - xmin) / dx))
                iy0 = max(0, math.floor((ty0 - ymin) / dy))
                iy1 = min(ny - 1, math.ceil((ty1 - ymin) / dy))
                v0x, v0y = p1[0] - p0[0], p1[1] - p0[1]
                v1x, v1y = p2[0] - p0[0], p2[1] - p0[1]
                denom = v0x * v1y - v1x * v0y
                if abs(denom) <= 1e-12:
                    continue
                for ix in range(ix0, ix1 + 1):
                    px = xs[ix] - p0[0]
                    for iy in range(iy0, iy1 + 1):
                        py = ys[iy] - p0[1]
                        u = (px * v1y - v1x * py) / denom
                        v = (v0x * py - px * v0y) / denom
                        if u >= -1e-6 and v >= -1e-6 and (u + v) <= 1.0 + 1e-6:
                            zt = p0[2] + u * (p1[2] - p0[2]) + v * (p2[2] - p0[2])
                            z_grid[ix, iy] = max(z_grid[ix, iy], zt)
        fexp.Next()

    return HeightMap(xs=xs, ys=ys, z=z_grid, z_min=float(z_grid.min()), z_max=float(z_grid.max()))


def _ball_drop_cutter_z(hmap: HeightMap, x: float, y: float, radius: float, is_ball: bool) -> float:
    """Compute minimum safe cutter tip Z at (x, y) so tool never gouges height map."""
    ix0 = max(0, int(np.searchsorted(hmap.xs, x - radius, side="left")) - 1)
    ix1 = min(hmap.xs.size - 1, int(np.searchsorted(hmap.xs, x + radius, side="right")))
    iy0 = max(0, int(np.searchsorted(hmap.ys, y - radius, side="left")) - 1)
    iy1 = min(hmap.ys.size - 1, int(np.searchsorted(hmap.ys, y + radius, side="right")))

    sub_x = hmap.xs[ix0 : ix1 + 1][:, None] - x
    sub_y = hmap.ys[iy0 : iy1 + 1][None, :] - y
    r2 = sub_x * sub_x + sub_y * sub_y
    sub_z = hmap.z[ix0 : ix1 + 1, iy0 : iy1 + 1]
    mask = r2 <= (radius * radius + 1e-9)
    if not np.any(mask):
        ix = min(hmap.xs.size - 1, max(0, round((x - hmap.xs[0]) / hmap.step_x)))
        iy = min(hmap.ys.size - 1, max(0, round((y - hmap.ys[0]) / hmap.step_y)))
        return float(hmap.z[ix, iy])
    if is_ball:
        # Ball nose tip Z must satisfy: z_tip + R - sqrt(R^2 - r^2) >= z_surf
        tip_z = sub_z[mask] - radius + np.sqrt(np.maximum(0.0, radius * radius - r2[mask]))
        return float(tip_z.max())
    return float(sub_z[mask].max())


def rough_3d(
    hmap: HeightMap,
    tool: CamTool,
    *,
    stock_top_z: float | None = None,
    stepdown_mm: float | None = None,
    stock_allowance_mm: float = 0.5,
    safe_z: float = 20.0,
) -> ToolpathResult:
    """3-axis Z-level roughing clearing material above height map + stock_allowance_mm."""
    sd = check_finite(
        tool.stepdown_mm if stepdown_mm is None else stepdown_mm, "stepdown_mm", positive=True
    )
    allow = check_finite(stock_allowance_mm, "stock_allowance_mm", non_negative=True)
    sz = check_finite(safe_z, "safe_z")
    z_top = hmap.z_max + allow if stock_top_z is None else check_finite(stock_top_z, "stock_top_z")
    z_bot = hmap.z_min + allow
    if z_top <= z_bot + 1e-6:
        raise CamError("CAM_INVALID_PARAM", "stock_top_z must be above surface minimum")

    n_passes = math.ceil((z_top - z_bot) / sd - 1e-9)
    if n_passes > MAX_Z_PASSES:
        raise CamError(
            "CAM_LIMIT_EXCEEDED", f"3D roughing Z passes ({n_passes}) exceed {MAX_Z_PASSES}"
        )

    step_y = max(hmap.step_y, tool.diameter_mm * tool.stepover_ratio)
    y_vals = np.arange(float(hmap.ys[0]), float(hmap.ys[-1]) + 1e-6, step_y)
    is_ball = tool.tool_type == "ENDMILL_BALL"

    segments: list[ToolpathSegment] = []
    cur = (float(hmap.xs[0]), float(hmap.ys[0]), sz)

    for p_idx in range(1, n_passes + 1):
        z_lvl = max(z_bot, z_top - p_idx * sd)
        for row_i, y in enumerate(y_vals):
            xs_iter = hmap.xs if (row_i % 2 == 0) else hmap.xs[::-1]
            row_pts: list[tuple[float, float, float]] = []
            for x in xs_iter:
                zf = _ball_drop_cutter_z(hmap, float(x), float(y), tool.radius_mm, is_ball) + allow
                z_cmd = max(z_lvl, zf)
                row_pts.append((float(x), float(y), z_cmd))
            if len(row_pts) < 2:
                continue
            p0 = row_pts[0]
            _append_seg(segments, ToolpathSegment("RAPID", cur, (p0[0], p0[1], sz)))
            _append_seg(
                segments, ToolpathSegment("PLUNGE", (p0[0], p0[1], sz), p0, feed=tool.feed_z_mm_min)
            )
            for a, b in itertools.pairwise(row_pts):
                _append_seg(segments, ToolpathSegment("CUT", a, b, feed=tool.feed_xy_mm_min))
            pend = row_pts[-1]
            _append_seg(segments, ToolpathSegment("RAPID", pend, (pend[0], pend[1], sz)))
            cur = (pend[0], pend[1], sz)

    return _finalize(segments, tool, n_passes)


def finish_scallop(
    hmap: HeightMap,
    tool: CamTool,
    *,
    target_scallop_mm: float = 0.05,
    safe_z: float = 20.0,
) -> ToolpathResult:
    """Ball-nose constant-scallop 3-axis surface finishing with exact drop-cutter clearance."""
    stepover = scallop_stepover(tool.radius_mm, target_scallop_mm)
    sz = check_finite(safe_z, "safe_z")
    is_ball = tool.tool_type == "ENDMILL_BALL"

    y_vals = np.arange(float(hmap.ys[0]), float(hmap.ys[-1]) + 1e-6, stepover)
    if y_vals.size == 0:
        y_vals = np.array([float(hmap.ys[0])])

    segments: list[ToolpathSegment] = []
    cur = (float(hmap.xs[0]), float(y_vals[0]), sz)
    started = False

    for row_i, y in enumerate(y_vals):
        x_step = min(hmap.step_x * 0.5, max(0.5, stepover * 0.5))
        xs_fine = np.arange(float(hmap.xs[0]), float(hmap.xs[-1]) + 1e-6, x_step)
        xs_iter = xs_fine if (row_i % 2 == 0) else xs_fine[::-1]
        pts = [
            (
                float(x),
                float(y),
                _ball_drop_cutter_z(hmap, float(x), float(y), tool.radius_mm, is_ball),
            )
            for x in xs_iter
        ]
        if not pts:
            continue
        p0 = pts[0]
        if not started:
            _append_seg(segments, ToolpathSegment("RAPID", cur, (p0[0], p0[1], sz)))
            _append_seg(
                segments, ToolpathSegment("PLUNGE", (p0[0], p0[1], sz), p0, feed=tool.feed_z_mm_min)
            )
            started = True
        else:
            _append_seg(segments, ToolpathSegment("CUT", cur, p0, feed=tool.feed_xy_mm_min))
        for a, b in itertools.pairwise(pts):
            _append_seg(segments, ToolpathSegment("CUT", a, b, feed=tool.feed_xy_mm_min))
        cur = pts[-1]

    if started:
        _append_seg(segments, ToolpathSegment("RAPID", cur, (cur[0], cur[1], sz)))
    return _finalize(segments, tool, 1)


def finish_waterline(
    hmap: HeightMap,
    tool: CamTool,
    *,
    stepdown_mm: float = 1.0,
    safe_z: float = 20.0,
) -> ToolpathResult:
    """Z-constant waterline finishing along steep surface contours."""
    return rough_3d(
        hmap,
        tool,
        stock_top_z=hmap.z_max,
        stepdown_mm=stepdown_mm,
        stock_allowance_mm=0.0,
        safe_z=safe_z,
    )
