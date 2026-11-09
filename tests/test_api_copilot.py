"""Track 9 Q1: Copilot API — findings per context, ask (commands, checks, help, LLM), settings."""

import json

import pytest

from backend.api import routes_copilot
from core.copilot import llm
from tests.test_api_model import box_and_hole
from tests.test_api_quote import estimator, plate_dxf  # noqa: F401 - fixture
from tests.test_api_quotes import active  # noqa: F401 - fixture


def ask(client, h, message, **ctx):
    r = client.post("/api/copilot/ask", json={"message": message, "context": ctx}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def upload(client, w, data):
    r = client.post(
        f"/api/documents/{w.doc}/dxf", files={"file": ("p.dxf", data)}, headers=w.h["designer"]
    )
    return r.json()["data"]["revision_id"]


def test_findings_for_drawing_and_model(client, world):
    h = world.h["designer"]
    r = client.get(f"/api/copilot/findings?document_id={world.doc}", headers=h)
    assert r.status_code == 200 and r.json()["data"]["findings"] == []  # empty document
    rid = upload(client, world, plate_dxf())
    data = client.get(f"/api/copilot/findings?revision_id={rid}", headers=h).json()["data"]
    assert data["document_type"] == "DRAWING"
    assert "DFM_TITLE_MISSING" in {f["code"] for f in data["findings"]}  # no title block
    _, _a, b = box_and_hole(client, world)
    data = client.get(f"/api/copilot/findings?document_id={world.doc}", headers=h).json()["data"]
    codes = {f["code"] for f in data["findings"]}
    assert "DFM_TITLE_MISSING" in codes  # the drawing is still checked
    assert sum(data["counts"].values()) == len(data["findings"])
    # a suppressed feature is reported and targets that feature
    client.post(f"/api/features/{b}/suppress", json={"suppressed": True}, headers=h)
    data = client.get(f"/api/copilot/findings?document_id={world.doc}", headers=h).json()["data"]
    sup = [f for f in data["findings"] if f["code"] == "DFM_FEATURE_SUPPRESSED"]
    assert sup and sup[0]["target"] == {"kind": "feature", "id": b}


def test_quote_and_general_drawings(client, world):
    """New projects say whether they are quoted; their documents take it, 캐드 메뉴 asks per
    drawing, and a general drawing gets drawing-quality checks only (no title/contour)."""
    h = world.h["designer"]
    p = client.post(
        "/api/projects",
        json={"project_code": "GEN-1", "project_name": "배치도", "needs_quote": False},
        headers=h,
    ).json()["data"]
    assert p["needs_quote"] is False
    new = lambda no, **kw: client.post(
        f"/api/projects/{p['project_id']}/documents",
        json={"doc_no": no, "doc_type": "DRAWING", "title": "t", **kw},
        headers=h,
    ).json()["data"]
    assert new("D1")["needs_quote"] is False  # the project's choice
    assert new("D2", needs_quote=True)["needs_quote"] is True  # asked per drawing
    rid = upload(client, world, plate_dxf())
    codes = lambda: {
        f["code"]
        for f in client.get(f"/api/copilot/findings?revision_id={rid}", headers=h).json()["data"][
            "findings"
        ]
    }
    assert "DFM_TITLE_MISSING" in codes()  # world's document: a quote drawing (default)
    r = client.patch(f"/api/documents/{world.doc}", json={"needs_quote": False}, headers=h)
    assert r.status_code == 200 and r.json()["data"]["needs_quote"] is False
    assert "DFM_TITLE_MISSING" not in codes()
    r = client.patch(
        f"/api/documents/{world.doc}", json={"needs_quote": True}, headers=world.h["viewer"]
    )
    assert r.status_code == 403
    assert client.patch(f"/api/documents/{world.doc}", json={}, headers=h).status_code == 422


def test_findings_validation_and_access(client, world):
    h = world.h["designer"]
    for q in ("", f"document_id={world.doc}&quote_id=1"):
        r = client.get(f"/api/copilot/findings?{q}", headers=h)
        assert r.status_code == 422
    # viewers cannot run checks; strangers do not see the document
    r = client.get(f"/api/copilot/findings?document_id={world.doc}", headers=world.h["viewer"])
    assert r.status_code == 403
    r = client.get("/api/copilot/findings?document_id=999999", headers=h)
    assert r.status_code == 404


def test_quote_advice(client, world, estimator, active):  # noqa: F811
    rid = upload(client, world, plate_dxf())
    q = client.post("/api/quotes", json={"revision_id": rid, "qty": 1}, headers=estimator).json()
    qid = q["data"]["quote_id"]
    data = client.get(f"/api/copilot/findings?quote_id={qid}", headers=estimator).json()["data"]
    mat = next(f for f in data["findings"] if f["code"] == "QUOTE_MATERIAL_REQUIRED")
    assert mat["severity"] == "ERROR" and "재질" in mat["fix"]
    a = ask(client, estimator, "견적 점검해 줘", page="quote", quote_id=qid)
    assert a["source"] == "RULE" and "오류" in a["answer"] and a["findings"]


def test_ask_commands_help_and_fallback(client, world, monkeypatch, tmp_path):
    h = world.h["designer"]
    a = ask(client, h, "100x50 사각형 (10,20)", page="cad2d")
    assert a["source"] == "RULE" and a["actions"][0]["inputs"] == ["REC", "10,20", "@100,50"]
    a = ask(client, h, "지름 30 높이 50 원통", page="model3d", document_id=world.doc)
    assert a["actions"][0]["feature_type"] == "PRIMITIVE"
    a = ask(client, h, "원 하나 그려", page="cad2d")
    assert a["source"] == "RULE" and not a["actions"] and "반지름" in a["answer"]
    a = ask(client, h, "스냅은 어떻게 쓰나요", page="cad2d")
    assert a["source"] == "KB" and "F3" in a["answer"]
    a = ask(client, h, "점검해 줘", page="model3d", document_id=world.doc)
    assert a["source"] == "RULE" and "지적 사항이 없습니다" in a["answer"]
    monkeypatch.delenv("AXCAD_GEMINI_API_KEY", raising=False)
    a = ask(client, h, "오늘 점심 뭐 먹지", page="home")
    assert a["source"] == "NONE" and a["related"]
    r = client.post("/api/copilot/ask", json={"message": "x" * 501}, headers=h)
    assert r.status_code == 422
    r = client.post(
        "/api/copilot/ask", json={"message": "a", "context": {"page": "evil"}}, headers=h
    )
    assert r.status_code == 422
    t = client.get("/api/copilot/help?page=model3d", headers=h).json()["data"]["topics"]
    assert t and all(set(x) == {"id", "q", "a"} for x in t)


def test_settings_admin_only_and_key_hidden(client, world, monkeypatch):
    monkeypatch.delenv("AXCAD_GEMINI_API_KEY", raising=False)
    r = client.put("/api/copilot/settings", json={"llm_enabled": True}, headers=world.h["designer"])
    assert r.status_code == 403
    admin = world.h["admin"]
    r = client.put(
        "/api/copilot/settings", json={"llm_enabled": True, "api_key": "AIzaSECRET"}, headers=admin
    )
    assert r.status_code == 200 and "AIzaSECRET" not in r.text
    got = client.get("/api/copilot/settings", headers=world.h["viewer"]).json()["data"]
    assert got["llm_enabled"] and got["llm_configured"] and got["key_source"] == "saved"
    r = client.put("/api/copilot/settings", json={"api_key": "bad key<script>"}, headers=admin)
    assert r.status_code == 422


def test_llm_fallback_sends_summary_only(client, world, monkeypatch):
    monkeypatch.delenv("AXCAD_GEMINI_API_KEY", raising=False)
    h = world.h["designer"]
    box_and_hole(client, world)
    seen = []

    def post(url, data, headers):
        seen.append(json.loads(data))
        return 200, json.dumps(
            {"candidates": [{"content": {"parts": [{"text": "LLM 답"}]}}]}
        ).encode()

    monkeypatch.setattr(llm, "_post", post)
    monkeypatch.setattr(llm, "LIMIT", llm.RateLimit(calls=1, window=60))
    q = "밀링 가공비를 줄이는 일반적인 방법은"
    a = ask(client, h, q, page="model3d", document_id=world.doc)
    assert a["source"] == "NONE" and not seen  # LLM off by default: nothing leaves the PC
    client.put(
        "/api/copilot/settings",
        json={"llm_enabled": True, "api_key": "K1"},
        headers=world.h["admin"],
    )
    a = ask(client, h, q, page="model3d", document_id=world.doc)
    assert a["source"] == "LLM" and a["answer"] == "LLM 답"
    sent = seen[0]["contents"][0]["parts"][0]["text"]
    ctx = json.loads(sent.split("\n", 1)[1].split("\n\n[질문]")[0])
    assert ctx["page"] == "model3d" and ctx["feature_counts"] == {"EXTRUDE": 2}
    for secret in ("a.dxf", "40", "30", "DXF", "revision"):  # no file names, sizes, ids
        assert secret not in json.dumps(ctx)
    a = ask(client, h, q, page="model3d")
    assert "너무 잦습니다" in a["answer"]  # rate limit
    a = ask(client, world.h["viewer"], q, page="home")
    assert a["source"] == "NONE" and len(seen) == 1  # viewers never spend the key


def test_settings_change_is_audited(client, world, monkeypatch):
    from sqlalchemy import text

    from backend.db.session import engine

    monkeypatch.delenv("AXCAD_GEMINI_API_KEY", raising=False)
    client.put(
        "/api/copilot/settings",
        json={"llm_enabled": True, "api_key": "K1"},
        headers=world.h["admin"],
    )
    with engine().connect() as c:
        row = c.execute(
            text("SELECT new_value, user_id FROM audit_logs WHERE object_type = 'copilot_settings'")
        ).one()
    assert row.new_value == {
        "llm_enabled": True,
        "key_source": "saved",
        "ollama_enabled": False,
        "ollama_url": "http://localhost:11434/v1",
        "key": "SET",
    }
    assert "K1" not in str(row.new_value) and row.user_id == world.ids["admin"]
    r = client.get("/api/audit-logs?object_type=copilot_settings", headers=world.h["reviewer"])
    assert r.status_code == 200 and r.json()["data"]["items"] == []  # admin-only log


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Copilot tests never reach the internet, even if a key is in the environment."""
    monkeypatch.setattr(
        routes_copilot.llm, "GEMINI_URL", "https://generativelanguage.googleapis.com/test"
    )
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def test_ollama_on_this_pc_answers_when_gemini_is_off(client, world, monkeypatch):
    monkeypatch.delenv("AXCAD_GEMINI_API_KEY", raising=False)
    admin, h = world.h["admin"], world.h["designer"]
    put = lambda js: client.put("/api/copilot/settings", json=js, headers=admin)
    for bad in ("http://10.0.0.7:11434/v1", "https://api.example.com/v1"):
        r = put({"ollama_enabled": True, "ollama_url": bad})
        assert r.status_code == 422 and r.json()["error"]["code"] == "REQUEST_INVALID"
    assert put({"ollama_enabled": True}).json()["data"]["ollama_enabled"] is True
    seen = []

    def post(url, data, headers, timeout=None):
        seen.append((url, json.loads(data)["model"]))
        content = (
            '(command "C" (list 0 0) 10)' if "coder" in json.loads(data)["model"] else "로컬 답"
        )
        return 200, json.dumps({"choices": [{"message": {"content": content}}]}).encode()

    monkeypatch.setattr(llm, "_post", post)
    monkeypatch.setattr(llm, "LIMIT", llm.RateLimit())
    a = ask(client, h, "밀링 가공비를 줄이는 일반적인 방법은", page="home")
    assert (
        a["source"] == "LLM" and a["answer"] == "로컬 답" and a["model"] == "ollama/qwen3:4b-q4_K_M"
    )
    r = client.post("/api/copilot/draw", json={"text": "집 모양을 그려 줘"}, headers=h).json()[
        "data"
    ]
    assert r["source"] == "LLM" and r["model"] == "ollama/qwen2.5-coder:7b-instruct-q4_K_M"
    assert {u for u, _ in seen} == {"http://localhost:11434/v1/chat/completions"}
