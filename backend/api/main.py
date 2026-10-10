import hashlib
import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.api import (
    routes_admin,
    routes_bom,
    routes_docs,
    routes_erp,
    routes_master,
    routes_model,
    routes_quote,
)
from backend.api.auth import CurrentUser, Db, get_document, get_revision, need
from backend.api.common import ApiError, pick
from backend.api.common import body as _body
from backend.db.models import Document, User
from backend.db.models import Revision as RevisionRow
from core.dxf.reader import (
    MAX_FILE_BYTES,
    MAX_TEXT_CHARS,
    DxfError,
    parse_dxf_with_timeout,
    run_isolated,
    validate_dxf_bytes,
)
from core.dxf.writer import apply_edits
from core.geometry.errors import GeomError

# ponytail: DXF/JSON stay on local disk (rows reference revision_id); object storage when multi-node
# ponytail: entity rows land in S8 when quote traceability needs FKs
VAR_DIR = Path(os.environ.get("AXCAD_VAR_DIR") or Path(__file__).resolve().parents[2] / "var")
REVISION_RE = re.compile(r"^[0-9a-f]{32}$")

app = FastAPI(title="AX-CAD")
app.include_router(routes_admin.router)
app.include_router(routes_docs.router)
app.include_router(routes_model.router)
app.include_router(routes_master.router)
app.include_router(routes_quote.router)
app.include_router(routes_bom.router)
app.include_router(routes_erp.router)
PARSE_SLOTS = threading.Semaphore(2)  # each parse is a process holding up to 2 GB
SLOT_WAIT_S = 60
MAX_EDIT_BODY = 5 * 1024**2
MAX_PAYLOAD_CHARS = 200 * 1024**2  # render JSON on disk; block amplification guard
MAX_VERTICES = 10_000  # per polyline
MAX_TOTAL_VERTICES = 200_000  # per edit request
MAX_FIELD_ERRORS = 20  # per 422 response


@app.middleware("http")
async def _reject_oversized(request: Request, call_next: Any) -> Any:
    # reject before Starlette spools/buffers the body; chunked POSTs (no length) would bypass it
    if request.method == "POST":
        raw = request.headers.get("content-length")
        if raw is None:
            return _error(411, "LENGTH_REQUIRED", "Content-Length header required")
        if not raw.isdigit():
            return _error(400, "REQUEST_INVALID", "Invalid Content-Length")
        path = request.url.path
        limit = (
            MAX_EDIT_BODY
            if path.endswith("/edits")
            else routes_model.MAX_IMPORT_BYTES + 1024**2
            if path.endswith("/imports")
            else MAX_FILE_BYTES + 1024**2
        )
        if int(raw) > limit:
            return _error(413, "FILE_TOO_LARGE", "Request body too large")
    return await call_next(request)


def _error(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    err = {"code": code, "message": message, "details": details}
    return JSONResponse(_body(error=err), status_code=status)


@app.exception_handler(DxfError)
async def _dxf_error(_: Request, exc: DxfError) -> JSONResponse:
    return _error(exc.http_status, exc.code, exc.message)


@app.exception_handler(ApiError)
async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
    return _error(exc.status, exc.code, exc.message)


@app.exception_handler(GeomError)
async def _geom_error(_: Request, exc: GeomError) -> JSONResponse:
    err = {"code": exc.code, "message": exc.message, "details": exc.details}
    return JSONResponse(_body(error=err), status_code=exc.http)


@app.exception_handler(IntegrityError)
async def _integrity_error(_: Request, exc: IntegrityError) -> JSONResponse:
    if getattr(exc.orig, "sqlstate", None) == "23505":  # unique_violation
        return _error(409, "DUPLICATE_KEY", "Duplicate key")
    return _error(409, "CONSTRAINT_VIOLATION", "Request conflicts with existing data")


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    if request.url.path.endswith("/edits"):
        return _error(422, "EDIT_INVALID", "Invalid edit request")
    # FN-08: 422 with the offending fields; field path and error type only, never the input.
    # Unknown keys are the client's own text: not echoed; count and length are capped.
    fields = [
        {
            "field": "<unknown>"
            if e["type"] == "extra_forbidden"
            else ".".join(map(str, e["loc"][1:] if e["loc"][0] == "body" else e["loc"]))[:100],
            "type": e["type"],
        }
        for e in exc.errors()[:MAX_FIELD_ERRORS]
    ]
    return _error(422, "REQUEST_INVALID", "Missing or invalid request fields", fields)


@app.exception_handler(StarletteHTTPException)
async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
    return _error(exc.status_code, code, str(exc.detail))


def _revision_no(n: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA (bijective base 26)."""
    n, out = n + 1, ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def _read_json(revision_id: str) -> dict[str, Any] | None:
    path = VAR_DIR / "revisions" / f"{revision_id}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _revision(db: Db, user: User, revision_id: str, lock: bool = False) -> Any:
    if not REVISION_RE.fullmatch(revision_id):
        raise ApiError(404, "REVISION_NOT_FOUND", "Revision not found")
    return get_revision(db, user, revision_id, lock)


def _result(rev: RevisionRow, payload: dict[str, Any], extra: list[str] | None = None) -> Any:
    summary = payload["summary"]
    return _body(
        {
            "document_id": rev.document_id,
            "revision_id": rev.revision_id,
            "revision_no": rev.revision_no,
            "parent_revision_id": rev.parent_revision_id,
            "entity_count": summary["entity_count"],
            "layer_count": summary["layer_count"],
            "block_count": summary["block_count"],
            "warnings": payload["warnings"] + (extra or []),
        }
    )


def _store(
    db: Db,
    user: User,
    doc: Document,
    revision_id: str,
    payload: dict[str, Any],
    data: bytes,
    note: str | None,
) -> Any:
    """Write the render JSON, add the revision row and make it current, in one DB transaction."""
    out = VAR_DIR / "revisions" / f"{revision_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, allow_nan=False)
    if len(text) > MAX_PAYLOAD_CHARS:
        raise DxfError("DXF_BLOCK_LIMIT", "Drawing too large to render", 422)
    out.write_text(text, encoding="utf-8")
    # revision_no from the count is race-free because callers hold the document row lock
    count = db.scalar(select(func.count()).where(RevisionRow.document_id == doc.document_id))
    rev = RevisionRow(
        revision_id=revision_id,
        document_id=doc.document_id,
        revision_no=_revision_no(count or 0),
        parent_revision_id=doc.current_revision_id,
        file_format="DXF",
        checksum=hashlib.sha256(data).hexdigest(),
        note=note,
        created_by=user.user_id,
    )
    db.add(rev)
    db.flush()
    doc.current_revision_id, doc.status = revision_id, "DRAFT"
    db.commit()
    return _result(rev, payload)


def _check_editable(doc: Document) -> None:
    if doc.status == "IN_REVIEW":
        raise ApiError(409, "DOCUMENT_IN_REVIEW", "Document is in review")


@app.post("/api/documents/{document_id}/dxf")
# sync def: FastAPI runs it in a threadpool, so the blocking parse never stalls the event loop
def upload_dxf(
    document_id: int,
    user: CurrentUser,
    db: Db,
    file: UploadFile = File(...),  # noqa: B008
    note: Annotated[str | None, Form(max_length=500)] = None,
) -> Any:
    doc = get_document(db, user, document_id, lock=True)
    need(user, "DESIGNER")
    _check_editable(doc)
    if not (file.filename or "").lower().endswith(".dxf"):
        raise DxfError("DXF_INVALID_FILE", "Only .dxf files are accepted")
    data = file.file.read(MAX_FILE_BYTES + 1)  # size cap; never read more than limit+1
    validate_dxf_bytes(data)

    cur = db.get(RevisionRow, doc.current_revision_id) if doc.current_revision_id else None
    if cur is not None and cur.checksum == hashlib.sha256(data).hexdigest():
        payload = _read_json(cur.revision_id)
        if payload is not None:
            return _result(cur, payload, ["NO_CHANGE"])  # FN-09: identical file, no new revision

    revision_id = uuid.uuid4().hex
    upload = VAR_DIR / "uploads" / f"{revision_id}.dxf"
    out = VAR_DIR / "revisions" / f"{revision_id}.json"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(data)
    try:
        with PARSE_SLOTS:
            payload = parse_dxf_with_timeout(str(upload))
        return _store(db, user, doc, revision_id, payload, data, note)
    except BaseException:
        db.rollback()
        upload.unlink(missing_ok=True)
        out.unlink(missing_ok=True)
        raise


@app.get("/api/health")
def health(db: Db) -> Any:
    """Liveness for the container healthcheck and monitoring: no auth, nothing but up/down."""
    try:
        db.execute(select(1))
    except SQLAlchemyError:
        raise ApiError(503, "UNAVAILABLE", "Database unavailable") from None
    if not os.access(VAR_DIR if VAR_DIR.exists() else VAR_DIR.parent, os.W_OK):
        raise ApiError(503, "UNAVAILABLE", "File storage not writable")
    return _body({"status": "ok"})


@app.get("/api/documents/{document_id}/revisions")
def list_revisions(document_id: int, user: CurrentUser, db: Db) -> Any:
    doc = get_document(db, user, document_id)
    rows = db.execute(
        select(RevisionRow, User.user_name)
        .join(User, User.user_id == RevisionRow.created_by)
        .where(RevisionRow.document_id == document_id)
        .order_by(RevisionRow.created_at.desc())
    ).all()
    items = [
        {
            **pick(r, "revision_id", "revision_no", "parent_revision_id", "checksum", "note"),
            **pick(r, "created_by", "created_at"),
            "created_by_name": name,
            "is_current": r.revision_id == doc.current_revision_id,
            # ponytail: reads each render JSON for the count; add a column if lists get long
            "entity_count": ((_read_json(r.revision_id) or {}).get("summary") or {}).get(
                "entity_count"
            ),
        }
        for r, name in rows
    ]
    return _body({"items": items, "total": len(items)})


@app.get("/api/revisions/{revision_id}/render")
def get_render(revision_id: str, user: CurrentUser, db: Db) -> Any:
    rev, _ = _revision(db, user, revision_id)
    payload = _read_json(rev.revision_id)
    if payload is None:
        return _error(404, "REVISION_NOT_FOUND", "Revision not found")
    return _body(payload)


def _signatures(payload: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """handle -> (layer, canonical geom/paths); block-expanded entities share their INSERT handle."""
    by: dict[str, list[dict[str, Any]]] = {}
    for e in payload["entities"]:
        by.setdefault(e["handle"], []).append(e)
    return {
        h: (es[0]["layer"], json.dumps([[e["type"], e.get("geom"), e["paths"]] for e in es]))
        for h, es in by.items()
    }


@app.get("/api/revisions/{revision_a}/diff/{revision_b}")
def diff_revisions(revision_a: str, revision_b: str, user: CurrentUser, db: Db) -> Any:
    ra, da = _revision(db, user, revision_a)
    rb, db_doc = _revision(db, user, revision_b)
    if da.document_id != db_doc.document_id:
        raise ApiError(422, "REVISION_MISMATCH", "Revisions belong to different documents")
    pa, pb = _read_json(ra.revision_id), _read_json(rb.revision_id)
    if pa is None or pb is None:
        return _error(404, "REVISION_NOT_FOUND", "Revision not found")
    sa, sb = _signatures(pa), _signatures(pb)
    layers: dict[str, dict[str, int]] = {}

    def bump(layer: str, kind: str) -> None:
        layers.setdefault(layer, {"added": 0, "removed": 0, "changed": 0})[kind] += 1

    for h in sb.keys() - sa.keys():
        bump(sb[h][0], "added")
    for h in sa.keys() - sb.keys():
        bump(sa[h][0], "removed")
    for h in sa.keys() & sb.keys():
        if sa[h] != sb[h]:
            bump(sb[h][0], "changed")
    total = {k: sum(v[k] for v in layers.values()) for k in ("added", "removed", "changed")}
    by_layer = [{"layer": k, **v} for k, v in sorted(layers.items())]
    return _body(
        {"revision_a": ra.revision_id, "revision_b": rb.revision_id, **total, "layers": by_layer}
    )


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
def edit_revision(revision_id: str, req: EditRequest, user: CurrentUser, db: Db) -> Any:
    rev, doc = _revision(db, user, revision_id, lock=True)
    need(user, "DESIGNER")
    _check_editable(doc)
    if doc.current_revision_id != rev.revision_id:
        raise ApiError(409, "REVISION_NOT_CURRENT", "Only the current revision can be edited")
    src = VAR_DIR / "uploads" / f"{revision_id}.dxf"
    if not src.is_file():
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
        return _store(db, user, doc, new_id, payload, dst.read_bytes(), None)
    except BaseException:
        db.rollback()
        dst.unlink(missing_ok=True)
        out.unlink(missing_ok=True)
        raise
    finally:
        PARSE_SLOTS.release()


@app.get("/api/revisions/{revision_id}/dxf")
def get_dxf(revision_id: str, user: CurrentUser, db: Db) -> Any:
    rev, doc = _revision(db, user, revision_id)
    if not {"ADMIN", "DESIGNER"} & set(user.role_codes) and not (
        doc.status in ("APPROVED", "RELEASED") and doc.current_revision_id == rev.revision_id
    ):
        raise ApiError(403, "EXPORT_NOT_APPROVED", "Only approved drawings can be exported")
    path = VAR_DIR / "uploads" / f"{rev.revision_id}.dxf"
    if not path.is_file():
        return _error(404, "REVISION_NOT_FOUND", "Revision not found")
    return FileResponse(path, media_type="application/dxf", filename=f"{rev.revision_id}.dxf")
