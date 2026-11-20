"""CPM Schedule, Material BOM, Korean Standard Cost Statement (Decimal), S-Curve,
3-Scenario Comparison & Monte Carlo (P50/P80/P95) Risk Simulation Engine."""

from __future__ import annotations

import csv
import io
import math
from collections import defaultdict, deque
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import numpy as np

from . import ConstructionSimError, CostRates

D = Decimal
WON = D("1")


def _won(val: Decimal) -> int:
    return int(val.quantize(WON, rounding=ROUND_HALF_UP))


def _topo_sort(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(t["task_id"]): t for t in tasks}
    if len(by_id) != len(tasks):
        raise ConstructionSimError("CONSIM_DUP_TASK", "중복된 task_id가 있습니다")

    indeg: dict[str, int] = {tid: 0 for tid in by_id}
    children: dict[str, list[str]] = defaultdict(list)
    for t in tasks:
        tid = str(t["task_id"])
        for p in t.get("predecessors") or []:
            pid = str(p)
            if pid not in by_id:
                raise ConstructionSimError("CONSIM_BAD_PRED", f"존재하지 않는 선행공정 ID: {pid}")
            if pid == tid:
                raise ConstructionSimError(
                    "CONSIM_CYCLE", f"자기 자신을 선행공정으로 지정할 수 없습니다: {tid}"
                )
            children[pid].append(tid)
            indeg[tid] += 1

    q = deque([tid for tid, deg in indeg.items() if deg == 0])
    order: list[dict[str, Any]] = []
    while q:
        u = q.popleft()
        order.append(by_id[u])
        for v in children[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                q.append(v)

    if len(order) != len(tasks):
        raise ConstructionSimError("CONSIM_CYCLE", "선후행 공정에 순환 의존성(Cycle)이 있습니다")
    return order


def _run_cpm(
    tasks: list[dict[str, Any]], duration_mult: float = 1.0
) -> tuple[list[dict[str, Any]], float, list[str]]:
    """Run Critical Path Method (Forward + Backward Pass) and return scheduled tasks."""
    ordered = _topo_sort(tasks)
    by_id = {str(t["task_id"]): t for t in ordered}
    children: dict[str, list[str]] = defaultdict(list)
    for t in ordered:
        tid = str(t["task_id"])
        for p in t.get("predecessors") or []:
            children[str(p)].append(tid)

    es: dict[str, float] = {}
    ef: dict[str, float] = {}
    dur: dict[str, float] = {}

    for t in ordered:
        tid = str(t["task_id"])
        d_raw = max(0.5, float(t.get("duration_days", 1.0)) * duration_mult)
        dur[tid] = round(d_raw, 2)
        preds = [str(p) for p in (t.get("predecessors") or [])]
        es[tid] = max((ef[p] for p in preds), default=0.0)
        ef[tid] = round(es[tid] + dur[tid], 2)

    proj_dur = max(ef.values(), default=0.0)

    ls: dict[str, float] = {}
    lf: dict[str, float] = {}
    for t in reversed(ordered):
        tid = str(t["task_id"])
        succs = children[tid]
        lf[tid] = min((ls[c] for c in succs), default=proj_dur)
        ls[tid] = round(lf[tid] - dur[tid], 2)

    scheduled: list[dict[str, Any]] = []
    critical_path: list[str] = []
    for t in ordered:
        tid = str(t["task_id"])
        tf = round(max(0.0, ls[tid] - es[tid]), 2)
        is_crit = tf <= 1e-3
        if is_crit:
            critical_path.append(tid)
        scheduled.append(
            {
                **by_id[tid],
                "sim_duration_days": dur[tid],
                "early_start": round(es[tid], 2),
                "early_finish": round(ef[tid], 2),
                "late_start": round(ls[tid], 2),
                "late_finish": round(lf[tid], 2),
                "total_float": tf,
                "is_critical": is_crit,
            }
        )
    return scheduled, round(proj_dur, 2), critical_path


def _compute_cost_statement(
    tasks: list[dict[str, Any]],
    rates: CostRates,
    *,
    mat_mult: float = 1.0,
    labor_mult: float = 1.0,
    equip_mult: float = 1.0,
) -> tuple[dict[str, int], list[dict[str, Any]], list[dict[str, Any]]]:
    """Compute exact Decimal Korean Construction Cost Statement, per-task costs, and rolled-up BOM."""
    direct_mat = D("0")
    direct_labor = D("0")
    direct_equip = D("0")
    bom_agg: dict[str, dict[str, Any]] = {}
    task_costs: list[dict[str, Any]] = []

    m_mult = D(str(mat_mult))
    l_mult = D(str(labor_mult))
    e_mult = D(str(equip_mult))

    for t in tasks:
        t_mat = D("0")
        for m in t.get("materials") or []:
            qty = D(str(m.get("qty", 0.0)))
            loss = D(str(m.get("loss_rate", 0.0)))
            up = D(str(m.get("unit_price_krw", 0.0)))
            gross_qty = qty * (D("1") + loss)
            line_amt = gross_qty * up * m_mult
            t_mat += line_amt

            code = str(m.get("code", m.get("name", "MAT")))
            if code not in bom_agg:
                bom_agg[code] = {
                    "code": code,
                    "name": str(m.get("name", code)),
                    "unit": str(m.get("unit", "EA")),
                    "net_qty": D("0"),
                    "gross_qty": D("0"),
                    "unit_price_krw": _won(up * m_mult),
                    "total_krw": D("0"),
                }
            bom_agg[code]["net_qty"] += qty
            bom_agg[code]["gross_qty"] += gross_qty
            bom_agg[code]["total_krw"] += line_amt

        t_lab = D(str(t.get("labor_cost_krw", 0.0))) * l_mult
        t_eq = D(str(t.get("equipment_cost_krw", 0.0))) * e_mult
        direct_mat += t_mat
        direct_labor += t_lab
        direct_equip += t_eq
        task_costs.append(
            {
                "task_id": str(t["task_id"]),
                "material_krw": _won(t_mat),
                "labor_krw": _won(t_lab),
                "equipment_krw": _won(t_eq),
                "direct_total_krw": _won(t_mat + t_lab + t_eq),
            }
        )

    # Korean Standard Construction Cost Statement (공사원가계산서)
    indirect_labor = direct_labor * D(str(rates.indirect_labor_rate))
    total_labor = direct_labor + indirect_labor
    indirect_expense = (direct_mat + direct_labor) * D(str(rates.indirect_expense_rate))
    total_expense = direct_equip + indirect_expense
    net_construction_cost = direct_mat + total_labor + total_expense
    general_admin = net_construction_cost * D(str(rates.general_admin_rate))
    profit = (total_labor + total_expense + general_admin) * D(str(rates.profit_rate))
    supply_amount = net_construction_cost + general_admin + profit
    vat = supply_amount * D(str(rates.vat_rate))
    grand_total = supply_amount + vat

    statement = {
        "direct_material_krw": _won(direct_mat),
        "direct_labor_krw": _won(direct_labor),
        "indirect_labor_krw": _won(indirect_labor),
        "direct_equipment_krw": _won(direct_equip),
        "indirect_expense_krw": _won(indirect_expense),
        "net_construction_cost_krw": _won(net_construction_cost),
        "general_admin_krw": _won(general_admin),
        "profit_krw": _won(profit),
        "supply_amount_krw": _won(supply_amount),
        "vat_krw": _won(vat),
        "grand_total_krw": _won(grand_total),
    }

    bom_list = [
        {
            "code": v["code"],
            "name": v["name"],
            "unit": v["unit"],
            "net_qty": round(float(v["net_qty"]), 3),
            "gross_qty": round(float(v["gross_qty"]), 3),
            "unit_price_krw": v["unit_price_krw"],
            "total_krw": _won(v["total_krw"]),
        }
        for v in bom_agg.values()
    ]
    bom_list.sort(key=lambda x: x["total_krw"], reverse=True)
    return statement, task_costs, bom_list


def _build_scurve(
    scheduled: list[dict[str, Any]],
    task_cost_map: dict[str, int],
    project_duration: float,
    grand_total_krw: int,
) -> list[dict[str, Any]]:
    """Build daily S-curve cashflow (scaled proportionally so final cumulative equals grand_total_krw)."""
    n_days = max(1, math.ceil(project_duration))
    daily = np.zeros(n_days, dtype=np.float64)
    direct_sum = sum(task_cost_map.values())
    overhead_scale = (grand_total_krw / float(direct_sum)) if direct_sum > 0 else 1.0

    for t in scheduled:
        tid = str(t["task_id"])
        t_cost = task_cost_map.get(tid, 0) * overhead_scale
        if t_cost <= 0:
            continue
        s0 = float(t["early_start"])
        s1 = float(t["early_finish"])
        span = max(0.01, s1 - s0)
        for day_idx in range(n_days):
            ov = max(0.0, min(s1, day_idx + 1.0) - max(s0, float(day_idx)))
            if ov > 0:
                daily[day_idx] += t_cost * (ov / span)

    cum = 0.0
    curve: list[dict[str, Any]] = []
    for day_idx in range(n_days):
        d_val = float(daily[day_idx])
        cum += d_val
        pct = min(100.0, (cum / max(1.0, float(grand_total_krw))) * 100.0)
        curve.append(
            {
                "day": day_idx + 1,
                "daily_cost_krw": round(d_val),
                "cumulative_cost_krw": round(cum),
                "progress_pct": round(pct, 2),
            }
        )
    if curve:
        curve[-1]["cumulative_cost_krw"] = grand_total_krw
        curve[-1]["progress_pct"] = 100.0
    return curve


def _run_monte_carlo(
    ordered_tasks: list[dict[str, Any]],
    task_cost_map: dict[str, int],
    grand_total_krw: int,
    *,
    trials: int = 1000,
    seed: int = 42,
) -> dict[str, Any]:
    """Run vectorized NumPy triangular Monte Carlo risk simulation for Cost & Duration (P50/P80/P95)."""
    rng = np.random.default_rng(seed)
    n_tasks = len(ordered_tasks)
    if n_tasks == 0:
        return {
            "trials": 0,
            "cost_p50_krw": 0,
            "cost_p80_krw": 0,
            "cost_p95_krw": 0,
            "duration_p50_days": 0.0,
            "duration_p80_days": 0.0,
            "duration_p95_days": 0.0,
            "top_risk_drivers": [],
        }

    id_to_idx = {str(t["task_id"]): i for i, t in enumerate(ordered_tasks)}
    pred_indices = [
        [id_to_idx[str(p)] for p in (t.get("predecessors") or [])] for t in ordered_tasks
    ]

    direct_sum = max(1.0, float(sum(task_cost_map.values())))
    overhead_scale = grand_total_krw / direct_sum

    cost_samples = np.zeros((trials, n_tasks), dtype=np.float64)
    ef_samples = np.zeros((trials, n_tasks), dtype=np.float64)

    for i, t in enumerate(ordered_tasks):
        lo = max(0.5, float(t.get("risk_low", 0.90)))
        hi = max(1.01, float(t.get("risk_high", 1.25)))
        mode_v = 1.0
        if lo >= mode_v:
            lo = 0.90
        c_mult = rng.triangular(lo, mode_v, hi, size=trials)
        d_mult = rng.triangular(lo, mode_v, hi, size=trials)

        base_cost = task_cost_map.get(str(t["task_id"]), 0) * overhead_scale
        cost_samples[:, i] = base_cost * c_mult

        base_dur = max(0.5, float(t.get("duration_days", 1.0))) * d_mult
        if pred_indices[i]:
            es_i = np.max(ef_samples[:, pred_indices[i]], axis=1)
        else:
            es_i = np.zeros(trials, dtype=np.float64)
        ef_samples[:, i] = es_i + base_dur

    total_costs = np.sum(cost_samples, axis=1)
    total_durs = np.max(ef_samples, axis=1)

    # Identify top 3 cost-variance drivers
    stds = np.std(cost_samples, axis=0)
    top_idx = np.argsort(stds)[::-1][:3]
    drivers = [
        {
            "task_id": str(ordered_tasks[int(i)]["task_id"]),
            "name": str(ordered_tasks[int(i)]["name"]),
            "cost_std_krw": round(float(stds[int(i)])),
        }
        for i in top_idx
        if stds[int(i)] > 0
    ]

    return {
        "trials": trials,
        "cost_p50_krw": round(float(np.percentile(total_costs, 50))),
        "cost_p80_krw": round(float(np.percentile(total_costs, 80))),
        "cost_p95_krw": round(float(np.percentile(total_costs, 95))),
        "duration_p50_days": round(float(np.percentile(total_durs, 50)), 1),
        "duration_p80_days": round(float(np.percentile(total_durs, 80)), 1),
        "duration_p95_days": round(float(np.percentile(total_durs, 95)), 1),
        "top_risk_drivers": drivers,
    }


def simulate_construction_plan(
    tasks: list[dict[str, Any]],
    *,
    rates: CostRates | None = None,
    monte_carlo_trials: int = 1000,
    seed: int = 42,
) -> dict[str, Any]:
    """Run full CPM schedule, Material BOM, Korean Cost Statement, S-Curve, 3 Scenarios & Monte Carlo."""
    if not tasks:
        raise ConstructionSimError("CONSIM_EMPTY_TASKS", "최소 1개 이상의 공정이 필요합니다")
    r = rates or CostRates()

    # 1. Baseline STANDARD scenario
    scheduled, proj_dur, crit_path = _run_cpm(tasks, duration_mult=1.0)
    stmt, t_costs, bom = _compute_cost_statement(scheduled, r)
    t_cost_map = {c["task_id"]: c["direct_total_krw"] for c in t_costs}
    t_cost_detail = {c["task_id"]: c for c in t_costs}

    for st in scheduled:
        st.update(t_cost_detail[str(st["task_id"])])

    scurve = _build_scurve(scheduled, t_cost_map, proj_dur, stmt["grand_total_krw"])

    # 2. Multi-scenario comparison (STANDARD vs CRASH_NIGHT vs VALUE_ENGINEER)
    _, dur_crash, _ = _run_cpm(tasks, duration_mult=0.75)
    stmt_crash, _, _ = _compute_cost_statement(
        tasks, r, mat_mult=1.0, labor_mult=1.35, equip_mult=1.15
    )

    _, dur_ve, _ = _run_cpm(tasks, duration_mult=1.10)
    stmt_ve, _, _ = _compute_cost_statement(
        tasks, r, mat_mult=0.88, labor_mult=1.0, equip_mult=0.95
    )

    scenarios = [
        {
            "code": "STANDARD",
            "name": "표준 공정 (Standard)",
            "duration_days": proj_dur,
            "grand_total_krw": stmt["grand_total_krw"],
            "delta_cost_krw": 0,
            "delta_days": 0.0,
            "note": "표준품셈 기준 주간 정상 작업",
        },
        {
            "code": "CRASH_NIGHT",
            "name": "돌관 · 야간 단축 (Crash)",
            "duration_days": dur_crash,
            "grand_total_krw": stmt_crash["grand_total_krw"],
            "delta_cost_krw": stmt_crash["grand_total_krw"] - stmt["grand_total_krw"],
            "delta_days": round(dur_crash - proj_dur, 1),
            "note": "공기 25% 단축 · 야간/휴일 노무 35% 및 장비 15% 할증",
        },
        {
            "code": "VALUE_ENGINEER",
            "name": "자재 절감 · VE (Value Eng.)",
            "duration_days": dur_ve,
            "grand_total_krw": stmt_ve["grand_total_krw"],
            "delta_cost_krw": stmt_ve["grand_total_krw"] - stmt["grand_total_krw"],
            "delta_days": round(dur_ve - proj_dur, 1),
            "note": "대체 자재/직구로 재료비 12% 절감 · 조달 기간 10% 여유",
        },
    ]

    # 3. Monte Carlo Risk Simulation (P50/P80/P95)
    mc = _run_monte_carlo(
        scheduled, t_cost_map, stmt["grand_total_krw"], trials=monte_carlo_trials, seed=seed
    )

    return {
        "project_duration_days": proj_dur,
        "critical_path": crit_path,
        "tasks": scheduled,
        "material_bom": bom,
        "cost_statement": stmt,
        "scurve": scurve,
        "scenarios": scenarios,
        "monte_carlo": mc,
    }


def export_simulation_csv(plan_name: str, sim: dict[str, Any]) -> bytes:
    """Export Excel-ready UTF-8 BOM CSV with WBS Schedule, Cost Statement, and Material BOM."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["AX-CAD 공사 공정 · 비용 시뮬레이션 내역서", plan_name])
    w.writerow(
        [
            "총 공기(일)",
            sim["project_duration_days"],
            "총공사비(VAT포함 KRW)",
            sim["cost_statement"]["grand_total_krw"],
        ]
    )
    w.writerow([])
    w.writerow(["[1] 공정표 (CPM)"])
    w.writerow(
        [
            "WBS",
            "Task ID",
            "공정명",
            "단계",
            "기간(일)",
            "시작일",
            "종료일",
            "여유(일)",
            "주공정",
            "재료비",
            "노무비",
            "장비경비",
            "직접비 합계",
        ]
    )
    for t in sim["tasks"]:
        w.writerow(
            [
                t["wbs"],
                t["task_id"],
                t["name"],
                t["phase"],
                t["sim_duration_days"],
                t["early_start"],
                t["early_finish"],
                t["total_float"],
                "Y" if t["is_critical"] else "N",
                t["material_krw"],
                t["labor_krw"],
                t["equipment_krw"],
                t["direct_total_krw"],
            ]
        )
    w.writerow([])
    w.writerow(["[2] 재료 소요량 집계표 (BOM)"])
    w.writerow(["코드", "품명", "단위", "정미수량", "할증포함수량", "단가(KRW)", "금액(KRW)"])
    for m in sim["material_bom"]:
        w.writerow(
            [
                m["code"],
                m["name"],
                m["unit"],
                m["net_qty"],
                m["gross_qty"],
                m["unit_price_krw"],
                m["total_krw"],
            ]
        )
    return ("\ufeff" + buf.getvalue()).encode("utf-8")
