"""S9 quotes API: create from a 2D revision or the 3D model, persisted lines/traces/logs
(TC-71 trace completeness), access rules."""

from decimal import Decimal as D

import pytest
from sqlalchemy import text

from backend.db.session import engine
from tests.test_api_master import FULL
from tests.test_api_model import box_and_hole
from tests.test_api_quote import estimator, plate_dxf  # noqa: F401 - fixture


@pytest.fixture
def active(client, world):
    admin = world.h["admin"]
    vid = client.post(
        "/api/master-versions",
        json={"version_code": "Q1", "effective_from": "2020-01-01"},
        headers=admin,
    ).json()["data"]["version_id"]
    client.put(f"/api/master-versions/{vid}", json=FULL, headers=admin)
    assert client.post(f"/api/master-versions/{vid}/activate", headers=admin).status_code == 200
    return vid


def upload(client, w):
    r = client.post(
        f"/api/documents/{w.doc}/dxf",
        files={"file": ("p.dxf", plate_dxf())},
        headers=w.h["designer"],
    )
    return r.json()["data"]["revision_id"]


def test_no_active_master_data(client, world, estimator):  # noqa: F811
    rid = upload(client, world)
    r = client.post("/api/quotes", json={"revision_id": rid, "qty": 1}, headers=estimator)
    assert r.status_code == 409 and r.json()["error"]["code"] == "MASTER_NOT_ACTIVE"


def test_quote_from_revision_is_traced(client, world, estimator, active):  # noqa: F811
    rid = upload(client, world)
    body = {"revision_id": rid, "qty": 3, "material_code": "SS400", "thickness_mm": "2"}
    r = client.post("/api/quotes", json=body, headers=estimator)
    assert r.status_code == 200, r.text
    q = r.json()["data"]
    assert q["master_version_id"] == active and q["status"] == "DRAFT" and not q["has_errors"]
    cats = {ln["cost_category"] for ln in q["lines"]}
    assert cats == {"MATERIAL", "LABOR", "OVERHEAD"}
    handles = {
        e["handle"]
        for e in client.get(f"/api/revisions/{rid}/render", headers=world.h["designer"]).json()[
            "data"
        ]["entities"]
    }
    for ln in q["lines"]:
        assert ln["traces"], ln["item_code"]
        for t in ln["traces"]:
            assert t["rule_code"] and t["formula_text"] and t["revision_id"] == rid
            assert t["source_kind"] != "ENTITY" or set(t["sources"]) <= handles
            assert t["source_count"] >= len(t["sources"]) > 0
    # amounts are whole tens (FLOOR, 10, TOTAL scope rounds the supply amount once)
    assert D(q["supply_amount"]) % 10 == 0 and D(q["total_amount"]) == D(q["supply_amount"]) + D(
        q["vat_amount"]
    )
    assert {k: q["inputs"][k] for k in ("qty", "material_code", "thickness_mm")} == {
        "qty": 3,
        "material_code": "SS400",
        "thickness_mm": "2",
    }
    assert "items" not in q["metrics"]  # snapshot keeps numbers; handles live in the traces
    # TC-71 as a DB query: no line without a trace, no trace without a source
    with engine().connect() as c:
        orphan = c.scalar(
            text(
                "SELECT count(*) FROM quote_lines l WHERE NOT EXISTS "
                "(SELECT 1 FROM quote_traces t WHERE t.quote_line_id = l.quote_line_id)"
            )
        )
    assert orphan == 0

    got = client.get(f"/api/quotes/{q['quote_id']}", headers=world.h["reviewer"])
    assert got.status_code == 200 and got.json()["data"]["supply_amount"] == q["supply_amount"]
    listing = client.get(f"/api/documents/{world.doc}/quotes", headers=estimator).json()["data"]
    assert [x["quote_id"] for x in listing["items"]] == [q["quote_id"]]


def test_missing_inputs_are_logged_not_guessed(client, world, estimator, active):  # noqa: F811
    rid = upload(client, world)
    r = client.post("/api/quotes", json={"revision_id": rid}, headers=estimator)
    assert r.status_code == 422 and r.json()["error"]["code"] == "QUOTE_QTY_REQUIRED"
    q = client.post("/api/quotes", json={"revision_id": rid, "qty": 1}, headers=estimator).json()[
        "data"
    ]
    assert q["has_errors"] and any(lg["code"] == "QUOTE_MATERIAL_REQUIRED" for lg in q["logs"])
    assert not any(ln["cost_category"] == "MATERIAL" for ln in q["lines"])


def test_quote_from_3d_model(client, world, estimator, active):  # noqa: F811
    box_and_hole(client, world)
    r = client.post(
        "/api/quotes",
        json={"document_id": world.doc, "qty": 2, "material_code": "SS400"},
        headers=estimator,
    )
    assert r.status_code == 200, r.text
    q = r.json()["data"]
    mat = next(ln for ln in q["lines"] if ln["cost_category"] == "MATERIAL")
    # box 12000 + cylinder pi*25*20 mm³, 7.85 g/cm³, scrap 5 %, 2 pcs
    vol = D(12000) + D("1570.796326794897")
    assert D(mat["calculated_qty"]) == pytest.approx(
        vol * D("7.85") / 1000000 * D("1.05") * 2, abs=D("0.000001")
    )
    assert {t["source_kind"] for t in mat["traces"]} == {"FEATURE"}


def test_quote_access(client, world, estimator, active, make_user, headers):  # noqa: F811
    rid = upload(client, world)
    body = {"revision_id": rid, "qty": 1, "material_code": "SS400", "thickness_mm": "2"}
    assert client.post("/api/quotes", json=body, headers=world.h["designer"]).status_code == 403
    qid = client.post("/api/quotes", json=body, headers=estimator).json()["data"]["quote_id"]
    make_user("outsider", "ESTIMATOR")
    assert client.get(f"/api/quotes/{qid}", headers=headers("outsider")).status_code == 404
    assert client.get(f"/api/quotes/{qid}", headers=world.h["viewer"]).status_code == 403
    for bad in (
        {"qty": 1},
        {"revision_id": rid, "document_id": world.doc, "qty": 1},
        body | {"qty": 0},
    ):
        assert client.post("/api/quotes", json=bad, headers=estimator).status_code == 400


def test_quote_rows_are_write_once_and_numbered(client, world, estimator, active):  # noqa: F811
    from sqlalchemy.exc import DBAPIError

    rid = upload(client, world)
    body = {"revision_id": rid, "qty": 2, "material_code": "SS400", "thickness_mm": "2"}
    q = client.post("/api/quotes", json=body, headers=estimator).json()["data"]
    assert q["quote_no"].endswith(f"-{q['quote_id']:06d}")
    assert q["inputs"]["input_source"] == {
        "qty": "USER",
        "material_code": "USER",
        "thickness_mm": "USER",
    }
    for sql in (
        "UPDATE quote_lines SET calculated_amount = 1 WHERE quote_id = :q",
        "UPDATE quote_traces SET rule_code = 'X'",
        "UPDATE quote_validation_logs SET code = 'X'",
        # an override without who/why is refused
        "UPDATE quote_lines SET override_amount = 1 WHERE quote_id = :q",
    ):
        with pytest.raises(DBAPIError), engine().begin() as c:
            r = c.execute(text(sql), {"q": q["quote_id"]})
            assert r.rowcount  # the trace/log tables must have had rows to refuse


def test_tc73_75_manual_adjustment(client, world, estimator, active):  # noqa: F811
    rid = upload(client, world)
    body = {"revision_id": rid, "qty": 3, "material_code": "SS400", "thickness_mm": "2"}
    q = client.post("/api/quotes", json=body, headers=estimator).json()["data"]
    mat = next(ln for ln in q["lines"] if ln["cost_category"] == "MATERIAL")
    url = f"/api/quote-lines/{mat['quote_line_id']}"

    # TC-74: reason missing/short, negative value
    for bad in (
        {"field": "amount", "value": "100"},
        {"field": "amount", "value": "100", "reason": "짧음"},
        {"field": "amount", "value": "-1", "reason": "고객 협의 단가"},
    ):
        r = client.patch(url, json=bad, headers=estimator)
        assert r.status_code == 422 and r.json()["error"]["code"] == "QUOTE_OVERRIDE_INVALID", bad
    assert (
        client.patch(
            url,
            json={"field": "amount", "value": "1", "reason": "x" * 5},
            headers=world.h["reviewer"],
        ).status_code
        == 403
    )

    # TC-73: calculated untouched, override + who/why stored, effective totals move, audit row
    r = client.patch(
        url,
        json={"field": "unit_price", "value": "2000", "reason": "고객 협의 단가 적용"},
        headers=estimator,
    )
    assert r.status_code == 200, r.text
    q2 = r.json()["data"]
    m2 = next(ln for ln in q2["lines"] if ln["quote_line_id"] == mat["quote_line_id"])
    assert (
        m2["calculated_amount"] == mat["calculated_amount"]
        and m2["calculated_unit_price"] == mat["calculated_unit_price"]
    )
    assert m2["override_unit_price"] == "2000.00" and m2["overridden_by"] == world.ids.get(
        "estimator", m2["overridden_by"]
    )
    assert m2["effective_amount"] == m2["override_amount"] != mat["calculated_amount"]
    assert q2["supply_amount"] == q["supply_amount"]  # header keeps the engine's numbers
    assert q2["effective"]["material_cost"] == m2["override_amount"]
    logs = client.get(
        f"/api/audit-logs?object_type=quote_lines&object_id={q['quote_id']}",
        headers=world.h["reviewer"],
    ).json()["data"]["items"]
    assert any(x["action"] == "UPDATE" for x in logs)

    v = client.post(f"/api/quotes/{q['quote_id']}/validate", headers=estimator).json()["data"]
    assert "QUOTE_OVERRIDE_LARGE" in {lg["code"] for lg in v["logs"]} and not v["has_errors"]

    r = client.delete(f"{url}/override", headers=estimator)
    m3 = next(ln for ln in r.json()["data"]["lines"] if ln["quote_line_id"] == mat["quote_line_id"])
    assert m3["override_amount"] is None and m3["override_reason"] is None

    # TC-75: a confirmed quote cannot be adjusted (approval itself lands in S11)
    with engine().begin() as c:
        c.execute(
            text("UPDATE quote_headers SET status = 'IN_REVIEW' WHERE quote_id = :q"),
            {"q": q["quote_id"]},
        )
        c.execute(  # the header guard (0013) only allows the approval path
            text("UPDATE quote_headers SET status = 'CONFIRMED' WHERE quote_id = :q"),
            {"q": q["quote_id"]},
        )
    r = client.patch(
        url,
        json={"field": "amount", "value": "100", "reason": "확정 후 조정 시도"},
        headers=estimator,
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "QUOTE_CONFIRMED"


def test_s10_review_override_guards(client, world, estimator, active):  # noqa: F811
    from sqlalchemy.exc import DBAPIError

    rid = upload(client, world)
    body = {"revision_id": rid, "qty": 3, "material_code": "SS400", "thickness_mm": "2"}
    q = client.post("/api/quotes", json=body, headers=estimator).json()["data"]
    mat = next(ln for ln in q["lines"] if ln["cost_category"] == "MATERIAL")
    url = f"/api/quote-lines/{mat['quote_line_id']}"
    why = "고객 협의 단가 적용"

    # M1: qty x price beyond numeric(18,2) is refused, not a 500
    r = client.patch(
        url, json={"field": "qty", "value": "999999999999", "reason": why}, headers=estimator
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "QUOTE_OVERRIDE_INVALID"
    # M3: values settle HALF_UP to the cent
    r = client.patch(
        url, json={"field": "unit_price", "value": "1000.005", "reason": why}, headers=estimator
    )
    m = next(ln for ln in r.json()["data"]["lines"] if ln["quote_line_id"] == mat["quote_line_id"])
    assert m["override_unit_price"] == "1000.01"
    # a lump-sum amount replaces the qty/price adjustment
    r = client.patch(
        url, json={"field": "amount", "value": "5000", "reason": why}, headers=estimator
    )
    m = next(ln for ln in r.json()["data"]["lines"] if ln["quote_line_id"] == mat["quote_line_id"])
    assert m["override_amount"] == "5000.00" and m["override_unit_price"] is None
    assert m["override_qty"] is None
    # L1: unknown line and other people's quotes read the same
    assert (
        client.patch(
            "/api/quote-lines/999999",
            json={"field": "amount", "value": "1", "reason": why},
            headers=estimator,
        ).json()["error"]["message"]
        == "Quote not found"
    )

    # M4: once confirmed, the database itself refuses override changes
    with engine().begin() as c:
        c.execute(
            text("UPDATE quote_headers SET status = 'IN_REVIEW' WHERE quote_id = :q"),
            {"q": q["quote_id"]},
        )
        c.execute(  # the header guard (0013) only allows the approval path
            text("UPDATE quote_headers SET status = 'CONFIRMED' WHERE quote_id = :q"),
            {"q": q["quote_id"]},
        )
    with pytest.raises(DBAPIError, match="is CONFIRMED"), engine().begin() as c:
        c.execute(
            text("UPDATE quote_lines SET override_reason = 'x' WHERE quote_line_id = :i"),
            {"i": mat["quote_line_id"]},
        )
