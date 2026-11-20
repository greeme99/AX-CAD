"""Linear Buckling FEA Solver: Geometric Stiffness K_G(sigma_0) & Critical Load Factors (CAE E6)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import sparse
from scipy.sparse import linalg as spla

from core.cae.fea import _check_restraints_not_underconstrained, _isotropic_d_matrix
from core.cae.mesh import CaeError, TetMesh
from core.cae.tet10 import (
    _GAUSS_PTS,
    _GAUSS_W,
    Tet10Mesh,
    assemble_tet10_stiffness_and_mass,
    tet10_shape_and_dnat,
    upgrade_tet4_to_tet10,
)


@dataclass
class BucklingResult:
    node_count: int
    element_count: int
    load_factors: list[float]
    buckling_modes: list[np.ndarray]

    def to_dict(self, mesh: Tet10Mesh) -> dict[str, Any]:
        return {
            "analysis_type": "BUCKLING",
            "node_count": self.node_count,
            "element_count": self.element_count,
            "buckling_factors": [round(lf, 4) for lf in self.load_factors],
            "nodes": np.round(mesh.nodes, 5).tolist(),
            "buckling_modes": [np.round(m, 5).tolist() for m in self.buckling_modes],
        }


def _assemble_geometric_stiffness_tet10(
    mesh: Tet10Mesh,
    u_global: np.ndarray,
    d_mat: np.ndarray,
) -> sparse.csr_matrix:
    """Assemble 3D Tet10 geometric stiffness matrix K_G(sigma_0) via 4-point Gauss quadrature."""
    n_nodes = mesh.node_count
    n_elems = mesh.element_count
    n_dof = 3 * n_nodes

    el_coords = mesh.nodes[mesh.elements]  # (M, 10, 3)
    el_dofs = np.empty((n_elems, 30), dtype=np.int32)
    for a in range(10):
        el_dofs[:, 3 * a] = 3 * mesh.elements[:, a]
        el_dofs[:, 3 * a + 1] = 3 * mesh.elements[:, a] + 1
        el_dofs[:, 3 * a + 2] = 3 * mesh.elements[:, a] + 2
    u_el = u_global[el_dofs]  # (M, 30)

    kg_all = np.zeros((n_elems, 30, 30), dtype=np.float64)

    for l0, l1, l2, l3 in _GAUSS_PTS:
        _, dn_nat = tet10_shape_and_dnat(l0, l1, l2, l3)
        j_mat = np.einsum("ia,mib->mab", dn_nat, el_coords)
        det_j = np.linalg.det(j_mat)
        inv_j = np.linalg.inv(j_mat)
        dn_xyz = np.einsum("mab,ib->mia", inv_j, dn_nat)  # (M, 10, 3)

        # Compute Gauss-point stress tensor sigma_0 = D * B * u_e
        b_mats = np.zeros((n_elems, 6, 30), dtype=np.float64)
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

        sig = np.einsum("ij,mjk,mk->mi", d_mat, b_mats, u_el)  # (M, 6)
        # 3x3 symmetric Cauchy stress tensor S per element
        s3 = np.zeros((n_elems, 3, 3), dtype=np.float64)
        s3[:, 0, 0] = sig[:, 0]
        s3[:, 1, 1] = sig[:, 1]
        s3[:, 2, 2] = sig[:, 2]
        s3[:, 0, 1] = s3[:, 1, 0] = sig[:, 3]
        s3[:, 1, 2] = s3[:, 2, 1] = sig[:, 4]
        s3[:, 0, 2] = s3[:, 2, 0] = sig[:, 5]

        # Scalar geometric block: g_ab = w * det_j * (grad_Na^T * S * grad_Nb) of shape (M, 10, 10)
        g_ab = np.einsum("m,mia,mab,mjb->mij", _GAUSS_W * det_j, dn_xyz, s3, dn_xyz)
        for c in range(3):
            kg_all[:, c::3, c::3] += g_ab

    rows = np.repeat(el_dofs, 30, axis=1).ravel()
    cols = np.tile(el_dofs, (1, 30)).ravel()
    return sparse.coo_matrix((kg_all.ravel(), (rows, cols)), shape=(n_dof, n_dof)).tocsr()


def solve_buckling(
    mesh: TetMesh | Tet10Mesh,
    *,
    youngs_modulus_mpa: float,
    poissons_ratio: float,
    restraints: list[dict[str, Any]],
    loads: list[dict[str, Any]],
    mode_count: int = 4,
) -> tuple[BucklingResult, Tet10Mesh]:
    """Solve linear buckling eigenvalue problem (K + lambda * K_G) phi = 0 on Tet10."""
    if not restraints:
        raise CaeError("CAE_UNDERCONSTRAINED", "buckling analysis requires restraints")
    if not loads:
        raise CaeError("CAE_INVALID_PARAM", "buckling analysis requires reference loads")
    if not (1 <= mode_count <= 20):
        raise CaeError("CAE_INVALID_PARAM", "mode_count must be in 1..20")

    t10 = mesh if isinstance(mesh, Tet10Mesh) else upgrade_tet4_to_tet10(mesh)
    fixed_lists = [t10.select_face_nodes(r.get("face_ref", r)) for r in restraints]
    fixed_nodes = (
        np.unique(np.concatenate(fixed_lists)) if fixed_lists else np.array([], dtype=np.int32)
    )
    _check_restraints_not_underconstrained(t10.nodes, fixed_nodes)

    k_global, _ = assemble_tet10_stiffness_and_mass(
        t10,
        youngs_modulus_mpa=youngs_modulus_mpa,
        poissons_ratio=poissons_ratio,
        include_mass=False,
    )

    n_nodes = t10.node_count
    n_dof = 3 * n_nodes
    f_global = np.zeros(n_dof, dtype=np.float64)
    for ld in loads:
        lnodes = t10.select_face_nodes(ld.get("face_ref", ld))
        if lnodes.size == 0:
            raise CaeError("CAE_INVALID_PARAM", "buckling load face_ref matched 0 nodes")
        vec = ld.get("vector_n", [0.0, 0.0, 0.0])
        f_global[3 * lnodes] += float(vec[0]) / lnodes.size
        f_global[3 * lnodes + 1] += float(vec[1]) / lnodes.size
        f_global[3 * lnodes + 2] += float(vec[2]) / lnodes.size

    fixed_dofs = np.concatenate([3 * fixed_nodes, 3 * fixed_nodes + 1, 3 * fixed_nodes + 2])
    free_mask = np.ones(n_dof, dtype=bool)
    free_mask[fixed_dofs] = False
    free_dofs = np.nonzero(free_mask)[0]

    # Step 1: Pre-buckling linear static solve K u_0 = f
    k_ff = k_global[free_dofs, :][:, free_dofs]
    u_0 = np.zeros(n_dof, dtype=np.float64)
    u_0[free_dofs] = spla.spsolve(k_ff, f_global[free_dofs])

    # Step 2: Geometric stiffness K_G(sigma_0)
    d_mat = _isotropic_d_matrix(youngs_modulus_mpa, poissons_ratio)
    kg_global = _assemble_geometric_stiffness_tet10(t10, u_0, d_mat)
    kg_ff = kg_global[free_dofs, :][:, free_dofs]

    # Step 3: Solve (-K_G) phi = mu * K phi (where lambda_cr = 1 / mu; largest positive mu = smallest positive lambda_cr)
    k_req = min(mode_count, max(1, free_dofs.size - 2))
    mu_vals, vecs = spla.eigsh(-kg_ff, k=k_req, M=k_ff, which="LA")
    pos_idx = [i for i in range(len(mu_vals)) if float(mu_vals[i]) > 1e-12]
    pos_idx.sort(key=lambda i: -float(mu_vals[i]))  # largest mu -> smallest positive lambda_cr

    if not pos_idx:
        raise CaeError(
            "CAE_NO_BUCKLING_MODE", "no positive buckling load factor found (load may be tensile)"
        )

    factors: list[float] = []
    modes: list[np.ndarray] = []
    for i in pos_idx:
        factors.append(1.0 / float(mu_vals[i]))
        u_full = np.zeros(n_dof, dtype=np.float64)
        u_full[free_dofs] = vecs[:, i]
        u_3 = u_full.reshape((n_nodes, 3))
        max_mag = float(np.linalg.norm(u_3, axis=1).max())
        if max_mag > 1e-12:
            u_3 = u_3 / max_mag
        modes.append(u_3)

    return (
        BucklingResult(
            node_count=n_nodes,
            element_count=t10.element_count,
            load_factors=factors,
            buckling_modes=modes,
        ),
        t10,
    )
