"""S11 quote revisions: a confirmed quote changes only through a new revision (FN-19), which
replaces it once approved (SUPERSEDED). COPY keeps the confirmed numbers, RECALC recomputes."""

from decimal import Decimal as D

import pytest

from tests.test_api_quote import estimator  # noqa: F401 - fixture
from tests.test_api_quote_approval import BODY, WHY, new_quote
from tests.test_api_quote_report import confirmed, supplier  # noqa: F401 - fixture
from tests.test_api_quotes import active, upload  # noqa: F401 - fixture

NOTE = "고객 요청으로 수량·단가 재협의"


def approve(client, w, estimator, q):  # noqa: F811
    q = client.post(
        f"/api/quotes/{q['quote_id']}/approvals",
        json={"approver_id": w.ids["reviewer"]},
        headers=estimator,
    ).json()["data"]
    aid = q["approvals"][0]["approval_id"]
    r = client.post(
        f"/api/quote-approvals/{aid}/decision",
        json={"decision": "APPROVED"},
        headers=w.h["reviewer"],
    )
    return r.json()["data"]


def test_copy_revision_replaces_the_confirmed_quote(client, world, estimator, active, supplier):  # noqa: F811
    draft = new_quote(client, world, estimator, **BODY)
    r = client.post(
        f"/api/quotes/{draft['quote_id']}/revisions",
        json={"mode": "COPY", "change_note": NOTE},
        headers=estimator,
    )
    assert r.status_code == 409  # drafts are edited directly, not revised

    q = confirmed(client, world, estimator)
    url = f"/api/quotes/{q['quote_id']}/revisions"
    issued = client.get(f"/api/quotes/{q['quote_id']}/report?official=true", headers=estimator)
    assert issued.status_code == 200
    assert (
        client.post(
            url, json={"mode": "COPY", "change_note": "짧음"}, headers=estimator
        ).status_code
        == 422
    )
    assert (
        client.post(
            url, json={"mode": "COPY", "change_note": NOTE}, headers=world.h["reviewer"]
        ).status_code
        == 403
    )

    r = client.post(url, json={"mode": "COPY", "change_note": NOTE}, headers=estimator)
    assert r.status_code == 200, r.text
    rev = r.json()["data"]
    assert rev["quote_no"] == f"{q['quote_no']}-R1" and rev["revision_no"] == 1
    assert (rev["parent_quote_id"], rev["status"], rev["change_note"]) == (
        q["quote_id"],
        "DRAFT",
        NOTE,
    )
    # the confirmed numbers, adjustments and traces come along
    pick = lambda ls: [
        (ln["calculated_amount"], ln["override_amount"], len(ln["traces"])) for ln in ls
    ]
    assert pick(rev["lines"]) == pick(q["lines"]) and rev["effective"] == q["effective"]
    assert (
        client.get(f"/api/quotes/{q['quote_id']}", headers=estimator).json()["data"][
            "next_quote_id"
        ]
        == rev["quote_id"]
    )
    again = client.post(url, json={"mode": "COPY", "change_note": NOTE}, headers=estimator)
    assert again.status_code == 409 and again.json()["error"]["code"] == "QUOTE_REVISION_EXISTS"

    # the revision is adjusted and approved: it replaces the original
    line = next(ln for ln in rev["lines"] if ln["cost_category"] == "LABOR")
    r = client.patch(
        f"/api/quote-lines/{line['quote_line_id']}",
        json={"field": "amount", "value": "10000", "reason": WHY},
        headers=estimator,
    )
    assert r.status_code == 200
    done = approve(client, world, estimator, rev)
    assert done["status"] == "CONFIRMED"
    old = client.get(f"/api/quotes/{q['quote_id']}", headers=estimator).json()["data"]
    assert old["status"] == "SUPERSEDED"
    assert D(done["effective"]["total_amount"]) != D(old["effective"]["total_amount"])
    # the replaced quote keeps what it issued, and issues nothing new
    rep = f"/api/quotes/{q['quote_id']}/report?official=true"
    assert client.get(rep, headers=estimator).content == issued.content
    x = client.get(f"{rep}&format=xlsx", headers=estimator)
    assert x.status_code == 409 and x.json()["error"]["code"] == "QUOTE_SUPERSEDED"
    assert (
        client.post(url, json={"mode": "COPY", "change_note": NOTE}, headers=estimator).status_code
        == 409
    )

    # the chain continues from the newest confirmed revision
    r2 = client.post(
        f"/api/quotes/{done['quote_id']}/revisions",
        json={"mode": "COPY", "change_note": NOTE},
        headers=estimator,
    ).json()["data"]
    assert r2["quote_no"] == f"{q['quote_no']}-R2" and r2["revision_no"] == 2
    listing = client.get(f"/api/documents/{world.doc}/quotes", headers=estimator).json()["data"][
        "items"
    ]
    assert {x["quote_id"]: x["revision_no"] for x in listing}[r2["quote_id"]] == 2


def test_recalc_revision_follows_the_current_drawing(client, world, estimator, active):  # noqa: F811
    q = approve(client, world, estimator, new_quote(client, world, estimator, **BODY))
    assert q["status"] == "CONFIRMED"
    new_rev = upload(client, world)  # the drawing was revised after the quote
    r = client.post(
        f"/api/quotes/{q['quote_id']}/revisions",
        json={"mode": "RECALC", "change_note": "도면 Rev B 반영 재산출"},
        headers=estimator,
    )
    assert r.status_code == 200, r.text
    rev = r.json()["data"]
    assert rev["revision_id"] == new_rev != q["revision_id"]
    assert rev["quote_no"].endswith("-R1") and rev["parent_quote_id"] == q["quote_id"]
    assert rev["inputs"]["qty"] == BODY["qty"] and rev["inputs"]["input_source"]["qty"] == "USER"
    assert all(t["revision_id"] == new_rev for ln in rev["lines"] for t in ln["traces"])


def test_review_fixes(client, world, estimator, active, make_user, headers):  # noqa: F811
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from backend.db.session import engine

    # a reviewer who adjusted the original cannot approve the COPY that carries the adjustment
    both = make_user("both", "ESTIMATOR", "REVIEWER")
    client.post(
        f"/api/projects/{world.pid}/members", json={"user_id": both}, headers=world.h["admin"]
    )
    q = new_quote(client, world, estimator, **BODY)
    client.patch(
        f"/api/quote-lines/{q['lines'][0]['quote_line_id']}",
        json={"field": "amount", "value": "9000", "reason": WHY},
        headers=headers("both"),
    )
    q = approve(client, world, estimator, q)
    assert q["status"] == "CONFIRMED"
    rev = client.post(
        f"/api/quotes/{q['quote_id']}/revisions",
        json={"mode": "COPY", "change_note": NOTE},
        headers=estimator,
    ).json()["data"]
    assert both in rev["authors"]
    r = client.post(
        f"/api/quotes/{rev['quote_id']}/approvals", json={"approver_id": both}, headers=estimator
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "APPROVER_SELF"

    # an unwanted draft revision is abandoned, which frees the confirmed quote for another one
    assert client.post(f"/api/quotes/{q['quote_id']}/abandon", headers=estimator).status_code == 409
    gone = client.post(f"/api/quotes/{rev['quote_id']}/abandon", headers=estimator).json()["data"]
    assert gone["status"] == "ABANDONED"
    r2 = client.post(
        f"/api/quotes/{q['quote_id']}/revisions",
        json={"mode": "COPY", "change_note": NOTE},
        headers=estimator,
    )
    assert r2.status_code == 200 and r2.json()["data"]["quote_no"].endswith("-R2")
    assert (
        client.get(f"/api/quotes/{q['quote_id']}", headers=estimator).json()["data"][
            "next_quote_id"
        ]
        == r2.json()["data"]["quote_id"]
    )

    # the header cannot be rewritten or pushed around outside the approval flow
    for sql in (
        "UPDATE quote_headers SET total_amount = 1 WHERE quote_id = :q",
        "UPDATE quote_headers SET parent_quote_id = NULL, revision_no = 0 WHERE quote_id = :r",
        "UPDATE quote_headers SET status = 'DRAFT' WHERE quote_id = :q",
    ):
        with pytest.raises(DBAPIError), engine().begin() as c:
            c.execute(text(sql), {"q": q["quote_id"], "r": rev["quote_id"]})


def test_master_outdated_is_flagged(client, world, estimator, active):  # noqa: F811
    from tests.test_api_master import FULL

    q = approve(client, world, estimator, new_quote(client, world, estimator, **BODY))
    admin = world.h["admin"]
    vid = client.post(
        "/api/master-versions",
        json={"version_code": "Q2", "effective_from": "2021-01-01"},
        headers=admin,
    ).json()["data"]["version_id"]
    client.put(f"/api/master-versions/{vid}", json=FULL, headers=admin)
    assert client.post(f"/api/master-versions/{vid}/activate", headers=admin).status_code == 200
    rev = client.post(
        f"/api/quotes/{q['quote_id']}/revisions",
        json={"mode": "COPY", "change_note": NOTE},
        headers=estimator,
    ).json()["data"]
    v = client.post(f"/api/quotes/{rev['quote_id']}/validate", headers=estimator).json()["data"]
    assert "QUOTE_MASTER_OUTDATED" in {lg["code"] for lg in v["logs"]} and not v["has_errors"]
