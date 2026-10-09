import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.dxf.reader import (
    MAX_FILE_BYTES,
    MAX_TEXT_CHARS,
    DxfError,
    parse_dxf_with_timeout,
    run_isolated,
    validate_dxf_bytes,
)
from core.dxf.writer import apply_edits

# ponytail: file storage until S4 introduces PostgreSQL
VAR_DIR = Path(os.environ.get("AXCAD_VAR_DIR") or Path(__file__).resolve().parents[2] / "var")
REVISION_RE = re.compile(r"^[0-9a-f]{32}$")

app = FastAPI(title="AX-CAD")
PARSE_SLOTS = threading.Semaphore(2)  # each parse is a process holding up to 2 GB
SLOT_WAIT_S = 60
MAX_EDIT_BODY = 5 * 1024**2
MAX_VERTICES = 10_000  # per polyline
MAX_TOTAL_VERTICES = 200_000  # per edit request


@app.middleware("http")
async def _reject_oversized(request: Request, call_next: Any) -> Any:
    # reject before Starlette spools/buffers the body; chunked POSTs (no length) would bypass it
    if request.method == "POST":
        raw = request.headers.get("content-length")
        if raw is None:
            return _error(411, "LENGTH_REQUIRED", "Content-Length header required")
        if not raw.isdigit():
            return _error(400, "REQUEST_INVALID", "Invalid Content-Length")
        limit = MAX_EDIT_BODY if request.url.path.endswith("/edits") else MAX_FILE_BYTES + 1024**2
        if int(raw) > limit:
            return _error(413, "FILE_TOO_LARGE", "Request body too large")
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
async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    if request.url.path.endswith("/edits"):
        return _error(422, "EDIT_INVALID", "Invalid edit request")
    return _error(400, "REQUEST_INVALID", "Missing or invalid request fields")


@app.exception_handler(StarletteHTTPException)
async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
    return _error(exc.status_code, code, str(exc.detail))


def _store(revision_id: str, payload: dict[str, Any]) -> dict[str, Any]:
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
    return _store(revision_id, payload)


@app.get("/api/revisions/{revision_id}/render")
def get_render(revision_id: str) -> Any:
    path = VAR_DIR / "revisions" / f"{revision_id}.json"
    if not REVISION_RE.fullmatch(revision_id) or not path.is_file():
        return _error(404, "REVISION_NOT_FOUND", "Revision not found")
    return _body(json.loads(path.read_text(encoding="utf-8")))


# --- edit request (trust boundary): mm values, finite and bounded ---
Coord = Annotated[float, Field(allow_inf_nan=False, ge=-1e9, le=1e9)]
Size = Annotated[float, Field(allow_inf_nan=False, gt=0, le=1e9)]
Angle = Annotated[float, Field(allow_inf_nan=False, ge=-1e9, le=1e9)]
Pt = Annotated[list[Coord], Field(min_length=2, max_length=2)]
Vertex = Annotated[list[Coord], Field(min_length=3, max_length=3)]  # [x, y, bulge]
Handle = Annotated[str, Field(pattern=r"^[0-9A-Fa-f]{1,16}$")]
LayerName = Annotated[str, Field(min_length=1, max_length=255)]
MAX_OPS = 10_000


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LineGeom(_Strict):
    type: Literal["LINE"]
    start: Pt
    end: Pt

    @model_validator(mode="after")
    def _non_degenerate(self) -> "LineGeom":
        if self.start == self.end:
            raise ValueError("zero-length line")
        return self


class CircleGeom(_Strict):
    type: Literal["CIRCLE"]
    center: Pt
    radius: Size


class ArcGeom(_Strict):
    type: Literal["ARC"]
    center: Pt
    radius: Size
    start_angle: Angle
    end_angle: Angle


class PolyGeom(_Strict):
    type: Literal["LWPOLYLINE"]
    points: Annotated[list[Vertex], Field(min_length=2, max_length=MAX_VERTICES)]
    closed: bool


class TextGeom(_Strict):
    type: Literal["TEXT"]
    insert: Pt
    height: Size
    value: Annotated[str, Field(min_length=1, max_length=MAX_TEXT_CHARS)]
    rotation: Angle


class DimLinearGeom(_Strict):
    type: Literal["DIM_LINEAR"]
    p1: Pt
    p2: Pt
    base: Pt
    angle: Angle

    @model_validator(mode="after")
    def _non_degenerate(self) -> "DimLinearGeom":
        if self.p1 == self.p2:
            raise ValueError("zero-length dimension")
        return self


class DimAlignedGeom(_Strict):
    type: Literal["DIM_ALIGNED"]
    p1: Pt
    p2: Pt
    distance: Coord

    @model_validator(mode="after")
    def _non_degenerate(self) -> "DimAlignedGeom":
        if self.p1 == self.p2 or self.distance == 0:
            raise ValueError("degenerate dimension")
        return self


class DimAngularGeom(_Strict):
    type: Literal["DIM_ANGULAR"]
    center: Pt
    p1: Pt
    p2: Pt
    base: Pt

    @model_validator(mode="after")
    def _distinct(self) -> "DimAngularGeom":
        if len({tuple(self.center), tuple(self.p1), tuple(self.p2)}) < 3:
            raise ValueError("degenerate angular dimension")
        return self


class DimRadiusGeom(_Strict):
    type: Literal["DIM_RADIUS"]
    center: Pt
    radius: Size
    angle: Angle


Geom = Annotated[
    LineGeom
    | CircleGeom
    | ArcGeom
    | PolyGeom
    | TextGeom
    | DimLinearGeom
    | DimAlignedGeom
    | DimAngularGeom
    | DimRadiusGeom,
    Field(discriminator="type"),
]


class Created(_Strict):
    layer: LayerName
    geom: Geom


class Modified(Created):
    handle: Handle


class EditRequest(_Strict):
    created: Annotated[list[Created], Field(max_length=MAX_OPS)] = []
    modified: Annotated[list[Modified], Field(max_length=MAX_OPS)] = []
    deleted: Annotated[list[Handle], Field(max_length=MAX_OPS)] = []

    @model_validator(mode="after")
    def _op_count(self) -> "EditRequest":
        if not 1 <= len(self.created) + len(self.modified) + len(self.deleted) <= MAX_OPS:
            raise ValueError("1..10000 operations required")
        geoms = [c.geom for c in self.created] + [m.geom for m in self.modified]
        if sum(len(g.points) for g in geoms if isinstance(g, PolyGeom)) > MAX_TOTAL_VERTICES:
            raise ValueError("too many polyline vertices")
        return self


@app.post("/api/revisions/{revision_id}/edits")
def edit_revision(revision_id: str, req: EditRequest) -> Any:
    src = VAR_DIR / "uploads" / f"{revision_id}.dxf"
    if not REVISION_RE.fullmatch(revision_id) or not src.is_file():
        return _error(404, "REVISION_NOT_FOUND", "Revision not found")
    new_id = uuid.uuid4().hex
    dst = VAR_DIR / "uploads" / f"{new_id}.dxf"
    out = VAR_DIR / "revisions" / f"{new_id}.json"
    if not PARSE_SLOTS.acquire(timeout=SLOT_WAIT_S):
        return _error(503, "SERVER_BUSY", "Server busy, retry later")
    try:
        run_isolated(
            apply_edits,
            str(src),
            str(dst),
            req.model_dump(),
            fail=("EDIT_INVALID", "Edit failed"),
        )
        payload = parse_dxf_with_timeout(str(dst))
        payload["parent_revision_id"] = revision_id
        return _store(new_id, payload)
    except BaseException:
        dst.unlink(missing_ok=True)
        out.unlink(missing_ok=True)
        raise
    finally:
        PARSE_SLOTS.release()


@app.get("/api/revisions/{revision_id}/dxf")
def get_dxf(revision_id: str) -> Any:
    path = VAR_DIR / "uploads" / f"{revision_id}.dxf"
    if not REVISION_RE.fullmatch(revision_id) or not path.is_file():
        return _error(404, "REVISION_NOT_FOUND", "Revision not found")
    return FileResponse(path, media_type="application/dxf", filename=f"{revision_id}.dxf")
