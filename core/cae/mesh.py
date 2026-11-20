"""3D Tetrahedral (Tet4) mesh generation & FaceRef boundary condition mapping (plan.md §24 E1)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.BRepGProp import BRepGProp
from OCP.gp import gp_Pnt
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_IN, TopAbs_ON
from OCP.TopoDS import TopoDS_Shape

MAX_NODES = 100_000
MAX_ELEMENTS = 400_000


class CaeError(Exception):
    """Domain error for CAE meshing and linear static FEA solving."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class TetMesh:
    nodes: np.ndarray  # (N, 3) float64
    elements: np.ndarray  # (M, 4) int32, positive Jacobian volume
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    is_box: bool = False

    @property
    def node_count(self) -> int:
        return int(self.nodes.shape[0])

    @property
    def element_count(self) -> int:
        return int(self.elements.shape[0])

    def select_face_nodes(self, face_ref: dict[str, Any]) -> np.ndarray:
        """Map a FaceRef signature (or axis/rim selector) to 0-based node indices on the boundary."""
        pts = self.nodes
        xmin, ymin, zmin = self.bbox_min
        xmax, ymax, zmax = self.bbox_max
        diag = math.hypot(xmax - xmin, ymax - ymin, zmax - zmin) or 1.0
        tol = float(face_ref.get("tol", max(1e-3, diag * 1e-4)))

        # 1. Explicit perimeter rim selector (for simply-supported plate boundary z=zmin around 4 edges)
        if face_ref.get("rim"):
            z_target = float(face_ref.get("z", zmin))
            on_x = (np.abs(pts[:, 0] - xmin) <= tol) | (np.abs(pts[:, 0] - xmax) <= tol)
            on_y = (np.abs(pts[:, 1] - ymin) <= tol) | (np.abs(pts[:, 1] - ymax) <= tol)
            on_z = np.abs(pts[:, 2] - z_target) <= tol
            mask = (on_x | on_y) & on_z
            return np.nonzero(mask)[0].astype(np.int32)

        # 2. Explicit axis plane selector: {"axis": "x"|"y"|"z", "value": float}
        if "axis" in face_ref and "value" in face_ref:
            ax = str(face_ref["axis"]).lower()
            idx = {"x": 0, "y": 1, "z": 2}.get(ax)
            if idx is None:
                raise CaeError("CAE_INVALID_PARAM", f"invalid axis in face_ref: {ax}")
            val = float(face_ref["value"])
            return np.nonzero(np.abs(pts[:, idx] - val) <= tol)[0].astype(np.int32)

        # 3. Standard FaceRef from core/geometry/refs.py: {"kind": "PLANE", "mid": [mx,my,mz], "normal"?: [nx,ny,nz]}
        mid = face_ref.get("mid")
        if mid is not None and len(mid) == 3:
            mx, my, mz = float(mid[0]), float(mid[1]), float(mid[2])
            normal = face_ref.get("normal")
            if normal is not None and len(normal) == 3:
                nx, ny, nz = float(normal[0]), float(normal[1]), float(normal[2])
                nlen = math.hypot(nx, ny, nz) or 1.0
                nx, ny, nz = nx / nlen, ny / nlen, nz / nlen
                dist = np.abs((pts[:, 0] - mx) * nx + (pts[:, 1] - my) * ny + (pts[:, 2] - mz) * nz)
                return np.nonzero(dist <= tol)[0].astype(np.int32)

            # Infer plane normal from whichever bounding-box face is closest to `mid`
            candidates = [
                (abs(mx - xmin), 0, xmin),
                (abs(mx - xmax), 0, xmax),
                (abs(my - ymin), 1, ymin),
                (abs(my - ymax), 1, ymax),
                (abs(mz - zmin), 2, zmin),
                (abs(mz - zmax), 2, zmax),
            ]
            best_dist, axis_idx, plane_val = min(candidates, key=lambda c: c[0])
            if best_dist <= max(tol * 10, 1.0):
                return np.nonzero(np.abs(pts[:, axis_idx] - plane_val) <= tol)[0].astype(np.int32)

            # Fallback: nearest nodes to mid point
            d2 = (pts[:, 0] - mx) ** 2 + (pts[:, 1] - my) ** 2 + (pts[:, 2] - mz) ** 2
            return np.nonzero(d2 <= (tol * 10) ** 2)[0].astype(np.int32)

        raise CaeError("CAE_INVALID_PARAM", "face_ref must specify mid, axis/value, or rim")


def _ensure_positive_jacobians(nodes: np.ndarray, elements: np.ndarray) -> np.ndarray:
    """Ensure every Tet4 element has strictly positive signed volume V_e > 0."""
    p0 = nodes[elements[:, 0]]
    v1 = nodes[elements[:, 1]] - p0
    v2 = nodes[elements[:, 2]] - p0
    v3 = nodes[elements[:, 3]] - p0
    det = np.einsum("ij,ij->i", np.cross(v1, v2), v3)
    valid = np.abs(det) > 1e-12
    elems = elements[valid].copy()
    det = det[valid]
    flip = det < 0
    if np.any(flip):
        elems[flip, 2], elems[flip, 3] = elems[flip, 3].copy(), elems[flip, 2].copy()
    if elems.shape[0] == 0:
        raise CaeError("CAE_MESH_FAILED", "no valid positive-Jacobian tetrahedra generated")
    return elems


def _kuhn_box_mesh(
    xmin: float,
    ymin: float,
    zmin: float,
    xmax: float,
    ymax: float,
    zmax: float,
    mesh_size_mm: float,
) -> TetMesh:
    lx, ly, lz = xmax - xmin, ymax - ymin, zmax - zmin
    nx = max(2, math.ceil(lx / mesh_size_mm))
    ny = max(2, math.ceil(ly / mesh_size_mm))
    nz = max(2, math.ceil(lz / mesh_size_mm))

    est_nodes = (nx + 1) * (ny + 1) * (nz + 1)
    est_elems = 6 * nx * ny * nz
    if est_nodes > MAX_NODES or est_elems > MAX_ELEMENTS:
        raise CaeError(
            "CAE_MESH_LIMIT_EXCEEDED",
            f"requested mesh ({est_nodes} nodes, {est_elems} elements) exceeds limit ({MAX_NODES} nodes, {MAX_ELEMENTS} elements)",
        )

    xs = np.linspace(xmin, xmax, nx + 1, dtype=np.float64)
    ys = np.linspace(ymin, ymax, ny + 1, dtype=np.float64)
    zs = np.linspace(zmin, zmax, nz + 1, dtype=np.float64)
    gx, gy, gz = np.meshgrid(xs, ys, zs, indexing="ij")
    nodes = np.column_stack([gx.ravel(), gy.ravel(), gz.ravel()])

    stride_x = (ny + 1) * (nz + 1)
    stride_y = nz + 1

    ix = np.arange(nx, dtype=np.int32)[:, None, None]
    iy = np.arange(ny, dtype=np.int32)[None, :, None]
    iz = np.arange(nz, dtype=np.int32)[None, None, :]
    base = (ix * stride_x + iy * stride_y + iz).ravel()

    n000 = base
    n100 = base + stride_x
    n010 = base + stride_y
    n110 = base + stride_x + stride_y
    n001 = base + 1
    n101 = base + stride_x + 1
    n011 = base + stride_y + 1
    n111 = base + stride_x + stride_y + 1

    # 6-tet Kuhn decomposition of each hexahedron cell sharing main diagonal n000 -> n111
    tets = np.vstack(
        [
            np.column_stack([n000, n100, n110, n111]),
            np.column_stack([n000, n110, n010, n111]),
            np.column_stack([n000, n010, n011, n111]),
            np.column_stack([n000, n011, n001, n111]),
            np.column_stack([n000, n001, n101, n111]),
            np.column_stack([n000, n101, n100, n111]),
        ]
    ).astype(np.int32)

    tets = _ensure_positive_jacobians(nodes, tets)
    return TetMesh(
        nodes=nodes,
        elements=tets,
        bbox_min=(xmin, ymin, zmin),
        bbox_max=(xmax, ymax, zmax),
        is_box=True,
    )


def generate_tet4_mesh(
    shape: TopoDS_Shape | None = None,
    mesh_size_mm: float = 5.0,
    *,
    box_dims: tuple[float, float, float] | None = None,
    box_origin: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> TetMesh:
    """Generate a 4-node tetrahedral (Tet4) volume mesh with MAX_NODES / MAX_ELEMENTS guard."""
    if (
        not isinstance(mesh_size_mm, (int, float))
        or not math.isfinite(mesh_size_mm)
        or mesh_size_mm <= 0
    ):
        raise CaeError("CAE_INVALID_PARAM", f"mesh_size_mm must be > 0 (got {mesh_size_mm})")

    if box_dims is not None:
        ox, oy, oz = box_origin
        lx, ly, lz = box_dims
        return _kuhn_box_mesh(ox, oy, oz, ox + lx, oy + ly, oz + lz, float(mesh_size_mm))

    if shape is None:
        raise CaeError("CAE_INVALID_PARAM", "either shape or box_dims must be provided")

    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    pmin, pmax = box.CornerMin(), box.CornerMax()
    xmin, ymin, zmin = float(pmin.X()), float(pmin.Y()), float(pmin.Z())
    xmax, ymax, zmax = float(pmax.X()), float(pmax.Y()), float(pmax.Z())
    lx, ly, lz = xmax - xmin, ymax - ymin, zmax - zmin
    if lx <= 1e-6 or ly <= 1e-6 or lz <= 1e-6:
        raise CaeError("CAE_MESH_FAILED", "degenerate 3D bounding box")

    # Fast check before allocating
    nx = max(2, math.ceil(lx / mesh_size_mm))
    ny = max(2, math.ceil(ly / mesh_size_mm))
    nz = max(2, math.ceil(lz / mesh_size_mm))
    if (nx + 1) * (ny + 1) * (nz + 1) > MAX_NODES or 6 * nx * ny * nz > MAX_ELEMENTS:
        raise CaeError(
            "CAE_MESH_LIMIT_EXCEEDED",
            f"mesh_size_mm ({mesh_size_mm}) exceeds MAX_NODES ({MAX_NODES}) / MAX_ELEMENTS ({MAX_ELEMENTS})",
        )

    # Check if the B-Rep volume matches its bounding box (axis-aligned box fast path)
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    vol = float(props.Mass())
    box_vol = lx * ly * lz
    if abs(vol - box_vol) <= 1e-3 * box_vol:
        return _kuhn_box_mesh(xmin, ymin, zmin, xmax, ymax, zmax, float(mesh_size_mm))

    # General B-Rep solid: start from Kuhn grid and keep tetrahedra whose centroid is inside the solid
    full = _kuhn_box_mesh(xmin, ymin, zmin, xmax, ymax, zmax, float(mesh_size_mm))
    centroids = full.nodes[full.elements].mean(axis=1)
    clf = BRepClass3d_SolidClassifier(shape)
    keep = np.zeros(full.elements.shape[0], dtype=bool)
    for i, (cx, cy, cz) in enumerate(centroids):
        clf.Perform(gp_Pnt(float(cx), float(cy), float(cz)), 1e-4)
        keep[i] = clf.State() in (TopAbs_IN, TopAbs_ON)

    kept_elems = full.elements[keep]
    if kept_elems.shape[0] == 0:
        raise CaeError("CAE_MESH_FAILED", "no tetrahedral elements inside solid")

    used_nodes, inv = np.unique(kept_elems, return_inverse=True)
    compact_nodes = full.nodes[used_nodes]
    compact_elems = inv.reshape(kept_elems.shape).astype(np.int32)
    return TetMesh(
        nodes=compact_nodes,
        elements=compact_elems,
        bbox_min=(xmin, ymin, zmin),
        bbox_max=(xmax, ymax, zmax),
        is_box=False,
    )
