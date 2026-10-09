import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.dxf.reader import MAX_FILE_BYTES, DxfError, parse_dxf_with_timeout, validate_dxf_bytes

# ponytail: file storage until S4 introduces PostgreSQL
VAR_DIR = Path(os.environ.get("AXCAD_VAR_DIR") or Path(__file__).resolve().parents[2] / "var")
REVISION_RE = re.compile(r"^[0-9a-f]{32}$")

app = FastAPI(title="AX-CAD")
PARSE_SLOTS = threading.Semaphore(2)  # each parse is a process holding up to 2 GB


@app.middleware("http")
async def _reject_oversized(request: Request, call_next: Any) -> Any:
    # reject before Starlette spools a multi-GB multipart body to disk
    if int(request.headers.get("content-length") or 0) > MAX_FILE_BYTES + 1024 * 1024:
        return _error(413, "FILE_TOO_LARGE", "File exceeds 50 MB")
    return await call_next(request)


def _body(data: Any = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"success": error is None, "data": data, "error": error}


def _error(status: int, code: str, message: str) -> JSONResponse:
    err = {"code": code, "message": message, "details": None}
    return JSONResponse(_body(error=err), status_code=status)


@app.exception_handler(DxfError)
async def _dxf_error(_: Request, exc: DxfError) -> JSONResponse:
    return _error(exc.http_status, exc.code, exc.message)


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return _error(400, "REQUEST_INVALID", "Missing or invalid request fields")


@app.exception_handler(StarletteHTTPException)
async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
    return _error(exc.status_code, code, str(exc.detail))


@app.post("/api/dxf")
# sync def: FastAPI runs it in a threadpool, so the blocking parse never stalls the event loop
def upload_dxf(file: UploadFile = File(...)) -> dict[str, Any]:  # noqa: B008
    if not (file.filename or "").lower().endswith(".dxf"):
        raise DxfError("DXF_INVALID_FILE", "Only .dxf files are accepted")
    data = file.file.read(MAX_FILE_BYTES + 1)  # size cap; never read more than limit+1
    validate_dxf_bytes(data)

    revision_id = uuid.uuid4().hex
    upload = VAR_DIR / "uploads" / f"{revision_id}.dxf"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(data)
    try:
        with PARSE_SLOTS:
            payload = parse_dxf_with_timeout(str(upload))
    except BaseException:
        upload.unlink(missing_ok=True)
        raise
    out = VAR_DIR / "revisions" / f"{revision_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, allow_nan=False), encoding="utf-8")
    summary = payload["summary"]
    return _body(
        {
            "revision_id": revision_id,
            "entity_count": summary["entity_count"],
            "layer_count": summary["layer_count"],
            "block_count": summary["block_count"],
            "warnings": payload["warnings"],
        }
    )


@app.get("/api/revisions/{revision_id}/render")
def get_render(revision_id: str) -> Any:
    path = VAR_DIR / "revisions" / f"{revision_id}.json"
    if not REVISION_RE.fullmatch(revision_id) or not path.is_file():
        return _error(404, "REVISION_NOT_FOUND", "Revision not found")
    return _body(json.loads(path.read_text(encoding="utf-8")))
