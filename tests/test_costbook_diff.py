"""Track 8 P6-4: 표준품셈 개정 비교 between two master data versions (synthetic numbers)."""

import io

from openpyxl import load_workbook

from core.quote_engine.costbook_diff import diff_costbooks, render_xlsx
from tests.test_api_master import CONSTRUCTION, FULL
from tests.test_costbook import ITEM, PRICES, RESOURCES, master


def book(items, resources, prices):
    return {"costbook_items": items, "costbook_resources": resources, "resource_prices": prices}


OLD = book([ITEM], RESOURCES, PRICES)


def newer():
    items = [
        ITEM | {"unit": "m2", "notice_no": "2026 고시"},
        ITEM | {"item_code": "CB-NEW", "item_name": "신규 공종"},
    ]
    res = [
        r | {"qty_per_unit": "0.07"} if r["resource_code"] == "L-PLASTERER" else r
        for r in RESOURCES
        if r["resource_code"] != "E-MIXER"  # removed in the new edition
    ]
    res += [RESOURCES[0] | {"item_code": "CB-NEW"}]
    prices = [
        p | {"unit_price": "300000"} if p["resource_code"] == "L-PLASTERER" else p for p in PRICES
    ]
    return book(items, res, prices)


def test_diff_by_code_reports_items_inputs_and_prices():
    d = diff_costbooks(OLD, newer())
    assert d["summary"] == {
        "items_added": 1, "items_removed": 0, "items_changed": 1, "items_same": 0, "prices_changed": 1,
    }  # fmt: skip
    changed = next(i for i in d["items"] if i["status"] == "CHANGED")
    assert [f["field"] for f in changed["fields"]] == ["고시번호"]
    by = {r["resource_code"]: r for r in changed["resources"]}
    assert (
        by["L-PLASTERER"]["status"] == "CHANGED" and by["L-PLASTERER"]["pct"] == 16.67
    )  # 0.06 -> 0.07
    assert by["E-MIXER"]["status"] == "REMOVED" and by["E-MIXER"]["b_qty"] is None
    assert set(by) == {"L-PLASTERER", "E-MIXER"}  # unchanged inputs are not listed
    added = next(i for i in d["items"] if i["status"] == "ADDED")
    assert added["b_code"] == "CB-NEW" and added["resources"][0]["status"] == "ADDED"
    p = d["prices"][0]
    assert p["resource_code"] == "L-PLASTERER" and p["a_price"] == "280000" and p["pct"] == 7.14


def test_identical_books_have_no_differences():
    d = diff_costbooks(OLD, OLD)
    assert d["items"] == [] and d["prices"] == [] and d["summary"]["items_same"] == 1


def test_match_by_name_when_the_codes_differ_between_editions():
    renumbered = book(
        [ITEM | {"item_code": "P0099-1"}], [r | {"item_code": "P0099-1"} for r in RESOURCES], PRICES
    )
    by_code = diff_costbooks(OLD, renumbered)
    assert by_code["summary"]["items_added"] == 1 and by_code["summary"]["items_removed"] == 1
    by_name = diff_costbooks(OLD, renumbered, "name")
    assert by_name["summary"]["items_same"] == 1 and by_name["items"] == []


def test_workbook_has_the_four_sheets():
    wb = load_workbook(io.BytesIO(render_xlsx(diff_costbooks(OLD, newer()), "2025", "2026")))
    assert wb.sheetnames == ["요약", "품셈 변경", "투입량 변경", "자원단가 변경"]
    assert wb["요약"]["A1"].value.endswith("2025 → 2026")
    assert wb["투입량 변경"].max_row >= 3 and wb["자원단가 변경"].max_row == 2


def test_api_compare(client, world):
    admin = world.h["admin"]
    full = FULL | CONSTRUCTION | {"work_items": []}
    a, _ = master(client, admin, full | OLD)
    b, _ = master(client, admin, full | newer())
    r = client.get(f"/api/master-versions/{a}/compare/{b}", headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["summary"]["items_added"] == 1 and r.json()["data"]["match"] == "code"
    x = client.get(f"/api/master-versions/{a}/compare/{b}?format=xlsx&match=name", headers=admin)
    assert x.status_code == 200 and x.content.startswith(b"PK")
    assert client.get(f"/api/master-versions/{a}/compare/99999", headers=admin).status_code == 404
    assert (
        client.get(f"/api/master-versions/{a}/compare/{b}", headers=world.h["viewer"]).status_code
        == 403
    )
