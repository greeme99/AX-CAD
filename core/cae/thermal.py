"""Steady-State 3D Thermal Conduction & Coupled Thermal-Structural Stress Solver (CAE E7)."""

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
class ThermalResult:
    node_count: int
    element_count: int
    temperatures_c: np.ndarray  # (N,)
    min_temp_c: float
    max_temp_c: float
    displacements_mm: np.ndarray | None = None
    nodal_von_mises_mpa: np.ndarray | None = None
    max_disp_mm: float | None = None
    max_von_mises_mpa: float | None = None
    min_safety_factor: float | None = None

    def to_dict(self, mesh: TetMesh) -> dict[str, Any]:
        out: dict[str, Any] = {
            "analysis_type": "THERMAL_STRUCTURAL"
            if self.nodal_von_mises_mpa is not None
            else "THERMAL",
            "node_count": self.node_count,
            "element_count": self.element_count,
            "min_temp_c": round(self.min_temp_c, 4),
            "max_temp_c": round(self.max_temp_c, 4),
            "nodes": np.round(mesh.nodes, 5).tolist(),
            "temperatures_c": np.round(self.temperatures_c, 4).tolist(),
        }
        if self.displacements_mm is not None and self.nodal_von_mises_mpa is not None:
            out["max_disp_mm"] = round(self.max_disp_mm or 0.0, 6)
            out["max_von_mises_mpa"] = round(self.max_von_mises_mpa or 0.0, 4)
            out["min_safety_factor"] = round(self.min_safety_factor or 999.0, 4)
            out["displacements_mm"] = np.round(self.displacements_mm, 6).tolist()
            out["von_mises_mpa"] = np.round(self.nodal_von_mises_mpa, 4).tolist()
        return out


def solve_thermal_and_stress(
    mesh: TetMesh,
    *,
    thermal_conductivity_w_mk: float = 50.0,
    thermal_bcs: list[dict[str, Any]],
    ambient_temp_c: float = 20.0,
    coupled_structural: bool = False,
    youngs_modulus_mpa: float = 210_000.0,
    poissons_ratio: float = 0.3,
    yield_strength_mpa: float = 250.0,
    thermal_expansion_1_k: float = 12e-6,
    restraints: list[dict[str, Any]] | None = None,
) -> ThermalResult:
    """Solve 3D steady-state heat conduction K_T T = q_T, optionally coupled to 3D thermal-structural stress."""
    if thermal_conductivity_w_mk <= 0 or not math.isfinite(thermal_conductivity_w_mk):
        raise CaeError("CAE_INVALID_PARAM", "thermal_conductivity_w_mk must be > 0")
    if not thermal_bcs:
        raise CaeError("CAE_INVALID_PARAM", "at least one thermal boundary condition is required")

    n_nodes = mesh.node_count
    n_elems = mesh.element_count
    k_mm = float(thermal_conductivity_w_mk) * 1e-3  # W/(m*K) -> W/(mm*K)

    # 1. Assemble 3D Tet4 scalar thermal conductivity matrix K_T
    el_nodes = mesh.nodes[mesh.elements]  # (M, 4, 3)
    j_mat = el_nodes[:, 1:, :] - el_nodes[:, 0:1, :]
    det_j = np.linalg.det(j_mat)
    vols = det_j / 6.0
    if np.any(vols <= 1e-12):
        raise CaeError("CAE_MESH_FAILED", "non-positive Tet4 volume in thermal solver")
    inv_j = np.linalg.inv(j_mat)
    grad_n123 = np.transpose(inv_j, (0, 2, 1))
    grad_n0 = -grad_n123.sum(axis=1, keepdims=True)
    grads = np.concatenate([grad_n0, grad_n123], axis=1)  # (M, 4, 3)

    kt_el = np.einsum("m,mia,mja->mij", k_mm * vols, grads, grads)  # (M, 4, 4)
    rows = np.repeat(mesh.elements, 4, axis=1).ravel()
    cols = np.tile(mesh.elements, (1, 4)).ravel()
    k_t = sparse.coo_matrix((kt_el.ravel(), (rows, cols)), shape=(n_nodes, n_nodes)).tolil()
    q_t = np.zeros(n_nodes, dtype=np.float64)

    fixed_t_map: dict[int, float] = {}
    for bc in thermal_bcs:
        btype = str(bc.get("type", "FIXED_TEMP")).upper()
        bnodes = mesh.select_face_nodes(bc.get("face_ref", bc))
        if bnodes.size == 0:
            raise CaeError("CAE_INVALID_PARAM", f"thermal BC ({btype}) matched 0 mesh nodes")
        if btype == "FIXED_TEMP":
            t_val = float(bc.get("temperature_c", ambient_temp_c))
            for nid in bnodes:
                fixed_t_map[int(nid)] = t_val
        elif btype == "HEAT_FLUX":
            # q_flux in W/m^2 -> W/mm^2 (* 1e-6)
            flux = float(bc.get("flux_w_m2", 0.0)) * 1e-6
            bpts = mesh.nodes[bnodes]
            spans = sorted(
                [float(np.ptp(bpts[:, 0])), float(np.ptp(bpts[:, 1])), float(np.ptp(bpts[:, 2]))]
            )
            area = max(1e-6, spans[1] * spans[2])
            q_t[bnodes] += (flux * area) / bnodes.size
        elif btype == "CONVECTION":
            # h in W/(m^2*K) -> W/(mm^2*K) (* 1e-6)
            h_conv = float(bc.get("h_w_m2k", 10.0)) * 1e-6
            t_inf = float(bc.get("ambient_temp_c", ambient_temp_c))
            bpts = mesh.nodes[bnodes]
            spans = sorted(
                [float(np.ptp(bpts[:, 0])), float(np.ptp(bpts[:, 1])), float(np.ptp(bpts[:, 2]))]
            )
            area = max(1e-6, spans[1] * spans[2])
            h_nodal = (h_conv * area) / bnodes.size
            for nid in bnodes:
                idx = int(nid)
                k_t[idx, idx] += h_nodal
                q_t[idx] += h_nodal * t_inf
        else:
            raise CaeError("CAE_INVALID_PARAM", f"unsupported thermal BC type: {btype}")

    k_t_csr = k_t.tocsr()
    temps = np.full(n_nodes, float(ambient_temp_c), dtype=np.float64)
    if fixed_t_map:
        fixed_ids = np.array(sorted(fixed_t_map.keys()), dtype=np.int32)
        for nid in fixed_ids:
            temps[nid] = fixed_t_map[int(nid)]
        free_mask = np.ones(n_nodes, dtype=bool)
        free_mask[fixed_ids] = False
        free_ids = np.nonzero(free_mask)[0]
        if free_ids.size > 0:
            rhs = q_t[free_ids] - k_t_csr[free_ids, :][:, fixed_ids] @ temps[fixed_ids]
            temps[free_ids] = spla.spsolve(k_t_csr[free_ids, :][:, free_ids], rhs)
    else:
        temps = spla.spsolve(k_t_csr, q_t)

    if not np.all(np.isfinite(temps)):
        raise CaeError(
            "CAE_UNDERCONSTRAINED",
            "singular thermal conductivity system (add FIXED_TEMP or CONVECTION)",
        )

    result = ThermalResult(
        node_count=n_nodes,
        element_count=n_elems,
        temperatures_c=temps,
        min_temp_c=float(temps.min()),
        max_temp_c=float(temps.max()),
    )

    if not coupled_structural:
        return result

    # 2. Coupled Thermal-Structural Stress: f_th = sum_e V_e * B^T * D * eps_th
    if not restraints:
        raise CaeError(
            "CAE_UNDERCONSTRAINED",
            "coupled thermal-structural analysis requires mechanical restraints",
        )
    fixed_lists = [mesh.select_face_nodes(r.get("face_ref", r)) for r in restraints]
    fixed_mech = (
        np.unique(np.concatenate(fixed_lists)) if fixed_lists else np.array([], dtype=np.int32)
    )
    _check_restraints_not_underconstrained(mesh.nodes, fixed_mech)

    d_mat = _isotropic_d_matrix(youngs_modulus_mpa, poissons_ratio)
    n_dof = 3 * n_nodes
    b_mats = np.zeros((n_elems, 6, 12), dtype=np.float64)
    el_dofs = np.empty((n_elems, 12), dtype=np.int32)
    for a in range(4):
        gx, gy, gz = grads[:, a, 0], grads[:, a, 1], grads[:, a, 2]
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

    db_mats = np.einsum("ij,mjk->mik", d_mat, b_mats)
    ke_mats = np.einsum("m,mji,mjk->mik", vols, b_mats, db_mats)
    m_rows = np.repeat(el_dofs, 12, axis=1).ravel()
    m_cols = np.tile(el_dofs, (1, 12)).ravel()
    k_mech = sparse.coo_matrix((ke_mats.ravel(), (m_rows, m_cols)), shape=(n_dof, n_dof)).tocsr()

    # Element average delta_T -> thermal strain eps_th = alpha * dT * [1, 1, 1, 0, 0, 0]
    dt_el = temps[mesh.elements].mean(axis=1) - float(ambient_temp_c)  # (M,)
    eps_th = np.zeros((n_elems, 6), dtype=np.float64)
    eps_th[:, 0] = eps_th[:, 1] = eps_th[:, 2] = float(thermal_expansion_1_k) * dt_el
    d_eps_th = np.einsum("ij,mj->mi", d_mat, eps_th)  # (M, 6)
    f_th_el = np.einsum("m,mji,mj->mi", vols, b_mats, d_eps_th)  # (M, 12)

    f_mech = np.zeros(n_dof, dtype=np.float64)
    np.add.at(f_mech, el_dofs, f_th_el)

    fixed_dofs = np.concatenate([3 * fixed_mech, 3 * fixed_mech + 1, 3 * fixed_mech + 2])
    free_mask_m = np.ones(n_dof, dtype=bool)
    free_mask_m[fixed_dofs] = False
    free_dofs_m = np.nonzero(free_mask_m)[0]

    u_global = np.zeros(n_dof, dtype=np.float64)
    u_global[free_dofs_m] = spla.spsolve(
        k_mech[free_dofs_m, :][:, free_dofs_m], f_mech[free_dofs_m]
    )
    u_nodes = u_global.reshape((n_nodes, 3))

    # True thermal stress: sigma = D * (B * u_e - eps_th)
    sigma_el = np.einsum("mik,mk->mi", db_mats, u_global[el_dofs]) - d_eps_th
    sxx, syy, szz = sigma_el[:, 0], sigma_el[:, 1], sigma_el[:, 2]
    txy, tyz, txz = sigma_el[:, 3], sigma_el[:, 4], sigma_el[:, 5]
    vm_el = np.sqrt(
        0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
        + 3.0 * (txy**2 + tyz**2 + txz**2)
    )

    nodal_vm = np.zeros(n_nodes, dtype=np.float64)
    nodal_w = np.zeros(n_nodes, dtype=np.float64)
    for a in range(4):
        np.add.at(nodal_vm, mesh.elements[:, a], vm_el * vols)
        np.add.at(nodal_w, mesh.elements[:, a], vols)
    nodal_vm = np.divide(nodal_vm, np.maximum(nodal_w, 1e-12))

    disp_mag = np.linalg.norm(u_nodes, axis=1)
    max_disp = float(disp_mag.max()) if disp_mag.size else 0.0
    max_vm = float(nodal_vm.max()) if nodal_vm.size else 0.0
    sy = float(yield_strength_mpa)
    result.displacements_mm = u_nodes
    result.nodal_von_mises_mpa = nodal_vm
    result.max_disp_mm = max_disp
    result.max_von_mises_mpa = max_vm
    result.min_safety_factor = float(sy / max_vm) if max_vm > 1e-9 else 999.0
    return result


@dataclass
class TransientThermalResult:
    node_count: int
    element_count: int
    times_s: list[float]
    final_temperatures_c: np.ndarray  # (N,)
    mean_temp_history_c: list[float]
    min_temp_c: float
    max_temp_c: float

    def to_dict(self, mesh: TetMesh) -> dict[str, Any]:
        return {
            "analysis_type": "TRANSIENT_THERMAL",
            "node_count": self.node_count,
            "element_count": self.element_count,
            "times_s": [round(t, 4) for t in self.times_s],
            "mean_temp_history_c": [round(t, 4) for t in self.mean_temp_history_c],
            "min_temp_c": round(self.min_temp_c, 4),
            "max_temp_c": round(self.max_temp_c, 4),
            "nodes": np.round(mesh.nodes, 5).tolist(),
            "temperatures_c": np.round(self.final_temperatures_c, 4).tolist(),
        }


def solve_transient_thermal(
    mesh: TetMesh,
    *,
    thermal_conductivity_w_mk: float = 50.0,
    density_kg_m3: float = 7850.0,
    specific_heat_j_kgk: float = 500.0,
    initial_temp_c: float = 100.0,
    ambient_temp_c: float = 20.0,
    thermal_bcs: list[dict[str, Any]],
    time_end_s: float = 60.0,
    time_steps: int = 20,
) -> TransientThermalResult:
    """Solve 3D transient heat conduction using Backward Euler:
    (C / dt + K_T) T^{n+1} = (C / dt) T^n + q^{n+1}.
    """
    if thermal_conductivity_w_mk <= 0 or density_kg_m3 <= 0 or specific_heat_j_kgk <= 0:
        raise CaeError("CAE_INVALID_PARAM", "thermal properties must be > 0")
    if time_end_s <= 0 or not (1 <= time_steps <= 500):
        raise CaeError("CAE_INVALID_PARAM", "invalid transient time_end_s / time_steps")
    if not thermal_bcs:
        raise CaeError("CAE_INVALID_PARAM", "at least one thermal BC is required")

    n_nodes = mesh.node_count
    n_elems = mesh.element_count
    k_mm = float(thermal_conductivity_w_mk) * 1e-3  # W/(mm*K)
    rho_cp_mm3 = float(density_kg_m3) * 1e-9 * float(specific_heat_j_kgk)  # J/(mm^3*K)

    el_nodes = mesh.nodes[mesh.elements]
    j_mat = el_nodes[:, 1:, :] - el_nodes[:, 0:1, :]
    vols = np.linalg.det(j_mat) / 6.0
    if np.any(vols <= 1e-12):
        raise CaeError("CAE_MESH_FAILED", "non-positive Tet4 volume in transient thermal solver")
    inv_j = np.linalg.inv(j_mat)
    grad_n123 = np.transpose(inv_j, (0, 2, 1))
    grad_n0 = -grad_n123.sum(axis=1, keepdims=True)
    grads = np.concatenate([grad_n0, grad_n123], axis=1)

    kt_el = np.einsum("m,mia,mja->mij", k_mm * vols, grads, grads)
    rows = np.repeat(mesh.elements, 4, axis=1).ravel()
    cols = np.tile(mesh.elements, (1, 4)).ravel()
    k_t = sparse.coo_matrix((kt_el.ravel(), (rows, cols)), shape=(n_nodes, n_nodes)).tolil()
    q_t = np.zeros(n_nodes, dtype=np.float64)

    # Lumped thermal capacitance diagonal C_ii = sum_e (rho * cp * V_e / 4)
    c_diag = np.zeros(n_nodes, dtype=np.float64)
    for a in range(4):
        np.add.at(c_diag, mesh.elements[:, a], rho_cp_mm3 * vols * 0.25)

    fixed_t_map: dict[int, float] = {}
    for bc in thermal_bcs:
        btype = str(bc.get("type", "CONVECTION")).upper()
        bnodes = mesh.select_face_nodes(bc.get("face_ref", bc))
        if bnodes.size == 0:
            raise CaeError("CAE_INVALID_PARAM", f"transient thermal BC ({btype}) matched 0 nodes")
        if btype == "FIXED_TEMP":
            t_val = float(bc.get("temperature_c", ambient_temp_c))
            for nid in bnodes:
                fixed_t_map[int(nid)] = t_val
        elif btype == "HEAT_FLUX":
            flux = float(bc.get("flux_w_m2", 0.0)) * 1e-6
            bpts = mesh.nodes[bnodes]
            spans = sorted(
                [float(np.ptp(bpts[:, 0])), float(np.ptp(bpts[:, 1])), float(np.ptp(bpts[:, 2]))]
            )
            area = max(1e-6, spans[1] * spans[2])
            q_t[bnodes] += (flux * area) / bnodes.size
        elif btype == "CONVECTION":
            h_conv = float(bc.get("h_w_m2k", 10.0)) * 1e-6
            t_inf = float(bc.get("ambient_temp_c", ambient_temp_c))
            bpts = mesh.nodes[bnodes]
            spans = sorted(
                [float(np.ptp(bpts[:, 0])), float(np.ptp(bpts[:, 1])), float(np.ptp(bpts[:, 2]))]
            )
            area = max(1e-6, spans[1] * spans[2])
            h_nodal = (h_conv * area) / bnodes.size
            for nid in bnodes:
                idx = int(nid)
                k_t[idx, idx] += h_nodal
                q_t[idx] += h_nodal * t_inf
        else:
            raise CaeError("CAE_INVALID_PARAM", f"unsupported thermal BC type: {btype}")

    dt = float(time_end_s) / float(time_steps)
    c_over_dt = c_diag / dt
    a_mat = (k_t.tocsc() + sparse.diags(c_over_dt, format="csc")).tocsc()

    t_cur = np.full(n_nodes, float(initial_temp_c), dtype=np.float64)
    fixed_ids = np.array(sorted(fixed_t_map.keys()), dtype=np.int32)
    free_mask = np.ones(n_nodes, dtype=bool)
    if fixed_ids.size > 0:
        free_mask[fixed_ids] = False
        for nid in fixed_ids:
            t_cur[nid] = fixed_t_map[int(nid)]
    free_ids = np.nonzero(free_mask)[0]

    # Pre-factorize constant Backward Euler LHS matrix for fast time stepping
    lu = spla.splu(a_mat[free_ids, :][:, free_ids]) if free_ids.size > 0 else None
    a_ff_fixed = (
        a_mat[free_ids, :][:, fixed_ids] if (free_ids.size > 0 and fixed_ids.size > 0) else None
    )

    times = [0.0]
    mean_hist = [float(np.mean(t_cur))]
    for step in range(1, time_steps + 1):
        rhs_all = c_over_dt * t_cur + q_t
        if lu is not None:
            rhs_free = rhs_all[free_ids]
            if a_ff_fixed is not None:
                rhs_free = rhs_free - a_ff_fixed @ t_cur[fixed_ids]
            t_cur[free_ids] = lu.solve(rhs_free)
        times.append(step * dt)
        mean_hist.append(float(np.mean(t_cur)))

    return TransientThermalResult(
        node_count=n_nodes,
        element_count=n_elems,
        times_s=times,
        final_temperatures_c=t_cur,
        mean_temp_history_c=mean_hist,
        min_temp_c=float(np.min(t_cur)),
        max_temp_c=float(np.max(t_cur)),
    )
