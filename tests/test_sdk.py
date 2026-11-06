"""Track 9 Q4: the Python SDK drives the real API (through a TestClient transport) — the three
examples end to end (TC-178)."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk"))
sys.path.insert(0, str(ROOT / "sdk" / "examples"))

import assembly_check
import box_quote
import sheet_flat
from axcad import AxcadError, Client

from tests.test_api_quote import estimator  # noqa: F401 - fixture
from tests.test_p4_parts import machining_master  # noqa: F401 - fixture


@pytest.fixture
def transport(client):
    def send(method, url, headers, data):
        path = url.split("://", 1)[1].split("/", 1)[1]
        r = client.request(method, "/" + path, headers=headers, content=data)
        return r.status_code, dict(r.headers), r.content

    return send


def test_examples(client, world, transport, tmp_path, make_user, headers, machining_master):  # noqa: F811
    from tests.conftest import PASSWORD

    uid = make_user("sdkuser", "DESIGNER", "ESTIMATOR")
    client.post(
        f"/api/projects/{world.pid}/members", json={"user_id": uid}, headers=world.h["admin"]
    )
    token = Client.login_token("http://localhost", "sdkuser", PASSWORD, transport=transport)
    cad = Client("http://localhost", token=token, transport=transport)

    m = sheet_flat.main(cad, world.pid, tmp_path / "flat.dxf")
    assert m["bend_count"] == 1 and m["pierce_count"] == 1
    assert (tmp_path / "flat.dxf").read_bytes().startswith(b"  0\nSECTION")

    q = box_quote.main(cad, world.pid, tmp_path / "q.xlsx")
    assert q["status"] == "DRAFT" and float(q["total_amount"]) > 0
    assert (tmp_path / "q.xlsx").read_bytes()[:2] == b"PK"

    res = assembly_check.main(cad, world.pid)
    assert res["clashes"] == [] and [n["distance_mm"] for n in res["near"]] == [
        pytest.approx(0, abs=1e-6)
    ]

    with pytest.raises(AxcadError) as e:
        cad.add_feature(999999, "PRIMITIVE", {"kind": "BOX", "length": 1, "width": 1, "height": 1})
    assert e.value.status == 404 and e.value.code == "DOCUMENT_NOT_FOUND"
    with pytest.raises(AxcadError):
        Client("http://localhost", token="bad", transport=transport).features(1)


def test_local_needs_a_token(monkeypatch):
    monkeypatch.delenv("AXCAD_LOCAL_TOKEN", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with pytest.raises(AxcadError):
        Client.local()
    c = Client.local(token="t0k")
    assert c.headers == {"X-AXCAD-Local": "t0k"} and c.base == "http://127.0.0.1:18000"


def test_tokens_stay_put():
    """Security review (BUG-58): no redirect is followed with the token, no plain http to another
    host, the install token only to this PC."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            self.send_response(302)
            self.send_header("Location", "/stolen")
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    cad = Client(f"http://127.0.0.1:{srv.server_port}", token="secret")
    with pytest.raises(AxcadError) as e:
        cad.features(1)
    srv.server_close()
    assert e.value.status == 302 and seen == ["/api/documents/1/features"]
    for bad in (
        lambda: Client("http://cad.example", token="t"),
        lambda: Client("file:///etc/passwd"),
        lambda: Client("https://cad.example", local_token="t"),
        lambda: Client.login_token("http://cad.example", "a", "b"),
    ):
        with pytest.raises(AxcadError, match="BAD_URL"):
            bad()
    assert Client("https://cad.example", token="t").headers["Authorization"] == "Bearer t"
