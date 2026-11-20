"""Modal (Natural Frequency & Mode Shape) FEA Solver on Tet10 (CAE E5)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.sparse import linalg as spla

from core.cae.fea import _check_restraints_not_underconstrained
from core.cae.mesh import CaeError, TetMesh
from core.cae.tet10 import Tet10Mesh, assemble_tet10_stiffness_and_mass, upgrade_tet4_to_tet10


@dataclass
class ModalResult:
    node_count: int
    element_count: int
    frequencies_hz: list[float]
    mode_shapes: list[np.ndarray]  # each (N, 3) normalized to max displacement = 1.0

    def to_dict(self, mesh: Tet10Mesh) -> dict[str, Any]:
        return {
            "analysis_type": "MODAL",
            "node_count": self.node_count,
            "element_count": self.element_count,
            "frequencies_hz": [round(f, 4) for f in self.frequencies_hz],
            "nodes": np.round(mesh.nodes, 5).tolist(),
            "mode_shapes": [np.round(m, 5).tolist() for m in self.mode_shapes],
        }


def solve_modal(
    mesh: TetMesh | Tet10Mesh,
    *,
    youngs_modulus_mpa: float,
    poissons_ratio: float,
    density_kg_m3: float = 7850.0,
    restraints: list[dict[str, Any]],
    mode_count: int = 6,
) -> tuple[ModalResult, Tet10Mesh]:
    """Solve generalized eigenvalue problem K phi = omega^2 M phi for lowest `mode_count` natural frequencies (Hz)."""
    if density_kg_m3 <= 0 or not math.isfinite(density_kg_m3):
        raise CaeError("CAE_INVALID_PARAM", "density_kg_m3 must be > 0")
    if not (1 <= mode_count <= 20):
        raise CaeError("CAE_INVALID_PARAM", "mode_count must be in 1..20")
    if not restraints:
        raise CaeError("CAE_UNDERCONSTRAINED", "modal analysis requires restraints")

    t10 = mesh if isinstance(mesh, Tet10Mesh) else upgrade_tet4_to_tet10(mesh)
    fixed_lists = [t10.select_face_nodes(r.get("face_ref", r)) for r in restraints]
    fixed_nodes = (
        np.unique(np.concatenate(fixed_lists)) if fixed_lists else np.array([], dtype=np.int32)
    )
    _check_restraints_not_underconstrained(t10.nodes, fixed_nodes)

    k_global, m_global = assemble_tet10_stiffness_and_mass(
        t10,
        youngs_modulus_mpa=youngs_modulus_mpa,
        poissons_ratio=poissons_ratio,
        density_kg_m3=density_kg_m3,
        include_mass=True,
    )
    assert m_global is not None

    n_nodes = t10.node_count
    n_dof = 3 * n_nodes
    fixed_dofs = np.concatenate([3 * fixed_nodes, 3 * fixed_nodes + 1, 3 * fixed_nodes + 2])
    free_mask = np.ones(n_dof, dtype=bool)
    free_mask[fixed_dofs] = False
    free_dofs = np.nonzero(free_mask)[0]

    k_ff = k_global[free_dofs, :][:, free_dofs]
    m_ff = m_global[free_dofs, :][:, free_dofs]
    k_req = min(mode_count, max(1, free_dofs.size - 2))

    # Shift-invert around sigma=0.0 finds the smallest positive eigenvalues omega^2 accurately
    eigvals, eigvecs = spla.eigsh(k_ff, k=k_req, M=m_ff, sigma=0.0, which="LM")
    order = np.argsort(eigvals)
    eigvals = eigvals[order]
    eigvecs = eigvecs[:, order]

    freqs: list[float] = []
    shapes: list[np.ndarray] = []
    for i in range(k_req):
        lam = float(eigvals[i])
        if lam <= 0.0:
            freqs.append(0.0)
        else:
            freqs.append(math.sqrt(lam) / math.tau)
        u_full = np.zeros(n_dof, dtype=np.float64)
        u_full[free_dofs] = eigvecs[:, i]
        u_3 = u_full.reshape((n_nodes, 3))
        max_mag = float(np.linalg.norm(u_3, axis=1).max())
        if max_mag > 1e-12:
            u_3 = u_3 / max_mag
        shapes.append(u_3)

    return (
        ModalResult(
            node_count=n_nodes,
            element_count=t10.element_count,
            frequencies_hz=freqs,
            mode_shapes=shapes,
        ),
        t10,
    )
