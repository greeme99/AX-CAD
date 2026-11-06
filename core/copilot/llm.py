"""Optional LLM fallback (off by default). Only questions the rules and the help base cannot answer
go out, only when an administrator turned it on and a key is set, and only with a summary of the
screen: page and document type, feature-type counts and finding codes. Drawing files, coordinates,
names and quote amounts never leave the PC. The answer is shown as plain text; it never runs a
command.

Key: AXCAD_GEMINI_API_KEY (environment) wins over the one saved from the admin screen
(<var>/copilot.json, never returned by the API)."""

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

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
)
TIMEOUT_S = 20.0
KEY_RE = re.compile(r"^[A-Za-z0-9_\-]{1,200}$")
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


class LlmError(Exception):
    pass


class Provider(Protocol):
    name: str

    def answer(self, question: str, context: dict[str, Any]) -> str: ...


Post = Callable[[str, bytes, dict[str, str]], tuple[int, bytes]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None  # the key header must never follow a redirect to another host


_OPENER = urllib.request.build_opener(_NoRedirect)


def _post(url: str, data: bytes, headers: dict[str, str]) -> tuple[int, bytes]:
    """Standard library HTTPS POST (no extra dependency in the installed app)."""
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with _OPENER.open(req, timeout=TIMEOUT_S) as r:
            return r.status, r.read(MAX_RESPONSE)
    except urllib.error.HTTPError as e:
        return e.code, b""


class Gemini:
    name = "gemini"

    def __init__(self, key: str, post: Post | None = None):
        self._key = key
        self._post = post or _post

    def answer(self, question: str, context: dict[str, Any]) -> str:
        prompt = f"[화면 요약]\n{json.dumps(context, ensure_ascii=False)}\n\n[질문]\n{question}"
        payload = {
            "systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 1024, "temperature": 0.3},
        }
        headers = {"content-type": "application/json", "x-goog-api-key": self._key}
        try:
            status, raw = self._post(GEMINI_URL, json.dumps(payload).encode(), headers)
        except (OSError, ValueError):  # URLError, timeouts, TLS, bad header values
            raise LlmError("LLM 서버에 연결하지 못했습니다") from None
        if status != 200:
            raise LlmError(f"LLM 응답 오류 ({status})")
        try:
            parts = json.loads(raw)["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        except (KeyError, IndexError, TypeError, ValueError):
            raise LlmError("LLM 응답을 읽지 못했습니다") from None
        if not text.strip():
            raise LlmError("LLM이 빈 답을 보냈습니다")
        return text.strip()[:MAX_ANSWER]


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


def save(var_dir: Path, enabled: bool | None, key: str | None) -> dict[str, Any]:
    """Update the saved settings; key "" clears it, None keeps it."""
    with _lock:
        data = load(var_dir)
        if enabled is not None:
            data["llm_enabled"] = enabled
        if key is not None:
            if key:
                data["gemini_api_key"] = key
            else:
                data.pop("gemini_api_key", None)
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
    env = os.environ.get("AXCAD_GEMINI_API_KEY", "").strip()
    if env:
        return (env, "env") if KEY_RE.fullmatch(env) else (None, None)
    saved = load(var_dir).get("gemini_api_key")
    return (saved, "saved") if isinstance(saved, str) and saved else (None, None)


def public(var_dir: Path) -> dict[str, Any]:
    """Settings as the API shows them: never the key itself."""
    key, src = key_of(var_dir)
    return {
        "llm_enabled": bool(load(var_dir).get("llm_enabled")),
        "llm_configured": key is not None,
        "key_source": src,
        "provider": "gemini",
        "model": GEMINI_MODEL,
    }


def provider(var_dir: Path) -> Provider | None:
    """The provider to use, or None when the LLM is off or has no key."""
    if not load(var_dir).get("llm_enabled"):
        return None
    key, _ = key_of(var_dir)
    return Gemini(key) if key else None


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
