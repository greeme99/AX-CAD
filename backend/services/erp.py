"""FN-24 ERP adapter: POST the BOM payload to the configured ERP endpoint with retries.

Configuration comes from the environment only (never from a request): ERP_API_URL, ERP_API_TOKEN
(optional bearer), ERP_TIMEOUT_S, ERP_BACKOFF_S. The token is sent as a header and is never
stored, logged or echoed in errors. Redirects are not followed (a 3xx is a failure), so the token
cannot be forwarded to another host.
"""

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db.models import IntegrationJob
from backend.db.session import engine

MAX_ATTEMPTS = 3
MAX_RESPONSE_BYTES = 64 * 1024
RETRY_STATUS = {408, 425, 429}  # plus every 5xx and network errors


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None  # urllib then raises HTTPError with the 3xx code


_opener = urllib.request.build_opener(_NoRedirect)


def configured() -> bool:
    url = os.environ.get("ERP_API_URL", "")
    return url.startswith(("https://", "http://"))


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
    timeout = float(os.environ.get("ERP_TIMEOUT_S", "10"))
    try:
        with _opener.open(req, timeout=timeout) as r:
            return r.status, r.read(MAX_RESPONSE_BYTES)
    except urllib.error.HTTPError as e:
        return e.code, e.read(MAX_RESPONSE_BYTES)


def _answer(raw: bytes) -> dict[str, Any]:
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {"value": v}
    except ValueError:
        return {"text": raw.decode("utf-8", "replace")[:2000]}


def deliver(job_id: int) -> None:
    """Background task: PENDING -> RUNNING -> SUCCESS | FAILED, up to MAX_ATTEMPTS tries."""
    backoff = float(os.environ.get("ERP_BACKOFF_S", "1"))
    with Session(engine()) as db:
        job = db.scalar(
            select(IntegrationJob)
            .where(IntegrationJob.job_id == job_id, IntegrationJob.status == "PENDING")
            .with_for_update(skip_locked=True)  # another worker already has it
        )
        if job is None:
            return
        job.status, job.updated_at = "RUNNING", func.now()
        db.commit()
        for attempt in range(1, MAX_ATTEMPTS + 1):
            job.attempt_count = attempt
            try:
                status, raw = _post(job.request_payload, job.idempotency_key)
                error = None if 200 <= status < 300 else f"ERP HTTP {status}"
                retry = status >= 500 or status in RETRY_STATUS
                job.response_payload = {"status": status, "body": _answer(raw)}
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                reason = getattr(e, "reason", e)
                error, retry = f"ERP unreachable: {type(reason).__name__}", True
            job.last_error, job.updated_at = error, func.now()
            if error is None or not retry or attempt == MAX_ATTEMPTS:
                job.status = "SUCCESS" if error is None else "FAILED"
                db.commit()
                return
            db.commit()
            time.sleep(backoff * 2 ** (attempt - 1))
