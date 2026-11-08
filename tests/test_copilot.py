"""Track 9 Q1: rule-based Copilot (plan.md §15.2, §15.8)."""

import json
import time

import pytest

from core.copilot import dfm, kb, llm, nlcmd

CAD2D = [
    ("100x50 사각형", ["REC", "0,0", "@100,50"]),
    ("사각형 100 x 50 (10,20)에", ["REC", "10,20", "@100,50"]),
    ("가로 120 세로 40 직사각형 그려줘", ["REC", "0,0", "@120,40"]),
    ("200*100 네모", ["REC", "0,0", "@200,100"]),
    ("정사각형 50", ["REC", "0,0", "@50,50"]),
    ("정사각형 30 (5,5)", ["REC", "5,5", "@30,30"]),
    ("반지름 20 원 (0,0)", ["C", "0,0", "20"]),
    ("지름 40 원", ["C", "0,0", "20"]),
    ("원 R15 (100,50)", ["C", "100,50", "15"]),
    ("직경 12.5 원을 (-10,3)에", ["C", "-10,3", "6.25"]),
    ("(0,0)에서 (100,0)까지 선", ["L", "0,0", "100,0", ""]),
    ("선 0,0 100,0 100,50", ["L", "0,0", "100,0", "100,50", ""]),
    ("길이 100 선", ["L", "0,0", "@100<0", ""]),
    ("길이 80 각도 30 직선 (10,10)", ["L", "10,10", "@80<30", ""]),
    ("육각형 반지름 30", ["POL", "6", "0,0", "@30,0"]),
    ("8각형 반지름 25 (50,50)", ["POL", "8", "50,50", "@25,0"]),
    ("오각형 반경 10", ["POL", "5", "0,0", "@10,0"]),
    ("문자 '품번 A-01' (0,-20)", ["T", "0,-20", "", "품번 A-01"]),
    ('텍스트 "REV B" 높이 5', ["T", "0,0", "5", "REV B"]),
]

MODEL3D = [
    ("100x50x20 상자", "PRIMITIVE", {"kind": "BOX", "length": 100, "width": 50, "height": 20}),
    (
        "박스 30x30x10 만들어줘",
        "PRIMITIVE",
        {"kind": "BOX", "length": 30, "width": 30, "height": 10},
    ),
    ("지름 30 높이 50 원통", "PRIMITIVE", {"kind": "CYLINDER", "radius": 15, "height": 50}),
    ("반지름 20 구", "PRIMITIVE", {"kind": "SPHERE", "radius": 20}),
    ("M10x30 볼트 8개", "STANDARD", {"kind": "HEX_BOLT", "size": "M10", "qty": 8, "length": 30}),
    (
        "M8 렌치볼트 길이 25 4개",
        "STANDARD",
        {"kind": "SOCKET_BOLT", "size": "M8", "qty": 4, "length": 25},
    ),
    ("M10 너트 8개", "STANDARD", {"kind": "HEX_NUT", "size": "M10", "qty": 8}),
    ("M6 스프링와셔 12개", "STANDARD", {"kind": "SPRING_WASHER", "size": "M6", "qty": 12}),
]


@pytest.mark.parametrize(("text", "inputs"), CAD2D)
def test_nl_2d_commands(text, inputs):
    r = nlcmd.parse(text, "cad2d")
    assert r and r["kind"] == "cad2d" and r["inputs"] == inputs, r


@pytest.mark.parametrize(("text", "ftype", "params"), MODEL3D)
def test_nl_3d_features(text, ftype, params):
    r = nlcmd.parse(text, "model3d")
    assert r and r["kind"] == "feature" and r["feature_type"] == ftype and r["params"] == params, r


def test_nl_counts_examples():
    assert len(CAD2D) + len(MODEL3D) >= 20  # §15.8: 20 example sentences


def test_nl_hole_dialog_and_hints():
    r = nlcmd.parse("M8 탭 구멍 4개 깊이 15", "model3d")
    assert r == {
        "kind": "dialog",
        "dialog": "HOLE",
        "values": {"count": 4, "size": "M8", "kind": "TAPPED", "depth": 15},
        "summary": r["summary"],
    }
    assert nlcmd.parse("지름 6.6 구멍 2개", "model3d")["values"] == {"count": 2, "diameter": 6.6}
    # a command with a missing value gives a hint, not a guess
    assert "error" in nlcmd.parse("사각형 그려줘", "cad2d")
    assert "error" in nlcmd.parse("원 하나", "cad2d")
    assert "error" in nlcmd.parse("상자 만들어", "model3d")
    assert "error" in nlcmd.parse("M8 볼트 4개", "model3d")  # bolt length
    assert "error" in nlcmd.parse("100x0 사각형", "cad2d")
    # not a command at all, or another page
    assert nlcmd.parse("스냅은 어떻게 쓰나요", "cad2d") is None
    assert nlcmd.parse("선택한 객체 복사", "cad2d") is None
    assert nlcmd.parse("타원 그리는 법", "cad2d") is None
    assert nlcmd.parse("100x50 사각형", "quote") is None
    assert nlcmd.parse("", "cad2d") is None


def test_nl_hostile_input_is_bounded():
    t0 = time.perf_counter()
    for page in ("cad2d", "model3d"):
        nlcmd.parse("1" * 100_000 + " 사각형", page)
        nlcmd.parse("(" * 50_000 + "원 반지름", page)
        nlcmd.parse("M" * 50_000 + " 볼트 구멍 상자", page)
    assert time.perf_counter() - t0 < 1.0
    r = nlcmd.parse("선 " + " ".join(f"{i},{i}" for i in range(500)), "cad2d")
    assert r["inputs"][0] == "L" and len(r["inputs"]) <= 52


def body(fid=1, holes=(), size=(100, 50, 10), warnings=()):
    return {
        "feature_id": fid,
        "name": "판",
        "bbox": {"size": list(size)},
        "holes": list(holes),
        "warnings": list(warnings),
    }


def hole(d, n=1, depth=None, drill=None, fid=7):
    return {"feature_id": fid, "diameter": d, "count": n, "depth": depth, "drill_length": drill}


def codes(items):
    return sorted(f["code"] for f in items)


def test_dfm_model_rules():
    feats = [
        {"feature_id": 1, "feature_type": "EXTRUDE", "status": "OK", "suppressed": False},
        {"feature_id": 2, "feature_type": "FILLET", "status": "FAILED", "suppressed": False},
        {"feature_id": 3, "feature_type": "HOLE", "status": "OK", "suppressed": True},
    ]
    items = dfm.check_model(
        feats,
        [
            body(
                holes=[
                    hole(0.8),  # tiny
                    hole(5, n=2, drill=120),  # 60 mm deep / 5 = 12 > 10
                    hole(8, depth=10),  # fine: a 10 mm block is machined, not punched
                ],
                warnings=["OPEN_SHELLS"],
            ),
            body(fid=9, size=(100, 50, 0.2)),
            body(fid=10, size=(100, 50, 3), holes=[hole(2, depth=3), hole(4, depth=3)]),
        ],
    )
    assert codes(items) == sorted(
        [
            "DFM_FEATURE_FAILED",
            "DFM_FEATURE_SUPPRESSED",
            "DFM_HOLE_TINY",
            "DFM_HOLE_DEEP",
            "DFM_HOLE_LT_THICKNESS",  # 2 mm hole in a 3 mm sheet
            "DFM_SHELL_CHECK",
            "DFM_THIN_BODY",
        ]
    )
    first = dfm.sort_findings(items)[0]
    assert first["severity"] == "ERROR" and first["target"] == {"kind": "feature", "id": 2}
    deep = next(f for f in items if f["code"] == "DFM_HOLE_DEEP")
    assert "12.0" in deep["message"] and deep["target"] == {"kind": "feature", "id": 7}
    # a clean part has nothing to say
    ok = [{"feature_id": 1, "feature_type": "EXTRUDE", "status": "OK", "suppressed": False}]
    assert dfm.check_model(ok, [body(holes=[hole(12, depth=10)])]) == []
    assert codes(dfm.check_model(ok, [])) == ["DFM_NO_BODY"]
    sketch_only = [{"feature_id": 1, "feature_type": "SKETCH", "status": "OK", "suppressed": False}]
    assert dfm.check_model(sketch_only, []) == []


def test_dfm_drawing_rules():
    m = {
        "warnings": [
            "METRIC_OPEN_CONTOUR:2",
            "METRIC_NO_OUTER_CONTOUR",
            "METRIC_ITEMS_TRUNCATED:3",
        ],
        "title_block": {"thickness_mm": 3.0, "missing": ["material"]},
        "holes_by_dia": {"2.5": 4, "6": 2},
    }
    items = dfm.sort_findings(dfm.check_drawing(m))
    assert codes(items) == [
        "DFM_HOLE_LT_THICKNESS",
        "DFM_NO_OUTER_CONTOUR",
        "DFM_OPEN_CONTOUR",
        "DFM_TITLE_MISSING",
    ]
    assert items[0]["severity"] == "ERROR"
    assert "2개" in next(f for f in items if f["code"] == "DFM_OPEN_CONTOUR")["message"]
    assert "2.5" in next(f for f in items if f["code"] == "DFM_HOLE_LT_THICKNESS")["message"]
    assert dfm.check_drawing({"warnings": [], "title_block": {"missing": []}}) == []


def test_quote_advice():
    logs = [
        {
            "severity": "ERROR",
            "code": "QUOTE_PRICE_MISSING",
            "message": "x: 단가가 없습니다",
            "line_no": 3,
        },
        {"severity": "WARN", "code": "QUOTE_SOMETHING_NEW", "message": "?", "line_no": None},
    ]
    a = dfm.advise_quote(logs)
    assert a[0]["fix"].startswith("기준정보") and a[0]["target"] == {"kind": "line", "id": 3}
    assert a[1]["fix"] and a[1]["target"] is None


def test_kb_search():
    ids = [h["id"] for h in kb.search("스냅은 어떻게 쓰나요?", "cad2d")]
    assert ids[0] == "snap"
    assert kb.search("구멍 탭 만드는 법", "model3d")[0]["id"] == "hole"
    assert kb.search("qwerty", "cad2d") == []
    assert all(
        "*" in e["pages"] or "model3d" in e["pages"]
        for e in kb.entries()
        if e["id"] in [t["id"] for t in kb.for_page("model3d")]
    )
    assert len({e["id"] for e in kb.entries()}) == len(kb.entries())


def fake_post(seen, status=200, payload=None):
    def post(url, data, headers):
        seen.append({"url": url, "data": json.loads(data), "headers": headers})
        body = (
            payload
            if payload is not None
            else {"candidates": [{"content": {"parts": [{"text": "답변입니다"}]}}]}
        )
        return status, json.dumps(body).encode()

    return post


def test_llm_settings_and_summary(tmp_path, monkeypatch):
    monkeypatch.delenv("AXCAD_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert llm.provider(tmp_path) is None and llm.public(tmp_path)["llm_configured"] is False
    llm.save(tmp_path, True, None)
    assert llm.provider(tmp_path) is None  # on, but no key
    llm.save(tmp_path, None, "AIzaTESTKEY")
    chain = llm.provider(tmp_path)
    assert isinstance(chain, llm.Chain) and [m.name for m in chain.members] == ["gemini"]
    pub = llm.public(tmp_path)
    assert pub == {
        "llm_enabled": True,
        "llm_configured": True,
        "key_source": "saved",
        "provider": "gemini",
        "model": "gemini-3.8-flash",
        "gemini_models": ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-2.5-flash-lite"],
        "ollama_enabled": False,
        "ollama_url": "http://localhost:11434/v1",
        "ollama_models": ["qwen3:4b-q4_K_M", "qwen2.5-coder:7b-instruct-q4_K_M"],
    }
    assert "AIzaTESTKEY" not in json.dumps(pub)
    monkeypatch.setenv("AXCAD_GEMINI_API_KEY", "ENVKEY")
    assert llm.key_of(tmp_path) == ("ENVKEY", "env")
    monkeypatch.setenv("AXCAD_GEMINI_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "DOCKEY")  # the guide's variable name works too
    assert llm.key_of(tmp_path) == ("DOCKEY", "env")
    monkeypatch.delenv("GEMINI_API_KEY")
    llm.save(tmp_path, False, "")
    assert llm.provider(tmp_path) is None and "gemini_api_key" not in llm.load(tmp_path)
    # Ollama alone (no key, no internet): still a provider
    llm.save(tmp_path, None, None, ollama_enabled=True, ollama_url="http://127.0.0.1:11434/v1")
    chain = llm.provider(tmp_path)
    assert isinstance(chain, llm.Chain) and [m.name for m in chain.members] == ["ollama"]
    for bad in (
        "http://192.168.0.5:11434/v1",
        "https://evil.example/v1",
        "http://localhost.evil.com/v1",
        "http://localhost@evil.com/v1",
        "file:///etc/passwd",
    ):
        with pytest.raises(ValueError):
            llm.save(tmp_path, None, None, ollama_url=bad)
        with pytest.raises(ValueError):
            llm.Ollama(bad)

    s = llm.summary(
        "model3d",
        "PART",
        [{"feature_type": "EXTRUDE"}, {"feature_type": "HOLE"}, {"feature_type": "HOLE"}],
        [{"severity": "WARN", "code": "DFM_HOLE_DEEP", "message": "판: 비밀 이름 12.3"}],
    )
    assert s == {
        "page": "model3d",
        "document_type": "PART",
        "feature_counts": {"EXTRUDE": 1, "HOLE": 2},
        "findings": ["WARN:DFM_HOLE_DEEP"],
    }


def test_llm_gemini_call():
    seen = []
    g = llm.Gemini("K", post=fake_post(seen))
    assert g.answer("질문", {"page": "home"}) == "답변입니다"
    req = seen[0]
    assert (
        req["url"].startswith("https://generativelanguage.googleapis.com/")
        and req["headers"]["x-goog-api-key"] == "K"
    )
    assert "질문" in req["data"]["contents"][0]["parts"][0]["text"] and "K" not in req["url"]
    with pytest.raises(llm.LlmError):
        llm.Gemini("K", post=fake_post([], status=429)).answer("q", {})
    with pytest.raises(llm.LlmError):
        llm.Gemini("K", post=fake_post([], payload={"candidates": []})).answer("q", {})

    def down(*_):
        raise OSError("connection refused")

    with pytest.raises(llm.LlmError):
        llm.Gemini("K", post=down).answer("q", {})
    long = {"candidates": [{"content": {"parts": [{"text": "가" * 9000}]}}]}
    assert len(llm.Gemini("K", post=fake_post([], payload=long)).answer("q", {})) == llm.MAX_ANSWER


def test_gemini_tries_the_models_in_order():
    seen: list = []
    answers = iter([(429, b"{}"), (404, b"{}")])

    def post(url, data, headers, *_):
        seen.append(url)
        try:
            return next(answers)
        except StopIteration:
            return 200, json.dumps(
                {"candidates": [{"content": {"parts": [{"text": "셋째"}]}}]}
            ).encode()

    g = llm.Gemini("K", post=post)
    assert g.answer("q", {}) == "셋째" and g.model == "gemini-2.5-flash-lite"
    assert [u.split("/models/")[1].split(":")[0] for u in seen] == list(llm.GEMINI_MODELS)
    with pytest.raises(llm.LlmError, match="429"):
        llm.Gemini("K", post=fake_post([], status=429)).answer("q", {})


def test_gemini_settings_follow_the_guide():
    seen: list = []
    llm.Gemini("K", post=fake_post(seen)).answer("q", {})
    cfg = seen[0]["data"]["generationConfig"]
    assert cfg == {"maxOutputTokens": 2048, "temperature": 0.2}


def ollama_post(seen, replies):
    replies = iter(replies)

    def post(url, data, headers, timeout=None):
        body = json.loads(data)
        seen.append(
            {
                "url": url,
                "model": body["model"],
                "body": body,
                "headers": headers,
                "timeout": timeout,
            }
        )
        status, text = next(replies)
        return status, json.dumps({"choices": [{"message": {"content": text}}]}).encode()

    return post


def test_ollama_models_and_think_block():
    seen: list = []
    o = llm.Ollama(post=ollama_post(seen, [(200, "<think>생각</think>\n답입니다")]))
    assert o.answer("q", {}) == "답입니다" and o.model == "qwen3:4b-q4_K_M"
    r = seen[0]
    assert r["url"] == "http://localhost:11434/v1/chat/completions"
    assert r["headers"]["authorization"] == "Bearer ollama" and r["timeout"] == llm.OLLAMA_TIMEOUT_S
    assert (
        r["body"]["temperature"] == 0.2
        and r["body"]["max_tokens"] == 2048
        and r["body"]["stream"] is False
    )
    # LISP: the code model first; a missing model falls back to the other one
    seen.clear()
    o = llm.Ollama(
        post=ollama_post(seen, [(404, ""), (200, '```lisp\n(command "C" (list 0 0) 5)\n```')])
    )
    assert o.lisp("원") == '(command "C" (list 0 0) 5)' and o.model == "qwen3:4b-q4_K_M"
    assert [x["model"] for x in seen] == ["qwen2.5-coder:7b-instruct-q4_K_M", "qwen3:4b-q4_K_M"]

    def down(*_a, **_k):
        raise OSError("refused")

    with pytest.raises(llm.LlmError, match="Ollama"):
        llm.Ollama(post=down).answer("q", {})


def test_chain_falls_back_and_runs_one_at_a_time():
    class P:
        def __init__(self, name, out=None):
            self.name, self.out, self.model = name, out, None

        def answer(self, q, c):
            assert not llm._ONE_AT_A_TIME.acquire(blocking=False)  # held for this request
            if self.out is None:
                raise llm.LlmError("안 됨")
            self.model = "m"
            return self.out

        lisp = lambda self, s: self.answer(s, {})

    c = llm.Chain([P("gemini"), P("ollama", "로컬 답")])
    assert c.answer("q", {}) == "로컬 답" and c.model == "ollama/m"
    assert llm._ONE_AT_A_TIME.acquire(blocking=False)  # released again
    llm._ONE_AT_A_TIME.release()
    with pytest.raises(llm.LlmError, match="gemini: 안 됨; ollama: 안 됨"):
        llm.Chain([P("gemini"), P("ollama")]).lisp("x")


def test_no_redirects_and_stdlib_only():
    import ast
    import pathlib

    src = pathlib.Path(llm.__file__).read_text()
    mods = {n.names[0].name for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Import)}
    assert "httpx" not in mods  # dev-only package: the installed app must not import it
    assert llm._NoRedirect().redirect_request() is None


def test_rate_limit_per_user_and_daily():
    r = llm.RateLimit(calls=2, window=60, daily=3)
    assert r.take(1) and r.take(1) and not r.take(1)
    assert r.take(2)  # another user has their own window
    assert not r.take(3)  # but the day's quota (the key's) is shared


def test_scrub_and_env_key(tmp_path, monkeypatch):
    q = "견적 담당 kim@acme.co.kr 010-1234-5678 사업자 1234567890 두께 12.5 길이 1000"
    assert llm.scrub(q) == "견적 담당 *** *** 사업자 *** 두께 12.5 길이 1000"
    monkeypatch.setenv("AXCAD_GEMINI_API_KEY", "bad key\u00e9")
    assert llm.key_of(tmp_path) == (None, None)


def test_nl_bounds():
    assert "error" in nlcmd.parse("원 반지름 5 (9999999999,0)", "cad2d")
    assert "error" in nlcmd.parse("길이 10 각도 720 선", "cad2d")
    r = nlcmd.parse("선 " + " ".join(f"{i},{i}" for i in range(100)), "cad2d")
    assert r["summary"].startswith("선 49개")


def test_dfm_sheet_rules():
    from core.geometry import sheetmetal as sm

    lines = [((0, 0), (100, 0)), ((100, 0), (100, 50)), ((100, 50), (0, 50)), ((0, 50), (0, 0))]
    geoms = [{"type": "LINE", "start": list(a), "end": list(b)} for a, b in lines]
    geoms.append({"type": "CIRCLE", "center": [50, 4], "radius": 2})  # 2 mm from edge 0
    spec = {
        "outline": sm.outline(geoms),
        "thickness": 2.0,
        "bend_radius": 1.0,
        "k_factor": 0.44,
        "flanges": [
            {"edge": 0, "length": 3, "angle": 90, "up": True, "inset_start": 0, "inset_end": 0},
            {"edge": 1, "length": 20, "angle": 90, "up": True, "inset_start": 0, "inset_end": 0},
        ],
    }
    assert codes(dfm.check_sheet(9, spec)) == sorted(
        ["SHEET_RADIUS_SMALL", "SHEET_FLANGE_SHORT", "SHEET_HOLE_NEAR_BEND", "SHEET_FLANGE_OVERLAP"]
    )
    spec["bend_radius"] = 2.0
    spec["flanges"] = [
        {"edge": 2, "length": 20, "angle": 90, "up": True, "inset_start": 0, "inset_end": 0}
    ]
    assert dfm.check_sheet(9, spec) == []
