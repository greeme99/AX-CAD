"""FN-20 quote validation on the current state of a quote (overrides included). Pure: the API
passes plain dicts. Any ERROR blocks the approval request (FN-22, S11)."""

from collections import Counter
from decimal import Decimal
from typing import Any

from core.quote_engine.cost import TOTAL_KEYS, effective_amount, summarize

OVERRIDE_WARN_RATIO = Decimal("0.3")  # ±30 %
# engine findings that validation recomputes itself (so a fixed override clears them)
RECOMPUTED = {"QUOTE_PRICE_MISSING", "QUOTE_MATERIAL_REQUIRED"}


def _log(severity: str, code: str, message: str, line_no: int | None = None) -> dict[str, Any]:
    return {"severity": severity, "code": code, "message": message, "line_no": line_no}


def validate_quote(
    header: dict[str, Any],
    lines: list[dict[str, Any]],
    ratios: dict[str, Any],
    engine_logs: list[dict[str, Any]],
    latest_revision_id: str | None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ln in lines:
        name, no = ln["item_name"], ln["line_no"]
        if effective_amount(ln) is None:
            out.append(_log("ERROR", "QUOTE_PRICE_MISSING", f"{name}: 단가가 없습니다", no))
        qty = ln["override_qty"] if ln.get("override_qty") is not None else ln["calculated_qty"]
        if Decimal(str(qty)) <= 0:
            out.append(_log("ERROR", "QUOTE_QTY_INVALID", f"{name}: 수량이 0 이하입니다", no))
        calc = None if ln["calculated_amount"] is None else Decimal(str(ln["calculated_amount"]))
        manual = ln.get("override_amount")
        if manual is not None and calc:  # values arrive as strings from the API: compare Decimals
            ratio = abs(Decimal(str(manual)) / calc - 1)
            if ratio > OVERRIDE_WARN_RATIO:
                out.append(
                    _log(
                        "WARN",
                        "QUOTE_OVERRIDE_LARGE",
                        f"{name}: 수동 금액이 자동값과 {ratio:.0%} 다릅니다",
                        no,
                    )
                )

    if not any(ln["cost_category"] == "MATERIAL" for ln in lines):
        out.append(
            _log(
                "ERROR",
                "QUOTE_MATERIAL_REQUIRED",
                "재료비 라인이 없습니다 (재질·두께를 지정해 재산출하세요)",
            )
        )

    keys = Counter(
        (ln["cost_category"], ln["item_code"], tuple(ln.get("source_key", ()))) for ln in lines
    )
    for (cat, code, _), n in keys.items():
        if n > 1:
            out.append(
                _log(
                    "WARN",
                    "QUOTE_DUPLICATE_LINE",
                    f"{cat} {code}: 같은 원천에서 라인이 {n}개 있습니다",
                )
            )

    # stored header = what the engine computed: the calculated lines must still add up to it
    calculated = [{**ln, "override_amount": None} for ln in lines]
    expected = summarize(calculated, ratios)
    if expected is None or any(Decimal(str(header[k])) != expected[k] for k in TOTAL_KEYS):
        out.append(_log("ERROR", "QUOTE_TOTAL_MISMATCH", "라인 합계와 견적 헤더 금액이 다릅니다"))

    if summarize(lines, ratios) is None:
        out.append(_log("ERROR", "QUOTE_AMOUNT_OVERFLOW", "조정 반영 합계가 허용 범위를 넘습니다"))

    # labour-ratio overhead is a calculated line: adjusting labour does not move it (L4)
    if any(
        ln["cost_category"] == "LABOR" and ln.get("override_amount") is not None for ln in lines
    ) and any(ln["cost_category"] == "OVERHEAD" and ln["item_code"] == "OVERHEAD" for ln in lines):
        out.append(
            _log(
                "WARN",
                "QUOTE_OVERHEAD_STALE",
                "노무비를 조정했지만 노무비 비율 제조간접비는 그대로입니다",
            )
        )

    if (
        header["source_kind"] == "REVISION"
        and latest_revision_id
        and header["revision_id"] != latest_revision_id
    ):
        out.append(_log("WARN", "QUOTE_SOURCE_OUTDATED", "원천 도면에 더 새로운 리비전이 있습니다"))

    out += [
        _log(lg["severity"], lg["code"], lg["message"])
        for lg in engine_logs
        if lg["severity"] == "ERROR" and lg["code"] not in RECOMPUTED
    ]
    return out
