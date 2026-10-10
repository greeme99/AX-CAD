"""S9 cost engine against hand calculations (TC-67 synthetic until the G1 manual quotes arrive),
plus TC-68 (missing price), TC-69 (zero metric), TC-70 (rounding) and the formula sandbox."""

from decimal import Decimal as D

import pytest

from core.quote_engine.cost import compute_quote, round_amount
from core.quote_engine.formula import FormulaError, evaluate

METRICS = {
    "cutting_length_mm": 1240.0,
    "hole_count": 6,
    "punch_hole_count": 0,
    "bend_count": 2,
    "bend_length_mm": 180.0,
    "net_area_mm2": 52000.0,
    "bbox": {"size": [300.0, 200.0]},
    "items": [
        {"handle": "A1", "role": "CUT"},
        {"handle": "A2", "role": "HOLE"},
        {"handle": "B1", "role": "BEND"},
    ],
}
SRC = {"kind": "REVISION", "revision_id": "r" * 32}


def bundle(**ratios):
    return {
        "materials": [
            {
                "material_code": "SS400",
                "thickness_min_mm": None,
                "thickness_max_mm": None,
                "density_g_cm3": "7.85",
                "unit_price_per_kg": "1200",
                "scrap_rate": "0.05",
            }
        ],
        "price_items": [
            {"item_code": "레이저", "item_type": "LABOR", "unit": "h", "unit_price": "35000"},
            {"item_code": "LASER-MC", "item_type": "MACHINE", "unit": "h", "unit_price": "60000"},
            {"item_code": "절곡", "item_type": "LABOR", "unit": "h", "unit_price": "30000"},
        ],
        "process_rules": [
            {
                "rule_code": "LASER",
                "process_code": "LASER",
                "process_name": "레이저 절단",
                "input_metric": "cutting_length_mm",
                "formula_text": "setup_min + cutting_length_mm / speed",
                "params": {"setup_min": 5, "speed": 3000},
                "labor_item_code": "레이저",
                "machine_item_code": "LASER-MC",
            },
            {
                "rule_code": "BEND",
                "process_code": "BEND",
                "process_name": "절곡",
                "input_metric": "bend_count",
                "formula_text": "setup_min + bend_count * per_bend",
                "params": {"setup_min": 3, "per_bend": 0.5},
                "labor_item_code": "절곡",
                "machine_item_code": None,
            },
        ],
        "cost_ratios": {
            "overhead_basis": "MACHINE_HOUR",
            "overhead_rate": None,
            "admin_rate": "0.06",
            "profit_rate": "0.1",
            "vat_rate": "0.1",
            "rounding_rule": "FLOOR",
            "rounding_unit": 10,
            "rounding_scope": "LINE",
            "material_basis": "NET",
        }
        | ratios,
    }


def quote(b=None, metrics=METRICS, **kw):
    args = {"qty": 4, "material_code": "SS400", "thickness_mm": D("3.2")} | kw
    return compute_quote(metrics, b or bundle(), SRC, **args)


def test_tc67_hand_calculated_quote_line_rounding():
    # 52000 mm² x 3.2 t x 7.85 = 1.30624 kg/pc x 1.05 x 4 = 5.486208 kg x 1200 = 6583.45 -> 6580
    # laser 5.41333 min/pc x 4 / 60 h: labour x 35000 = 12631.11 -> 12630, machine x 60000 -> 21650
    # bend 4 min/pc x 4 / 60 h x 30000 = 8000
    r = quote()
    amounts = {(ln["cost_category"], ln["item_code"]): ln["calculated_amount"] for ln in r["lines"]}
    assert amounts == {
        ("MATERIAL", "SS400"): D("6580.00"),
        ("LABOR", "레이저"): D("12630.00"),
        ("OVERHEAD", "LASER-MC"): D("21650.00"),
        ("LABOR", "절곡"): D("8000.00"),
    }
    t = r["totals"]
    assert (t["manufacturing_cost"], t["admin_cost"], t["total_cost"]) == (
        D("48860.00"),
        D("2930.00"),
        D("51790.00"),
    )
    assert (t["profit"], t["supply_amount"], t["vat_amount"], t["total_amount"]) == (
        D("5170.00"),
        D("56960.00"),
        D("5690.00"),
        D("62650.00"),
    )
    assert r["has_errors"] is False
    # every line is traced to its source entities, rule and price (TC-71 at engine level)
    for ln in r["lines"]:
        assert ln["traces"] and all(t["rule_code"] and t["source_ref"] for t in ln["traces"])
    laser = next(ln for ln in r["lines"] if ln["item_code"] == "레이저")
    assert {t["source_ref"] for t in laser["traces"]} == {"A1", "A2"}
    assert (
        laser["traces"][0]["inputs"]["cutting_length_mm"] == "1240.0"
        and laser["traces"][0]["unit_price"] == "35000"
    )


def test_tc70_total_scope_rounds_once_and_ceiling_has_no_residue():
    r = quote(bundle(rounding_scope="TOTAL"))
    t = r["totals"]
    assert (t["supply_amount"], t["vat_amount"], t["total_amount"]) == (
        D("56970.00"),
        D("5690.00"),
        D("62660.00"),
    )
    # 4 min x 4 / 60 h x 30000 is exactly 8000: CEILING must not make it 8010
    r = quote(bundle(rounding_rule="CEILING"))
    assert next(ln for ln in r["lines"] if ln["item_code"] == "절곡")["calculated_amount"] == D(
        "8000.00"
    )
    assert round_amount(D("1234.5"), "HALF_UP", 1) == D("1235.00")
    assert round_amount(D("1234.5"), "FLOOR", 100) == D("1200.00")
    assert round_amount(D(1201), "CEILING", 100) == D("1300.00")


def test_tc68_missing_price_creates_line_but_is_excluded():
    b = bundle()
    b["price_items"] = [p for p in b["price_items"] if p["item_code"] != "LASER-MC"]
    r = quote(b)
    mc = next(ln for ln in r["lines"] if ln["item_code"] == "LASER-MC")
    assert mc["excluded"] and mc["calculated_amount"] is None
    assert r["has_errors"] and any(lg["code"] == "QUOTE_PRICE_MISSING" for lg in r["logs"])
    assert r["totals"]["overhead_cost"] == D("0.00")


def test_tc69_zero_metric_makes_no_line_and_unmapped_metrics_warn():
    r = quote(metrics=METRICS | {"bend_count": 0, "bend_length_mm": 0.0})
    assert not any(ln["item_code"] == "절곡" for ln in r["lines"])
    assert any(lg["code"] == "QUOTE_ZERO_QTY" for lg in r["logs"])
    assert not any(
        lg["code"] == "RULE_NOT_MAPPED" for lg in quote()["logs"]
    )  # laser holes: in cut length
    r = quote(metrics=METRICS | {"punch_hole_count": 3})  # punched holes, no rule prices them
    assert any(lg["code"] == "RULE_NOT_MAPPED" and "펀칭" in lg["message"] for lg in r["logs"])
    b = bundle()
    b["process_rules"] = b["process_rules"][:1]
    r = quote(b)
    assert any(lg["code"] == "RULE_NOT_MAPPED" and "절곡" in lg["message"] for lg in r["logs"])


def test_material_rules():
    assert any(lg["code"] == "QUOTE_MATERIAL_REQUIRED" for lg in quote(material_code=None)["logs"])
    assert any(lg["code"] == "QUOTE_THICKNESS_REQUIRED" for lg in quote(thickness_mm=None)["logs"])
    assert any(
        lg["code"] == "QUOTE_MATERIAL_UNKNOWN" for lg in quote(material_code="AL6061")["logs"]
    )
    # BBOX basis: 300 x 200 x 3.2 x 7.85 / 1e6 = 1.50720 kg/pc
    r = quote(bundle(material_basis="BBOX"))
    mat = next(ln for ln in r["lines"] if ln["cost_category"] == "MATERIAL")
    assert mat["calculated_qty"] == D("6.330240")  # x 1.05 x 4
    # labour-ratio overhead replaces machine lines
    r = quote(bundle(overhead_basis="LABOR_RATIO", overhead_rate="0.5"))
    oh = [ln for ln in r["lines"] if ln["cost_category"] == "OVERHEAD"]
    assert len(oh) == 1 and oh[0]["calculated_amount"] == D(
        "10310.00"
    )  # (12630 + 8000) x 0.5 -> floor 10


def test_outsource_rule_and_3d_source():
    b = bundle()
    b["process_rules"] = [
        {
            "rule_code": "PAINT",
            "process_code": "PAINT",
            "process_name": "도장",
            "input_metric": "surface_area_mm2",
            "formula_text": "surface_area_mm2 / 1000000 * won_per_m2",
            "params": {"won_per_m2": 8000},
            "labor_item_code": None,
            "machine_item_code": None,
        }
    ]
    metrics3d = {
        "bodies": [
            {
                "feature_id": 7,
                "volume_mm3": 6000.0,
                "surface_area_mm2": 2200.0,
                "bbox": {"size": [10.0, 20.0, 30.0]},
            }
        ]
    }
    r = compute_quote(
        metrics3d,
        b,
        {"kind": "DOCUMENT_3D", "document_id": 1},
        qty=10,
        material_code="SS400",
        thickness_mm=None,
    )
    paint = next(ln for ln in r["lines"] if ln["cost_category"] == "OUTSOURCE")
    assert paint["calculated_unit_price"] == D("17.60") and paint["calculated_amount"] == D(
        "170.00"
    )  # 176 -> floor 10
    assert (
        paint["traces"][0]["source_kind"] == "FEATURE" and paint["traces"][0]["source_ref"] == "7"
    )
    mat = next(ln for ln in r["lines"] if ln["cost_category"] == "MATERIAL")
    assert mat["calculated_qty"] == D("0.494550")  # 6000 x 7.85 / 1e6 x 1.05 x 10


@pytest.mark.parametrize(
    "bad",
    [
        "__import__('os').system('id')",
        "a.__class__",
        "a ** 9",
        "open('x')",
        "lambda: 1",
        "a[0]",
        "'s'",
        "True",
    ],
)
def test_formula_sandbox(bad):
    with pytest.raises(FormulaError):
        evaluate(bad, {"a": 1})


def test_formula_errors_become_logs():
    b = bundle()
    b["process_rules"][0] = b["process_rules"][0] | {
        "formula_text": "cutting_length_mm / (speed - 3000)"
    }
    r = quote(b)
    assert any(lg["code"] == "RULE_FORMULA_ERROR" for lg in r["logs"]) and r["has_errors"]
