"""S11-b FN-21 quote documents through the API: TC-79 (PDF text), TC-80 (draft vs official),
TC-81 (XLSX cells), issued-document registry."""

import hashlib
import io
from decimal import Decimal as D

import pytest
from openpyxl import load_workbook
from pypdf import PdfReader
from sqlalchemy import text

from backend.db.session import engine
from core.quote_engine.report import korean_amount
from tests.test_api_quote import estimator  # noqa: F401 - fixture
from tests.test_api_quote_approval import BODY, WHY, new_quote
from tests.test_api_quotes import active  # noqa: F401 - fixture


def pdf_text(data):
    return "".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages)


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
    r = client.get(url, headers=estimator)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].endswith(f'"{q["quote_no"]}-DRAFT.pdf"')
    assert "초안(DRAFT)" in pdf_text(r.content)
    # roles and membership
    assert client.get(url, headers=world.h["designer"]).status_code == 403
    make_user("outsider", "ESTIMATOR")
    assert client.get(url, headers=headers("outsider")).status_code == 404
    assert client.get(f"{url}?format=docx", headers=estimator).status_code == 400


def test_tc79_81_official_documents(client, world, estimator, active):  # noqa: F811
    q = confirmed(client, world, estimator)
    eff = q["effective"]
    url = f"/api/quotes/{q['quote_id']}/report?official=true"

    r = client.get(url, headers=world.h["reviewer"])
    assert r.status_code == 200
    body = pdf_text(r.content)
    total = D(eff["total_amount"])
    assert "DRAFT" not in body and "견" in body
    assert f"{D(eff['supply_amount']):,.0f}" in body and f"{total:,.0f}" in body
    assert f"일금 {korean_amount(int(total))}원정" in body
    assert "[수동]" in body and WHY in body  # basis attachment shows the manual line + reason
    again = client.get(url, headers=world.h["reviewer"]).content
    assert again == r.content  # dated by the approval: re-issue gives the same document

    x = client.get(f"{url}&format=xlsx&basis=false", headers=estimator)
    assert x.headers["content-disposition"].endswith(f'"{q["quote_no"]}.xlsx"')
    wb = load_workbook(io.BytesIO(x.content))
    assert wb.sheetnames == ["견적서"]
    item = next(r for r in wb["견적서"].iter_rows(values_only=True) if r[0] == 1)
    assert item[3] == BODY["qty"] and D(str(item[6])) == D(eff["supply_amount"])
    assert D(str(item[7])) == D(eff["vat_amount"])

    with engine().connect() as c:
        rows = c.execute(
            text(
                "SELECT format, official, sha256 FROM quote_reports "
                "WHERE quote_id = :q ORDER BY report_id"
            ),
            {"q": q["quote_id"]},
        ).all()
    assert [(f, o) for f, o, _ in rows] == [("pdf", True), ("pdf", True), ("xlsx", True)]
    assert rows[0][2] == hashlib.sha256(r.content).hexdigest() == rows[1][2]
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError), engine().begin() as c:
        c.execute(text("UPDATE quote_reports SET official = false"))
