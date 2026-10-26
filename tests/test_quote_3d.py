"""Track 8 P4-1: machining quote inputs of 3D bodies — stock (block or round bar with the
allowance), removed volume, hole totals from the hole features — and their traces."""

import math
from decimal import Decimal as D

import pytest

from core.quote_engine.cost import compute_quote, stock
from tests.test_api_pad import post
from tests.test_p2_parts import fid, shaft

RATIOS = {
    "overhead_basis": "MACHINE_HOUR",
    "admin_rate": "0",
    "profit_rate": "0",
    "vat_rate": "0",
    "rounding_rule": "HALF_UP",
    "rounding_unit": 1,
    "rounding_scope": "TOTAL",
    "material_basis": "BBOX",
    "stock_allowance_mm": "2",
}
MATERIAL = {
    "material_code": "SS400",
    "density_g_cm3": "7.85",
    "unit_price_per_kg": "1000",
    "scrap_rate": "0",
    "thickness_min_mm": None,
    "thickness_max_mm": None,
}


def rule(code, metric, formula):
    return {
        "rule_code": code,
        "process_code": code,
        "process_name": code,
        "input_metric": metric,
        "formula_text": formula,
        "params": {},
        "labor_item_code": "LAB",
        "machine_item_code": None,
    }


BUNDLE = {
    "cost_ratios": RATIOS,
    "materials": [MATERIAL],
    "price_items": [{"item_code": "LAB", "item_type": "LABOR", "unit": "h", "unit_price": "60000"}],
    "process_rules": [
        rule("DRILL", "drill_length_mm", "drill_length_mm / 10"),  # 10 mm per minute
        rule("TAP", "tap_count", "tap_count * 0.5"),
        rule("MILL", "mill_removed_volume_mm3", "mill_removed_volume_mm3 / 10000"),
        rule("TURN", "turn_removed_volume_mm3", "turn_removed_volume_mm3 / 20000"),
    ],
}
SRC = {"kind": "DOCUMENT_3D", "document_id": 1}


def plate(holes=()):
    return {
        "feature_id": 1,
        "name": "Box001",
        "volume_mm3": 12000,
        "surface_area_mm2": 3400,
        "bbox": {"size": [40, 30, 10]},
        "holes": list(holes),
        "turn_axis": None,
    }


TAPS = {
    "feature_id": 7,
    "count": 2,
    "diameter": 5,
    "drill_length": 16,
    "depth": 8,
    "thread_length": 6,
    "threaded": True,
    "head": None,
}


def test_stock_block_and_round_bar():
    assert stock(plate(), D(2)) == {"stock_volume_mm3": D(44 * 34 * 14)}
    bar = stock({"bbox": {"size": [130, 30, 30]}, "turn_axis": "X"}, D(2))
    assert bar["stock_diameter_mm"] == 34 and bar["stock_length_mm"] == 134
    assert float(bar["stock_volume_mm3"]) == pytest.approx(math.pi * 34**2 / 4 * 134, rel=1e-12)


def test_machining_lines_trace_to_their_features():
    q = compute_quote(
        {"bodies": [plate([TAPS])]}, BUNDLE, SRC, qty=1, material_code="SS400", thickness_mm=None
    )
    by = {ln["item_code"]: ln for ln in q["lines"]}
    stock_vol = D(44 * 34 * 14)
    assert by["SS400"]["calculated_qty"] == pytest.approx(
        stock_vol * D("7.85") / 1000000, abs=D("1e-6")
    )  # stock, not net
    assert by["SS400"]["traces"][0]["inputs"]["stock_allowance_mm"] == "2"
    hours = {ln["item_name"]: ln["calculated_qty"] for ln in q["lines"] if ln["unit"] == "h"}
    want = {
        "DRILL 노무": D(16) / 10 / 60,
        "TAP 노무": D(1) / 60,
        "MILL 노무": (stock_vol - 12000) / 10000 / 60,
    }
    assert hours.keys() == want.keys()
    assert all(hours[k] == pytest.approx(want[k], abs=D("1e-6")) for k in want)  # stored to 1e-6
    traces = {ln["item_name"]: ln["traces"][0] for ln in q["lines"]}
    assert traces["DRILL 노무"]["sources"] == ["7"]  # the hole feature, not the body
    assert traces["MILL 노무"]["sources"] == ["1"]
    # the turning rule does not apply to a milled block: skipped quietly, not warned
    assert not [lg for lg in q["logs"] if lg["severity"] != "INFO"]


def test_parts_are_priced_one_by_one():
    bar = {**plate(), "feature_id": 2, "name": "Revolution002", "turn_axis": "Z"}
    q = compute_quote(
        {"bodies": [plate(), bar]}, BUNDLE, SRC, qty=3, material_code="SS400", thickness_mm=None
    )
    names = [ln["item_name"] for ln in q["lines"]]
    assert "Box001 · MILL 노무" in names and "Revolution002 · TURN 노무" in names
    assert "Box001 · TURN 노무" not in names


def test_bodies_carry_holes_and_turning_axis(client, world):
    box = {"kind": "BOX", "length": 40, "width": 30, "height": 10}
    b = fid(post(client, world, world.doc, "PRIMITIVE", box))["feature_id"]
    spec = {"size": "M6", "kind": "TAPPED", "thread_depth": 6}
    hole = {"target_feature_id": b, "positions": [[10, 10], [30, 20]], "depth": 8, "spec": spec}
    h = fid(post(client, world, world.doc, "HOLE", hole))["feature_id"]
    shaft(client, world)
    r = client.get(f"/api/documents/{world.doc}/metrics3d", headers=world.h["designer"])
    bodies = {x["feature_id"]: x for x in r.json()["data"]["bodies"]}
    (holes,) = [x["holes"] for x in bodies.values() if x["holes"]]
    assert holes == [
        {
            "feature_id": h,
            "count": 2,
            "diameter": 5.0,
            "drill_length": pytest.approx(16),
            "depth": 8,
            "thread_length": 6,
            "threaded": True,
            "head": None,
        }
    ]
    assert sorted(str(x["turn_axis"]) for x in bodies.values()) == ["None", "X"]
