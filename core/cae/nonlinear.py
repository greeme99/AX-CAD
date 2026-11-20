"""CAE E9: Nonlinear Elastoplastic 3D Tet4 Solver (J2 von Mises Isotropic Hardening).

Uses incremental load stepping + full Newton-Raphson equilibrium iterations with
J2 radial-return mapping and consistent algorithmic elastoplastic tangent modulus.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import sparse
from scipy.sparse import linalg as spla

from core.cae.fea import _check_restraints_not_underconstrained, _isotropic_d_matrix
from core.cae.mesh import CaeError, TetMesh


@dataclass
class NonlinearPlasticResult:
    node_count: int
    element_count: int
    displacements_mm: np.ndarray  # (N, 3)
    nodal_von_mises_mpa: np.ndarray  # (N,)
    nodal_eq_plastic_strain: np.ndarray  # (N,)
    max_disp_mm: float
    max_von_mises_mpa: float
    max_plastic_strain: float
    converged: bool
    total_nr_iterations: int
    final_residual_norm: float
    load_history: list[dict[str, float]]

    def to_dict(self, mesh: TetMesh) -> dict[str, Any]:
        return {
            "analysis_type": "NONLINEAR_STATIC",
            "node_count": self.node_count,
            "element_count": self.element_count,
            "max_disp_mm": round(self.max_disp_mm, 6),
            "max_von_mises_mpa": round(self.max_von_mises_mpa, 4),
            "max_plastic_strain": round(self.max_plastic_strain, 6),
            "converged": self.converged,
            "total_nr_iterations": self.total_nr_iterations,
            "final_residual_norm": round(self.final_residual_norm, 8),
            "load_history": self.load_history,
            "nodes": np.round(mesh.nodes, 5).tolist(),
            "displacements_mm": np.round(self.displacements_mm, 6).tolist(),
            "von_mises_mpa": np.round(self.nodal_von_mises_mpa, 4).tolist(),
            "eq_plastic_strain": np.round(self.nodal_eq_plastic_strain, 6).tolist(),
        }


def _j2_return_mapping_batch(
    d_strain: np.ndarray,
    eps_p_old: np.ndarray,
    alpha_old: np.ndarray,
    *,
    e_mpa: float,
    nu: float,
    sigma_y: float,
    h_iso: float,
    d_el: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Vectorized J2 von Mises radial-return mapping for M Tet4 elements.

    Voigt convention: [eps_xx, eps_yy, eps_zz, gamma_xy, gamma_yz, gamma_xz].
    Returns (stress (M,6), eps_p_new (M,6), alpha_new (M,), d_tan (M,6,6)).
    """
    m = d_strain.shape[0]
    g_mod = e_mpa / (2.0 * (1.0 + nu))
    k_bulk = e_mpa / (3.0 * (1.0 - 2.0 * nu))

    # Trial elastic strain = total strain - old plastic strain
    eps_e_trial = d_strain - eps_p_old
    sig_trial = np.einsum("ij,mj->mi", d_el, eps_e_trial)  # (M, 6)

    # Mean stress & deviatoric stress (Voigt)
    p_mean = (sig_trial[:, 0] + sig_trial[:, 1] + sig_trial[:, 2]) / 3.0
    s_dev = sig_trial.copy()
    s_dev[:, 0] -= p_mean
    s_dev[:, 1] -= p_mean
    s_dev[:, 2] -= p_mean

    # Norm of deviatoric tensor ||s|| = sqrt(s11^2+s22^2+s33^2 + 2*(s12^2+s23^2+s13^2))
    s_norm = np.sqrt(
        s_dev[:, 0] ** 2
        + s_dev[:, 1] ** 2
        + s_dev[:, 2] ** 2
        + 2.0 * (s_dev[:, 3] ** 2 + s_dev[:, 4] ** 2 + s_dev[:, 5] ** 2)
    )
    q_trial = math.sqrt(1.5) * s_norm  # von Mises equivalent trial stress
    yield_cur = sigma_y + h_iso * alpha_old
    f_trial = q_trial - yield_cur

    sig_new = sig_trial.copy()
    eps_p_new = eps_p_old.copy()
    alpha_new = alpha_old.copy()
    d_tan = np.broadcast_to(d_el, (m, 6, 6)).copy()

    plastic_idx = np.nonzero(f_trial > 1e-9)[0]
    if plastic_idx.size == 0:
        return sig_new, eps_p_new, alpha_new, d_tan

    # Identity & deviatoric projector in Voigt notation
    i_vec = np.array([1.0, 1.0, 1.0, 0.0, 0.0, 0.0], dtype=np.float64)
    p_dev = np.diag([1.0, 1.0, 1.0, 0.5, 0.5, 0.5]) - np.outer(i_vec, i_vec) / 3.0

    for idx in plastic_idx:
        sn = float(s_norm[idx])
        if sn <= 1e-12:
            continue
        d_gamma = float(f_trial[idx]) / (3.0 * g_mod + h_iso)
        n_voigt = s_dev[idx] / sn  # unit deviatoric tensor in stress Voigt form
        # Stress return: s_new = (1 - 2*G*sqrt(3/2)*d_gamma / sn) * s_dev
        factor = 1.0 - (3.0 * g_mod * d_gamma) / float(q_trial[idx])
        s_new = factor * s_dev[idx]
        sig_new[idx] = s_new + p_mean[idx] * i_vec

        # Plastic strain increment in engineering Voigt (shear components multiplied by 2)
        d_ep = math.sqrt(1.5) * d_gamma * n_voigt
        d_ep[3:] *= 2.0
        eps_p_new[idx] = eps_p_old[idx] + d_ep
        alpha_new[idx] = alpha_old[idx] + d_gamma

        # Consistent algorithmic tangent modulus D_ep
        theta = 1.0 - (3.0 * g_mod * d_gamma) / float(q_trial[idx])
        theta_bar = 1.0 / (1.0 + h_iso / (3.0 * g_mod)) - (1.0 - theta)
        n_bar = n_voigt.copy()
        d_tan[idx] = (
            k_bulk * np.outer(i_vec, i_vec)
            + 2.0 * g_mod * theta * p_dev
            - 2.0 * g_mod * theta_bar * np.outer(n_bar, n_bar)
        )

    return sig_new, eps_p_new, alpha_new, d_tan


def solve_nonlinear_elastoplastic(
    mesh: TetMesh,
    *,
    youngs_modulus_mpa: float = 210_000.0,
    poissons_ratio: float = 0.3,
    yield_strength_mpa: float = 250.0,
    tangent_modulus_mpa: float = 10_000.0,
    restraints: list[dict[str, Any]],
    loads: list[dict[str, Any]],
    load_steps: int = 8,
    max_nr_iter: int = 20,
    tol: float = 1e-5,
) -> NonlinearPlasticResult:
    """Solve 3D Tet4 elastoplastic static equilibrium using incremental Newton-Raphson."""
    if not (0.0 <= tangent_modulus_mpa < youngs_modulus_mpa):
        raise CaeError(
            "CAE_INVALID_PARAM",
            "tangent_modulus_mpa must satisfy 0 <= E_t < E",
        )
    if not restraints:
        raise CaeError("CAE_UNDERCONSTRAINED", "at least one restraint is required")
    if not loads:
        raise CaeError("CAE_INVALID_PARAM", "at least one load is required")

    n_nodes = mesh.node_count
    n_elems = mesh.element_count
    n_dof = 3 * n_nodes
    d_el = _isotropic_d_matrix(youngs_modulus_mpa, poissons_ratio)
    h_iso = (youngs_modulus_mpa * tangent_modulus_mpa) / (youngs_modulus_mpa - tangent_modulus_mpa)

    # Element B matrices & volumes
    el_nodes = mesh.nodes[mesh.elements]
    j_mat = el_nodes[:, 1:, :] - el_nodes[:, 0:1, :]
    det_j = np.linalg.det(j_mat)
    vols = det_j / 6.0
    if np.any(vols <= 1e-12):
        raise CaeError("CAE_MESH_FAILED", "degenerate Tet4 element in nonlinear solver")
    inv_j = np.linalg.inv(j_mat)
    grad_n123 = np.transpose(inv_j, (0, 2, 1))
    grad_n0 = -grad_n123.sum(axis=1, keepdims=True)
    grads = np.concatenate([grad_n0, grad_n123], axis=1)

    b_mats = np.zeros((n_elems, 6, 12), dtype=np.float64)
    for a in range(4):
        gx, gy, gz = grads[:, a, 0], grads[:, a, 1], grads[:, a, 2]
        c = 3 * a
        b_mats[:, 0, c] = gx
        b_mats[:, 1, c + 1] = gy
        b_mats[:, 2, c + 2] = gz
        b_mats[:, 3, c] = gy
        b_mats[:, 3, c + 1] = gx
        b_mats[:, 4, c + 1] = gz
        b_mats[:, 4, c + 2] = gy
        b_mats[:, 5, c] = gz
        b_mats[:, 5, c + 2] = gx

    dof_idx = np.empty((n_elems, 12), dtype=np.int32)
    for a in range(4):
        dof_idx[:, 3 * a] = 3 * mesh.elements[:, a]
        dof_idx[:, 3 * a + 1] = 3 * mesh.elements[:, a] + 1
        dof_idx[:, 3 * a + 2] = 3 * mesh.elements[:, a] + 2
    rows = np.repeat(dof_idx, 12, axis=1).ravel()
    cols = np.tile(dof_idx, (1, 12)).ravel()

    # Restraints & external load vector
    fixed_nodes_list: list[int] = []
    for r in restraints:
        nids = mesh.select_face_nodes(r.get("face_ref", r))
        fixed_nodes_list.extend(int(i) for i in nids)
    fixed_nodes = np.unique(np.array(fixed_nodes_list, dtype=np.int32))
    _check_restraints_not_underconstrained(mesh.nodes, fixed_nodes)

    fixed_dofs = np.concatenate([3 * fixed_nodes, 3 * fixed_nodes + 1, 3 * fixed_nodes + 2])
    free_mask = np.ones(n_dof, dtype=bool)
    free_mask[fixed_dofs] = False
    free_dofs = np.nonzero(free_mask)[0]

    f_total = np.zeros(n_dof, dtype=np.float64)
    for ld in loads:
        nids = mesh.select_face_nodes(ld.get("face_ref", ld))
        if nids.size == 0:
            raise CaeError("CAE_INVALID_PARAM", "nonlinear load matched 0 mesh nodes")
        fv = ld.get("force_n", [0.0, 0.0, 0.0])
        per = np.array([float(fv[0]), float(fv[1]), float(fv[2])], dtype=np.float64) / nids.size
        for nid in nids:
            i = int(nid)
            f_total[3 * i : 3 * i + 3] += per

    u = np.zeros(n_dof, dtype=np.float64)
    eps_p = np.zeros((n_elems, 6), dtype=np.float64)
    alpha = np.zeros(n_elems, dtype=np.float64)
    sig = np.zeros((n_elems, 6), dtype=np.float64)

    total_nr = 0
    converged_all = True
    final_rel_res = 0.0
    load_history: list[dict[str, float]] = []

    for step in range(1, load_steps + 1):
        lam = step / float(load_steps)
        f_step = lam * f_total
        f_norm = max(float(np.linalg.norm(f_step[free_dofs])), 1e-12)
        eps_p_commit = eps_p.copy()
        alpha_commit = alpha.copy()
        step_converged = False

        for _it in range(1, max_nr_iter + 1):
            total_nr += 1
            u_el = u[dof_idx]
            eps_tot = np.einsum("mij,mj->mi", b_mats, u_el)
            sig, eps_p_trial, alpha_trial, d_tan = _j2_return_mapping_batch(
                eps_tot,
                eps_p_commit,
                alpha_commit,
                e_mpa=youngs_modulus_mpa,
                nu=poissons_ratio,
                sigma_y=yield_strength_mpa,
                h_iso=h_iso,
                d_el=d_el,
            )
            # Internal force vector f_int = sum_e V_e * B_e^T * sig_e
            f_el = np.einsum("m,mji,mj->mi", vols, b_mats, sig)  # (M, 12)
            f_int = np.zeros(n_dof, dtype=np.float64)
            np.add.at(f_int, dof_idx, f_el)

            res = f_step - f_int
            rel_res = float(np.linalg.norm(res[free_dofs])) / f_norm
            final_rel_res = rel_res
            if rel_res <= tol:
                eps_p = eps_p_trial
                alpha = alpha_trial
                step_converged = True
                break

            # Assemble tangent stiffness matrix
            db = np.einsum("mij,mjk->mik", d_tan, b_mats)
            ke = np.einsum("m,mji,mjk->mik", vols, b_mats, db)
            k_tan = sparse.coo_matrix((ke.ravel(), (rows, cols)), shape=(n_dof, n_dof)).tocsc()
            du = spla.spsolve(k_tan[free_dofs, :][:, free_dofs], res[free_dofs])
            u[free_dofs] += du

        if not step_converged:
            converged_all = False

        disp_step = u.reshape(n_nodes, 3)
        load_history.append(
            {
                "load_factor": round(lam, 4),
                "max_disp_mm": round(float(np.max(np.linalg.norm(disp_step, axis=1))), 6),
                "max_plastic_strain": round(float(np.max(alpha)), 6),
            }
        )

    # Element -> nodal averaging for von Mises & equivalent plastic strain
    sxx, syy, szz, sxy, syz, sxz = (sig[:, k] for k in range(6))
    el_vm = np.sqrt(
        0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
        + 3.0 * (sxy**2 + syz**2 + sxz**2)
    )
    nodal_vm = np.zeros(n_nodes, dtype=np.float64)
    nodal_ep = np.zeros(n_nodes, dtype=np.float64)
    counts = np.zeros(n_nodes, dtype=np.float64)
    for a in range(4):
        np.add.at(nodal_vm, mesh.elements[:, a], el_vm)
        np.add.at(nodal_ep, mesh.elements[:, a], alpha)
        np.add.at(counts, mesh.elements[:, a], 1.0)
    counts = np.maximum(counts, 1.0)
    nodal_vm /= counts
    nodal_ep /= counts

    disp = u.reshape(n_nodes, 3)
    return NonlinearPlasticResult(
        node_count=n_nodes,
        element_count=n_elems,
        displacements_mm=disp,
        nodal_von_mises_mpa=nodal_vm,
        nodal_eq_plastic_strain=nodal_ep,
        max_disp_mm=float(np.max(np.linalg.norm(disp, axis=1))),
        max_von_mises_mpa=float(np.max(nodal_vm)),
        max_plastic_strain=float(np.max(nodal_ep)),
        converged=converged_all,
        total_nr_iterations=total_nr,
        final_residual_norm=final_rel_res,
        load_history=load_history,
    )
