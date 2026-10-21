"""Track 6-3/6-4 construction cost engine (pure): work items over QTO measures, 공사원가계산서
ratio lines, adjust factors, scenarios and price validity."""

from datetime import date
from decimal import Decimal as D

from core.quote_engine.construction import compute_construction
from core.quote_engine.cost import summarize

RATIOS = {
    "overhead_basis": "MACHINE_HOUR",
    "admin_rate": "0.06",
    "profit_rate": "0.1",
    "vat_rate": "0.1",
    "rounding_rule": "FLOOR",
    "rounding_unit": 10,
    "rounding_scope": "TOTAL",
    "indirect_labor_rate": "0.1",
    "indirect_cost_rate": "0.05",
}


def work(code, f, mat, lab, exp, loss=None, **kw):
    return {
        "item_code": code,
        "item_name": code,
        "spec": None,
        "work_category": "건축",
        "unit": "m2",
        "qty_formula": f,
        "loss_rate": loss,
        "material_price": mat,
        "labor_price": lab,
        "expense_price": exp,
        "valid_until": None,
        **kw,
    }


BUNDLE = {
    "cost_ratios": RATIOS,
    "work_items": [
        work("A-PAINT", "wall_length_m * height_m * 2", "3000", "5000", "0", "0.05"),
        work("D-WALL", "demo_wall_length_m * height_m", "0", "20000", "5000"),
        work("E-LIGHT", "fixture_count", "30000", "10000", "0"),
        work("M-DUCT", "duct_length_m", "1000", "1000", "0"),
    ],
    "adjust_factors": [
        {"factor_type": "REGION", "code": "SEOUL", "factor": "1.05"},
        {"factor_type": "ESCALATION", "code": "2027Q1", "factor": "1.02"},
    ],
}
QTO = {
    "measures": {
        "wall_length_m": 10,
        "demo_wall_length_m": 4,
        "room_area_m2": 20,
        "fixture_count": 2,
        "duct_length_m": 0,
    },
    "items": [
        {"role": "WALL", "status": "NEW", "handles": ["A1"]},
        {"role": "WALL", "status": "KEEP", "handles": ["A2"]},
        {"role": "WALL", "status": "DEMO", "handles": ["B9"]},
        {"role": "FIXTURE", "status": "NEW", "handles": ["F1", "F2"]},
    ],
}
SRC = {"kind": "CONSTRUCTION", "revision_id": "cur", "base_revision_id": "base"}


def run(bundle=BUNDLE, height=D("2.5"), **kw):
    return compute_construction(QTO, bundle, SRC, height_m=height, **kw)


def amounts(r):
    return {(ln["item_code"], ln["cost_category"]): ln["calculated_amount"] for ln in r["lines"]}


def test_work_items_and_cost_statement():
    r = run()
    a = amounts(r)
    assert a[("A-PAINT", "MATERIAL")] == D("157500.00")  # 50 m2 × 1.05 loss × 3000
    assert a[("A-PAINT", "LABOR")] == D("250000.00")
    assert ("A-PAINT", "OVERHEAD") not in a  # price 0: no such cost
    assert a[("D-WALL", "LABOR")] == D("200000.00") and a[("D-WALL", "OVERHEAD")] == D("50000.00")
    assert a[("E-LIGHT", "MATERIAL")] == D("60000.00")
    assert a[("INDIRECT_LABOR", "LABOR")] == D("47000.00")  # 470,000 × 10 %
    assert a[("INDIRECT_COST", "OVERHEAD")] == D("36725.00")  # (217,500 + 517,000) × 5 %
    t = r["totals"]
    assert t["material_cost"] == D("217500.00") and t["labor_cost"] == D("517000.00")
    assert t["manufacturing_cost"] == D("821225.00")
    assert t["admin_cost"] == D("49273.50")
    assert t["profit"] == D("65299.85")  # (노무비 + 경비 + 일반관리비) × 10 %, material excluded
    assert t["supply_amount"] == D("935790.00") and t["total_amount"] == D("1029360.00")
    assert not r["has_errors"]
    assert any(lg["code"] == "QTO_NOT_APPLICABLE" for lg in r["logs"])  # M-DUCT: no ducts


def test_traces_point_at_qto_items_of_the_right_revision():
    lines = {(ln["item_code"], ln["cost_category"]): ln for ln in run()["lines"]}
    paint = lines[("A-PAINT", "MATERIAL")]["traces"]
    assert [(t["revision_id"], t["sources"]) for t in paint] == [("cur", ["A1", "A2"])]
    assert paint[0]["inputs"]["loss_rate"] == "0.05" and paint[0]["rule_code"] == "WORK:A-PAINT"
    demo = lines[("D-WALL", "LABOR")]["traces"]
    assert [(t["revision_id"], t["sources"]) for t in demo] == [("base", ["B9"])]
    assert lines[("INDIRECT_LABOR", "LABOR")]["traces"][0]["source_kind"] == "REVISION"


def test_adjust_factors_scale_prices_not_ratios():
    r = run(adjust={"region": "SEOUL", "escalation": "2027Q1"})
    lines = {(ln["item_code"], ln["cost_category"]): ln for ln in r["lines"]}
    paint = lines[("A-PAINT", "MATERIAL")]
    assert paint["calculated_unit_price"] == D("3213.00")  # 3000 × 1.05 × 1.02
    assert paint["traces"][0]["unit_price"] == "3000"  # the master price stays traceable
    assert paint["traces"][0]["inputs"]["region_factor"] == "1.05"
    il = lines[("INDIRECT_LABOR", "LABOR")]
    assert il["calculated_unit_price"] == D("0.10")  # a rate is never multiplied
    base = run()["totals"]["labor_cost"]
    assert r["totals"]["labor_cost"] == (base * D("1.071")).quantize(D("0.01"))
    bad = run(adjust={"region": "BUSAN"})
    assert any(lg["code"] == "QUOTE_FACTOR_UNKNOWN" for lg in bad["logs"]) and bad["has_errors"]


def test_scenario_factors_by_category_and_quantity():
    base = run()["totals"]
    r = run(adjust={"scenario": {"material": "1.2", "qty": "1.1"}})["totals"]
    assert r["material_cost"] == (base["material_cost"] * D("1.32")).quantize(D("0.01"))
    assert r["labor_cost"] == (base["labor_cost"] * D("1.1")).quantize(D("0.01"))


def test_validity_and_missing_inputs():
    old = BUNDLE | {
        "work_items": [work("A-PAINT", "wall_length_m", "1", "1", "0", valid_until="2026-01-31")]
    }
    r = run(old, on=date(2026, 10, 21))
    assert [lg["code"] for lg in r["logs"]] == ["QUOTE_PRICE_EXPIRED"] and not r["has_errors"]
    assert run(old, on=date(2026, 1, 31))["logs"] == []  # valid through that day
    r = run(height=None)
    assert any(lg["code"] == "QUOTE_HEIGHT_REQUIRED" for lg in r["logs"]) and r["has_errors"]
    r = run(BUNDLE | {"work_items": [work("X", "wall_length_m", None, "1", "0")]})
    assert any(lg["code"] == "QUOTE_PRICE_MISSING" for lg in r["logs"])


def test_part_quote_profit_basis_unchanged():
    lines = [{"cost_category": "MATERIAL", "excluded": False, "calculated_amount": "1000"}]
    ratios = RATIOS | {"rounding_unit": 1}
    assert summarize(lines, ratios)["profit"] == D("106.00")  # (1000 + 60) × 10 %
    assert summarize(lines, ratios, construction=True)["profit"] == D("6.00")  # admin only
