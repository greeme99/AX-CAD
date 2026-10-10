"""S11-b FN-21 quote documents through the API: TC-79 (PDF text), TC-80 (draft vs official),
TC-81 (XLSX cells), issued-document registry (official copies frozen once issued)."""

import hashlib
import io
import json
from decimal import Decimal as D

import pytest
from openpyxl import load_workbook
from pypdf import PdfReader
from sqlalchemy import text

from backend.api import routes_quote
from backend.db.session import engine
from core.quote_engine.report import korean_amount
from tests.test_api_quote import estimator  # noqa: F401 - fixture
from tests.test_api_quote_approval import BODY, WHY, new_quote
from tests.test_api_quotes import active  # noqa: F401 - fixture

REAL = {
    "company": "(주)에이엑스정밀",
    "business_no": "123-45-67890",
    "ceo": "김대표",
    "address": "경기도 안산시 단원구 공단로 1",
    "phone": "031-123-4567",
    "email": "quote@ax.example",
    "validity_days": 30,
    "delivery": "발주 후 14일",
    "payment": "익월 말 현금",
    "note": "운송비 별도",
}


@pytest.fixture
def supplier(tmp_path, monkeypatch):
    f = tmp_path / "supplier.json"
    f.write_text(json.dumps(REAL, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(routes_quote, "SUPPLIER_FILE", f)
    return f


def pdf_text(data):
    return "".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages)


def registry(qid):
    with engine().connect() as c:
        return c.execute(
            text(
                "SELECT format, official, basis, sha256, content IS NOT NULL FROM quote_reports "
                "WHERE quote_id = :q ORDER BY report_id"
            ),
            {"q": qid},
        ).all()


def confirmed(client, world, estimator):  # noqa: F811
    q = new_quote(client, world, estimator, **BODY)
    line = q["lines"][0]["quote_line_id"]
    client.patch(
        f"/api/quote-lines/{line}",
        json={"field": "amount", "value": "9000", "reason": WHY},
        headers=estimator,
    )
    q = client.post(
        f"/api/quotes/{q['quote_id']}/approvals",
        json={"approver_id": world.ids["reviewer"]},
        headers=estimator,
    ).json()["data"]
    aid = q["approvals"][0]["approval_id"]
    r = client.post(
        f"/api/quote-approvals/{aid}/decision",
        json={"decision": "APPROVED"},
        headers=world.h["reviewer"],
    )
    assert r.json()["data"]["status"] == "CONFIRMED", r.text
    return r.json()["data"]


def test_tc80_draft_until_approved(client, world, estimator, active, make_user, headers):  # noqa: F811
    q = new_quote(client, world, estimator, **BODY)
    url = f"/api/quotes/{q['quote_id']}/report"
    r = client.get(f"{url}?official=true", headers=estimator)
    assert r.status_code == 403 and r.json()["error"]["code"] == "QUOTE_NOT_APPROVED"
    r = client.get(url, headers=estimator)  # the sample supplier is fine for drafts
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].endswith(f'"{q["quote_no"]}-DRAFT.pdf"')
    assert r.headers["cache-control"] == "no-store"
    body = pdf_text(r.content)
    assert "초안(DRAFT)" in body and "산출근거" not in body  # basis is opt-in
    assert "내부용·대외비" in pdf_text(client.get(f"{url}?basis=true", headers=estimator).content)
    assert registry(q["quote_id"]) == []  # drafts are not issued documents
    # roles and membership
    for role in ("designer", "viewer"):
        assert client.get(url, headers=world.h[role]).status_code == 403
    uid = make_user("maker", "MANUFACTURING")  # reads quotes, does not export them
    client.post(
        f"/api/projects/{world.pid}/members", json={"user_id": uid}, headers=world.h["admin"]
    )
    assert client.get(url, headers=headers("maker")).status_code == 403
    make_user("outsider", "ESTIMATOR")
    assert client.get(url, headers=headers("outsider")).status_code == 404
    assert client.get(f"{url}?format=docx", headers=estimator).status_code == 422


def test_official_needs_real_supplier_and_no_internal_basis(client, world, estimator, active):  # noqa: F811
    q = confirmed(client, world, estimator)
    url = f"/api/quotes/{q['quote_id']}/report?official=true"
    r = client.get(url, headers=estimator)  # repo file holds the sample values
    assert r.status_code == 409 and r.json()["error"]["code"] == "SUPPLIER_NOT_CONFIGURED"
    r = client.get(f"{url}&basis=true", headers=estimator)
    assert r.status_code == 422 and r.json()["error"]["code"] == "REPORT_BASIS_INTERNAL"
    assert registry(q["quote_id"]) == []


def test_tc79_81_official_documents(client, world, estimator, active, supplier):  # noqa: F811
    q = confirmed(client, world, estimator)
    eff = q["effective"]
    url = f"/api/quotes/{q['quote_id']}/report?official=true"

    r = client.get(url, headers=world.h["reviewer"])
    assert r.status_code == 200, r.text
    body = pdf_text(r.content)
    total = D(eff["total_amount"])
    assert "DRAFT" not in body and REAL["company"] in body and REAL["business_no"] in body
    assert f"{D(eff['supply_amount']):,.0f}" in body and f"{total:,.0f}" in body
    assert f"일금 {korean_amount(int(total))}원정" in body
    assert "산출근거" not in body and WHY not in body  # internal basis never goes out

    # issued once: later edits to the supplier settings do not change the document
    supplier.write_text(json.dumps(REAL | {"company": "(주)바뀐상호"}), encoding="utf-8")
    assert client.get(url, headers=estimator).content == r.content

    x = client.get(f"{url}&format=xlsx", headers=estimator)
    assert x.headers["content-disposition"].endswith(f'"{q["quote_no"]}.xlsx"')
    wb = load_workbook(io.BytesIO(x.content))
    assert wb.sheetnames == ["견적서"]
    item = next(r for r in wb["견적서"].iter_rows(values_only=True) if r[0] == 1)
    assert item[3] == BODY["qty"] and D(str(item[6])) == D(eff["supply_amount"])
    assert D(str(item[7])) == D(eff["vat_amount"])
    assert client.get(f"{url}&format=xlsx", headers=estimator).content == x.content

    rows = registry(q["quote_id"])
    assert [(f, o, b, stored) for f, o, b, _, stored in rows] == [
        ("pdf", True, False, True),
        ("xlsx", True, False, True),
    ]
    assert rows[0][3] == hashlib.sha256(r.content).hexdigest()
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError), engine().begin() as c:
        c.execute(text("UPDATE quote_reports SET official = false"))
