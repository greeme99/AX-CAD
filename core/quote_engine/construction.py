"""Track 6-3 construction/MEP quote (pure: QTO measures + master bundle in, lines + totals out).

Per work item (공종): 수량 = qty_formula(QTO measures, height_m)
  재료비 = 수량 × (1 + 손실률) × 재료단가     (MATERIAL)
  노무비 = 수량 × 노무단가                   (LABOR)
  경비   = 수량 × 경비단가                   (OVERHEAD)
then the 공사원가계산서 ratio lines:
  간접노무비 = 직접노무비 × 간접노무비율      (LABOR)
  간접공사비 = (재료비 + 노무비) × 간접공사비율 (OVERHEAD)
  순공사원가 -> 일반관리비 -> 이윤 = (노무비 + 경비 + 일반관리비) × 이윤율 -> VAT  (cost.summarize)
A price of 0 means the item has no such cost; a missing (None) price is an error line.
Every line is traced to the QTO items its formula reads (handles of the current revision, or of
the base revision for demo_* quantities)."""

from decimal import Decimal
from typing import Any

from core.quote_engine.cost import MAX_SOURCES, Quote, _d, finish
from core.quote_engine.formula import FormulaError, evaluate, names
from core.quote_engine.qto import STATUS

D = Decimal
PARTS = (
    ("MATERIAL", "material_price", "재료비"),
    ("LABOR", "labor_price", "노무비"),
    ("OVERHEAD", "expense_price", "경비"),
)


def _role_status(name: str) -> tuple[str, set[str]] | None:
    """measure name -> (QTO role, item statuses it counts). height_m -> None."""
    statuses = {"NEW", "KEEP"}  # unprefixed: the current drawing
    for st, prefix in STATUS.items():
        if name.startswith(prefix + "_"):
            name, statuses = name[len(prefix) + 1 :], {st}
    if name.startswith("room_"):
        return "ROOM", statuses
    for suffix in ("_length_m", "_count"):
        if name.endswith(suffix):
            return name.removesuffix(suffix).upper(), statuses
    return None


def _traces(qto: dict[str, Any], used: set[str], source: dict[str, Any]) -> list[dict[str, Any]]:
    """Source blocks for the QTO items behind `used` measures: one per revision involved."""
    by_rev: dict[str, set[str]] = {}
    for n in used:
        rs = _role_status(n)
        if rs is None:
            continue
        role, statuses = rs
        for it in qto["items"]:
            if it["role"] == role and it["status"] in statuses:
                rev = (
                    source["base_revision_id"] if it["status"] == "DEMO" else source["revision_id"]
                )
                by_rev.setdefault(rev, set()).update(it["handles"])
    if not by_rev:  # a constant or height-only formula: the drawing as a whole
        rid = source["revision_id"]
        return [
            {"source_kind": "REVISION", "sources": [rid], "source_count": 1, "revision_id": rid}
        ]
    return [
        {
            "source_kind": "ENTITY",
            "sources": sorted(hs)[:MAX_SOURCES],
            "source_count": len(hs),
            "revision_id": rev,
        }
        for rev, hs in sorted(by_rev.items())
    ]


def compute_construction(
    qto: dict[str, Any],
    bundle: dict[str, Any],
    source: dict[str, Any],
    *,
    height_m: Decimal | None,
    adjust: dict[str, Any] | None = None,
    on: Any = None,
) -> dict[str, Any]:
    """source: {"kind": "CONSTRUCTION", "revision_id", "base_revision_id" | None}."""
    ratios = bundle["cost_ratios"]
    q = Quote(ratios, bundle, adjust, on)
    items = bundle.get("work_items") or []
    if not items:
        q.log("ERROR", "QUOTE_NO_WORK_ITEMS", "기준정보에 공종 단가가 없습니다")
    if any(w.startswith("METRIC_ITEMS_TRUNCATED") for w in qto.get("warnings", [])):
        q.log(
            "WARN", "QTO_TRACE_TRUNCATED", "수량산출 항목이 많아 일부 라인의 원천 목록이 잘렸습니다"
        )
    env = {k: D(str(v)) for k, v in qto["measures"].items()}
    if height_m is not None:
        env["height_m"] = height_m
    for w in items:
        label = w["item_name"]
        try:
            used = names(w["qty_formula"])
            if "height_m" in used and height_m is None:
                q.log("ERROR", "QUOTE_HEIGHT_REQUIRED", f"{label}: 층고(높이)를 입력하세요")
                continue
            value = evaluate(w["qty_formula"], env)
        except FormulaError as e:
            q.log("ERROR", "RULE_FORMULA_ERROR", f"{label}: {e}")
            continue
        if value <= 0:
            q.log("INFO", "QTO_NOT_APPLICABLE", f"{label}: 도면에 해당 수량이 없습니다")
            continue
        q.expired(w, label)
        sources = _traces(qto, used, source)
        loss = _d(w["loss_rate"]) or D(0)
        inputs = {k: str(env[k]) for k in sorted(used)}
        for category, key, part in PARTS:
            price = _d(w[key])
            if price == 0:
                continue
            qty, formula = value, w["qty_formula"]
            extra: dict[str, str] = {}
            if category == "MATERIAL" and loss:
                qty, formula = value * (1 + loss), f"({formula}) × (1 + loss_rate)"
                extra = {"loss_rate": str(loss)}
            trace = {
                "rule_code": f"WORK:{w['item_code']}",
                "price_item_code": w["item_code"],
                "unit_price": None if price is None else str(price),
                "inputs": {**inputs, **extra},
                "formula_text": formula,
            }
            q.line(
                category,
                w["item_code"],
                f"{label} {part}",
                w["unit"],
                qty,
                price,
                [{**s, **trace} for s in sources],
            )

    rid = source["revision_id"]
    whole = {"source_kind": "REVISION", "sources": [rid], "source_count": 1, "revision_id": rid}

    def ratio(category: str, code: str, name: str, base: Decimal, key: str, text: str) -> None:
        if base <= 0:
            return
        rate = _d(ratios.get(key))
        trace = {
            **whole,
            "rule_code": f"INDIRECT:{code}",
            "price_item_code": None,
            "unit_price": None if rate is None else str(rate),
            "inputs": {"base": str(base)},
            "formula_text": text,
        }
        q.line(category, code, name, "식", base, rate, [trace], adjust=False)

    ratio(
        "LABOR",
        "INDIRECT_LABOR",
        "간접노무비",
        q.total("LABOR"),
        "indirect_labor_rate",
        "직접노무비 × 간접노무비율",
    )
    ratio(
        "OVERHEAD",
        "INDIRECT_COST",
        "간접공사비",
        q.total("MATERIAL") + q.total("LABOR"),
        "indirect_cost_rate",
        "(재료비 + 노무비) × 간접공사비율",
    )
    return finish(q, ratios, construction=True)
