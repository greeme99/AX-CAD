"""CAE (Computer-Aided Engineering) Linear Static FEA engine — plan.md §24 (E1~E3)."""

from core.cae.fea import FeaResult, solve_linear_static
from core.cae.mesh import MAX_ELEMENTS, MAX_NODES, CaeError, TetMesh, generate_tet4_mesh

__all__ = [
    "MAX_ELEMENTS",
    "MAX_NODES",
    "CaeError",
    "FeaResult",
    "TetMesh",
    "generate_tet4_mesh",
    "solve_linear_static",
]
