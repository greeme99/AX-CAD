"""CAE E8: Multi-Body Assembly Contact FEA (Bonded & Frictionless No-Penetration).

Assembles multiple 3D Tet4 bodies into a unified sparse system and enforces:
  - BONDED contact: 3-DOF penalty tie springs between matched interface node pairs.
  - NO_PENETRATION (frictionless unilateral) contact: active-set penalty iteration
    activating normal stiffness only when normal gap g_n = (u_B - u_A).n + g_0 < 0
    (compression), and releasing when g_n >= 0 (separation/tension).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import sparse
from scipy.sparse import linalg as spla

from core.cae.fea import _isotropic_d_matrix
from core.cae.mesh import CaeError, TetMesh


@dataclass
class ContactBodySpec:
    name: str
    mesh: TetMesh
    youngs_modulus_mpa: float = 210_000.0
    poissons_ratio: float = 0.3
    yield_strength_mpa: float = 250.0


@dataclass
class ContactPairSpec:
    body_a: int
    body_b: int
    contact_type: str = "BONDED"  # "BONDED" | "NO_PENETRATION"
    normal: tuple[float, float, float] = (1.0, 0.0, 0.0)  # outward from A toward B
    search_tol_mm: float = 1.5
    penalty_factor: float = 1e4


@dataclass
class ContactResult:
    body_count: int
    total_nodes: int
    total_elements: int
    pair_count: int
    active_contact_nodes: int
    iterations: int
    converged: bool
    max_disp_mm: float
    max_von_mises_mpa: float
    body_max_disp_mm: list[float]
    displacements_mm: np.ndarray  # (N_total, 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis_type": "CONTACT",
            "body_count": self.body_count,
            "node_count": self.total_nodes,
            "element_count": self.total_elements,
            "pair_count": self.pair_count,
            "active_contact_nodes": self.active_contact_nodes,
            "iterations": self.iterations,
            "converged": self.converged,
            "max_disp_mm": round(self.max_disp_mm, 6),
            "max_von_mises_mpa": round(self.max_von_mises_mpa, 4),
            "body_max_disp_mm": [round(v, 6) for v in self.body_max_disp_mm],
        }


def _assemble_tet4_stiffness(
    mesh: TetMesh, d_mat: np.ndarray, node_offset: int, total_dof: int
) -> tuple[sparse.csc_matrix, np.ndarray, np.ndarray]:
    """Assemble global Tet4 stiffness matrix for one body offset at node_offset."""
    el_nodes = mesh.nodes[mesh.elements]  # (M, 4, 3)
    j_mat = el_nodes[:, 1:, :] - el_nodes[:, 0:1, :]
    det_j = np.linalg.det(j_mat)
    vols = det_j / 6.0
    if np.any(vols <= 1e-12):
        raise CaeError("CAE_MESH_FAILED", "degenerate Tet4 element in contact solver")
    inv_j = np.linalg.inv(j_mat)
    grad_n123 = np.transpose(inv_j, (0, 2, 1))
    grad_n0 = -grad_n123.sum(axis=1, keepdims=True)
    grads = np.concatenate([grad_n0, grad_n123], axis=1)  # (M, 4, 3)

    m = mesh.element_count
    b_mats = np.zeros((m, 6, 12), dtype=np.float64)
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

    db = np.einsum("ij,mjk->mik", d_mat, b_mats)
    ke = np.einsum("m,mji,mjk->mik", vols, b_mats, db)  # (M, 12, 12)

    g_elems = mesh.elements + node_offset
    dof_idx = np.empty((m, 12), dtype=np.int32)
    for a in range(4):
        dof_idx[:, 3 * a] = 3 * g_elems[:, a]
        dof_idx[:, 3 * a + 1] = 3 * g_elems[:, a] + 1
        dof_idx[:, 3 * a + 2] = 3 * g_elems[:, a] + 2

    rows = np.repeat(dof_idx, 12, axis=1).ravel()
    cols = np.tile(dof_idx, (1, 12)).ravel()
    k_mat = sparse.coo_matrix((ke.ravel(), (rows, cols)), shape=(total_dof, total_dof)).tocsc()
    return k_mat, b_mats, dof_idx


def _match_interface_pairs(
    mesh_a: TetMesh,
    offset_a: int,
    mesh_b: TetMesh,
    offset_b: int,
    tol_mm: float,
) -> list[tuple[int, int, float]]:
    """Find nearest node pairs between body A and body B within search tolerance."""
    pts_a = mesh_a.nodes
    pts_b = mesh_b.nodes
    # Pairwise squared distances (compact for assembly contact interfaces)
    diff = pts_a[:, None, :] - pts_b[None, :, :]
    dist = np.linalg.norm(diff, axis=2)
    min_b = np.argmin(dist, axis=1)
    min_d = dist[np.arange(pts_a.shape[0]), min_b]
    pairs: list[tuple[int, int, float]] = []
    for ia in range(pts_a.shape[0]):
        if min_d[ia] <= tol_mm:
            ib = int(min_b[ia])
            pairs.append((offset_a + ia, offset_b + ib, float(min_d[ia])))
    return pairs


def solve_contact_assembly(
    bodies: list[ContactBodySpec],
    contacts: list[ContactPairSpec],
    restraints: list[dict[str, Any]],
    loads: list[dict[str, Any]],
    *,
    max_iter: int = 12,
) -> ContactResult:
    """Solve multi-body 3D linear elastic assembly with Bonded & Frictionless No-Penetration contacts.

    Each restraint/load dict may include `"body_index": int` (defaults to 0 for restraints, last body for loads).
    """
    if len(bodies) < 2:
        raise CaeError("CAE_INVALID_PARAM", "contact assembly requires at least 2 bodies")

    offsets: list[int] = []
    total_nodes = 0
    total_elements = 0
    for b in bodies:
        offsets.append(total_nodes)
        total_nodes += b.mesh.node_count
        total_elements += b.mesh.element_count
    total_dof = 3 * total_nodes

    k_base = sparse.csc_matrix((total_dof, total_dof), dtype=np.float64)
    body_b_mats: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    e_ref = max(b.youngs_modulus_mpa for b in bodies)
    for idx, b in enumerate(bodies):
        d_mat = _isotropic_d_matrix(b.youngs_modulus_mpa, b.poissons_ratio)
        kb, b_mats, dof_idx = _assemble_tet4_stiffness(b.mesh, d_mat, offsets[idx], total_dof)
        k_base = k_base + kb
        body_b_mats.append((d_mat, b_mats, dof_idx))

    # Restraints & external load vector
    fixed_dofs_set: set[int] = set()
    for r in restraints:
        bidx = int(r.get("body_index", 0))
        if not (0 <= bidx < len(bodies)):
            raise CaeError("CAE_INVALID_PARAM", f"invalid body_index {bidx} in restraint")
        nids = bodies[bidx].mesh.select_face_nodes(r.get("face_ref", r))
        for nid in nids:
            g = offsets[bidx] + int(nid)
            fixed_dofs_set.update((3 * g, 3 * g + 1, 3 * g + 2))

    if len(fixed_dofs_set) < 9:
        raise CaeError(
            "CAE_UNDERCONSTRAINED", "at least 3 fixed nodes required in contact assembly"
        )

    f_ext = np.zeros(total_dof, dtype=np.float64)
    for ld in loads:
        bidx = int(ld.get("body_index", len(bodies) - 1))
        if not (0 <= bidx < len(bodies)):
            raise CaeError("CAE_INVALID_PARAM", f"invalid body_index {bidx} in load")
        nids = bodies[bidx].mesh.select_face_nodes(ld.get("face_ref", ld))
        if nids.size == 0:
            raise CaeError("CAE_INVALID_PARAM", "contact load face matched 0 nodes")
        fv = ld.get("force_n", [0.0, 0.0, 0.0])
        per_node = (
            np.array([float(fv[0]), float(fv[1]), float(fv[2])], dtype=np.float64) / nids.size
        )
        for nid in nids:
            g = offsets[bidx] + int(nid)
            f_ext[3 * g : 3 * g + 3] += per_node

    # Pre-match all contact pairs
    matched_groups: list[tuple[ContactPairSpec, np.ndarray, list[tuple[int, int, float]]]] = []
    total_pairs = 0
    for cp in contacts:
        n_vec = np.array(cp.normal, dtype=np.float64)
        n_norm = float(np.linalg.norm(n_vec))
        if n_norm <= 1e-12:
            raise CaeError("CAE_INVALID_PARAM", "contact normal cannot be zero")
        n_unit = n_vec / n_norm
        pairs = _match_interface_pairs(
            bodies[cp.body_a].mesh,
            offsets[cp.body_a],
            bodies[cp.body_b].mesh,
            offsets[cp.body_b],
            cp.search_tol_mm,
        )
        matched_groups.append((cp, n_unit, pairs))
        total_pairs += len(pairs)

    if total_pairs == 0:
        raise CaeError("CAE_INVALID_PARAM", "no contact node pairs found within search_tol_mm")

    # Active-set flags for NO_PENETRATION pairs (start active so initial solve is well-posed)
    active_sets: list[list[bool]] = [[True] * len(pairs) for (_, _, pairs) in matched_groups]
    fixed_dofs = np.array(sorted(fixed_dofs_set), dtype=np.int32)
    free_mask = np.ones(total_dof, dtype=bool)
    free_mask[fixed_dofs] = False
    free_dofs = np.nonzero(free_mask)[0]

    # Tiny stabilization spring (1e-6 * E) to prevent singular matrix if a free body separates completely
    k_reg = sparse.eye(total_dof, format="csc") * (e_ref * 1e-6)

    u = np.zeros(total_dof, dtype=np.float64)
    converged = False
    iters = 0

    for it in range(1, max_iter + 1):
        iters = it
        c_rows: list[int] = []
        c_cols: list[int] = []
        c_vals: list[float] = []

        for g_idx, (cp, n_unit, pairs) in enumerate(matched_groups):
            kp = e_ref * cp.penalty_factor
            ctype = cp.contact_type.upper()
            n_outer = np.outer(n_unit, n_unit)
            for p_idx, (ga, gb, _gap0) in enumerate(pairs):
                if ctype == "BONDED":
                    k_block = kp * np.eye(3)
                elif ctype == "NO_PENETRATION":
                    if not active_sets[g_idx][p_idx]:
                        continue
                    k_block = kp * n_outer
                else:
                    raise CaeError("CAE_INVALID_PARAM", f"unsupported contact_type: {ctype}")

                da = [3 * ga, 3 * ga + 1, 3 * ga + 2]
                db = [3 * gb, 3 * gb + 1, 3 * gb + 2]
                for i in range(3):
                    for j in range(3):
                        val = float(k_block[i, j])
                        if val == 0.0:
                            continue
                        c_rows.extend([da[i], db[i], da[i], db[i]])
                        c_cols.extend([da[j], db[j], db[j], da[j]])
                        c_vals.extend([val, val, -val, -val])

        k_contact = sparse.coo_matrix(
            (c_vals, (c_rows, c_cols)), shape=(total_dof, total_dof)
        ).tocsc()
        k_tot = k_base + k_contact + k_reg
        u = np.zeros(total_dof, dtype=np.float64)
        u[free_dofs] = spla.spsolve(k_tot[free_dofs, :][:, free_dofs], f_ext[free_dofs])

        # Update active sets for NO_PENETRATION contacts
        changed = False
        for g_idx, (cp, n_unit, pairs) in enumerate(matched_groups):
            if cp.contact_type.upper() != "NO_PENETRATION":
                continue
            for p_idx, (ga, gb, _gap0) in enumerate(pairs):
                ua = u[3 * ga : 3 * ga + 3]
                ub = u[3 * gb : 3 * gb + 3]
                # Normal gap: positive = separation, negative = penetration
                g_n = float(np.dot(ub - ua, n_unit))
                new_active = g_n < -1e-9
                if new_active != active_sets[g_idx][p_idx]:
                    active_sets[g_idx][p_idx] = new_active
                    changed = True

        if not changed:
            converged = True
            break

    active_count = sum(sum(1 for flag in flags if flag) for flags in active_sets)
    disp = u.reshape(total_nodes, 3)
    disp_mag = np.linalg.norm(disp, axis=1)

    body_max_disp: list[float] = []
    max_vm = 0.0
    for idx, b in enumerate(bodies):
        n0 = offsets[idx]
        n1 = n0 + b.mesh.node_count
        body_max_disp.append(float(np.max(disp_mag[n0:n1])))
        d_mat, b_mats, dof_idx = body_b_mats[idx]
        u_el = u[dof_idx]  # (M, 12)
        eps = np.einsum("mij,mj->mi", b_mats, u_el)
        sig = np.einsum("ij,mj->mi", d_mat, eps)
        sxx, syy, szz, sxy, syz, sxz = (sig[:, k] for k in range(6))
        vm = np.sqrt(
            0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
            + 3.0 * (sxy**2 + syz**2 + sxz**2)
        )
        max_vm = max(max_vm, float(np.max(vm)))

    return ContactResult(
        body_count=len(bodies),
        total_nodes=total_nodes,
        total_elements=total_elements,
        pair_count=total_pairs,
        active_contact_nodes=active_count,
        iterations=iters,
        converged=converged,
        max_disp_mm=float(np.max(disp_mag)),
        max_von_mises_mpa=max_vm if math.isfinite(max_vm) else 0.0,
        body_max_disp_mm=body_max_disp,
        displacements_mm=disp,
    )
