"""Track 8 P6-4: what changed in the 표준품셈 between two master data versions (e.g. the 2025 and the
2026 edition): items added / removed / changed, per-resource input changes, resource price changes.

Items are matched by 품셈 code, or by name + spec when the editions number their items differently
(the draft codes `P<쪽>-<n>` of P6-2 follow the page, so they differ between editions). Pure: two
bundles in, a plain dict out (the API serves it as JSON or as a workbook)."""

from decimal import Decimal
from io import BytesIO
from typing import Any

D = Decimal
ITEM_FIELDS = (
    "item_name",
    "spec",
    "unit",
    "category",
    "notice_no",
    "daily_output",
)  # page_no moves between editions
FIELD_LABEL = {
    "item_name": "품명", "spec": "규격", "unit": "단위", "category": "분류", "notice_no": "고시번호", "daily_output": "일당 시공량",
}  # fmt: skip
STATUS_LABEL = {"ADDED": "추가", "REMOVED": "삭제", "CHANGED": "변경"}


def _pct(a: Decimal, b: Decimal) -> float | None:
    return None if a == 0 else float(round((b - a) / a * 100, 2))


def _count(rows: list[dict[str, Any]], status: str) -> int:
    return sum(1 for r in rows if r["status"] == status)


def _key(item: dict[str, Any], match: str) -> tuple[str, ...]:
    if match == "name":
        return (str(item["item_name"]).strip(), str(item.get("spec") or "").strip())
    return (str(item["item_code"]),)


def _resources(bundle: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for r in bundle.get("costbook_resources") or []:
        out.setdefault(r["item_code"], {})[r["resource_code"]] = r
    return out


def _names(*bundles: dict[str, Any]) -> dict[str, str]:
    return {
        p["resource_code"]: p["resource_name"]
        for b in bundles
        for p in b.get("resource_prices") or []
    }


def _latest_prices(bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for p in bundle.get("resource_prices") or []:
        cur = best.get(p["resource_code"])
        if cur is None or str(p.get("price_date") or "") >= str(cur.get("price_date") or ""):
            best[p["resource_code"]] = p
    return best


def _resource_changes(
    ra: dict[str, dict[str, Any]], rb: dict[str, dict[str, Any]], names: dict[str, str]
) -> list[dict[str, Any]]:
    out = []
    for code in sorted(ra.keys() | rb.keys()):
        a, b = ra.get(code), rb.get(code)
        qa = D(str(a["qty_per_unit"])) if a else None
        qb = D(str(b["qty_per_unit"])) if b else None
        cur = b if b is not None else a
        assert cur is not None
        base = {
            "resource_code": code,
            "resource_name": names.get(code),
            "resource_type": cur["resource_type"],
        }
        if a is None or b is None:
            out.append(
                {
                    **base,
                    "status": "ADDED" if a is None else "REMOVED",
                    "a_qty": qa and str(qa),
                    "b_qty": qb and str(qb),
                    "pct": None,
                }
            )
        elif qa != qb or a.get("percent_of") != b.get("percent_of"):
            out.append(
                {
                    **base,
                    "status": "CHANGED",
                    "a_qty": str(qa),
                    "b_qty": str(qb),
                    "pct": _pct(qa, qb) if qa is not None and qb is not None else None,
                }
            )
    return out


def diff_costbooks(a: dict[str, Any], b: dict[str, Any], match: str = "code") -> dict[str, Any]:
    """a = the older bundle, b = the newer one. Items that are the same are only counted."""
    ia = {_key(i, match): i for i in a.get("costbook_items") or []}
    ib = {_key(i, match): i for i in b.get("costbook_items") or []}
    ra, rb, names = _resources(a), _resources(b), _names(a, b)
    items: list[dict[str, Any]] = []
    same = 0
    for k in sorted(ia.keys() | ib.keys()):
        x, y = ia.get(k), ib.get(k)
        cur = y if y is not None else x
        assert cur is not None  # the key came from one of the two books
        row: dict[str, Any] = {
            "name": cur["item_name"],
            "spec": cur.get("spec"),
            "a_code": x and x["item_code"],
            "b_code": y and y["item_code"],
            "fields": [],
            "resources": [],
        }
        if x is None or y is None:
            row["status"] = "ADDED" if x is None else "REMOVED"
            only = y if y is not None else x
            assert only is not None
            res = (rb if x is None else ra).get(only["item_code"], {})
            row["resources"] = _resource_changes(
                {} if x is None else res, res if x is None else {}, names
            )
        else:
            row["fields"] = [
                {"field": FIELD_LABEL[f], "a": x.get(f), "b": y.get(f)}
                for f in ITEM_FIELDS
                if (x.get(f) or None) != (y.get(f) or None)
            ]
            row["resources"] = _resource_changes(
                ra.get(x["item_code"], {}), rb.get(y["item_code"], {}), names
            )
            if not row["fields"] and not row["resources"]:
                same += 1
                continue
            row["status"] = "CHANGED"
        items.append(row)
    pa, pb = _latest_prices(a), _latest_prices(b)
    prices = []
    for code in sorted(pa.keys() | pb.keys()):
        x, y = pa.get(code), pb.get(code)
        xp = D(str(x["unit_price"])) if x and x.get("unit_price") is not None else None
        yp = D(str(y["unit_price"])) if y and y.get("unit_price") is not None else None
        if x is not None and y is not None and xp == yp:
            continue
        status = "ADDED" if x is None else "REMOVED" if y is None else "CHANGED"
        prices.append({
            "resource_code": code, "resource_name": names.get(code), "status": status,
            "a_price": xp and str(xp), "b_price": yp and str(yp),
            "pct": _pct(xp, yp) if xp is not None and yp is not None else None,
            "a_date": x and x.get("price_date"), "b_date": y and y.get("price_date"),
        })  # fmt: skip
    return {
        "match": match,
        "summary": {
            "items_added": _count(items, "ADDED"),
            "items_removed": _count(items, "REMOVED"),
            "items_changed": _count(items, "CHANGED"),
            "items_same": same,
            "prices_changed": len(prices),
        },
        "items": items,
        "prices": prices,
    }


def render_xlsx(diff: dict[str, Any], a_code: str, b_code: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "요약"
    s = diff["summary"]
    for row in (
        [f"표준품셈 개정 비교: {a_code} → {b_code}"],
        ["대응 기준", "품셈 코드" if diff["match"] == "code" else "품명 + 규격"],
        ["추가 항목", s["items_added"]], ["삭제 항목", s["items_removed"]],
        ["변경 항목", s["items_changed"]], ["변경 없음", s["items_same"]],
        ["자원단가 변경", s["prices_changed"]],
    ):  # fmt: skip
        ws.append(row)
    ws["A1"].font = Font(bold=True)
    it = wb.create_sheet("품셈 변경")
    it.append(
        ["상태", "품명", "규격", f"{a_code} 코드", f"{b_code} 코드", "변경 항목", "이전", "이후"]
    )
    rs = wb.create_sheet("투입량 변경")
    rs.append(
        [
            "품명",
            f"{a_code} 코드",
            f"{b_code} 코드",
            "자원",
            "구분",
            "상태",
            "이전 투입량",
            "이후 투입량",
            "증감 %",
        ]
    )
    for r in diff["items"]:
        head = [STATUS_LABEL[r["status"]], r["name"], r["spec"], r["a_code"], r["b_code"]]
        for f in r["fields"] or [{"field": None, "a": None, "b": None}]:
            it.append([*head, f["field"], f["a"], f["b"]])
        for x in r["resources"]:
            rs.append(
                [
                    r["name"],
                    r["a_code"],
                    r["b_code"],
                    x["resource_name"] or x["resource_code"],
                    x["resource_type"],
                    STATUS_LABEL[x["status"]],
                    x["a_qty"],
                    x["b_qty"],
                    x["pct"],
                ]
            )
    ps = wb.create_sheet("자원단가 변경")
    ps.append(
        [
            "자원",
            "상태",
            f"{a_code} 단가",
            f"{b_code} 단가",
            "증감 %",
            f"{a_code} 기준일",
            f"{b_code} 기준일",
        ]
    )
    for p in diff["prices"]:
        ps.append(
            [
                p["resource_name"] or p["resource_code"],
                STATUS_LABEL[p["status"]],
                p["a_price"],
                p["b_price"],
                p["pct"],
                p["a_date"],
                p["b_date"],
            ]
        )
    for sheet in (it, rs, ps):
        for c in sheet[1]:
            c.font = Font(bold=True)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
