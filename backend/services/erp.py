"""FN-24 ERP adapter: POST the BOM payload to the configured ERP endpoint with retries.

Configuration comes from the environment only (never from a request): ERP_API_URL (https; plain
http only to localhost), ERP_API_TOKEN (optional bearer), ERP_TIMEOUT_S (<= 30), ERP_BACKOFF_S.
The token is sent as a header and is never stored, logged or echoed (it is redacted from ERP
answers too). Redirects are not followed (a 3xx is a failure), so the token cannot be forwarded.
Each run owns the job through `run_id`: a retry hands out a new one and an older worker that
wakes up later can no longer write.
"""

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from backend.db.models import IntegrationJob
from backend.db.session import engine

MAX_ATTEMPTS = 3
MAX_TIMEOUT_S = 30.0
MAX_RESPONSE_BYTES = 64 * 1024
MAX_STORED_CHARS = 2000
RETRY_STATUS = {408, 425, 429}  # plus every 5xx and network errors
LOCAL = {"localhost", "127.0.0.1", "::1"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None  # urllib then raises HTTPError with the 3xx code


_opener = urllib.request.build_opener(_NoRedirect)


def configured() -> bool:
    u = urlsplit(os.environ.get("ERP_API_URL", ""))
    return bool(u.hostname) and (
        u.scheme == "https" or (u.scheme == "http" and u.hostname in LOCAL)
    )


def _timeout() -> float:
    try:
        return min(max(float(os.environ.get("ERP_TIMEOUT_S", "10")), 1.0), MAX_TIMEOUT_S)
    except ValueError:
        return 10.0


def _read(r: Any, deadline: float) -> bytes:
    """Bounded in size and in total time (a trickling server cannot hold the worker)."""
    out = b""
    while len(out) < MAX_RESPONSE_BYTES and time.monotonic() < deadline:
        chunk = r.read(min(4096, MAX_RESPONSE_BYTES - len(out)))
        if not chunk:
            break
        out += chunk
    return out


def _post(payload: dict[str, Any], key: str) -> tuple[int, bytes]:
    headers = {"Content-Type": "application/json", "Idempotency-Key": key}
    if token := os.environ.get("ERP_API_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        os.environ["ERP_API_URL"],
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers=headers,
        method="POST",
    )
    timeout = _timeout()
    deadline = time.monotonic() + timeout
    try:
        with _opener.open(req, timeout=timeout) as r:
            return r.status, _read(r, deadline)
    except urllib.error.HTTPError as e:
        return e.code, _read(e, deadline)


def _answer(raw: bytes) -> str:
    """Stored as short text only: no JSON parsing of foreign bodies, token and NULs removed."""
    text = raw.decode("utf-8", "replace").replace("\x00", "")
    if token := os.environ.get("ERP_API_TOKEN"):
        text = text.replace(token, "***")
    return text[:MAX_STORED_CHARS]


def deliver(job_id: int) -> None:
    """Background task: PENDING -> RUNNING -> SUCCESS | FAILED, up to MAX_ATTEMPTS tries."""
    try:
        backoff = min(float(os.environ.get("ERP_BACKOFF_S", "1")), 10.0)
    except ValueError:
        backoff = 1.0
    run = uuid.uuid4()
    with Session(engine()) as db:
        job = db.scalar(
            select(IntegrationJob)
            .where(IntegrationJob.job_id == job_id, IntegrationJob.status == "PENDING")
            .with_for_update(skip_locked=True)  # another worker already has it
        )
        if job is None:
            return
        job.status, job.run_id, job.updated_at = "RUNNING", run, func.now()
        payload, key = job.request_payload, job.idempotency_key
        db.commit()

        def write(**values: Any) -> bool:
            """Only while this run still owns the job (a retry may have taken it over)."""
            r = db.execute(
                update(IntegrationJob)
                .where(IntegrationJob.job_id == job_id, IntegrationJob.run_id == run)
                .values(**values, updated_at=func.now())
            )
            db.commit()
            return bool(getattr(r, "rowcount", 0))

        for attempt in range(1, MAX_ATTEMPTS + 1):
            if not write(attempt_count=attempt):
                return
            response = None
            try:
                status, raw = _post(payload, key)
                error = None if 200 <= status < 300 else f"ERP HTTP {status}"
                retry = status >= 500 or status in RETRY_STATUS
                response = {"status": status, "body": _answer(raw)}
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                error, retry = f"ERP unreachable: {type(getattr(e, 'reason', e)).__name__}", True
            except Exception as e:  # noqa: BLE001 - protocol errors etc.: never leave it RUNNING
                error, retry = f"ERP transfer error: {type(e).__name__}", False
            done = error is None or not retry or attempt == MAX_ATTEMPTS
            values: dict[str, Any] = {"last_error": error, "response_payload": response}
            if done:
                values["status"] = "SUCCESS" if error is None else "FAILED"
            if not write(**values) or done:
                return
            time.sleep(backoff * 2 ** (attempt - 1))
