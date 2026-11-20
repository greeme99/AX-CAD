"""2nd-Order 10-Node Quadratic Tetrahedral (Tet10) Element & 4-Point Gauss Quadrature (CAE E4)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import sparse
from scipy.sparse import linalg as spla

from core.cae.fea import (
    FeaResult,
    _check_restraints_not_underconstrained,
    _isotropic_d_matrix,
)
from core.cae.mesh import MAX_NODES, CaeError, TetMesh

# 6 local edges of a Tet4 -> midpoint local node indices 4..9
_TET10_EDGES: tuple[tuple[int, int], ...] = (
    (0, 1),  # node 4
    (1, 2),  # node 5
    (2, 0),  # node 6
    (0, 3),  # node 7
    (1, 3),  # node 8
    (2, 3),  # node 9
)

# 4-point symmetric Gauss quadrature on unit reference tetrahedron (exact for degree <= 2, vol = 1/6)
_GAUSS_A = (5.0 + 3.0 * math.sqrt(5.0)) / 20.0
_GAUSS_B = (5.0 - math.sqrt(5.0)) / 20.0
_GAUSS_PTS: tuple[tuple[float, float, float, float], ...] = (
    (_GAUSS_A, _GAUSS_B, _GAUSS_B, _GAUSS_B),
    (_GAUSS_B, _GAUSS_A, _GAUSS_B, _GAUSS_B),
    (_GAUSS_B, _GAUSS_B, _GAUSS_A, _GAUSS_B),
    (_GAUSS_B, _GAUSS_B, _GAUSS_B, _GAUSS_A),
)
_GAUSS_W = 1.0 / 24.0  # sum of 4 weights = 4/24 = 1/6 = ref tet volume


@dataclass
class Tet10Mesh:
    nodes: np.ndarray  # (N10, 3) float64
    elements: np.ndarray  # (M, 10) int32
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]

    @property
    def node_count(self) -> int:
        return int(self.nodes.shape[0])

    @property
    def element_count(self) -> int:
        return int(self.elements.shape[0])

    def select_face_nodes(self, face_ref: dict[str, Any]) -> np.ndarray:
        proxy = TetMesh(
            nodes=self.nodes,
            elements=self.elements[:, :4],
            bbox_min=self.bbox_min,
            bbox_max=self.bbox_max,
        )
        return proxy.select_face_nodes(face_ref)


def upgrade_tet4_to_tet10(mesh: TetMesh) -> Tet10Mesh:
    """Promote a linear Tet4 mesh to a quadratic 10-node Tet10 mesh by inserting unique edge midpoints."""
    n_corner = mesh.node_count
    n_elems = mesh.element_count
    elems4 = mesh.elements
    elems10 = np.empty((n_elems, 10), dtype=np.int32)
    elems10[:, :4] = elems4

    edge_map: dict[tuple[int, int], int] = {}
    mid_coords: list[np.ndarray] = []
    next_id = n_corner

    for local_offset, (ea, eb) in enumerate(_TET10_EDGES, start=4):
        u = elems4[:, ea]
        v = elems4[:, eb]
        lo = np.minimum(u, v)
        hi = np.maximum(u, v)
        col = np.empty(n_elems, dtype=np.int32)
        for i in range(n_elems):
            key = (int(lo[i]), int(hi[i]))
            nid = edge_map.get(key)
            if nid is None:
                nid = next_id
                edge_map[key] = nid
                mid_coords.append(0.5 * (mesh.nodes[key[0]] + mesh.nodes[key[1]]))
                next_id += 1
            col[i] = nid
        elems10[:, local_offset] = col

    if next_id > MAX_NODES:
        raise CaeError(
            "CAE_MESH_LIMIT_EXCEEDED",
            f"Tet10 node count ({next_id}) exceeds MAX_NODES ({MAX_NODES})",
        )

    nodes10 = (
        np.vstack([mesh.nodes, np.asarray(mid_coords, dtype=np.float64)])
        if mid_coords
        else mesh.nodes.copy()
    )
    return Tet10Mesh(
        nodes=nodes10,
        elements=elems10,
        bbox_min=mesh.bbox_min,
        bbox_max=mesh.bbox_max,
    )


def tet10_shape_and_dnat(
    l0: float, l1: float, l2: float, l3: float
) -> tuple[np.ndarray, np.ndarray]:
    """Evaluate 10 quadratic shape functions N(10,) and natural derivatives dN/d(l1,l2,l3) (10,3).

    Using l0 = 1 - l1 - l2 - l3 so dl0/dl_k = -1 for k in (1, 2, 3).
    """
    n = np.array(
        [
            l0 * (2.0 * l0 - 1.0),
            l1 * (2.0 * l1 - 1.0),
            l2 * (2.0 * l2 - 1.0),
            l3 * (2.0 * l3 - 1.0),
            4.0 * l0 * l1,
            4.0 * l1 * l2,
            4.0 * l2 * l0,
            4.0 * l0 * l3,
            4.0 * l1 * l3,
            4.0 * l2 * l3,
        ],
        dtype=np.float64,
    )
    # Derivatives wrt (l1, l2, l3), accounting for l0 = 1 - l1 - l2 - l3
    d0 = -(4.0 * l0 - 1.0)
    dn = np.array(
        [
            [d0, d0, d0],
            [4.0 * l1 - 1.0, 0.0, 0.0],
            [0.0, 4.0 * l2 - 1.0, 0.0],
            [0.0, 0.0, 4.0 * l3 - 1.0],
            [4.0 * (l0 - l1), -4.0 * l1, -4.0 * l1],
            [4.0 * l2, 4.0 * l1, 0.0],
            [-4.0 * l2, 4.0 * (l0 - l2), -4.0 * l2],
            [-4.0 * l3, -4.0 * l3, 4.0 * (l0 - l3)],
            [4.0 * l3, 0.0, 4.0 * l1],
            [0.0, 4.0 * l3, 4.0 * l2],
        ],
        dtype=np.float64,
    )
    return n, dn


def assemble_tet10_stiffness_and_mass(
    mesh: Tet10Mesh,
    *,
    youngs_modulus_mpa: float,
    poissons_ratio: float,
    density_kg_m3: float = 7850.0,
    include_mass: bool = False,
) -> tuple[sparse.csr_matrix, sparse.csr_matrix | None]:
    """Assemble global sparse 3D Tet10 stiffness matrix K (and optional consistent mass matrix M)."""
    n_nodes = mesh.node_count
    n_elems = mesh.element_count
    n_dof = 3 * n_nodes
    d_mat = _isotropic_d_matrix(youngs_modulus_mpa, poissons_ratio)
    # Convert kg/m^3 -> ton/mm^3 (1e-12) so K (N/mm) & M (ton = N*s^2/mm) yield rad^2/s^2
    rho = float(density_kg_m3) * 1e-12

    el_coords = mesh.nodes[mesh.elements]  # (M, 10, 3)
    ke_all = np.zeros((n_elems, 30, 30), dtype=np.float64)
    me_all = np.zeros((n_elems, 30, 30), dtype=np.float64) if include_mass else None

    for l0, l1, l2, l3 in _GAUSS_PTS:
        n_vec, dn_nat = tet10_shape_and_dnat(l0, l1, l2, l3)
        # Jacobian J_g = d(x,y,z)/d(l1,l2,l3): (M, 3, 3) where J[m, a, b] = sum_i dn_nat[i, a] * x[m, i, b]
        j_mat = np.einsum("ia,mib->mab", dn_nat, el_coords)  # (M, 3, 3)
        det_j = np.linalg.det(j_mat)
        if np.any(det_j <= 1e-12):
            raise CaeError("CAE_MESH_FAILED", "non-positive Jacobian in Tet10 Gauss integration")
        inv_j = np.linalg.inv(j_mat)  # (M, 3, 3)
        # Physical gradients dN/d(x,y,z): (M, 10, 3)
        dn_xyz = np.einsum("mab,ib->mia", inv_j, dn_nat)

        b_mats = np.zeros((n_elems, 6, 30), dtype=np.float64)
        for a in range(10):
            gx = dn_xyz[:, a, 0]
            gy = dn_xyz[:, a, 1]
            gz = dn_xyz[:, a, 2]
            c0 = 3 * a
            b_mats[:, 0, c0] = gx
            b_mats[:, 1, c0 + 1] = gy
            b_mats[:, 2, c0 + 2] = gz
            b_mats[:, 3, c0] = gy
            b_mats[:, 3, c0 + 1] = gx
            b_mats[:, 4, c0 + 1] = gz
            b_mats[:, 4, c0 + 2] = gy
            b_mats[:, 5, c0] = gz
            b_mats[:, 5, c0 + 2] = gx

        db = np.einsum("ij,mjk->mik", d_mat, b_mats)
        ke_all += np.einsum("m,mji,mjk->mik", _GAUSS_W * det_j, b_mats, db)

        if me_all is not None:
            nn = np.outer(n_vec, n_vec)  # (10, 10)
            w_m = rho * _GAUSS_W * det_j  # (M,)
            for c in range(3):
                me_all[:, c::3, c::3] += w_m[:, None, None] * nn[None, :, :]

    el_dofs = np.empty((n_elems, 30), dtype=np.int32)
    for a in range(10):
        el_dofs[:, 3 * a] = 3 * mesh.elements[:, a]
        el_dofs[:, 3 * a + 1] = 3 * mesh.elements[:, a] + 1
        el_dofs[:, 3 * a + 2] = 3 * mesh.elements[:, a] + 2

    rows = np.repeat(el_dofs, 30, axis=1).ravel()
    cols = np.tile(el_dofs, (1, 30)).ravel()
    k_global = sparse.coo_matrix((ke_all.ravel(), (rows, cols)), shape=(n_dof, n_dof)).tocsr()
    m_global = (
        sparse.coo_matrix((me_all.ravel(), (rows, cols)), shape=(n_dof, n_dof)).tocsr()
        if me_all is not None
        else None
    )
    return k_global, m_global


def solve_tet10_static(
    mesh: Tet10Mesh,
    *,
    youngs_modulus_mpa: float,
    poissons_ratio: float,
    yield_strength_mpa: float,
    restraints: list[dict[str, Any]],
    loads: list[dict[str, Any]],
) -> FeaResult:
    """Pure 2nd-order Tet10 3D linear static FEA solve (zero bending locking, no analytical override)."""
    if not restraints:
        raise CaeError("CAE_UNDERCONSTRAINED", "at least one restraint is required")
    if not loads:
        raise CaeError("CAE_INVALID_PARAM", "at least one load is required")

    fixed_lists = [mesh.select_face_nodes(r.get("face_ref", r)) for r in restraints]
    fixed_nodes = (
        np.unique(np.concatenate(fixed_lists)) if fixed_lists else np.array([], dtype=np.int32)
    )
    _check_restraints_not_underconstrained(mesh.nodes, fixed_nodes)

    k_global, _ = assemble_tet10_stiffness_and_mass(
        mesh,
        youngs_modulus_mpa=youngs_modulus_mpa,
        poissons_ratio=poissons_ratio,
        include_mass=False,
    )

    n_nodes = mesh.node_count
    n_dof = 3 * n_nodes
    f_global = np.zeros(n_dof, dtype=np.float64)
    for ld in loads:
        ltype = str(ld.get("type", "FORCE")).upper()
        lnodes = mesh.select_face_nodes(ld.get("face_ref", ld))
        if lnodes.size == 0:
            raise CaeError("CAE_INVALID_PARAM", "load face_ref matched 0 nodes")
        if ltype == "FORCE":
            vec = ld.get("vector_n", [0.0, 0.0, 0.0])
            fx, fy, fz = float(vec[0]), float(vec[1]), float(vec[2])
            f_global[3 * lnodes] += fx / lnodes.size
            f_global[3 * lnodes + 1] += fy / lnodes.size
            f_global[3 * lnodes + 2] += fz / lnodes.size
        elif ltype == "PRESSURE":
            mag = float(ld.get("magnitude_mpa", 0.0))
            lpts = mesh.nodes[lnodes]
            spans = sorted(
                [float(np.ptp(lpts[:, 0])), float(np.ptp(lpts[:, 1])), float(np.ptp(lpts[:, 2]))]
            )
            area = max(1e-6, spans[1] * spans[2])
            total_f = mag * area
            normal = ld.get("direction", [0.0, 0.0, -1.0])
            nx, ny, nz = float(normal[0]), float(normal[1]), float(normal[2])
            nlen = math.hypot(nx, ny, nz) or 1.0
            f_global[3 * lnodes] += (total_f * (nx / nlen)) / lnodes.size
            f_global[3 * lnodes + 1] += (total_f * (ny / nlen)) / lnodes.size
            f_global[3 * lnodes + 2] += (total_f * (nz / nlen)) / lnodes.size
        else:
            raise CaeError("CAE_INVALID_PARAM", f"unsupported load type: {ltype}")

    fixed_dofs = np.concatenate([3 * fixed_nodes, 3 * fixed_nodes + 1, 3 * fixed_nodes + 2])
    free_mask = np.ones(n_dof, dtype=bool)
    free_mask[fixed_dofs] = False
    free_dofs = np.nonzero(free_mask)[0]

    u_global = np.zeros(n_dof, dtype=np.float64)
    u_f = spla.spsolve(k_global[free_dofs, :][:, free_dofs], f_global[free_dofs])
    if not np.all(np.isfinite(u_f)):
        raise CaeError("CAE_UNDERCONSTRAINED", "singular Tet10 stiffness matrix")
    u_global[free_dofs] = u_f
    u_nodes = u_global.reshape((n_nodes, 3))

    # Centroid stress evaluation at (0.25, 0.25, 0.25, 0.25)
    d_mat = _isotropic_d_matrix(youngs_modulus_mpa, poissons_ratio)
    el_coords = mesh.nodes[mesh.elements]
    _, dn_nat = tet10_shape_and_dnat(0.25, 0.25, 0.25, 0.25)
    j_mat = np.einsum("ia,mib->mab", dn_nat, el_coords)
    det_j = np.linalg.det(j_mat)
    inv_j = np.linalg.inv(j_mat)
    dn_xyz = np.einsum("mab,ib->mia", inv_j, dn_nat)

    n_elems = mesh.element_count
    b_mats = np.zeros((n_elems, 6, 30), dtype=np.float64)
    el_dofs = np.empty((n_elems, 30), dtype=np.int32)
    for a in range(10):
        gx, gy, gz = dn_xyz[:, a, 0], dn_xyz[:, a, 1], dn_xyz[:, a, 2]
        c0 = 3 * a
        b_mats[:, 0, c0] = gx
        b_mats[:, 1, c0 + 1] = gy
        b_mats[:, 2, c0 + 2] = gz
        b_mats[:, 3, c0] = gy
        b_mats[:, 3, c0 + 1] = gx
        b_mats[:, 4, c0 + 1] = gz
        b_mats[:, 4, c0 + 2] = gy
        b_mats[:, 5, c0] = gz
        b_mats[:, 5, c0 + 2] = gx
        el_dofs[:, c0] = 3 * mesh.elements[:, a]
        el_dofs[:, c0 + 1] = 3 * mesh.elements[:, a] + 1
        el_dofs[:, c0 + 2] = 3 * mesh.elements[:, a] + 2

    sigma_el = np.einsum("ij,mjk,mk->mi", d_mat, b_mats, u_global[el_dofs])
    sxx, syy, szz = sigma_el[:, 0], sigma_el[:, 1], sigma_el[:, 2]
    txy, tyz, txz = sigma_el[:, 3], sigma_el[:, 4], sigma_el[:, 5]
    vm_el = np.sqrt(
        0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
        + 3.0 * (txy**2 + tyz**2 + txz**2)
    )

    vols = det_j / 6.0
    nodal_vm = np.zeros(n_nodes, dtype=np.float64)
    nodal_w = np.zeros(n_nodes, dtype=np.float64)
    for a in range(10):
        np.add.at(nodal_vm, mesh.elements[:, a], vm_el * vols)
        np.add.at(nodal_w, mesh.elements[:, a], vols)
    nodal_vm = np.divide(nodal_vm, np.maximum(nodal_w, 1e-12))

    disp_mag = np.linalg.norm(u_nodes, axis=1)
    max_disp = float(disp_mag.max()) if disp_mag.size else 0.0
    max_vm = float(nodal_vm.max()) if nodal_vm.size else 0.0
    sy = float(yield_strength_mpa)
    nodal_sf = np.where(nodal_vm > 1e-6, np.minimum(999.0, sy / np.maximum(nodal_vm, 1e-9)), 999.0)
    min_sf = float(sy / max_vm) if max_vm > 1e-9 else 999.0

    return FeaResult(
        node_count=n_nodes,
        element_count=n_elems,
        displacements_mm=u_nodes,
        nodal_disp_mag_mm=disp_mag,
        nodal_von_mises_mpa=nodal_vm,
        nodal_safety_factor=nodal_sf,
        max_disp_mm=max_disp,
        max_von_mises_mpa=max_vm,
        min_safety_factor=min_sf,
    )
