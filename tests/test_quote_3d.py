"""Track 8 P4-1: machining quote inputs of 3D bodies — stock (block or round bar with the
allowance), removed volume, hole totals from the hole features — and their traces."""

import math
from decimal import Decimal as D

import pytest

from core.quote_engine.cost import compute_quote, stock
from tests.test_api_pad import post
from tests.test_api_quote import estimator  # noqa: F401 - fixture
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


def test_default_machining_rules_price_a_tapped_block(client, world, estimator):  # noqa: F811
    """P4-2: the default rules go into a draft (existing codes kept) and price the machining."""
    from tests.test_api_master import FULL

    admin = world.h["admin"]
    vid = client.post(
        "/api/master-versions",
        json={"version_code": "M1", "effective_from": "2020-01-01"},
        headers=admin,
    ).json()["data"]["version_id"]
    mine = {
        **FULL,
        "price_items": [
            *FULL["price_items"],
            {"item_code": "M-LAB", "item_type": "LABOR", "unit": "h", "unit_price": "50000"},
        ],
    }
    assert client.put(f"/api/master-versions/{vid}", json=mine, headers=admin).status_code == 200
    r = client.post(f"/api/master-versions/{vid}/machining-defaults", headers=admin)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert "M-LAB" not in d["added"] and {"M-DRILL", "M-TAP", "M-MILL", "M-TURN"} <= set(d["added"])
    assert (
        next(p for p in d["price_items"] if p["item_code"] == "M-LAB")["unit_price"] == "50000.00"
    )
    again = client.post(f"/api/master-versions/{vid}/machining-defaults", headers=admin)
    assert again.json()["data"]["added"] == []
    assert (
        client.post(
            f"/api/master-versions/{vid}/machining-defaults", headers=world.h["designer"]
        ).status_code
        == 403
    )
    assert client.post(f"/api/master-versions/{vid}/activate", headers=admin).status_code == 200
    box = {"kind": "BOX", "length": 40, "width": 30, "height": 10}
    b = fid(post(client, world, world.doc, "PRIMITIVE", box))["feature_id"]
    spec = {"size": "M6", "kind": "TAPPED", "thread_depth": 6}
    hole = {"target_feature_id": b, "positions": [[10, 10], [30, 20]], "depth": 8, "spec": spec}
    h = fid(post(client, world, world.doc, "HOLE", hole))["feature_id"]
    q = client.post(
        "/api/quotes",
        json={"document_id": world.doc, "qty": 2, "material_code": "SS400"},
        headers=estimator,
    ).json()["data"]
    lines = {ln["item_name"]: ln for ln in q["lines"]}
    # tap: 2 x 0.3 min + 2 x 6 mm x 2 / 200 mm/min = 0.72 min per piece, 2 pieces, labour 50,000/h
    tap = lines["탭 노무"]
    assert D(tap["calculated_qty"]) == pytest.approx(D("0.72") * 2 / 60, abs=D("1e-6"))
    assert tap["traces"][0]["sources"] == [str(h)]
    assert {"드릴 노무", "드릴 기계경비", "밀링 노무", "밀링 기계경비"} <= set(lines)
    assert "선반 노무" not in lines


def test_per_part_material_and_quantity(client, world, estimator):  # noqa: F811
    """P4-3: each body with its own material and quantity; unknown parts are refused."""
    from tests.test_api_master import FULL

    admin = world.h["admin"]
    vid = client.post(
        "/api/master-versions",
        json={"version_code": "M2", "effective_from": "2020-01-01"},
        headers=admin,
    ).json()["data"]["version_id"]
    client.put(f"/api/master-versions/{vid}", json=FULL, headers=admin)
    assert client.post(f"/api/master-versions/{vid}/activate", headers=admin).status_code == 200
    box = {"kind": "BOX", "length": 40, "width": 30, "height": 10}
    a = fid(post(client, world, world.doc, "PRIMITIVE", box))["feature_id"]
    b = fid(post(client, world, world.doc, "PRIMITIVE", box | {"origin": [0, 50, 0]}))["feature_id"]
    parts = [{"feature_id": a, "material_code": "SS400", "qty": 3}, {"feature_id": b, "qty": 1}]
    body = {"document_id": world.doc, "parts": parts}
    q = client.post("/api/quotes", json=body, headers=estimator).json()["data"]
    mats = {ln["item_name"]: D(ln["calculated_qty"]) for ln in q["lines"] if ln["unit"] == "kg"}
    one = D(12000) * D("7.85") / 1000000 * D("1.05")
    assert mats == {"Box001 · SS400 소재": pytest.approx(one * 3, abs=D("1e-6"))}
    # the second body has no material: an error naming it, not a silent zero
    assert any(
        lg["code"] == "QUOTE_MATERIAL_REQUIRED" and "Box002" in lg["message"] for lg in q["logs"]
    )
    assert q["inputs"]["parts"][0] == parts[0] and q["inputs"]["qty"] == 1
    r = client.post(
        "/api/quotes",
        json={"document_id": world.doc, "qty": 1, "parts": [{"feature_id": 999999}]},
        headers=estimator,
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "QUOTE_PART_UNKNOWN"
