"""FN-20 quote validation on the current state of a quote (overrides included). Pure: the API
passes plain dicts. Any ERROR blocks the approval request (FN-22, S11)."""

from collections import Counter
from decimal import Decimal
from typing import Any

from core.quote_engine.cost import TOTAL_KEYS, effective_amount, summarize

OVERRIDE_WARN_RATIO = Decimal("0.3")  # ±30 %
# engine findings that validation recomputes itself (so a fixed override clears them)
RECOMPUTED = {"QUOTE_PRICE_MISSING", "QUOTE_MATERIAL_REQUIRED"}


# drawing/model findings behind the measured quantities: a person confirms them before approval.
# WARN, not ERROR: the estimator may already have corrected the affected lines by override.
SOURCE_WARN = {
    "METRIC_UNITS_ASSUMED_MM": "도면 단위가 지정되지 않아 mm로 가정했습니다. 치수(축척)를 확인하세요",
    "METRIC_OPEN_CONTOUR": "닫히지 않은 윤곽 {n}개가 있습니다. 절단길이·면적을 확인하세요",
    "METRIC_DUPLICATE_GEOMETRY": "겹친 선 {n}개는 한 번만 셌습니다. 도면을 확인하세요",
    "METRIC_NO_OUTER_CONTOUR": "외곽 윤곽을 찾지 못해 면적·중량이 산출되지 않았습니다",
    "METRIC_MULTIPLE_PARTS": "분리된 윤곽 {n}개 중 가장 큰 것만 부품 외곽으로 봤습니다",
    "OPEN_SHELLS": "닫히지 않은 셸이 있어 부피·중량을 신뢰할 수 없습니다",
    "SEWN": "곡면을 봉합해 만든 솔리드입니다. 부피를 확인하세요",
}


def _log(severity: str, code: str, message: str, line_no: int | None = None) -> dict[str, Any]:
    return {"severity": severity, "code": code, "message": message, "line_no": line_no}


def _source_warnings(metrics: dict[str, Any] | None) -> list[dict[str, Any]]:
    m = metrics or {}
    found = [("", w) for w in m.get("warnings", [])]  # 2D snapshot
    found += [
        (f"{b.get('name', '')}: ", w) for b in m.get("bodies", []) for w in b.get("warnings", [])
    ]
    out = []
    for prefix, w in found:
        code, _, n = w.partition(":")
        if code in SOURCE_WARN:
            text = prefix + SOURCE_WARN[code].format(n=n)
            out.append(_log("WARN", "QUOTE_SOURCE_CHECK", text))
    return out


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
    out += _source_warnings(header.get("metrics"))

    out += [
        _log(lg["severity"], lg["code"], lg["message"])
        for lg in engine_logs
        if lg["severity"] == "ERROR" and lg["code"] not in RECOMPUTED
    ]
    return out
