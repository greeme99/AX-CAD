"""FN-20 quote validation rules, one test per check."""

from decimal import Decimal as D

from core.quote_engine.cost import summarize
from core.quote_engine.validate import validate_quote
from tests.test_cost import bundle, quote

RATIOS = bundle()["cost_ratios"]


def state(**override):
    r = quote()
    lines = [
        {
            **ln,
            "override_qty": None,
            "override_unit_price": None,
            "override_amount": None,
            "source_key": [s for t in ln["traces"] for s in t["sources"]],
        }
        for ln in r["lines"]
    ]
    header = {"source_kind": "REVISION", "revision_id": "r" * 32, **r["totals"], **override}
    return header, lines, r["logs"]


def codes(header, lines, logs=(), latest="r" * 32):
    return {lg["code"] for lg in validate_quote(header, lines, RATIOS, list(logs), latest)}


def test_clean_quote_has_no_findings():
    h, lines, logs = state()
    assert codes(h, lines, logs) == set()


def test_each_rule():
    h, lines, _ = state()
    lines[1] = lines[1] | {"excluded": True, "calculated_amount": None}
    assert "QUOTE_PRICE_MISSING" in codes(h, lines)
    lines[1] = lines[1] | {"override_amount": D(12000)}  # a manual amount settles it
    assert "QUOTE_PRICE_MISSING" not in codes(h, lines)

    h, lines, _ = state()
    assert "QUOTE_MATERIAL_REQUIRED" in codes(
        h, [ln for ln in lines if ln["cost_category"] != "MATERIAL"]
    )

    h, lines, _ = state()
    lines[0] = lines[0] | {"override_qty": D(0)}
    assert "QUOTE_QTY_INVALID" in codes(h, lines)

    h, lines, _ = state()
    assert "QUOTE_DUPLICATE_LINE" in codes(h, [*lines, lines[1] | {"line_no": 99}])

    h, lines, _ = state()
    assert "QUOTE_TOTAL_MISMATCH" in codes(h | {"supply_amount": D(1)}, lines)

    h, lines, _ = state()
    lines[0] = lines[0] | {"override_amount": lines[0]["calculated_amount"] * D("1.31")}
    assert "QUOTE_OVERRIDE_LARGE" in codes(h, lines)
    lines[0] = lines[0] | {"override_amount": lines[0]["calculated_amount"] * D("1.2")}
    assert "QUOTE_OVERRIDE_LARGE" not in codes(h, lines)

    h, lines, _ = state()
    assert "QUOTE_SOURCE_OUTDATED" in codes(h, lines, latest="n" * 32)


def test_engine_errors_carry_over_unless_recomputed():
    h, lines, _ = state()
    logs = [
        {"severity": "ERROR", "code": "RULE_FORMULA_ERROR", "message": "x"},
        {"severity": "ERROR", "code": "QUOTE_PRICE_MISSING", "message": "fixed by an override"},
        {"severity": "WARN", "code": "QUOTE_ZERO_QTY", "message": "w"},
    ]
    assert codes(h, lines, logs) == {"RULE_FORMULA_ERROR"}


def test_effective_summary_uses_overrides():
    _, lines, _ = state()
    base = summarize(lines, RATIOS)
    lines[0] = lines[0] | {"override_amount": lines[0]["calculated_amount"] + 1000}
    assert summarize(lines, RATIOS)["material_cost"] == base["material_cost"] + 1000
