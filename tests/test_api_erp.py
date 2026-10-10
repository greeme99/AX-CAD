"""S12 FN-24 ERP transfer against a mock ERP server: TC-93 (success + payload schema),
TC-94 (500 twice then success), TC-95 (idempotency), TC-96 (unapproved BOM)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.test_api_bom import upload_assembly

TOKEN = "erp-test-token"


class MockErp:
    def __init__(self):
        self.script: list[int] = []
        self.requests: list[dict] = []
        mock = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                mock.requests.append({"headers": dict(self.headers), "body": json.loads(body)})
                code = mock.script.pop(0) if mock.script else 200
                self.send_response(code)
                if code == 302:
                    self.send_header("Location", "http://example.invalid/steal")
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                # a careless ERP echoing the request headers back (token included)
                echo = {"erp_doc": f"E-{len(mock.requests)}", "auth": self.headers["Authorization"]}
                self.wfile.write(json.dumps(echo).encode())

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/bom"


@pytest.fixture
def erp(monkeypatch):
    m = MockErp()
    monkeypatch.setenv("ERP_API_URL", m.url)
    monkeypatch.setenv("ERP_API_TOKEN", TOKEN)
    monkeypatch.setenv("ERP_BACKOFF_S", "0")
    yield m
    m.server.shutdown()


@pytest.fixture
def maker(client, world, make_user, headers):
    uid = make_user("maker", "MANUFACTURING")
    client.post(
        f"/api/projects/{world.pid}/members", json={"user_id": uid}, headers=world.h["admin"]
    )
    return headers("maker")


def approve(client, w):
    a = client.post(
        f"/api/documents/{w.doc}/approvals",
        json={"approver_id": w.ids["reviewer"]},
        headers=w.h["designer"],
    ).json()["data"]
    r = client.post(
        f"/api/approvals/{a['approval_id']}/decision",
        json={"decision": "APPROVED"},
        headers=w.h["reviewer"],
    )
    assert r.json()["data"]["status"] == "APPROVED", r.text


def mapped_bom(client, w, maker, tmp_path):
    upload_assembly(client, w, tmp_path)
    b = client.post(
        f"/api/documents/{w.doc}/boms", json={"source": "REVISION"}, headers=maker
    ).json()["data"]
    nut = next(i for i in b["items"] if i["mapping_status"] == "UNMAPPED")
    return client.patch(
        f"/api/bom-items/{nut['bom_item_id']}", json={"part_no": "N-M6"}, headers=maker
    ).json()["data"]


def test_tc93_95_96_transfer(client, world, maker, erp, tmp_path):
    b = mapped_bom(client, world, maker, tmp_path)
    url = f"/api/boms/{b['bom_id']}/erp"
    r = client.post(url, headers=maker)  # TC-96: drawing not approved yet
    assert r.status_code == 403 and r.json()["error"]["code"] == "BOM_NOT_APPROVED"
    approve(client, world)
    assert client.post(url, headers=world.h["designer"]).status_code == 403  # role

    r = client.post(url, headers=maker)  # TC-93
    assert r.status_code == 200, r.text
    job = client.get(f"/api/integration-jobs?bom_id={b['bom_id']}", headers=maker).json()["data"][
        "items"
    ][0]
    assert (job["status"], job["attempt_count"]) == ("SUCCESS", 1)
    assert job["response_payload"] == {"status": 200}  # ERP bodies are for admins only
    admin_job = client.get("/api/integration-jobs", headers=world.h["admin"]).json()["data"]
    stored = admin_job["items"][0]["response_payload"]["body"]
    assert '"erp_doc": "E-1"' in stored and TOKEN not in stored and "Bearer ***" in stored
    sent = erp.requests[0]
    assert sent["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert (
        sent["headers"]["Idempotency-Key"]
        == job["idempotency_key"]
        == (f"BOM:{world.doc}:{b['revision_id']}")
    )
    p = sent["body"]
    assert p["schema"] == "ax-cad.bom.v1" and p["bom_no"] == b["bom_no"]
    assert set(p["document"]) == {"doc_no", "title", "revision_no", "source_type"}
    assert {i["part_no"]: i["qty"] for i in p["items"]} == {"B-M6": 34, "BR-100": 8, "N-M6": 1}
    assert TOKEN not in json.dumps(
        client.get("/api/integration-jobs", headers=world.h["admin"]).json()
    )
    # what went out cannot change afterwards: no remapping, the delivered job is final
    item = b["items"][0]["bom_item_id"]
    r = client.patch(f"/api/bom-items/{item}", json={"part_no": "X-1"}, headers=maker)
    assert r.status_code == 409
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from backend.db.session import engine

    with pytest.raises(DBAPIError), engine().begin() as c:
        c.execute(text("UPDATE integration_jobs SET last_error = 'x'"))

    r = client.post(url, headers=maker)  # TC-95: same document + revision
    assert r.json()["data"]["duplicate"] and r.json()["data"]["job_id"] == job["job_id"]
    assert len(erp.requests) == 1


def test_tc94_retry_and_failures(client, world, maker, erp, tmp_path):
    b = mapped_bom(client, world, maker, tmp_path)
    approve(client, world)
    erp.script[:] = [500, 500, 500]
    client.post(f"/api/boms/{b['bom_id']}/erp", headers=maker)
    job = client.get("/api/integration-jobs", headers=maker).json()["data"]["items"][0]
    assert (job["status"], job["attempt_count"], job["last_error"]) == ("FAILED", 3, "ERP HTTP 500")
    # TC-94: manual retry, the ERP fails twice more then accepts
    erp.script[:] = [500, 503, 200]
    r = client.post(f"/api/integration-jobs/{job['job_id']}/retry", headers=maker)
    assert r.status_code == 200
    job = client.get("/api/integration-jobs", headers=maker).json()["data"]["items"][0]
    assert (job["status"], job["attempt_count"]) == ("SUCCESS", 3)
    assert (
        len(erp.requests) == 6 and len({r["headers"]["Idempotency-Key"] for r in erp.requests}) == 1
    )
    retry = client.post(f"/api/integration-jobs/{job['job_id']}/retry", headers=maker)
    assert retry.status_code == 409  # a delivered job is not sent again
    # the frozen request payload is audited once (INSERT), not re-copied by each status change
    r = client.get(
        f"/api/audit-logs?object_type=integration_jobs&object_id={job['job_id']}",
        headers=world.h["admin"],
    )
    logs = r.json()["data"]["items"]
    assert len(logs) > 2 and logs[-1]["action"] == "INSERT"
    assert "request_payload" in logs[-1]["new_value"]
    assert all("request_payload" not in (x["new_value"] or {}) for x in logs[:-1])
    assert any(x["new_value"]["status"] == "SUCCESS" for x in logs[:-1])  # changes still logged


def test_rejections_redirects_and_config(client, world, maker, erp, tmp_path, monkeypatch):
    from backend.services import erp as svc

    b = mapped_bom(client, world, maker, tmp_path)
    approve(client, world)
    erp.script[:] = [302]  # a redirect is a failure, not followed (token stays home)
    client.post(f"/api/boms/{b['bom_id']}/erp", headers=maker)
    job = client.get("/api/integration-jobs", headers=maker).json()["data"]["items"][0]
    assert (job["status"], job["attempt_count"], job["last_error"]) == ("FAILED", 1, "ERP HTTP 302")
    erp.script[:] = [400]  # the ERP refused the payload: no blind retries
    client.post(f"/api/integration-jobs/{job['job_id']}/retry", headers=maker)
    job = client.get("/api/integration-jobs", headers=maker).json()["data"]["items"][0]
    assert (job["status"], job["attempt_count"]) == ("FAILED", 1)
    assert len(erp.requests) == 2
    assert client.get("/api/integration-jobs", headers=world.h["designer"]).status_code == 403

    # the drawing is revised (back to DRAFT): a retry of the old job stays home
    upload_assembly(client, world, tmp_path)
    r = client.post(f"/api/integration-jobs/{job['job_id']}/retry", headers=maker)
    assert r.status_code == 403 and r.json()["error"]["code"] == "BOM_NOT_APPROVED"
    assert len(erp.requests) == 2

    for url, ok in (
        ("https://erp.example/api", True),
        ("http://erp.example/api", False),  # bearer token in clear text
        ("http://127.0.0.1:9/x", True),
        ("file:///etc/passwd", False),
    ):
        monkeypatch.setenv("ERP_API_URL", url)
        assert svc.configured() is ok, url
    monkeypatch.delenv("ERP_API_URL")
    assert not svc.configured()
    r = client.post(f"/api/integration-jobs/{job['job_id']}/retry", headers=maker)
    assert r.status_code == 403  # still not approved, checked before the configuration
