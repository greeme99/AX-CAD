"""S11 FN-22 quote approval: DRAFT -> IN_REVIEW -> CONFIRMED | (rejected) DRAFT (TC-78)."""

import pytest
from sqlalchemy import text

from backend.db.session import engine
from tests.test_api_quote import estimator  # noqa: F401 - fixture
from tests.test_api_quotes import active, upload  # noqa: F401 - fixture

BODY = {"qty": 3, "material_code": "SS400", "thickness_mm": "2"}
WHY = "고객 협의 단가 적용"


def new_quote(client, world, estimator, **body):  # noqa: F811
    rid = upload(client, world)
    r = client.post("/api/quotes", json={"revision_id": rid, **body}, headers=estimator)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_tc78_errors_block_review(client, world, estimator, active):  # noqa: F811
    q = new_quote(client, world, estimator, qty=1)  # no material -> QUOTE_MATERIAL_REQUIRED
    r = client.post(
        f"/api/quotes/{q['quote_id']}/approvals",
        json={"approver_id": world.ids["reviewer"]},
        headers=estimator,
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "QUOTE_HAS_ERRORS"
    assert "QUOTE_MATERIAL_REQUIRED" in r.json()["error"]["message"]
    got = client.get(f"/api/quotes/{q['quote_id']}", headers=estimator).json()["data"]
    assert got["status"] == "DRAFT" and got["approvals"] == []


def test_quote_approval_flow(client, world, estimator, active, make_user, headers):  # noqa: F811
    q = new_quote(client, world, estimator, **BODY)
    qid, line = q["quote_id"], q["lines"][0]["quote_line_id"]
    url = f"/api/quotes/{qid}/approvals"
    rev = {"approver_id": world.ids["reviewer"]}

    assert client.post(url, json=rev, headers=world.h["designer"]).status_code == 403
    bad = client.post(url, json={"approver_id": world.ids["viewer"]}, headers=estimator)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "APPROVER_INVALID"
    # a reviewer who adjusted the numbers cannot approve them
    uid = make_user("both", "ESTIMATOR", "REVIEWER")
    client.post(
        f"/api/projects/{world.pid}/members", json={"user_id": uid}, headers=world.h["admin"]
    )
    client.patch(
        f"/api/quote-lines/{line}",
        json={"field": "amount", "value": "1000", "reason": WHY},
        headers=headers("both"),
    )
    r = client.post(url, json={"approver_id": uid}, headers=estimator)
    assert r.status_code == 422 and r.json()["error"]["code"] == "APPROVER_SELF"

    r = client.post(url, json=rev | {"comment": "검토 부탁드립니다"}, headers=estimator)
    assert r.status_code == 200, r.text
    q = r.json()["data"]
    assert q["status"] == "IN_REVIEW" and q["approvals"][0]["status"] == "PENDING"
    aid = q["approvals"][0]["approval_id"]
    assert client.post(url, json=rev, headers=estimator).json()["error"]["code"] == "INVALID_STATE"

    # under review the numbers are frozen: API and database
    r = client.patch(
        f"/api/quote-lines/{line}",
        json={"field": "amount", "value": "2000", "reason": WHY},
        headers=estimator,
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "QUOTE_IN_REVIEW"
    assert client.delete(f"/api/quote-lines/{line}/override", headers=estimator).status_code == 409
    with pytest.raises(Exception, match="is IN_REVIEW"), engine().begin() as c:
        c.execute(
            text("UPDATE quote_lines SET override_reason = 'x' WHERE quote_line_id = :i"),
            {"i": line},
        )

    # inbox: only the approver sees it
    inbox = client.get("/api/quote-approvals?status=PENDING", headers=world.h["reviewer"])
    assert [(x["approval_id"], x["quote_no"]) for x in inbox.json()["data"]["items"]] == [
        (aid, q["quote_no"])
    ]
    # the amount shown is what gets approved: overrides included, not the engine total
    shown = inbox.json()["data"]["items"][0]["total_amount"]
    assert shown == q["effective"]["total_amount"] != q["total_amount"]
    assert client.get("/api/quote-approvals", headers=estimator).json()["data"]["items"] == []
    decision = f"/api/quote-approvals/{aid}/decision"
    assert (
        client.post(decision, json={"decision": "APPROVED"}, headers=headers("both")).status_code
        == 404
    )

    # reject needs a comment and returns the quote to DRAFT
    r = client.post(decision, json={"decision": "REJECTED"}, headers=world.h["reviewer"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "COMMENT_REQUIRED"
    r = client.post(
        decision,
        json={"decision": "REJECTED", "comment": "단가 근거 보완"},
        headers=world.h["reviewer"],
    )
    assert r.json()["data"]["status"] == "DRAFT"

    # second round: approve -> CONFIRMED, then nothing moves
    aid = client.post(url, json=rev, headers=estimator).json()["data"]["approvals"][0][
        "approval_id"
    ]
    decision = f"/api/quote-approvals/{aid}/decision"
    q = client.post(decision, json={"decision": "APPROVED"}, headers=world.h["reviewer"]).json()[
        "data"
    ]
    assert q["status"] == "CONFIRMED"
    assert [a["status"] for a in q["approvals"]] == ["APPROVED", "REJECTED"]
    again = client.post(
        decision, json={"decision": "REJECTED", "comment": "x"}, headers=world.h["reviewer"]
    )
    assert again.status_code == 409
    r = client.patch(
        f"/api/quote-lines/{line}",
        json={"field": "amount", "value": "2000", "reason": WHY},
        headers=estimator,
    )
    assert r.json()["error"]["code"] == "QUOTE_CONFIRMED"
    logs = client.get(
        f"/api/audit-logs?object_type=quote_approvals&object_id={qid}", headers=world.h["reviewer"]
    ).json()["data"]["items"]
    assert len(logs) >= 4  # two requests, two decisions


def test_admin_cancels_stuck_review(client, world, estimator, active):  # noqa: F811
    q = new_quote(client, world, estimator, **BODY)
    q = client.post(
        f"/api/quotes/{q['quote_id']}/approvals",
        json={"approver_id": world.ids["reviewer"]},
        headers=estimator,
    ).json()["data"]
    cancel = f"/api/quote-approvals/{q['approvals'][0]['approval_id']}/cancel"
    assert client.post(cancel, headers=estimator).status_code == 403
    r = client.post(cancel, headers=world.h["admin"])
    assert r.json()["data"]["status"] == "DRAFT"
    assert r.json()["data"]["approvals"][0]["comment"] == "취소됨(관리자)"
