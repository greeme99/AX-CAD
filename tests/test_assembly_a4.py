"""Assembly A4 tests: exact mass/CoG/inertia tensor, indented/rolled-up BOM, and A-ASM labor (ASM-T09)."""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Pnt

from core.geometry import assembly as asm
from core.geometry import assembly_mass as am
from core.geometry.serialize import to_brep


def _box_inertia_about_cog(m: float, lx: float, ly: float, lz: float) -> np.ndarray:
    return np.diag(
        [
            m * (ly**2 + lz**2) / 12.0,
            m * (lx**2 + lz**2) / 12.0,
            m * (lx**2 + ly**2) / 12.0,
        ]
    )


def test_asm_t09_mass_cog_and_parallel_axis_inertia_analytical() -> None:
    """Two steel blocks match analytical mass, CoG, and inertia tensor to <= 1e-9 relative error."""
    rho = 7.85
    b1 = to_brep(BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 100.0, 50.0, 20.0).Shape())
    b2 = to_brep(BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 40.0, 40.0, 40.0).Shape())
    comps = [
        {"id": 1, "name": "Plate", "part": "b1", "t": [0.0, 0.0, 0.0], "r": [0.0, 0.0, 0.0]},
        {"id": 2, "name": "Block", "part": "b2", "t": [150.0, 20.0, 10.0], "r": [0.0, 0.0, 0.0]},
    ]
    res = am.assembly_mass_job({"b1": b1, "b2": b2}, comps, {"b1": rho, "b2": rho})

    # Analytical values
    m1 = 100.0 * 50.0 * 20.0 * rho * 1e-6  # 0.785 kg
    m2 = 40.0 * 40.0 * 40.0 * rho * 1e-6  # 0.5024 kg
    c1 = np.array([50.0, 25.0, 10.0])
    c2 = np.array([170.0, 40.0, 30.0])
    m_tot = m1 + m2
    c_tot = (m1 * c1 + m2 * c2) / m_tot

    d1, d2 = c1 - c_tot, c2 - c_tot
    eye3 = np.eye(3)
    i_expected = (
        _box_inertia_about_cog(m1, 100.0, 50.0, 20.0)
        + m1 * (float(d1 @ d1) * eye3 - np.outer(d1, d1))
        + _box_inertia_about_cog(m2, 40.0, 40.0, 40.0)
        + m2 * (float(d2 @ d2) * eye3 - np.outer(d2, d2))
    )

    assert res["total_mass_kg"] == pytest.approx(m_tot, rel=1e-9)
    assert res["center_of_gravity_mm"] == pytest.approx(c_tot.tolist(), rel=1e-9)
    i_got = np.asarray(res["inertia_kg_mm2"])
    assert np.allclose(i_got, i_expected, rtol=1e-9, atol=1e-9)


def test_asm_t09_multilevel_bom_and_a_asm_labor_quote() -> None:
    """Indented vs rolled-up BOM and Decimal A-ASM labor rule match hand calculation."""
    tree = {
        1: [
            {
                "component_id": 10,
                "name": "SubA",
                "component_kind": "SUBASSEMBLY",
                "source_document_id": 2,
                "source_feature_id": None,
                "placement": {"t": [0, 0, 0], "r": [0, 0, 0]},
                "suppressed": False,
            },
            {
                "component_id": 11,
                "name": "Bolt-Top",
                "component_kind": "PART",
                "source_document_id": 3,
                "source_feature_id": 301,
                "placement": {"t": [10, 0, 0], "r": [0, 0, 0]},
                "suppressed": False,
            },
        ],
        2: [
            {
                "component_id": 20,
                "name": "Bolt-1",
                "component_kind": "PART",
                "source_document_id": 3,
                "source_feature_id": 301,
                "placement": {"t": [0, 0, 0], "r": [0, 0, 0]},
                "suppressed": False,
            },
            {
                "component_id": 21,
                "name": "Bracket",
                "component_kind": "PART",
                "source_document_id": 4,
                "source_feature_id": 401,
                "placement": {"t": [0, 20, 0], "r": [0, 0, 0]},
                "suppressed": False,
            },
        ],
    }
    flat = asm.expand_hierarchy(1, lambda did: tree.get(did, []))
    ind = am.build_assembly_bom(flat, "indented")
    assert len(ind) == 3
    assert [(x["level"], x["path"]) for x in ind] == [
        (2, "SubA/Bolt-1"),
        (2, "SubA/Bracket"),
        (1, "Bolt-Top"),
    ]

    rolled = am.build_assembly_bom(flat, "rolled_up")
    by_doc = {x["source_document_id"]: x["qty"] for x in rolled}
    assert by_doc == {3: 2, 4: 1}

    # A-ASM labor: 3 parts * 2.0 min + 4 mates * 1.5 min = 12.0 min -> 12/60 * 45000 = 9000 KRW
    labor = am.compute_assembly_labor(
        part_count=3,
        mate_count=4,
        base_min_per_part=Decimal("2.0"),
        min_per_mate=Decimal("1.5"),
        hourly_rate=Decimal(45000),
    )
    assert labor["rule_code"] == "A-ASM"
    assert labor["labor_minutes"] == "12.0"
    assert labor["labor_cost_krw"] == "9000"
