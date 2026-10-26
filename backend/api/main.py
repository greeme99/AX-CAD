import hashlib
import hmac
import json
import logging
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.api import (
    routes_admin,
    routes_bom,
    routes_docs,
    routes_master,
    routes_model,
    routes_quote,
)
from backend.api.auth import CurrentUser, Db, get_document, get_revision, local_login, need
from backend.api.common import ApiError, pick
from backend.api.common import body as _body
from backend.db.models import Document, User
from backend.db.models import Revision as RevisionRow
from core.dxf.dwg import dwg_to_dxf
from core.dxf.plot import plot_file, scale_text
from core.dxf.reader import (
    MAX_FILE_BYTES,
    MAX_SPLINE_FIT,
    MAX_TEXT_CHARS,
    RENDER_FORMAT,
    DxfError,
    parse_dxf_with_timeout,
    run_isolated,
    validate_dxf_bytes,
)
from core.dxf.template import blank_dxf
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
PARSE_SLOTS = threading.Semaphore(2)  # each parse is a process holding up to 2 GB
DWG_SLOTS = threading.Semaphore(1)  # a stuck converter (up to 120 s) must not block parsing
_REPARSE_GUARD = threading.Lock()
# ponytail: one small lock per re-parsed revision, kept for the process lifetime
_REPARSE_LOCKS: dict[str, threading.Lock] = {}
SLOT_WAIT_S = 60
MAX_EDIT_BODY = 5 * 1024**2
MAX_PAYLOAD_CHARS = 200 * 1024**2  # render JSON on disk; block amplification guard
MAX_VERTICES = 10_000  # per polyline
MAX_TOTAL_VERTICES = 200_000  # per edit request
MAX_FIELD_ERRORS = 20  # per 422 response
MAX_FEATURE_BODY = 256 * 1024  # feature params and other JSON edits (500 hole positions ~ 20 KB)
# JSON-only POSTs that get the small limit (P2-5 adds suppress, move and measure)
SMALL_POSTS = (
    "/features",
    "/pdf",
    "/solve",
    "/blank",
    "/suppress",
    "/move",
    "/measure",
    "/drawing",
)


LOCAL_HOST = re.compile(
    r"(?:https?://)?(?:localhost|127\.0\.0\.1|\[::1\])(?::\d{1,5})?", re.IGNORECASE
)


@app.middleware("http")
async def _local_guard(request: Request, call_next: Any) -> Any:
    """Per-PC mode (no login, AXCAD_LOCAL_USER + AXCAD_LOCAL_TOKEN): only this PC's AX-CAD window
    may call the API. Every request carries the install's token (X-AXCAD-Local, handed to the
    browser by start.ps1), so other sites (CSRF: a form cannot set it, fetch needs a CORS preflight
    this API never answers), rebound domains and other Windows accounts (the token file is readable
    by this user only) get nothing. Host / X-Forwarded-Host / Origin must still be localhost."""
    if local_login() is not None:
        h = request.headers
        names = [h.get("host", ""), *(h.getlist("x-forwarded-host")), *(h.getlist("origin"))]
        local = all(LOCAL_HOST.fullmatch(v) for v in names)
        token = os.environ["AXCAD_LOCAL_TOKEN"].encode()
        if not local or not hmac.compare_digest(h.get("x-axcad-local", "").encode(), token):
            return _error(403, "LOCAL_ONLY", "Only this PC's AX-CAD window may use the API")
    return await call_next(request)


@app.middleware("http")
async def _reject_oversized(request: Request, call_next: Any) -> Any:
    # reject before Starlette spools/buffers the body; chunked POSTs (no length) would bypass it
    if request.method in ("POST", "PATCH", "PUT"):
        raw = request.headers.get("content-length")
        if raw is None:
            return _error(411, "LENGTH_REQUIRED", "Content-Length header required")
        if not raw.isdigit():
            return _error(400, "REQUEST_INVALID", "Invalid Content-Length")
        path = request.url.path
        limit = (
            MAX_FEATURE_BODY
            if request.method == "PATCH" or path.endswith(SMALL_POSTS)
            else MAX_EDIT_BODY
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


@app.post("/api/documents/{document_id}/dxf")
# sync def: FastAPI runs it in a threadpool, so the blocking parse never stalls the event loop
def upload_dxf(
    document_id: int,
    user: CurrentUser,
    db: Db,
    file: UploadFile = File(...),  # noqa: B008
    note: Annotated[str | None, Form(max_length=500)] = None,
) -> Any:
    get_document(db, user, document_id)  # who may upload, checked before any converter runs
    need(user, "DESIGNER")
    name = (file.filename or "").lower()
    if not name.endswith((".dxf", ".dwg")):
        raise DxfError("DXF_INVALID_FILE", "Only .dxf and .dwg files are accepted")
    data = file.file.read(MAX_FILE_BYTES + 1)  # size cap; never read more than limit+1
    converted: list[str] = []  # warnings of the DWG conversion
    if name.endswith(".dwg"):  # Track 8 P1-5: through the converter the user installed
        if len(data) > MAX_FILE_BYTES:
            raise DxfError("FILE_TOO_LARGE", "File exceeds 50 MB", 413)
        db.commit()  # no transaction (and no row lock) is held while the converter runs
        if not DWG_SLOTS.acquire(timeout=SLOT_WAIT_S):
            return _error(503, "SERVER_BUSY", "Server busy, retry later")
        try:
            data, kind, clean = dwg_to_dxf(data)
        finally:
            DWG_SLOTS.release()
        converted = [f"DWG_CONVERTED:{kind}"] + ([] if clean else ["DWG_CONVERT_PARTIAL"])
    doc = get_document(db, user, document_id, lock=True)
    validate_dxf_bytes(data)

    cur = db.get(RevisionRow, doc.current_revision_id) if doc.current_revision_id else None
    if cur is not None and cur.checksum == hashlib.sha256(data).hexdigest():
        payload = _read_json(cur.revision_id)
        if payload is not None:
            return _result(cur, payload, ["NO_CHANGE"])  # FN-09: identical file, no new revision
    return _save_dxf(db, user, doc, data, note, converted)


class BlankRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sheet: Literal["A4", "A3", "A2", "A1", "A0"] = "A3"
    portrait: bool = False


@app.post("/api/documents/{document_id}/blank")
def start_blank(document_id: int, req: BlankRequest, user: CurrentUser, db: Db) -> Any:
    """Start drafting in AX-CAD: the first revision is an empty drawing with standard layers."""
    get_document(db, user, document_id)
    need(user, "DESIGNER")
    doc = get_document(db, user, document_id, lock=True)
    if doc.current_revision_id is not None:
        raise ApiError(409, "DOCUMENT_HAS_REVISION", "The drawing already has a revision")
    return _save_dxf(db, user, doc, blank_dxf(req.sheet, req.portrait), "빈 도면에서 시작", [])


def _save_dxf(
    db: Db, user: User, doc: Document, data: bytes, note: str | None, converted: list[str]
) -> Any:
    """Parse checked DXF bytes into a new current revision (upload, DWG, blank start)."""
    revision_id = uuid.uuid4().hex
    upload = VAR_DIR / "uploads" / f"{revision_id}.dxf"
    out = VAR_DIR / "revisions" / f"{revision_id}.json"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(data)
    try:
        with PARSE_SLOTS:
            payload = parse_dxf_with_timeout(str(upload))
        payload["warnings"][:0] = converted
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
    return _body(_payload(db, rev))


def _payload(db: Db, rev: Any) -> dict[str, Any]:
    """Stored render payload, re-parsed once when it predates the current format."""
    payload = _read_json(rev.revision_id)
    if payload is None:
        raise ApiError(404, "REVISION_NOT_FOUND", "Revision not found")
    rid = rev.revision_id
    src = VAR_DIR / "uploads" / f"{rid}.dxf"
    if payload.get("format") == RENDER_FORMAT or not src.is_file():
        return payload
    # stored before new fields: re-parse once, one request per revision, inside the parse slots;
    # if that fails or the server is busy, the stored payload is still served (BUG-48)
    db.commit()  # give the connection back during the worker parse
    with _REPARSE_GUARD:
        lock = _REPARSE_LOCKS.setdefault(rid, threading.Lock())
    with lock:
        again = _read_json(rid)
        if again is not None and again.get("format") == RENDER_FORMAT:
            return again  # another request did it meanwhile
        if not PARSE_SLOTS.acquire(timeout=SLOT_WAIT_S):
            return payload
        try:
            fresh = parse_dxf_with_timeout(str(src))
        except DxfError as exc:
            logging.getLogger(__name__).warning("re-parse of %s failed: %s", rid, exc.code)
            return payload
        finally:
            PARSE_SLOTS.release()
        fresh["parent_revision_id"] = payload.get("parent_revision_id")
        text = json.dumps(fresh, allow_nan=False)
        if len(text) <= MAX_PAYLOAD_CHARS:
            out = VAR_DIR / "revisions" / f"{rid}.json"
            tmp = out.with_name(f"{rid}.{uuid.uuid4().hex}.tmp")
            tmp.write_text(text, encoding="utf-8")
            try:
                os.replace(tmp, out)  # readers never see half a file
            except OSError:  # Windows: a reader has it open; the next view retries
                tmp.unlink(missing_ok=True)
        return fresh


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
# no control characters: ezdxf writes names unescaped, a newline would inject DXF group codes
NAME_RE = r"^[^\x00-\x1f\x7f]+$"
LayerName = Annotated[str, Field(min_length=1, max_length=255, pattern=NAME_RE)]
LinetypeName = Annotated[str, Field(min_length=1, max_length=255, pattern=NAME_RE)]
EntityColor = Annotated[int, Field(ge=0, le=256)]  # ACI; 0 = BYBLOCK, 256 = BYLAYER
LayerColor = Annotated[int, Field(ge=1, le=255)]
MAX_OPS = 10_000
MAX_LAYER_OPS = 1_000
MAX_BLOCK_DEFS = 100
MAX_RENAMES = 20


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


class HatchGeom(_Strict):
    type: Literal["HATCH"]
    boundary: Annotated[list[Vertex], Field(min_length=2, max_length=MAX_VERTICES)]  # closed loop
    pattern: Annotated[str, Field(pattern=r"^[A-Za-z0-9_\-]{1,31}$")]  # SOLID or a library name
    scale: Annotated[float, Field(allow_inf_nan=False, ge=0.01, le=1000)]
    angle: Angle


BlockName = Annotated[str, Field(min_length=1, max_length=255, pattern=NAME_RE)]
InsertScale = Annotated[float, Field(allow_inf_nan=False, ge=-1e6, le=1e6)]


class InsertGeom(_Strict):
    type: Literal["INSERT"]
    name: BlockName
    insert: Pt
    xscale: InsertScale
    yscale: InsertScale
    rotation: Angle

    @model_validator(mode="after")
    def _non_degenerate(self) -> "InsertGeom":
        if self.xscale == 0 or self.yscale == 0:
            raise ValueError("zero block scale")
        return self


class PointGeom(_Strict):
    type: Literal["POINT"]
    point: Pt


class EllipseGeom(_Strict):
    type: Literal["ELLIPSE"]
    center: Pt
    major: Pt  # major axis vector (mm)
    ratio: Annotated[float, Field(allow_inf_nan=False, gt=0, le=1)]
    start_param: Angle  # radians
    end_param: Angle

    @model_validator(mode="after")
    def _non_degenerate(self) -> "EllipseGeom":
        if self.major == [0, 0]:
            raise ValueError("zero major axis")
        return self


class SplineGeom(_Strict):
    type: Literal["SPLINE"]
    # the CAD curve solve is O(n^2) in memory: 1,000 fit points is ~24 MB, 10,000 would be GBs
    fit: Annotated[list[Pt], Field(min_length=2, max_length=MAX_SPLINE_FIT)]

    @model_validator(mode="after")
    def _distinct(self) -> "SplineGeom":
        if any(a == b for a, b in zip(self.fit, self.fit[1:], strict=False)):
            raise ValueError("repeated spline point")
        return self


class LeaderGeom(_Strict):
    type: Literal["LEADER"]
    vertices: Annotated[list[Pt], Field(min_length=2, max_length=1000)]


class MultiLeaderGeom(_Strict):
    type: Literal["MULTILEADER"]
    vertices: Annotated[list[Pt], Field(min_length=2, max_length=1000)]
    value: Annotated[str, Field(min_length=1, max_length=MAX_TEXT_CHARS)]
    height: Size


class DimDiameterGeom(_Strict):
    type: Literal["DIM_DIAMETER"]
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
    | DimRadiusGeom
    | DimDiameterGeom
    | HatchGeom
    | InsertGeom
    | PointGeom
    | EllipseGeom
    | SplineGeom
    | LeaderGeom
    | MultiLeaderGeom,
    Field(discriminator="type"),
]


class Created(_Strict):
    layer: LayerName
    geom: Geom
    color: EntityColor | None = None  # None: BYLAYER when created, unchanged when modified
    linetype: LinetypeName | None = None


class Modified(Created):
    handle: Handle


class LayerOp(_Strict):
    """Applied after the entity edits, in order; name is the layer's name at that point."""

    name: LayerName
    create: bool = False
    rename: LayerName | None = None
    color: LayerColor | None = None
    linetype: LinetypeName | None = None
    locked: bool | None = None


class BlockDef(_Strict):
    """New block from the given entities (mm, world coordinates); base = its insertion point."""

    name: BlockName
    base: Pt
    entities: Annotated[list[Created], Field(min_length=1, max_length=MAX_OPS)]


class EditRequest(_Strict):
    created: Annotated[list[Created], Field(max_length=MAX_OPS)] = []
    modified: Annotated[list[Modified], Field(max_length=MAX_OPS)] = []
    deleted: Annotated[list[Handle], Field(max_length=MAX_OPS)] = []
    layers: Annotated[list[LayerOp], Field(max_length=MAX_LAYER_OPS)] = []
    blocks: Annotated[list[BlockDef], Field(max_length=MAX_BLOCK_DEFS)] = []

    @model_validator(mode="after")
    def _op_count(self) -> "EditRequest":
        in_blocks = [c.geom for b in self.blocks for c in b.entities]
        if sum(op.rename is not None for op in self.layers) > MAX_RENAMES:
            raise ValueError("too many layer renames")  # each one rescans every entity
        n = len(self.created) + len(self.modified) + len(self.deleted) + len(self.layers)
        n += len(in_blocks)
        if not 1 <= n <= MAX_OPS:
            raise ValueError("1..10000 operations required")
        geoms = [c.geom for c in self.created] + [m.geom for m in self.modified] + in_blocks
        n_vertices = sum(len(g.points) for g in geoms if isinstance(g, PolyGeom))
        n_vertices += sum(len(g.boundary) for g in geoms if isinstance(g, HatchGeom))
        n_vertices += sum(len(g.fit) for g in geoms if isinstance(g, SplineGeom))
        n_vertices += sum(
            len(g.vertices) for g in geoms if isinstance(g, (LeaderGeom, MultiLeaderGeom))
        )
        if n_vertices > MAX_TOTAL_VERTICES:
            raise ValueError("too many polyline vertices")
        return self


@app.post("/api/revisions/{revision_id}/edits")
def edit_revision(revision_id: str, req: EditRequest, user: CurrentUser, db: Db) -> Any:
    rev, doc = _revision(db, user, revision_id, lock=True)
    need(user, "DESIGNER")
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


PLOT_TIMEOUT_S = 60


class PlotRequest(_Strict):
    paper: Literal["A4", "A3", "A2", "A1", "A0"] = "A3"
    landscape: bool = True
    # paper mm per model mm (0.5 = 1:2); None = the largest standard scale that fits
    scale: Annotated[float, Field(allow_inf_nan=False, ge=1e-4, le=100)] | None = None
    hidden: Annotated[list[LayerName], Field(max_length=MAX_LAYER_OPS)] = []  # layers left out
    color: bool = False  # default: black on white, as drawings are printed


@app.post("/api/revisions/{revision_id}/pdf")
def plot_revision(revision_id: str, req: PlotRequest, user: CurrentUser, db: Db) -> Any:
    """Track 8 P1-4: the drawing's model space on a titled sheet."""
    rev, doc = _revision(db, user, revision_id)
    payload = _payload(db, rev)
    author = db.get(User, rev.created_by)
    title = {
        "doc_no": doc.doc_no,
        "title": doc.title,
        "revision": str(rev.revision_no),
        "date": rev.created_at.strftime("%Y-%m-%d"),
        "author": author.user_name if author else "",
    }
    if payload.get("format") != RENDER_FORMAT:  # re-parse failed or busy: plot what is stored
        logging.getLogger(__name__).info("plotting stored payload of %s", rev.revision_id)
    db.commit()  # give the connection back while the worker draws
    # a separate process with the parse limits (memory, 60 s) inside the parse slots (BUG-48)
    if not PARSE_SLOTS.acquire(timeout=SLOT_WAIT_S):
        return _error(503, "SERVER_BUSY", "Server busy, retry later")
    try:
        pdf, scale = run_isolated(
            plot_file,
            str(VAR_DIR / "revisions" / f"{rev.revision_id}.json"),
            req.paper,
            req.landscape,
            req.scale,
            title,
            set(req.hidden),
            req.color,
            timeout_s=PLOT_TIMEOUT_S,
            fail=("PLOT_FAILED", "Drawing could not be plotted"),
        )
    finally:
        PARSE_SLOTS.release()
    return Response(
        pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{rev.revision_id}.pdf"',
            "X-Plot-Scale": scale_text(scale),
        },
    )


@app.get("/api/revisions/{revision_id}/dxf")
def get_dxf(revision_id: str, user: CurrentUser, db: Db) -> Any:
    rev, _ = _revision(db, user, revision_id)  # Track 7: no approval gate, members may export
    path = VAR_DIR / "uploads" / f"{rev.revision_id}.dxf"
    if not path.is_file():
        return _error(404, "REVISION_NOT_FOUND", "Revision not found")
    return FileResponse(path, media_type="application/dxf", filename=f"{rev.revision_id}.dxf")
