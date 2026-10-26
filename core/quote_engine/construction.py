"""Track 6-3 construction/MEP quote (pure: QTO measures + master bundle in, lines + totals out).

Per work item (공종): 수량 = qty_formula(QTO measures, height_m)
  재료비 = 수량 × (1 + 손실률) × 재료단가     (MATERIAL)
  노무비 = 수량 × 노무단가                   (LABOR)
  경비   = 수량 × 경비단가                   (OVERHEAD)
or, for a work item linked to a 표준품셈 item (Track 8 P4-6, 일위대가), one line per resource:
  수량 × 단위당 투입량 (× (1 + 손실률) for materials) × 자원단가
  노무 -> LABOR, 재료 -> MATERIAL, 장비(기계경비) -> OVERHEAD; traced to the 품셈 notice and page
then the 공사원가계산서 ratio lines:
  간접노무비 = 직접노무비 × 간접노무비율      (LABOR)
  간접공사비 = (재료비 + 노무비) × 간접공사비율 (OVERHEAD)
  순공사원가 -> 일반관리비 -> 이윤 = (노무비 + 경비 + 일반관리비) × 이윤율 -> VAT  (cost.summarize)
A price of 0 means the item has no such cost; a missing (None) price is an error line.
Every line is traced to the QTO items its formula reads (handles of the current revision, or of
the base revision for demo_* quantities).
Track 8 P4-6d: with external quantities (Revit / IFC elements, each tagged with a 공종 code), a work
item whose code the elements carry takes Σ their quantities instead of its formula (units must
match), traced to the element ids (source kind EXTERNAL)."""

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
RESOURCE_CATEGORY = {"LABOR": "LABOR", "MATERIAL": "MATERIAL", "EQUIPMENT": "OVERHEAD"}
CATEGORY_ORDER = {"MATERIAL": 0, "LABOR": 1, "OVERHEAD": 2}


def _costbook_lines(
    q: Quote,
    bundle: dict[str, Any],
    w: dict[str, Any],
    value: Decimal,
    inputs: dict[str, str],
    sources: list[dict[str, Any]],
) -> None:
    """일위대가 (P4-6): a work item priced from its 표준품셈 item, one line per resource."""
    code = w["costbook_item_code"]
    item = next((i for i in bundle.get("costbook_items") or [] if i["item_code"] == code), None)
    rows = [r for r in bundle.get("costbook_resources") or [] if r["item_code"] == code]
    if item is None or not rows:
        q.log("ERROR", "QUOTE_COSTBOOK_EMPTY", f"{w['item_name']}: 품셈 {code}의 투입량이 없습니다")
        return
    prices = {p["resource_code"]: p for p in bundle.get("resource_prices") or []}
    loss = _d(w["loss_rate"]) or D(0)
    source = {
        "costbook_item": code,
        "notice_no": item.get("notice_no"),
        "page_no": item.get("page_no"),
        "source_sha256": item.get("source_sha256"),
        "reviewer": item.get("reviewer"),
        "work_qty": str(value),
    }
    for r in sorted(rows, key=lambda r: CATEGORY_ORDER[RESOURCE_CATEGORY[r["resource_type"]]]):
        rp = prices.get(r["resource_code"])
        name = f"{w['item_name']} · {rp['resource_name'] if rp else r['resource_code']}"
        per = D(str(r["qty_per_unit"]))
        qty, formula = value * per, f"({w['qty_formula']}) × qty_per_unit"
        extra: dict[str, str | None] = {"qty_per_unit": str(per)}
        if r["resource_type"] == "MATERIAL" and loss:
            qty, formula = qty * (1 + loss), f"{formula} × (1 + loss_rate)"
            extra["loss_rate"] = str(loss)
        if rp:
            q.expired(rp, name)
            day = rp.get("price_date")  # a date from the database, text from a bundle
            extra |= {
                "price_date": None if day is None else str(day),
                "price_source": rp.get("source"),
            }
        price = _d(rp["unit_price"]) if rp else None
        trace = {
            "rule_code": f"COSTBOOK:{code}",
            "price_item_code": r["resource_code"],
            "unit_price": None if price is None else str(price),
            "inputs": {**inputs, **{k: v for k, v in {**source, **extra}.items() if v is not None}},
            "formula_text": formula,
        }
        q.line(
            RESOURCE_CATEGORY[r["resource_type"]],
            f"{w['item_code']}/{r['resource_code']}",
            name,
            rp["unit"] if rp else "",
            qty,
            price,
            [{**s, **trace} for s in sources],
        )


def _priced(
    q: Quote,
    bundle: dict[str, Any],
    w: dict[str, Any],
    value: Decimal,
    inputs: dict[str, str],
    sources: list[dict[str, Any]],
) -> None:
    """Lines of one work item's quantity: its 일위대가 or its own three prices."""
    if w.get("costbook_item_code"):
        _costbook_lines(q, bundle, w, value, inputs, sources)
        return
    label = w["item_name"]
    loss = _d(w["loss_rate"]) or D(0)
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


def _role_status(name: str) -> tuple[str, set[str]] | None:
    """measure name -> (QTO role, item statuses it counts). height_m -> None."""
    statuses = {"NEW", "KEEP"}  # unprefixed: the current drawing
    for st, prefix in STATUS.items():
        if name.startswith(prefix + "_"):
            name, statuses = name[len(prefix) + 1 :], {st}
    if name.startswith("obj_"):  # P4-6c drawing object
        return f"OBJ:{name[4:]}", statuses
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
    rid = source["revision_id"]
    ext: dict[str, list[dict[str, Any]]] = {}
    for el in (qto.get("external") or {}).get("elements", []):
        ext.setdefault(el["work_item_code"], []).append(el)
    unmapped = set(ext) - {w["item_code"] for w in items}
    if unmapped:
        q.log(
            "WARN",
            "QUOTE_EXTERNAL_UNMAPPED",
            f"공종 단가에 없는 외부 수량 코드: {', '.join(sorted(unmapped)[:10])}",
        )
    for w in items:
        label = w["item_name"]
        if w["item_code"] in ext:
            els = ext[w["item_code"]]
            if any(el["unit"] != w["unit"] for el in els):
                q.log(
                    "ERROR",
                    "QUOTE_UNIT_MISMATCH",
                    f"{label}: 외부 수량 단위가 {w['unit']}가 아닙니다",
                )
                continue
            value = sum((D(str(el["quantity"])) for el in els), D(0))
            if value <= 0:
                q.log("INFO", "QTO_NOT_APPLICABLE", f"{label}: 외부 수량이 0입니다")
                continue
            q.expired(w, label)
            ids = sorted({el["element_id"] for el in els})
            sources = [
                {
                    "source_kind": "EXTERNAL",
                    "sources": ids[:MAX_SOURCES],
                    "source_count": len(ids),
                    "revision_id": rid,
                }
            ]
            inputs = {
                "external_qty": str(value),
                "source_file": qto["external"]["source_file"],
                "quantities_sha256": qto["external"].get("sha256", ""),
            }
            _priced(q, bundle, {**w, "qty_formula": "external_qty"}, value, inputs, sources)
            continue
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
        inputs = {k: str(env[k]) for k in sorted(used)}
        _priced(q, bundle, w, value, inputs, sources)

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
