"""Tests for Construction Process, Material Takeoff & 5D Cost Simulation (CSIM-T01~T05)."""

from __future__ import annotations

import pytest

from core.construction_sim import ConstructionSimError, CostRates
from core.construction_sim.ai_advisor import advise_construction_simulation
from core.construction_sim.simulator import export_simulation_csv, simulate_construction_plan
from core.construction_sim.templates import DOMAIN_META, generate_template_tasks


def test_csim_t01_all_6_domain_templates_and_work_modes() -> None:
    """CSIM-T01: All 6 domains generate valid WBS tasks with procedures & materials across modes."""
    for domain in DOMAIN_META:
        tasks_install = generate_template_tasks(domain, "INSTALL", 100.0)
        assert len(tasks_install) >= 4
        assert all(t["procedures"] for t in tasks_install)

    # HVAC REPLACE includes refrigerant recovery/demolition + installation; DEMOLISH stops after demolition
    hvac_replace = generate_template_tasks("MEP_HVAC", "REPLACE", 4.0)
    hvac_demolish = generate_template_tasks("MEP_HVAC", "DEMOLISH", 4.0)
    assert any("해체" in t["name"] for t in hvac_replace)
    assert any("시운전" in t["name"] for t in hvac_replace)
    assert len(hvac_demolish) == 2
    assert all("시운전" not in t["name"] for t in hvac_demolish)


def test_csim_t02_cpm_critical_path_and_cycle_rejection() -> None:
    """CSIM-T02: CPM computes exact ES/EF/LS/LF/Float, critical path, and rejects cyclic dependencies."""
    tasks = [
        {
            "task_id": "A",
            "wbs": "1",
            "name": "Start",
            "phase": "P1",
            "duration_days": 3,
            "predecessors": [],
            "materials": [],
            "labor_cost_krw": 1_000_000,
            "equipment_cost_krw": 0,
        },
        {
            "task_id": "B",
            "wbs": "2",
            "name": "Long",
            "phase": "P2",
            "duration_days": 6,
            "predecessors": ["A"],
            "materials": [],
            "labor_cost_krw": 2_000_000,
            "equipment_cost_krw": 0,
        },
        {
            "task_id": "C",
            "wbs": "3",
            "name": "Short",
            "phase": "P2",
            "duration_days": 2,
            "predecessors": ["A"],
            "materials": [],
            "labor_cost_krw": 500_000,
            "equipment_cost_krw": 0,
        },
        {
            "task_id": "D",
            "wbs": "4",
            "name": "End",
            "phase": "P3",
            "duration_days": 2,
            "predecessors": ["B", "C"],
            "materials": [],
            "labor_cost_krw": 500_000,
            "equipment_cost_krw": 0,
        },
    ]
    sim = simulate_construction_plan(tasks, monte_carlo_trials=200)
    assert sim["project_duration_days"] == 11.0
    assert sim["critical_path"] == ["A", "B", "D"]
    by_id = {t["task_id"]: t for t in sim["tasks"]}
    assert by_id["C"]["total_float"] == 4.0
    assert by_id["C"]["is_critical"] is False
    assert by_id["B"]["is_critical"] is True

    # Cycle must raise CONSIM_CYCLE
    cyclic = [
        {
            "task_id": "X",
            "wbs": "1",
            "name": "X",
            "phase": "P",
            "duration_days": 2,
            "predecessors": ["Y"],
        },
        {
            "task_id": "Y",
            "wbs": "2",
            "name": "Y",
            "phase": "P",
            "duration_days": 2,
            "predecessors": ["X"],
        },
    ]
    with pytest.raises(ConstructionSimError) as exc:
        simulate_construction_plan(cyclic)
    assert exc.value.code == "CONSIM_CYCLE"


def test_csim_t03_korean_cost_statement_and_material_bom() -> None:
    """CSIM-T03: Exact Decimal Korean Construction Cost Statement, BOM loss_rate, and S-Curve closure."""
    tasks = generate_template_tasks("INTERIOR_RENO", "RENOVATE", 120.0)
    sim = simulate_construction_plan(tasks, rates=CostRates(), monte_carlo_trials=300)
    stmt = sim["cost_statement"]
    assert stmt["direct_material_krw"] > 0
    assert stmt["direct_labor_krw"] > 0
    assert stmt["net_construction_cost_krw"] == (
        stmt["direct_material_krw"]
        + stmt["direct_labor_krw"]
        + stmt["indirect_labor_krw"]
        + stmt["direct_equipment_krw"]
        + stmt["indirect_expense_krw"]
    )
    assert stmt["grand_total_krw"] == stmt["supply_amount_krw"] + stmt["vat_krw"]
    assert len(sim["material_bom"]) >= 4
    # S-curve ends at 100% and exact grand_total_krw
    assert sim["scurve"][-1]["cumulative_cost_krw"] == stmt["grand_total_krw"]
    assert sim["scurve"][-1]["progress_pct"] == 100.0


def test_csim_t04_scenarios_and_monte_carlo_monotonicity() -> None:
    """CSIM-T04: Crash shortens duration & raises cost; VE lowers cost; Monte Carlo P50 <= P80 <= P95."""
    tasks = generate_template_tasks("BUILDING", "NEW_BUILD", 150.0)
    sim = simulate_construction_plan(tasks, monte_carlo_trials=500, seed=7)
    sc_map = {s["code"]: s for s in sim["scenarios"]}
    assert sc_map["CRASH_NIGHT"]["duration_days"] < sc_map["STANDARD"]["duration_days"]
    assert sc_map["CRASH_NIGHT"]["grand_total_krw"] > sc_map["STANDARD"]["grand_total_krw"]
    assert sc_map["VALUE_ENGINEER"]["grand_total_krw"] < sc_map["STANDARD"]["grand_total_krw"]

    mc = sim["monte_carlo"]
    assert mc["cost_p50_krw"] <= mc["cost_p80_krw"] <= mc["cost_p95_krw"]
    assert mc["duration_p50_days"] <= mc["duration_p80_days"] <= mc["duration_p95_days"]
    assert len(mc["top_risk_drivers"]) >= 1


def test_csim_t05_csv_export_and_advisor_fallback() -> None:
    """CSIM-T05: CSV export emits UTF-8 BOM with schedule & BOM; advisor degrades cleanly offline."""
    tasks = generate_template_tasks("MEP_ELECTRICAL", "REPLACE", 80.0)
    sim = simulate_construction_plan(tasks, monte_carlo_trials=200)
    raw = export_simulation_csv("전기 교체 공사", sim)
    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    assert "전기 교체 공사" in text
    assert "[2] 재료 소요량 집계표" in text

    adv = advise_construction_simulation("MEP_ELECTRICAL", "REPLACE", sim, use_ai=False)
    assert adv["provider"] == "local-engine"
    assert "주공정" in adv["advice"]
