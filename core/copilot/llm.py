"""Optional LLM fallback (off by default). Only questions the rules and the help base cannot answer
go out, only when an administrator turned it on and a key is set, and only with a summary of the
screen: page and document type, feature-type counts and finding codes. Drawing files, coordinates,
names and quote amounts never leave the PC. The answer is shown as plain text; it never runs a
command.

Key: AXCAD_EXAONE_API_KEY (or EXAONE_API_KEY) in the environment wins over the one saved from the
admin screen (<var>/copilot.json, never returned by the API).

Fallback order (2026-11-08 AI Copilot API guide; 2nd step changed to K-EXAONE 2.5 on 2026-11-11):
rules -> K-EXAONE 2.5 (EXAONE API, OpenAI-compatible) -> Ollama on this PC (OLLAMA_MODELS; code
first for LISP). Standard library only: the installed app gets no openai package. EXAONE is reached
at its one fixed address and Ollama on this PC only (localhost)."""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

EXAONE_MODEL = "k-exaone_v2.5"
EXAONE_URL = "https://api.exaone.ai/v1/chat/completions"  # fixed: the key header goes nowhere else
EXAONE_MAX_TOKENS = 4096  # the model always reasons first and max_tokens counts the reasoning
EXAONE_TIMEOUT_S = 60.0
OLLAMA_MODELS = ("qwen3:4b-q4_K_M", "qwen2.5-coder:7b-instruct-q4_K_M")  # general, then code
OLLAMA_URL = "http://localhost:11434/v1"
OLLAMA_URL_RE = re.compile(
    r"^http://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d{1,5})?(?:/[\w./-]*)?$"
)
TEMPERATURE = 0.2
MAX_TOKENS = 2048
TIMEOUT_S = 20.0
OLLAMA_TIMEOUT_S = 120.0  # a 7B model on a PC CPU is slow
KEY_RE = re.compile(r"^[A-Za-z0-9_.~+/=\-]{1,200}$")  # RFC 6750 token chars: header-safe
MAX_ANSWER = 4000
MAX_QUESTION = 500
MAX_RESPONSE = 1_000_000  # bytes read from the LLM
RATE = (10, 60.0)  # calls per window seconds, per user
DAILY = 200  # calls per day, all users of this server process together (the key's quota)
SECRET_RE = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.-]+|\d[\d-]{6,}\d"
)  # e-mails, phone / account numbers
SYSTEM = (
    "당신은 사내 CAD·견적 프로그램 AX-CAD의 도우미입니다. 한국어로 간결하게(10문장 이내) 답하세요. "
    "모르는 것은 모른다고 하고, 프로그램에 없는 기능을 지어내지 마세요. "
    "기계 설계·제조 공정·원가 견적(재료비·노무비·경비)의 일반 지식은 답해도 됩니다."
)


# Track 11: Text to Draw when the rules cannot — the LLM writes AX-LISP, which the screen's own
# interpreter then checks and runs (no other code path); only the sentence goes out
LISP_SYSTEM = (
    "당신은 AX-CAD의 AX-LISP(AutoLISP 부분집합) 작성기입니다. 사용자 문장을 2D 도면으로 그리는 "
    "AX-LISP 코드만 출력하세요(설명·마크다운 없이). 단위 mm, 원점 (0 0). 쓸 수 있는 것: defun setq if cond "
    "progn while repeat foreach lambda + - * / sin cos atan sqrt polar distance list car cadr nth "
    "strcat rtos itoa command setvar(CLAYER만). command 명령과 입력 순서: "
    '"REC" 구석점 반대구석점 | "C" 중심 반지름 | "L" 점... "" | "PL" 점... "C"(닫기) 또는 "" | '
    '"A" "C" 중심 시작점 끝점(반시계) | "POL" 변수 중심 꼭짓점 | "T" 삽입점 높이 "문자" | '
    '"DLI" 점1 점2 치수위치. 점은 (list x y). 파일·네트워크·사용자 입력(get…) 함수는 쓰지 마세요.'
)
MAX_LISP = 8000


class LlmError(Exception):
    pass


class Provider(Protocol):
    name: str
    model: str | None  # which model gave the last answer

    def answer(self, question: str, context: dict[str, Any]) -> str: ...

    def lisp(self, sentence: str) -> str: ...


Post = Callable[..., tuple[int, bytes]]


def _code(text: str) -> str:
    """LISP from a model's answer: no fences, no qwen3 <think> block."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text.strip())[:MAX_LISP]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None  # the key header must never follow a redirect to another host


_OPENER = urllib.request.build_opener(_NoRedirect)


def _post(
    url: str, data: bytes, headers: dict[str, str], timeout: float = TIMEOUT_S
) -> tuple[int, bytes]:
    """Standard library HTTP POST (no extra dependency in the installed app)."""
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return r.status, r.read(MAX_RESPONSE)
    except urllib.error.HTTPError as e:
        return e.code, b""


class Exaone:
    """K-EXAONE 2.5 through the EXAONE API (OpenAI-compatible). It cannot skip reasoning, so a
    question gets reasoning_effort "low" (fast) and LISP "high" (code); the reasoning itself
    (message.reasoning_content) is never read, kept or shown. Sampling is the vendor's recommendation
    (temperature 1.0, top_p 0.95)."""

    name = "exaone"

    def __init__(self, key: str, post: Post | None = None):
        self._key, self._post = key, post or _post
        self.model: str | None = None

    def answer(self, question: str, context: dict[str, Any]) -> str:
        prompt = f"[화면 요약]\n{json.dumps(context, ensure_ascii=False)}\n\n[질문]\n{question}"
        return self._chat(SYSTEM, prompt, "low")[:MAX_ANSWER]

    def lisp(self, sentence: str) -> str:
        return _code(self._chat(LISP_SYSTEM, sentence, "high"))

    def _chat(self, system: str, prompt: str, effort: str) -> str:
        payload = {
            "model": EXAONE_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": EXAONE_MAX_TOKENS,
            "temperature": 1.0,
            "top_p": 0.95,
            "reasoning_effort": effort,
            "stream": False,
        }
        headers = {"content-type": "application/json", "authorization": f"Bearer {self._key}"}
        try:
            status, raw = self._post(
                EXAONE_URL, json.dumps(payload).encode(), headers, EXAONE_TIMEOUT_S
            )
        except (OSError, ValueError):  # URLError, timeouts, TLS, bad header values
            raise LlmError("LLM 서버에 연결하지 못했습니다") from None
        if status != 200:
            raise LlmError(f"LLM 응답 오류 ({status})")
        try:
            text = json.loads(raw)["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError):
            raise LlmError("LLM 응답을 읽지 못했습니다") from None
        if not isinstance(text, str) or not text.strip():  # also: all tokens spent on reasoning
            raise LlmError("LLM이 빈 답을 보냈습니다")
        self.model = EXAONE_MODEL
        return text.strip()


class Ollama:
    """Models on this PC through Ollama's OpenAI-compatible API (the key is ignored by Ollama)."""

    name = "ollama"

    def __init__(self, base: str = OLLAMA_URL, post: Post | None = None):
        if not OLLAMA_URL_RE.fullmatch(base):
            raise ValueError("Ollama is reached on this PC only (http://localhost...)")
        self._url, self._post = base.rstrip("/") + "/chat/completions", post or _post
        self.model: str | None = None

    def answer(self, question: str, context: dict[str, Any]) -> str:
        prompt = f"[화면 요약]\n{json.dumps(context, ensure_ascii=False)}\n\n[질문]\n{question}"
        text = re.sub(
            r"<think>.*?</think>", "", self._chat(SYSTEM, prompt, OLLAMA_MODELS), flags=re.DOTALL
        )
        return text.strip()[:MAX_ANSWER]

    def lisp(self, sentence: str) -> str:
        return _code(self._chat(LISP_SYSTEM, sentence, OLLAMA_MODELS[::-1]))  # the code model first

    def _chat(self, system: str, prompt: str, models: tuple[str, ...]) -> str:
        headers = {"content-type": "application/json", "authorization": "Bearer ollama"}
        error = LlmError("Ollama 모델이 없습니다")
        for model in models:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "temperature": TEMPERATURE,
                "max_tokens": MAX_TOKENS,
                "stream": False,
            }
            try:
                status, raw = self._post(
                    self._url, json.dumps(payload).encode(), headers, OLLAMA_TIMEOUT_S
                )
            except (OSError, ValueError):
                error = LlmError("Ollama에 연결하지 못했습니다 (Ollama가 실행 중인지 확인)")
                break  # the server is down: the other model will not answer either
            try:
                text = json.loads(raw)["choices"][0]["message"]["content"] if status == 200 else ""
            except (KeyError, IndexError, TypeError, ValueError):
                text = ""
            if isinstance(text, str) and text.strip():
                self.model = model
                return text.strip()
            error = LlmError(f"Ollama 모델 {model} 응답 오류 ({status})")
        raise error


_ONE_AT_A_TIME = threading.Semaphore(1)  # one LLM request at a time on this PC


class Chain:
    """EXAONE, then Ollama: the first that answers; one request at a time."""

    def __init__(self, members: list[Provider]):
        self.members = members
        self.name = "+".join(m.name for m in members)
        self.model: str | None = None

    def answer(self, question: str, context: dict[str, Any]) -> str:
        return self._first(lambda m: m.answer(question, context))

    def lisp(self, sentence: str) -> str:
        return self._first(lambda m: m.lisp(sentence))

    def _first(self, ask: Callable[[Provider], str]) -> str:
        if not _ONE_AT_A_TIME.acquire(timeout=OLLAMA_TIMEOUT_S):
            raise LlmError("다른 LLM 요청을 처리하고 있습니다. 잠시 뒤 다시 시도하세요")
        try:
            errors = []
            for m in self.members:
                try:
                    out = ask(m)
                except LlmError as e:
                    errors.append(f"{m.name}: {e}")
                    continue
                self.model = f"{m.name}/{m.model}"
                return out
            raise LlmError("모든 LLM이 응답하지 못했습니다 — " + "; ".join(errors))
        finally:
            _ONE_AT_A_TIME.release()


# --- settings ----------------------------------------------------------------------------------

_lock = threading.Lock()


def _path(var_dir: Path) -> Path:
    return var_dir / "copilot.json"


def load(var_dir: Path) -> dict[str, Any]:
    try:
        data = json.loads(_path(var_dir).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(
    var_dir: Path,
    enabled: bool | None,
    key: str | None,
    ollama_enabled: bool | None = None,
    ollama_url: str | None = None,
) -> dict[str, Any]:
    """Update the saved settings; key "" clears it, None keeps it."""
    if ollama_url is not None and not OLLAMA_URL_RE.fullmatch(ollama_url):
        raise ValueError("Ollama is reached on this PC only (http://localhost...)")
    with _lock:
        data = load(var_dir)
        if enabled is not None:
            data["llm_enabled"] = enabled
        if ollama_enabled is not None:
            data["ollama_enabled"] = ollama_enabled
        if ollama_url is not None:
            data["ollama_url"] = ollama_url
        data.pop("gemini_api_key", None)  # the Gemini step is gone: drop a key saved for it
        if key is not None:
            if key:
                data["exaone_api_key"] = key
            else:
                data.pop("exaone_api_key", None)
        p = _path(var_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        # owner only on POSIX; on Windows the per-user data folder (%LOCALAPPDATA%) protects it
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)  # a stale .tmp keeps its old mode through O_CREAT
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, p)
        return data


def key_of(var_dir: Path) -> tuple[str | None, str | None]:
    """(key, where it comes from: "env" | "saved" | None)."""
    env = (os.environ.get("AXCAD_EXAONE_API_KEY") or os.environ.get("EXAONE_API_KEY") or "").strip()
    if env:
        return (env, "env") if KEY_RE.fullmatch(env) else (None, None)
    saved = load(var_dir).get("exaone_api_key")
    return (saved, "saved") if isinstance(saved, str) and saved else (None, None)


def public(var_dir: Path) -> dict[str, Any]:
    """Settings as the API shows them: never the key itself."""
    key, src = key_of(var_dir)
    return {
        "llm_enabled": bool(load(var_dir).get("llm_enabled")),
        "llm_configured": key is not None,
        "key_source": src,
        "provider": "exaone",
        "model": EXAONE_MODEL,
        "exaone_model": EXAONE_MODEL,  # absent from older servers: the screen warns (BUG-61)
        "ollama_enabled": bool(load(var_dir).get("ollama_enabled")),
        "ollama_url": _ollama_url(var_dir),
        "ollama_models": list(OLLAMA_MODELS),
    }


def _ollama_url(var_dir: Path) -> str:
    url = load(var_dir).get("ollama_url")
    return url if isinstance(url, str) and OLLAMA_URL_RE.fullmatch(url) else OLLAMA_URL


def provider(var_dir: Path) -> Provider | None:
    """EXAONE (on and keyed), then Ollama (on), as one chain; None when neither is."""
    data, (key, _) = load(var_dir), key_of(var_dir)
    members: list[Provider] = []
    if data.get("llm_enabled") and key:
        members.append(Exaone(key))
    if data.get("ollama_enabled"):
        members.append(Ollama(_ollama_url(var_dir)))
    return Chain(members) if members else None


class RateLimit:
    """`calls` per `window` seconds per user, and `daily` per day for everyone together."""

    def __init__(self, calls: int = RATE[0], window: float = RATE[1], daily: int = DAILY):
        self.calls, self.window, self.daily = calls, window, daily
        self._times: dict[int, deque[float]] = {}
        self._day: deque[float] = deque()
        self._lock = threading.Lock()

    def take(self, user_id: int = 0) -> bool:
        now = time.monotonic()
        with self._lock:
            while self._day and now - self._day[0] > 86400:
                self._day.popleft()
            q = self._times.setdefault(user_id, deque())
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.calls or len(self._day) >= self.daily:
                return False
            q.append(now)
            self._day.append(now)
            return True


def scrub(text: str) -> str:
    """The question as sent: e-mail addresses and long digit runs (phone, account, business
    numbers) masked; ordinary sizes like 1000 or 12.5 stay."""
    return SECRET_RE.sub("***", text)


LIMIT = RateLimit()


def summary(
    page: str, doc_type: str | None, features: list[dict[str, Any]], findings: list[dict[str, Any]]
) -> dict[str, Any]:
    """What the LLM may see: counts and codes only (no names, coordinates, files or amounts)."""
    counts: dict[str, int] = {}
    for f in features:
        counts[f["feature_type"]] = counts.get(f["feature_type"], 0) + 1
    return {
        "page": page,
        "document_type": doc_type,
        "feature_counts": counts,
        "findings": sorted({f"{f['severity']}:{f['code']}" for f in findings})[:30],
    }
