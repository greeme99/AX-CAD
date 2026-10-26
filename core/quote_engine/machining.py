"""Track 8 P4-2: default machining rules for 3D quotes (drill, tap, counterbore, countersink,
milling, turning). Formulas give standard minutes per piece from the P4-1 metrics; setup is spread
over the quantity. The numbers are industry reference values, not a company standard: a master
data draft takes them as a starting point and the estimator replaces the values with the shop's
own (formulas and params stay editable like any process rule)."""

from typing import Any

PRICE_ITEMS: list[dict[str, Any]] = [
    {"item_code": "M-LAB", "item_type": "LABOR", "unit": "h", "unit_price": "35000"},
    {"item_code": "M-DRILL-MC", "item_type": "MACHINE", "unit": "h", "unit_price": "15000"},
    {"item_code": "M-MILL-MC", "item_type": "MACHINE", "unit": "h", "unit_price": "40000"},
    {"item_code": "M-TURN-MC", "item_type": "MACHINE", "unit": "h", "unit_price": "30000"},
]


def _rule(
    code: str, name: str, metric: str, formula: str, params: dict[str, float], machine: str
) -> dict[str, Any]:
    return {
        "rule_code": code,
        "process_code": code,
        "process_name": name,
        "input_metric": metric,
        "formula_text": formula,
        "params": params,
        "labor_item_code": "M-LAB",
        "machine_item_code": machine,
    }


PROCESS_RULES: list[dict[str, Any]] = [
    _rule(
        "M-DRILL",
        "드릴",
        "hole_count",
        "setup_min / part_qty + hole_count * index_min + drill_length_mm / feed_mm_per_min",
        {"setup_min": 3, "index_min": 0.2, "feed_mm_per_min": 80},
        "M-DRILL-MC",
    ),
    _rule(  # in and out at the tapping feed
        "M-TAP",
        "탭",
        "tap_count",
        "tap_count * index_min + tap_length_mm * 2 / tap_mm_per_min",
        {"index_min": 0.3, "tap_mm_per_min": 200},
        "M-DRILL-MC",
    ),
    _rule(
        "M-CBORE",
        "카운터보어",
        "counterbore_count",
        "counterbore_count * min_each",
        {"min_each": 0.8},
        "M-DRILL-MC",
    ),
    _rule(
        "M-CSINK",
        "접시 가공",
        "countersink_count",
        "countersink_count * min_each",
        {"min_each": 0.4},
        "M-DRILL-MC",
    ),
    _rule(
        "M-MILL",
        "밀링",
        "mill_removed_volume_mm3",
        "setup_min / part_qty + mill_removed_volume_mm3 / mrr_mm3_per_min",
        {"setup_min": 30, "mrr_mm3_per_min": 12000},
        "M-MILL-MC",
    ),
    _rule(
        "M-TURN",
        "선반",
        "turn_removed_volume_mm3",
        "setup_min / part_qty + turn_removed_volume_mm3 / mrr_mm3_per_min",
        {"setup_min": 20, "mrr_mm3_per_min": 18000},
        "M-TURN-MC",
    ),
]


def add_defaults(bundle: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The bundle with the default price items and rules whose codes it lacks (nothing it already
    has is changed), and the codes added."""
    out = dict(bundle)
    added: list[str] = []
    for key, code, rows in (
        ("price_items", "item_code", PRICE_ITEMS),
        ("process_rules", "rule_code", PROCESS_RULES),
    ):
        have = {r[code] for r in bundle.get(key) or []}
        new = [r for r in rows if r[code] not in have]
        out[key] = [*(bundle.get(key) or []), *new]
        added += [r[code] for r in new]
    return out, added
