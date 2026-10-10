"""DB-backed API tests: auth, RBAC, projects/documents, revisions, approvals, audit triggers."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.db.session import engine
from tests.conftest import PASSWORD

LINE = {"type": "LINE", "start": [0, 0], "end": [5, 5]}


def login(client, who, password=PASSWORD):
    return client.post("/api/auth/login", json={"login_id": who, "password": password})


def upload(client, w, data, who="designer"):
    return client.post(
        f"/api/documents/{w.doc}/dxf", files={"file": ("a.dxf", data)}, headers=w.h[who]
    )


def edit(client, w, rid, body, who="designer"):
    return client.post(f"/api/revisions/{rid}/edits", json=body, headers=w.h[who])


def test_login_flow(client, make_user, headers):
    uid = make_user("alice", "DESIGNER")
    make_user("gone", "VIEWER", active=False)
    ok = login(client, "alice").json()["data"]
    assert ok["user"] == {
        "user_id": uid,
        "login_id": "alice",
        "user_name": "Alice",
        "roles": ["DESIGNER"],
    }
    bad = login(client, "alice", "wrong-password")
    unknown = login(client, "nobody")
    assert bad.status_code == unknown.status_code == 401
    assert bad.json() == unknown.json() and bad.json()["error"]["code"] == "AUTH_FAILED"
    assert login(client, "gone").status_code == 401
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {ok['access_token']}"})
    assert me.json()["data"]["login_id"] == "alice"
    r = client.post("/api/auth/refresh", json={"refresh_token": ok["refresh_token"]})
    assert r.status_code == 200 and r.json()["data"]["access_token"]
    # a refresh token is not an access token and vice versa
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {ok['refresh_token']}"})
    assert r.status_code == 401
    r = client.post("/api/auth/refresh", json={"refresh_token": ok["access_token"]})
    assert r.status_code == 401


def test_lockout_after_five_failures(client, make_user, headers):
    make_user("root", "ADMIN")
    uid = make_user("bob", "VIEWER")
    for _ in range(5):
        assert login(client, "bob", "wrong-password").status_code == 401
    r = login(client, "bob")  # correct password, but locked: same 401 as any failure
    assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_FAILED"
    r = client.patch(f"/api/users/{uid}", json={"unlock": True}, headers=headers("root"))
    assert r.status_code == 200 and login(client, "bob").status_code == 200


def test_auth_required(client, world):
    r = client.get("/api/projects")
    assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_REQUIRED"
    r = client.get("/api/projects", headers={"Authorization": "Bearer junk"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "AUTH_INVALID"
    for path in (f"/api/documents/{world.doc}", "/api/approvals", "/api/audit-logs"):
        assert client.get(path).status_code == 401


def test_rbac_and_membership(client, world, make_user, headers, dxf):
    assert upload(client, world, dxf(), "viewer").status_code == 403
    outsider = make_user("outsider", "DESIGNER")
    h = headers("outsider")
    assert client.get(f"/api/projects/{world.pid}", headers=h).status_code == 404
    assert client.get(f"/api/documents/{world.doc}", headers=h).status_code == 404
    assert client.get("/api/projects", headers=h).json()["data"]["items"] == []
    assert upload(client, world, dxf(), "admin").status_code == 200
    assert upload(client, world, dxf(3), "viewer").status_code == 403
    # visible but not allowed to write -> 403; non-admin cannot manage users or members
    v = world.h["viewer"]
    doc = {"doc_no": "X", "doc_type": "PART", "title": "t"}
    r = client.post(f"/api/projects/{world.pid}/documents", headers=v, json=doc)
    assert r.status_code == 403
    r = client.post(f"/api/projects/{world.pid}/members", json={"user_id": outsider}, headers=v)
    assert r.status_code == 403
    assert client.get("/api/users", headers=v).status_code == 403
    assert client.get("/api/audit-logs", headers=v).status_code == 403
    assert client.get("/api/audit-logs", headers=world.h["reviewer"]).status_code == 200


def test_duplicates_conflict(client, world):
    d = world.h["designer"]
    r = client.post("/api/projects", json={"project_code": "P1", "project_name": "x"}, headers=d)
    assert r.status_code == 409 and r.json()["error"]["code"] == "DUPLICATE_KEY"
    doc = {"doc_no": "D1", "doc_type": "PART", "title": "again"}
    r = client.post(f"/api/projects/{world.pid}/documents", json=doc, headers=d)
    assert r.status_code == 409 and r.json()["error"]["code"] == "DUPLICATE_KEY"
    r = client.get(f"/api/projects/{world.pid}/documents?q=d1&status=DRAFT", headers=d)
    assert r.json()["data"]["total"] == 1


def test_audit_logs_scoped_to_member_projects(client, world, dxf):
    adm, rv = world.h["admin"], world.h["reviewer"]
    upload(client, world, dxf())  # revision in P1, where the reviewer is a member
    r = client.post("/api/projects", json={"project_code": "P2", "project_name": "x"}, headers=adm)
    p2 = r.json()["data"]["project_id"]
    doc = {"doc_no": "D9", "doc_type": "DRAWING", "title": "other"}
    d2 = client.post(f"/api/projects/{p2}/documents", json=doc, headers=adm).json()["data"]
    mv = {"version_code": "M1", "effective_from": "2027-01-01"}
    vid = client.post("/api/master-versions", json=mv, headers=adm).json()["data"]["version_id"]

    def seen(h):
        items = client.get("/api/audit-logs?limit=500", headers=h).json()["data"]["items"]
        return {(x["object_type"], x["object_id"]) for x in items}

    mine = seen(rv)
    assert ("documents", str(world.doc)) in mine and ("projects", str(world.pid)) in mine
    assert any(t == "document_revisions" for t, _ in mine)
    assert ("projects", str(p2)) not in mine and ("documents", str(d2["document_id"])) not in mine
    # master data (prices, rates) only for those the master API serves: ESTIMATOR (and ADMIN)
    assert ("master_versions", str(vid)) not in mine
    assert ("projects", str(p2)) in seen(adm)
    url = f"/api/audit-logs?object_type=documents&object_id={d2['document_id']}"
    assert client.get(url, headers=rv).json()["data"]["total"] == 0  # filters cannot widen scope
    # membership is checked when reading: a removed member loses the project's history
    client.delete(f"/api/projects/{world.pid}/members/{world.ids['reviewer']}", headers=adm)
    assert ("documents", str(world.doc)) not in seen(rv)
    assert not {t for t, _ in mine} & {"users", "user_roles", "integration_jobs"}


def test_tc24_missing_fields_named(client, world):
    d = world.h["designer"]
    r = client.post("/api/projects", json={"project_name": "x"}, headers=d)
    err = r.json()["error"]
    assert r.status_code == 422 and err["code"] == "REQUEST_INVALID"
    assert err["details"] == [{"field": "project_code", "type": "missing"}]
    r = client.post(f"/api/projects/{world.pid}/documents", json={"title": "t"}, headers=d)
    fields = {f["field"] for f in r.json()["error"]["details"]}
    assert r.status_code == 422 and fields == {"doc_no", "doc_type"}
    secret = "not-echoed-123"  # field errors never carry the submitted value back
    r = client.post("/api/projects", json={"project_code": secret, "x": secret}, headers=d)
    assert r.status_code == 422 and secret not in r.text


def test_revision_chain_and_diff(client, world, dxf):
    d = world.h["designer"]
    data = dxf(2)  # ezdxf stamps a fresh GUID per write, so reuse the same bytes
    first = upload(client, world, data).json()["data"]
    a = first["revision_id"]
    assert first["revision_no"] == "A" and first["parent_revision_id"] is None
    same = upload(client, world, data).json()["data"]
    assert same["revision_id"] == a and "NO_CHANGE" in same["warnings"]
    ents = client.get(f"/api/revisions/{a}/render", headers=d).json()["data"]["entities"]
    h1, h2 = ents[0]["handle"], ents[1]["handle"]
    body = {
        "created": [{"layer": "0", "geom": LINE}],
        "modified": [{"handle": h1, "layer": "0", "geom": {**LINE, "end": [9, 9]}}],
        "deleted": [h2],
    }
    b = edit(client, world, a, body).json()["data"]
    assert b["revision_no"] == "B" and b["parent_revision_id"] == a
    doc = client.get(f"/api/documents/{world.doc}", headers=d).json()["data"]
    assert doc["current_revision_id"] == b["revision_id"] == doc["current_revision"]["revision_id"]
    r = edit(client, world, a, {"deleted": [h1]})  # A is no longer current
    assert r.status_code == 409 and r.json()["error"]["code"] == "REVISION_NOT_CURRENT"
    items = client.get(f"/api/documents/{world.doc}/revisions", headers=d).json()["data"]["items"]
    assert [i["revision_no"] for i in items] == ["B", "A"] and items[0]["entity_count"] == 2
    r = client.get(f"/api/revisions/{a}/diff/{b['revision_id']}", headers=d).json()["data"]
    assert (r["added"], r["removed"], r["changed"]) == (1, 1, 1)
    assert r["layers"] == [{"layer": "0", "added": 1, "removed": 1, "changed": 1}]


def test_approval_flow(client, world, dxf):
    w, d, rv, v = world, world.h["designer"], world.h["reviewer"], world.h["viewer"]
    rid = upload(client, w, dxf()).json()["data"]["revision_id"]
    ask = f"/api/documents/{w.doc}/approvals"
    assert client.post(ask, json={"approver_id": w.ids["designer"]}, headers=d).status_code == 422
    r = client.post(ask, json={"approver_id": w.ids["viewer"]}, headers=d)
    assert r.json()["error"]["code"] == "APPROVER_INVALID"
    r = client.post(ask, json={"approver_id": w.ids["reviewer"]}, headers=d)
    item = r.json()["data"]
    assert item["status"] == "PENDING" and item["revision_id"] == rid and item["revision_no"] == "A"
    doc = client.get(f"/api/documents/{w.doc}", headers=d).json()["data"]
    assert doc["status"] == "IN_REVIEW"
    r = edit(client, w, rid, {"created": [{"layer": "0", "geom": LINE}]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "DOCUMENT_IN_REVIEW"
    assert client.get(f"/api/revisions/{rid}/dxf", headers=v).status_code == 403
    assert client.get("/api/approvals?status=PENDING", headers=d).json()["data"]["total"] == 0
    inbox = client.get("/api/approvals?status=PENDING", headers=rv).json()["data"]
    assert inbox["total"] == 1
    decide = f"/api/approvals/{item['approval_id']}/decision"
    assert client.post(decide, json={"decision": "APPROVED"}, headers=d).status_code == 404
    r = client.post(decide, json={"decision": "REJECTED", "comment": "  "}, headers=rv)
    assert r.status_code == 422 and r.json()["error"]["code"] == "COMMENT_REQUIRED"
    r = client.post(decide, json={"decision": "REJECTED", "comment": "fix dims"}, headers=rv)
    assert r.json()["data"]["status"] == "REJECTED"
    assert client.get(f"/api/documents/{w.doc}", headers=d).json()["data"]["status"] == "REJECTED"
    item = client.post(ask, json={"approver_id": w.ids["reviewer"]}, headers=d).json()["data"]
    r = client.post(
        f"/api/approvals/{item['approval_id']}/decision", json={"decision": "APPROVED"}, headers=rv
    )
    assert r.json()["data"]["status"] == "APPROVED"
    assert client.get(f"/api/revisions/{rid}/dxf", headers=v).status_code == 200
    assert client.post(f"/api/documents/{w.doc}/release", headers=v).status_code == 403
    r = client.post(f"/api/documents/{w.doc}/release", headers=rv)
    assert r.json()["data"]["status"] == "RELEASED"
    r = edit(client, w, rid, {"created": [{"layer": "0", "geom": LINE}]})  # allowed, back to DRAFT
    assert r.status_code == 200
    assert client.get(f"/api/documents/{w.doc}", headers=d).json()["data"]["status"] == "DRAFT"
    assert client.get(f"/api/revisions/{rid}/dxf", headers=v).status_code == 403
    assert client.get(f"/api/revisions/{rid}/dxf", headers=d).status_code == 200


def test_audit_trail(client, world, dxf):
    rid = upload(client, world, dxf()).json()["data"]["revision_id"]
    client.post(
        f"/api/documents/{world.doc}/approvals",
        json={"approver_id": world.ids["reviewer"]},
        headers=world.h["designer"],
    )
    rv = world.h["reviewer"]

    def logs(kind, oid):
        r = client.get(f"/api/audit-logs?object_type={kind}&object_id={oid}", headers=rv)
        return r.json()["data"]["items"]

    doc_logs = logs("documents", world.doc)  # D1: object_id comes from the PK column, not "id"
    assert [x["action"] for x in doc_logs] == ["UPDATE", "UPDATE", "INSERT"]
    assert all(x["user_id"] == world.ids["designer"] for x in doc_logs)
    assert doc_logs[0]["new_value"]["status"] == "IN_REVIEW"
    assert [x["action"] for x in logs("document_revisions", rid)] == ["INSERT"]
    assert len(logs("approvals", 1)) == 1
    assert logs("projects", world.pid)[0]["user_id"] == world.ids["admin"]
    with engine().connect() as conn:
        users = conn.execute(
            text("SELECT old_value, new_value FROM audit_logs WHERE object_type='users'")
        )
        assert users.rowcount > 0 and "password_hash" not in str(users.fetchall())
    for sql in ("UPDATE audit_logs SET action = 'x'", "DELETE FROM audit_logs"):
        with pytest.raises(DBAPIError, match="append-only"), engine().begin() as conn:
            conn.execute(text(sql))


def test_security_guards(client, world, make_user, headers, dxf):
    w, adm = world, world.h["admin"]
    r = client.patch(f"/api/users/{w.ids['admin']}", json={"is_active": False}, headers=adm)
    assert r.status_code == 409 and r.json()["error"]["code"] == "LAST_ADMIN"
    # the revision author may not be the approver, even when asked by someone else
    dr = make_user("dr", "DESIGNER", "REVIEWER")
    client.post(f"/api/projects/{w.pid}/members", json={"user_id": dr}, headers=adm)
    upload(client, w, dxf(), who="designer")
    w.h["dr"] = headers("dr")
    upload(client, w, dxf(2), who="dr")
    ask = f"/api/documents/{w.doc}/approvals"
    r = client.post(ask, json={"approver_id": dr}, headers=w.h["designer"])
    assert r.json()["error"]["code"] == "APPROVER_SELF"
    # admin can cancel a stuck approval
    item = client.post(ask, json={"approver_id": w.ids["reviewer"]}, headers=w.h["dr"])
    aid = item.json()["data"]["approval_id"]
    assert client.post(f"/api/approvals/{aid}/cancel", headers=w.h["designer"]).status_code == 403
    assert client.post(f"/api/approvals/{aid}/cancel", headers=adm).status_code == 200
    doc = client.get(f"/api/documents/{w.doc}", headers=adm).json()["data"]
    assert doc["status"] == "DRAFT"
    # account history is ADMIN-only
    users_log = "/api/audit-logs?object_type=users"
    assert client.get(users_log, headers=w.h["reviewer"]).json()["data"]["total"] == 0
    assert client.get(users_log, headers=adm).json()["data"]["total"] > 0
    # archived projects are read-only
    client.patch(f"/api/projects/{w.pid}", json={"status": "ARCHIVED"}, headers=adm)
    r = client.post(
        f"/api/projects/{w.pid}/documents",
        json={"doc_no": "X", "doc_type": "DRAWING", "title": "x"},
        headers=w.h["designer"],
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "PROJECT_ARCHIVED"
