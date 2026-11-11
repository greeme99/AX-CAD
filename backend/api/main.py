import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.api import (
    routes_admin,
    routes_assembly,
    routes_blocks,
    routes_bom,
    routes_copilot,
    routes_docs,
    routes_lisp,
    routes_master,
    routes_model,
    routes_quote,
    routes_system,
    routes_underlay,
)
from backend.api.auth import (
    CurrentUser,
    Db,
    get_document,
    get_project,
    get_revision,
    local_login,
    need,
)
from backend.api.common import ApiError, pick
from backend.api.common import body as _body
from backend.db.models import Document, UnderlayFile, User
from backend.db.models import Revision as RevisionRow
from core.dxf import dimvars, param_blocks
from core.dxf.dwg import dwg_release, dwg_to_dxf, export_drawing
from core.dxf.dwg import info as dwg_info
from core.dxf.dwg import selftest as dwg_selftest
from core.dxf.dwg import sweep_leftovers as dwg_sweep
from core.dxf.objects import (
    MAX_XDATA_BYTES,
    MAX_XRECORD_BYTES,
    check_tags,
    dictionary_entry,
    entity_tags,
)
from core.dxf.plot import plot_file, scale_text, sheet_file
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
dwg_sweep()  # HANDOFF §6: DWG conversion folders left by an earlier crash or power cut
app.include_router(routes_admin.router)
app.include_router(routes_docs.router)
app.include_router(routes_model.router)
app.include_router(routes_master.router)
app.include_router(routes_quote.router)
app.include_router(routes_bom.router)
app.include_router(routes_system.router)
app.include_router(routes_copilot.router)
app.include_router(routes_assembly.router)
app.include_router(routes_blocks.router)
app.include_router(routes_lisp.router)
app.include_router(routes_underlay.router)
PARSE_SLOTS = threading.Semaphore(2)  # each parse is a process holding up to 2 GB
DWG_SLOTS = threading.Semaphore(1)  # a stuck converter (up to 120 s) must not block parsing
_REPARSE_GUARD = threading.Lock()
# ponytail: one small lock per re-parsed revision, kept for the process lifetime
_REPARSE_LOCKS: dict[str, threading.Lock] = {}
SLOT_WAIT_S = 60
MAX_EDIT_BODY = 5 * 1024**2
MAX_JSON_BODY = 8 * 1024**2  # other JSON bodies: quotes (external quantities), master bundles
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
            else routes_system.MAX_PATCH_BYTES + 1024**2
            if path == "/api/system/updates"
            else routes_master.MAX_PDF_BYTES + 1024**2
            if path == "/api/master-data/costbook-draft"
            else routes_underlay.MAX_UNDERLAY_BYTES + 1024**2
            if path.endswith("/underlays")
            else MAX_IMPORT_BODY
            if path.endswith("/drawings/import")
            else MAX_JSON_BODY
            if request.headers.get("content-type", "").startswith("application/json")
            else MAX_FILE_BYTES + 1024**2
        )
        if int(raw) > limit:
            return _error(413, "FILE_TOO_LARGE", "Request body too large")
    return await call_next(request)


API_HEADERS = {
    # answers are data or downloads, never pages: nothing in them may run or be framed. nginx sets
    # the same on the server; the per-PC bundle's Next.js proxy forwards these (its headers() rules
    # do not reach rewritten responses)
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; sandbox",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    # token-authenticated data stays out of the browser cache (a route may set its own, e.g. underlay
    # previews: setdefault below keeps it)
    "Cache-Control": "no-store",
}


@app.middleware("http")
async def _api_headers(request: Request, call_next: Any) -> Any:
    # registered last = outermost: also covers the guards' own error answers above
    response = await call_next(request)
    if request.url.path.startswith("/api"):  # same scope as nginx (dev /docs keeps its CDN assets)
        for k, v in API_HEADERS.items():
            response.headers.setdefault(k, v)
    return response


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
    data = file.file.read(MAX_FILE_BYTES + 1)  # size cap; never read more than limit+1
    return _upload(db, user, document_id, (file.filename or "").lower(), data, note)


def _upload(db: Db, user: User, document_id: int, name: str, data: bytes, note: str | None) -> Any:
    """A DXF or DWG (converted) as the document's new current revision; access already checked."""
    if not name.endswith((".dxf", ".dwg")):
        raise DxfError("DXF_INVALID_FILE", "Only .dxf and .dwg files are accepted")
    converted: list[str] = []  # warnings of the DWG conversion
    source: dict[str, Any] | None = None
    if name.endswith(".dwg"):  # Track 8 P1-5: through the converter the user installed
        if len(data) > MAX_FILE_BYTES:
            raise DxfError("FILE_TOO_LARGE", "File exceeds 50 MB", 413)
        db.commit()  # no transaction (and no row lock) is held while the converter runs
        if not DWG_SLOTS.acquire(timeout=SLOT_WAIT_S):
            return _error(503, "SERVER_BUSY", "Server busy, retry later")
        try:
            dwg, (data, kind, clean) = data, dwg_to_dxf(data)
        finally:
            DWG_SLOTS.release()
        converted = [f"DWG_CONVERTED:{kind}"] + ([] if clean else ["DWG_CONVERT_PARTIAL"])
        source = {"dwg": dwg, "converter": kind, "clean": clean, "release": dwg_release(dwg)}
    doc = get_document(db, user, document_id, lock=True)
    validate_dxf_bytes(data)

    cur = db.get(RevisionRow, doc.current_revision_id) if doc.current_revision_id else None
    if cur is not None and cur.checksum == hashlib.sha256(data).hexdigest():
        payload = _read_json(cur.revision_id)
        if payload is not None:
            return _result(cur, payload, ["NO_CHANGE"])  # FN-09: identical file, no new revision
    return _save_dxf(db, user, doc, data, note, converted, source)


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
    db: Db,
    user: User,
    doc: Document,
    data: bytes,
    note: str | None,
    converted: list[str],
    source: dict[str, Any] | None = None,
) -> Any:
    """Parse checked DXF bytes into a new current revision (upload, DWG, blank start). A DWG's
    original and its conversion report are kept next to the DXF (Track 10)."""
    revision_id = uuid.uuid4().hex
    upload = VAR_DIR / "uploads" / f"{revision_id}.dxf"
    original = VAR_DIR / "uploads" / f"{revision_id}.dwg"
    report = VAR_DIR / "uploads" / f"{revision_id}.conv.json"
    out = VAR_DIR / "revisions" / f"{revision_id}.json"
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(data)
    try:
        with PARSE_SLOTS:
            payload = parse_dxf_with_timeout(str(upload))
        payload["warnings"][:0] = converted
        if source is not None:
            original.write_bytes(source["dwg"])
            report.write_text(json.dumps(_conversion(source, data, payload)), encoding="utf-8")
        return _store(db, user, doc, revision_id, payload, data, note)
    except BaseException:
        db.rollback()
        for f in (upload, original, report, out):
            f.unlink(missing_ok=True)
        raise


def _conversion(source: dict[str, Any], dxf: bytes, payload: dict[str, Any]) -> dict[str, Any]:
    """What a DWG became: converter, source release, sizes, objects by type and what was lost."""
    kinds: Counter[str] = Counter(e.get("type", "?") for e in payload["entities"])
    lost = [w for w in payload["warnings"] if w.startswith(("UNSUPPORTED_ENTITY", "DXF_XREF"))]
    return {
        "converter": source["converter"],
        "clean": source["clean"],
        "source_release": source["release"],
        "source_bytes": len(source["dwg"]),
        "dxf_bytes": len(dxf),
        "summary": payload["summary"],
        "entities_by_type": dict(sorted(kinds.items())),
        "lost": lost,
    }


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
    rev, doc = _revision(db, user, revision_id)
    return _body(_with_underlays(db, doc.project_id, _payload(db, rev)))


def _with_underlays(db: Db, project_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """2D underlay U3: each linked underlay says which stored file it is and how big its page is
    (so the editor can draw the frame at once); the stored payload itself is not changed."""
    ents = [e for e in payload.get("entities", []) if e.get("type") == "UNDERLAY"]
    shas = {e["underlay"]["sha256"] for e in ents if e["underlay"].get("sha256")}
    if not shas:
        return payload
    rows = {
        r.sha256: r
        for r in db.scalars(
            select(UnderlayFile).where(
                UnderlayFile.project_id == project_id, UnderlayFile.sha256.in_(shas)
            )
        )
    }
    out = []
    for e in payload["entities"]:
        sha = e["underlay"].get("sha256") if e.get("type") == "UNDERLAY" else None
        row = rows.get(sha) if sha else None
        if row is None:
            out.append(e)
            continue
        page = e["underlay"]["page"]
        size = row.pages[page] if 0 <= page < len(row.pages) else None
        info = {
            **e["underlay"],
            "file_id": row.underlay_id,
            "page_count": row.page_count,
            "size": [size["width"], size["height"]] if size else None,
        }
        out.append({**e, "underlay": info})
    return {**payload, "entities": out}


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
NAME_RE = r"^[^\x00-\x1f\x7f-\x9f\u2028\u2029]+$"
LayerName = Annotated[str, Field(min_length=1, max_length=255, pattern=NAME_RE)]
LinetypeName = Annotated[str, Field(min_length=1, max_length=255, pattern=NAME_RE)]
EntityColor = Annotated[int, Field(ge=0, le=256)]  # ACI; 0 = BYBLOCK, 256 = BYLAYER
LayerColor = Annotated[int, Field(ge=1, le=255)]
MAX_OPS = 10_000
MAX_LAYER_OPS = 1_000
MAX_BLOCK_DEFS = 100
MAX_RENAMES = 20
MAX_TOTAL_TAGS = 100_000


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


# W7a (plan §22.8): MTEXT. A newline is a paragraph break; other control characters would end the
# DXF group value, so they are refused (format codes such as \\P or {\\H2;..} are plain text)
MTextValue = Annotated[
    str,
    Field(
        min_length=1,
        max_length=MAX_TEXT_CHARS,
        pattern=r"^[^\x00-\x09\x0b\x0c\x0e-\x1f\x7f-\x9f\u2028\u2029]+$",
    ),
]
# one line of text: no control characters at all (a newline would inject DXF group codes)
LineText = Annotated[
    str,
    Field(
        min_length=1,
        max_length=MAX_TEXT_CHARS,
        pattern=r"^[^\x00-\x1f\x7f-\x9f\u2028\u2029]+$",
    ),
]


class TextGeom(_Strict):
    type: Literal["TEXT"]
    insert: Pt
    height: Size
    value: LineText
    rotation: Angle


class _DimOptions(_Strict):
    """W4 (plan §22.4): a dimension's own variables (ActiveX Dim* properties) and text override."""

    overrides: Annotated[dict[str, Any], Field(max_length=100)] | None = None
    text: (
        Annotated[
            str, Field(max_length=MAX_TEXT_CHARS, pattern=r"^[^\x00-\x1f\x7f-\x9f\u2028\u2029]*$")
        ]
        | None
    ) = None

    @field_validator("overrides")
    @classmethod
    def _dimvars(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        for k, x in (v or {}).items():
            dimvars.check(k, x)
        return v

    @field_validator("text")
    @classmethod
    def _no_field(cls, v: str | None) -> str | None:
        if v is not None and "%<" in v:  # AutoCAD field codes (%<\AcVar ...>%) show file data
            raise ValueError("field codes are not allowed")
        return v


class DimLinearGeom(_DimOptions):
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


class DimAlignedGeom(_DimOptions):
    type: Literal["DIM_ALIGNED"]
    p1: Pt
    p2: Pt
    distance: Coord

    @model_validator(mode="after")
    def _non_degenerate(self) -> "DimAlignedGeom":
        if self.p1 == self.p2 or self.distance == 0:
            raise ValueError("degenerate dimension")
        return self


class DimAngularGeom(_DimOptions):
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


class DimRadiusGeom(_DimOptions):
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


class MTextGeom(_Strict):
    type: Literal["MTEXT"]
    insert: Pt
    height: Size
    width: Annotated[float, Field(allow_inf_nan=False, ge=0, le=1e9)] = 0
    value: MTextValue
    rotation: Angle
    attachment: Annotated[int, Field(ge=1, le=9)] = 1  # 1 top left .. 9 bottom right

    @model_validator(mode="after")
    def _no_split_code(self) -> "MTextGeom":
        # "\\" + newline would become "\\\\P": an escaped backslash and a literal P, not a break
        if re.search(r"\\\r?\n", self.value):
            raise ValueError("a backslash cannot end a line")
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
    value: MTextValue
    height: Size


class DimDiameterGeom(_DimOptions):
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
    | MultiLeaderGeom
    | MTextGeom,
    Field(discriminator="type"),
]

# W7a: DXF lineweights (1/100 mm; -1 BYLAYER, -2 BYBLOCK, -3 default)
Lineweight = Literal[
    -3,
    -2,
    -1,
    0,
    5,
    9,
    13,
    15,
    18,
    20,
    25,
    30,
    35,
    40,
    50,
    53,
    60,
    70,
    80,
    90,
    100,
    106,
    120,
    140,
    158,
    200,
    211,
]
LayoutName = Annotated[str, Field(min_length=1, max_length=255, pattern=NAME_RE)]
# W7c (plan §22.8): [group code, value]; codes and value types checked in core.dxf.objects
XTag = Annotated[list[Any], Field(min_length=2, max_length=2)]
XAppName = Annotated[
    str,
    Field(
        min_length=1, max_length=255, pattern=r"^[^\x00-\x1f\x7f-\x9f\u2028\u2029<>/\\\":;?*|,=`]+$"
    ),
]
# W7d: an attribute tag (no spaces, as AutoCAD) and a block's attribute definitions
AttTag = Annotated[str, Field(min_length=1, max_length=255, pattern=r"^[^\s\x00-\x1f\x7f-\x9f]+$")]
MAX_ATTDEFS = 500
XData = Annotated[
    dict[XAppName, Annotated[list[XTag], Field(max_length=4000)]], Field(max_length=64)
]


def _check_xdata(v: dict[str, list[list[Any]]] | None) -> dict[str, list[list[Any]]] | None:
    if v is not None:
        size = sum(
            len(app.encode()) + 3 + check_tags(tags, "xdata", 255) for app, tags in v.items()
        )
        if size > MAX_XDATA_BYTES:
            raise ValueError("XDATA exceeds 16 KB per entity")
    return v


class Created(_Strict):
    layer: LayerName
    geom: Geom
    color: EntityColor | None = None  # None: BYLAYER when created, unchanged when modified
    linetype: LinetypeName | None = None
    # W7a (plan §22.8): None = left as is (created: the default)
    lineweight: Lineweight | None = None
    true_color: Annotated[int, Field(ge=0, le=0xFFFFFF)] | None = None  # 0xRRGGBB
    transparency: Annotated[int, Field(ge=0, le=90)] | None = None  # percent, as AutoCAD
    ltscale: Annotated[float, Field(allow_inf_nan=False, gt=0, le=1e6)] | None = None
    visible: bool | None = None
    space: LayoutName | None = None  # "Model" (default) or a paper space layout name
    xdata: XData | None = None  # W7c: {application: groups}; [] removes that application's
    # W7d: an INSERT's attribute values by tag (the block's attribute definitions)
    attributes: Annotated[dict[AttTag, LineText], Field(max_length=MAX_ATTDEFS)] | None = None

    _xdata = field_validator("xdata")(_check_xdata)

    @model_validator(mode="after")
    def _attributes_on_inserts(self) -> "Created":
        if self.attributes is not None and self.geom.type != "INSERT":
            raise ValueError("attributes are only for block references")
        return self


class Modified(Created):
    handle: Handle

    @model_validator(mode="after")
    def _stays_put(self) -> "Modified":
        if self.space is not None:  # an entity keeps its space: delete and create to move it
            raise ValueError("space is only for created entities")
        return self


class LayerOp(_Strict):
    """Applied after the entity edits, in order; name is the layer's name at that point."""

    name: LayerName
    create: bool = False
    rename: LayerName | None = None
    color: LayerColor | None = None
    linetype: LinetypeName | None = None
    locked: bool | None = None
    # W7b (plan §22.8): the rest of the layer table record
    lineweight: Lineweight | None = None
    true_color: Annotated[int, Field(ge=0, le=0xFFFFFF)] | None = None
    plot: bool | None = None
    off: bool | None = None
    frozen: bool | None = None
    description: (
        Annotated[str, Field(max_length=255, pattern=r"^[^\x00-\x1f\x7f-\x9f\u2028\u2029]*$")]
        | None
    ) = None


SymName = Annotated[
    str,
    Field(
        min_length=1, max_length=255, pattern=r"^[^\x00-\x1f\x7f-\x9f\u2028\u2029<>/\\\":;?*|,=`]+$"
    ),
]  # W7b: no characters AutoCAD forbids in symbol names
Len = Annotated[float, Field(allow_inf_nan=False, ge=0, le=1e6)]
# a font is a file name only (looked up in the font folders): no path, no drive
FontFile = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[\w .\-가-힣]+\.(?i:ttf|ttc|otf|shx)$")
]


class DimVars(_Strict):
    """The dimension style variables scripts set most (lengths in mm)."""

    dimtxt: Len | None = None
    dimasz: Len | None = None
    dimexe: Len | None = None
    dimexo: Len | None = None
    dimgap: Len | None = None
    dimdec: Annotated[int, Field(ge=0, le=8)] | None = None
    dimscale: Annotated[float, Field(allow_inf_nan=False, gt=0, le=1e6)] | None = None
    dimtxsty: SymName | None = None


class TableOp(_Strict):
    """W7b (plan §22.8): a symbol table record. create/modify run before the entity edits (new
    entities may use them), rename/delete after them (references are updated or checked)."""

    kind: Literal["linetype", "textstyle", "dimstyle", "regapp"]
    op: Literal["create", "modify", "rename", "delete"]
    name: SymName
    new_name: SymName | None = None
    description: (
        Annotated[str, Field(max_length=255, pattern=r"^[^\x00-\x1f\x7f-\x9f\u2028\u2029]*$")]
        | None
    ) = None
    # dash > 0, gap < 0, dot 0 (mm); empty = continuous
    pattern: (
        Annotated[
            list[Annotated[float, Field(allow_inf_nan=False, ge=-1e6, le=1e6)]],
            Field(max_length=12),
        ]
        | None
    ) = None
    font: FontFile | None = None
    height: Len | None = None
    width: Annotated[float, Field(allow_inf_nan=False, gt=0, le=100)] | None = None
    oblique: Annotated[float, Field(allow_inf_nan=False, ge=-85, le=85)] | None = None
    dim: DimVars | None = None

    @model_validator(mode="after")
    def _fits(self) -> "TableOp":
        fields = {
            "linetype": {"description", "pattern"},
            "textstyle": {"font", "height", "width", "oblique"},
            "dimstyle": {"dim"},
            "regapp": set(),
        }[self.kind]
        given = {
            k
            for k in ("description", "pattern", "font", "height", "width", "oblique", "dim")
            if getattr(self, k) is not None
        }
        if self.op in ("create", "modify"):
            if given - fields:
                raise ValueError(f"fields {sorted(given - fields)} are not for a {self.kind}")
            if self.new_name is not None:
                raise ValueError("new_name is only for rename")
        elif given:
            raise ValueError(f"{self.op} takes no record fields")
        if (self.op == "rename") != (self.new_name is not None):
            raise ValueError("rename needs new_name")
        if self.pattern and any(v != 0 and abs(v) < 0.01 for v in self.pattern):
            raise ValueError("a dash or gap is 0 (a dot) or at least 0.01 mm")
        if self.kind == "regapp" and self.op in ("modify", "rename"):
            raise ValueError("a registered application is only created or deleted")
        return self


DictKey = Annotated[str, Field(min_length=1, max_length=255, pattern=NAME_RE)]


class DictOp(_Strict):
    """W7c (plan §22.8): an XRecord in the named object dictionary (or an entity's extension
    dictionary), `path` down from the root; set makes missing dictionaries on the way."""

    path: Annotated[list[DictKey], Field(min_length=1, max_length=8)]
    op: Literal["set", "delete"]
    handle: Handle | None = None
    xrecord: Annotated[list[XTag], Field(max_length=10_000)] | None = None

    @model_validator(mode="after")
    def _fits(self) -> "DictOp":
        if (self.op == "set") != (self.xrecord is not None):
            raise ValueError("set needs xrecord, delete takes none")
        if (
            self.xrecord is not None
            and check_tags(self.xrecord, "xrecord", 2048) > MAX_XRECORD_BYTES
        ):
            raise ValueError("XRecord exceeds 64 KB")
        return self


class AttDef(_Strict):
    """W7d: an attribute definition in a block (mm, world coordinates like the block's entities)."""

    tag: AttTag
    prompt: Annotated[
        str, Field(max_length=255, pattern=r"^[^\x00-\x1f\x7f-\x9f\u2028\u2029]*$")
    ] = ""
    default: Annotated[
        str, Field(max_length=MAX_TEXT_CHARS, pattern=r"^[^\x00-\x1f\x7f-\x9f\u2028\u2029]*$")
    ] = ""
    insert: Pt
    height: Size
    rotation: Angle = 0
    layer: LayerName = "0"
    invisible: bool = False
    constant: bool = False
    verify: bool = False
    preset: bool = False


class BlockDef(_Strict):
    """New block from the given entities (mm, world coordinates); base = its insertion point.
    W7d: op redefine replaces a block's contents (its references follow, their attributes are
    synchronised), rename and delete (only an unused block) run after the entity edits."""

    op: Literal["create", "redefine", "rename", "delete"] = "create"
    name: BlockName
    new_name: BlockName | None = None
    base: Pt | None = None
    entities: Annotated[list[Created], Field(max_length=MAX_OPS)] = []
    attdefs: Annotated[list[AttDef], Field(max_length=MAX_ATTDEFS)] = []
    # Track 9 Q4: a parametric block's template and values (the BLOCK description, DXF group 4)
    description: Annotated[str, Field(max_length=4000)] | None = None

    @model_validator(mode="after")
    def _in_the_block(self) -> "BlockDef":
        if any(c.space is not None for c in self.entities):  # W7a: they live in the block itself
            raise ValueError("block entities take no space")
        if self.op in ("create", "redefine"):
            if self.base is None or not (self.entities or self.attdefs):
                raise ValueError("a block needs a base point and contents")
            if self.new_name is not None:
                raise ValueError("new_name is only for rename")
            tags = [a.tag.upper() for a in self.attdefs]
            if len(tags) != len(set(tags)):
                raise ValueError("attribute tags must be unique")
        else:
            given = self.base is not None or self.entities or self.attdefs or self.description
            if given or (self.op == "rename") != (self.new_name is not None):
                raise ValueError(
                    f"{self.op} takes only the name"
                    + (" and new_name" if self.op == "rename" else "")
                )
        return self

    @field_validator("description")
    @classmethod
    def _pb_only(cls, v: str | None) -> str | None:
        # only a parametric block's JSON, one line: a raw newline would end the DXF group value
        if v is None:
            return None
        bad = any(ord(c) < 32 or 0x7F <= ord(c) <= 0x9F or c in "\u2028\u2029" for c in v)
        if bad or param_blocks.parse_description(v) is None:
            raise ValueError("description must be a parametric block description")
        return v


class UnderlayFlags(_Strict):
    on: bool = True
    monochrome: bool = False
    adjust: bool = False  # adjust the colours for the background
    inside: bool = True  # the clip keeps what is inside (False: outside)


class UnderlayClip(_Strict):
    """In page millimetres from the page's lower-left corner, unscaled and unrotated."""

    kind: Literal["rect", "poly"]
    points: Annotated[list[Pt], Field(min_length=2, max_length=500)]

    @model_validator(mode="after")
    def _shape(self) -> "UnderlayClip":
        if self.kind == "rect":
            (a, b) = self.points[:2] if len(self.points) == 2 else (None, None)
            if a is None or b is None or a[0] == b[0] or a[1] == b[1]:
                raise ValueError("a rectangle clip is two opposite corners")
        elif len(self.points) < 3:
            raise ValueError("a polygon clip needs three points")
        return self


class UnderlayGeom(_Strict):
    """A PDF/DWFx reference placed in the drawing (2D underlay U2)."""

    format: Literal["pdf", "dwf"]  # dwf = a DWFx file
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    name: Annotated[str, Field(min_length=1, max_length=120, pattern=NAME_RE)]
    page: Annotated[int, Field(ge=0, le=499)]
    insert: Pt  # mm; the page's lower-left corner
    scale: Annotated[float, Field(allow_inf_nan=False, ge=1e-6, le=1e6)]  # model mm per page mm
    rotation: Angle
    flags: UnderlayFlags = UnderlayFlags()
    contrast: Annotated[int, Field(ge=0, le=100)] = 50
    fade: Annotated[int, Field(ge=0, le=80)] = 0
    clip: UnderlayClip | None = None


class UnderlayOp(_Strict):
    op: Literal["create", "modify", "delete"]
    handle: Handle | None = None
    layer: LayerName | None = None
    geom: UnderlayGeom | None = None

    @model_validator(mode="after")
    def _fields(self) -> "UnderlayOp":
        ok = {
            "create": self.geom is not None and self.layer is not None and self.handle is None,
            "modify": self.geom is not None and self.handle is not None,
            "delete": self.handle is not None and self.geom is None,
        }[self.op]
        if not ok:
            raise ValueError("underlay operation fields do not fit its kind")
        return self


class LayoutOp(_Strict):
    """W7d: a paper space layout. create runs before the entity edits (new entities may go on it),
    rename and delete after them."""

    op: Literal["create", "rename", "delete"]
    name: SymName
    new_name: SymName | None = None

    @model_validator(mode="after")
    def _fits(self) -> "LayoutOp":
        if (self.op == "rename") != (self.new_name is not None):
            raise ValueError("rename needs new_name")
        if "model" in (self.name.lower(), (self.new_name or "").lower()):
            raise ValueError("the model space is not a layout to change")
        return self


class GroupOp(_Strict):
    """W7d: a named group of model space entities (modify replaces its members)."""

    op: Literal["create", "modify", "delete"]
    name: SymName
    new_name: SymName | None = None
    description: (
        Annotated[str, Field(max_length=255, pattern=r"^[^\x00-\x1f\x7f-\x9f\u2028\u2029]*$")]
        | None
    ) = None
    selectable: bool | None = None
    handles: Annotated[list[Handle], Field(min_length=1, max_length=MAX_OPS)] | None = None

    @model_validator(mode="after")
    def _fits(self) -> "GroupOp":
        if self.op == "create" and (not self.handles or self.new_name is not None):
            raise ValueError("create needs handles")
        if self.op == "delete" and (
            self.handles is not None
            or self.new_name
            or self.description is not None
            or self.selectable is not None
        ):
            raise ValueError("delete takes only the name")
        return self


class EditRequest(_Strict):
    created: Annotated[list[Created], Field(max_length=MAX_OPS)] = []
    modified: Annotated[list[Modified], Field(max_length=MAX_OPS)] = []
    deleted: Annotated[list[Handle], Field(max_length=MAX_OPS)] = []
    layers: Annotated[list[LayerOp], Field(max_length=MAX_LAYER_OPS)] = []
    blocks: Annotated[list[BlockDef], Field(max_length=MAX_BLOCK_DEFS)] = []
    underlays: Annotated[list[UnderlayOp], Field(max_length=MAX_OPS)] = []
    tables: Annotated[list[TableOp], Field(max_length=MAX_LAYER_OPS)] = []  # W7b
    dictionaries: Annotated[list[DictOp], Field(max_length=MAX_LAYER_OPS)] = []  # W7c
    layouts: Annotated[list[LayoutOp], Field(max_length=100)] = []  # W7d
    groups: Annotated[list[GroupOp], Field(max_length=MAX_LAYER_OPS)] = []  # W7d

    @model_validator(mode="after")
    def _op_count(self) -> "EditRequest":
        in_blocks = [c.geom for b in self.blocks for c in b.entities]
        if sum(op.rename is not None for op in self.layers) > MAX_RENAMES:
            raise ValueError("too many layer renames")  # each one rescans every entity
        if sum(op.op in ("rename", "delete") for op in self.tables) > MAX_RENAMES:
            raise ValueError("too many table renames/deletes")  # each one rescans every entity
        n = len(self.created) + len(self.modified) + len(self.deleted) + len(self.layers)
        n += len(in_blocks) + len(self.underlays) + len(self.tables) + len(self.dictionaries)
        n += len(self.layouts) + len(self.groups) + sum(len(b.attdefs) for b in self.blocks)
        n += sum(1 for b in self.blocks if b.op in ("rename", "delete"))
        n += sum(len(g.handles or []) for g in self.groups)
        renames = sum(b.op != "create" for b in self.blocks)  # redefine rescans too
        renames += sum(op.op != "create" for op in self.layouts)
        if renames > MAX_RENAMES:
            raise ValueError("too many block / layout renames and deletes")  # each one rescans
        n_tags = sum(len(op.xrecord or []) for op in self.dictionaries)
        n_tags += sum(
            len(t) for c in self.created + self.modified for t in (c.xdata or {}).values()
        )
        if n_tags > MAX_TOTAL_TAGS:  # W7c review L1: bounds the work of one save
            raise ValueError("too many XDATA / XRecord groups")
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


def _check_underlays(db: Db, project_id: int, req: EditRequest) -> None:
    """An underlay may only point at a file of this drawing's own project (2D underlay U2)."""
    wanted = {op.geom.sha256: op.geom for op in req.underlays if op.geom is not None}
    if not wanted:
        return
    rows = {
        r.sha256: r
        for r in db.scalars(
            select(UnderlayFile).where(
                UnderlayFile.project_id == project_id, UnderlayFile.sha256.in_(list(wanted))
            )
        )
    }
    for sha, g in wanted.items():
        row = rows.get(sha)
        if row is None:
            raise ApiError(422, "EDIT_UNDERLAY_NOT_FOUND", "참조 파일이 이 프로젝트에 없습니다")
        if (g.format == "pdf") != (row.kind == "pdf") or g.page >= row.page_count:
            raise ApiError(422, "EDIT_UNDERLAY_PAGE", "참조 파일의 형식이나 페이지가 맞지 않습니다")


@app.post("/api/revisions/{revision_id}/edits")
def edit_revision(revision_id: str, req: EditRequest, user: CurrentUser, db: Db) -> Any:
    rev, doc = _revision(db, user, revision_id, lock=True)
    need(user, "DESIGNER")
    if doc.current_revision_id != rev.revision_id:
        raise ApiError(409, "REVISION_NOT_CURRENT", "Only the current revision can be edited")
    src = VAR_DIR / "uploads" / f"{revision_id}.dxf"
    if not src.is_file():
        return _error(404, "REVISION_NOT_FOUND", "Revision not found")
    _check_underlays(db, doc.project_id, req)
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
    # Track 8 P3-4: a paper-space sheet printed as laid out (its paper, viewports, title block);
    # paper, landscape and scale are then the sheet's own
    sheet: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    underlays: bool = (
        True  # 2D underlay U5: PDF/DWFx references under the drawing (model space plots)
    )


MAX_PLOT_UNDERLAYS = 5


def plot_underlays(
    db: Db, project_id: int, payload: dict[str, Any], hidden: set[str]
) -> list[dict[str, Any]]:
    """The page images of the drawing's linked, visible underlays for the plot (2D underlay U5).
    One that cannot be made (file gone, render failed) is left out: the drawing still plots."""
    from backend.api import routes_underlay as ru

    shown = {ly["name"] for ly in payload["layers"] if ly.get("visible", True)} - hidden
    out: list[dict[str, Any]] = []
    made: dict[tuple[Any, ...], bytes] = {}
    for e in _with_underlays(db, project_id, payload)["entities"]:
        u = e.get("underlay")
        if e.get("type") != "UNDERLAY" or not u or e["layer"] not in shown:
            continue
        if not (u.get("linked") and u.get("file_id") and u.get("size") and u["flags"]["on"]):
            continue
        row = db.get(UnderlayFile, u["file_id"])
        if row is None or len(out) >= MAX_PLOT_UNDERLAYS:
            continue
        width = min(
            4000, max(1200, round(u["size"][0] * 6))
        )  # about 150 dpi at the page's own size
        key = (u["file_id"], u["page"], width, bool(u["flags"]["monochrome"]), int(u["contrast"]))
        if key not in made:  # the same page placed twice is made once
            try:
                png = ru.worker.run(
                    ru.ops.render, row.kind, ru._path(row), u["page"], width, [], None, timeout_s=60
                )
                made[key] = ru.tone(png, key[3], key[4])
            except (ru.UnderlayError, ApiError):
                continue
        out.append(
            {
                "png": made[key],
                "size": u["size"],
                "insert": u["insert"],
                "scale": u["scale"],
                "rotation": u["rotation"],
                "fade": u["fade"],
                "clip": _clip_polygon(u.get("clip")),
                "inside": bool(u["flags"]["inside"]),
            }
        )
    return out


def _clip_polygon(clip: dict[str, Any] | None) -> list[list[float]] | None:
    if not clip:
        return None
    pts = clip["points"]
    if clip["kind"] == "rect":
        (ax, ay), (bx, by) = pts[0], pts[1]
        x0, x1, y0, y1 = min(ax, bx), max(ax, bx), min(ay, by), max(ay, by)
        return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    return [list(p) for p in pts]


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
    path = str(VAR_DIR / "revisions" / f"{rev.revision_id}.json")
    if req.sheet is not None:
        job: tuple[Any, ...] = (sheet_file, path, req.sheet, doc.title, set(req.hidden), req.color)
    else:
        job = (plot_file, path, req.paper, req.landscape, req.scale, title, set(req.hidden))
        job += (
            req.color,
            plot_underlays(db, doc.project_id, payload, set(req.hidden)) if req.underlays else [],
        )
    try:
        pdf, scale = run_isolated(
            *job, timeout_s=PLOT_TIMEOUT_S, fail=("PLOT_FAILED", "Drawing could not be plotted")
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


def _read_objects(db: Db, user: Any, revision_id: str, func: Any, *args: Any) -> Any:
    rev, _ = _revision(db, user, revision_id)
    src = VAR_DIR / "uploads" / f"{rev.revision_id}.dxf"
    if not src.is_file():
        return _error(404, "REVISION_NOT_FOUND", "Revision not found")
    db.commit()  # give the connection back during the worker read
    if not PARSE_SLOTS.acquire(timeout=SLOT_WAIT_S):
        return _error(503, "SERVER_BUSY", "Server busy, retry later")
    try:
        return _body(run_isolated(func, str(src), *args))
    finally:
        PARSE_SLOTS.release()


@app.get("/api/revisions/{revision_id}/entities/{handle}")
def get_entity(
    revision_id: str,
    handle: Annotated[str, PathParam(pattern=r"^[0-9A-Fa-f]{1,16}$")],
    user: CurrentUser,
    db: Db,
) -> Any:
    """W7c: one object's DXF groups as AutoLISP `entget` sees them (XDATA included); the same
    data any member can already download with the drawing."""
    return _read_objects(db, user, revision_id, entity_tags, handle)


@app.get("/api/revisions/{revision_id}/dictionaries")
def get_dictionary(
    revision_id: str,
    user: CurrentUser,
    db: Db,
    path: Annotated[list[DictKey], Query(max_length=8)] = [],  # noqa: B006 - FastAPI copies it
    handle: Annotated[str | None, Query(pattern=r"^[0-9A-Fa-f]{1,16}$")] = None,
) -> Any:
    """W7c: a dictionary's entries or an XRecord's groups (root: the named object dictionary,
    or the extension dictionary of the entity `handle`)."""
    return _read_objects(db, user, revision_id, dictionary_entry, list(path), handle)


@app.get("/api/revisions/{revision_id}/dxf")
def get_dxf(revision_id: str, user: CurrentUser, db: Db) -> Any:
    rev, _ = _revision(db, user, revision_id)  # Track 7: no approval gate, members may export
    path = VAR_DIR / "uploads" / f"{rev.revision_id}.dxf"
    if not path.is_file():
        return _error(404, "REVISION_NOT_FOUND", "Revision not found")
    return FileResponse(path, media_type="application/dxf", filename=f"{rev.revision_id}.dxf")


# --- Track 10: DWG save, versions, original, conversion report, batch import, converter -----


@app.get("/api/system/dwg-converter")
def dwg_converter(user: CurrentUser) -> Any:
    """Which DWG converter this PC has and what it can do (re-checked on every call)."""
    return _body(dwg_info())


@app.post("/api/system/dwg-converter/test")
def dwg_converter_test(user: CurrentUser) -> Any:
    """Run this PC's converter once on a built-in drawing (DXF -> DWG 2018 -> DXF)."""
    need(user, "DESIGNER")
    if not DWG_SLOTS.acquire(timeout=EXPORT_WAIT_S):
        return _error(503, "SERVER_BUSY", "Server busy, retry later")
    try:
        return _body(dwg_selftest())
    finally:
        DWG_SLOTS.release()


@app.get("/api/revisions/{revision_id}/export")
def export_revision(
    revision_id: str,
    user: CurrentUser,
    db: Db,
    format: Literal["DWG", "DXF"] = "DWG",
    version: Literal["2018", "2013", "2010", "2007", "2004", "2000", "R12"] = "2018",
) -> Any:
    """Save as: the revision as DWG, or DXF of an older AutoCAD release (ODA File Converter)."""
    rev, _ = _revision(db, user, revision_id)
    path = VAR_DIR / "uploads" / f"{rev.revision_id}.dxf"
    if not path.is_file():
        return _error(404, "REVISION_NOT_FOUND", "Revision not found")
    # a revision never changes: each format/version is converted once (BUG-59)
    cached = VAR_DIR / "exports" / f"{rev.revision_id}-{version}.{format.lower()}"
    if cached.is_file():
        data = cached.read_bytes()
    else:
        db.commit()  # nothing is held while the converter runs
        # a short wait: a queue of exports must not hold request threads (or uploads) for long
        if not DWG_SLOTS.acquire(timeout=EXPORT_WAIT_S):
            return _error(503, "SERVER_BUSY", "Server busy, retry later")
        try:
            data = export_drawing(path.read_bytes(), format, version)
        finally:
            DWG_SLOTS.release()
        cached.parent.mkdir(parents=True, exist_ok=True)
        tmp = cached.with_suffix(f".{uuid.uuid4().hex}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, cached)
    ext = format.lower()
    return Response(
        data,
        media_type="application/acad" if format == "DWG" else "application/dxf",
        headers={
            "Content-Disposition": f'attachment; filename="{rev.revision_id}-{version}.{ext}"'
        },
    )


@app.get("/api/revisions/{revision_id}/original")
def original_dwg(revision_id: str, user: CurrentUser, db: Db) -> Any:
    """The DWG a revision was converted from, byte for byte."""
    rev, _ = _revision(db, user, revision_id)
    path = VAR_DIR / "uploads" / f"{rev.revision_id}.dwg"
    if not path.is_file():
        return _error(404, "ORIGINAL_NOT_FOUND", "This revision was not uploaded as DWG")
    return FileResponse(path, media_type="application/acad", filename=f"{rev.revision_id}.dwg")


@app.get("/api/revisions/{revision_id}/conversion")
def conversion_report(revision_id: str, user: CurrentUser, db: Db) -> Any:
    rev, _ = _revision(db, user, revision_id)
    path = VAR_DIR / "uploads" / f"{rev.revision_id}.conv.json"
    if not path.is_file():
        return _error(404, "ORIGINAL_NOT_FOUND", "This revision was not uploaded as DWG")
    return _body(json.loads(path.read_text(encoding="utf-8")))


log = logging.getLogger(__name__)
MAX_IMPORT_FILES = 20
MAX_IMPORT_BODY = 200 * 1024**2  # one batch request (files are 50 MB each)
IMPORT_BUDGET_S = 600  # one batch request; later files are reported, not converted
EXPORT_WAIT_S = 5
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _drop_empty(db: Db, document_id: int | None) -> None:
    """A batch file that failed leaves no empty document behind."""
    db.rollback()
    try:
        doc = db.get(Document, document_id) if document_id else None
        if doc is not None and doc.current_revision_id is None:
            db.delete(doc)
            db.commit()
    except SQLAlchemyError:
        db.rollback()
        log.exception("could not remove the empty document %s", document_id)


DOC_NO_BAD = re.compile(r"[^A-Za-z0-9._-]+")


@app.post("/api/projects/{project_id}/drawings/import")
def import_drawings(
    project_id: int,
    user: CurrentUser,
    db: Db,
    files: list[UploadFile] = File(...),  # noqa: B008
) -> Any:
    """Batch: one new DRAWING document per DXF/DWG file (doc no. from the file name); a file that
    fails is reported and the others go on."""
    if get_project(db, user, project_id).status == "ARCHIVED":
        raise ApiError(409, "PROJECT_ARCHIVED", "Archived projects are read-only")
    need(user, "DESIGNER")
    if not 1 <= len(files) <= MAX_IMPORT_FILES:
        raise ApiError(422, "IMPORT_TOO_MANY", f"1~{MAX_IMPORT_FILES} files at a time")
    taken = set(db.scalars(select(Document.doc_no).where(Document.project_id == project_id)))
    results = []
    deadline = time.monotonic() + IMPORT_BUDGET_S
    for n, f in enumerate(files, 1):
        fname = CONTROL.sub("", f.filename or "")[-200:]
        stem = Path(fname).stem
        if time.monotonic() > deadline:
            results.append(
                {"file": fname, "ok": False, "code": "IMPORT_TIME_LIMIT", "message": "skipped"}
            )
            continue
        base = DOC_NO_BAD.sub("_", stem).strip("._-")[:56] or f"DWG-{n}"
        doc_no, k = base, 1
        while doc_no in taken:
            k += 1
            doc_no = f"{base}-{k}"
        made: int | None = None
        try:
            data = f.file.read(MAX_FILE_BYTES + 1)
            if not fname.lower().endswith((".dxf", ".dwg")):
                raise DxfError("DXF_INVALID_FILE", "Only .dxf and .dwg files are accepted")
            doc = Document(
                project_id=project_id,
                created_by=user.user_id,
                doc_no=doc_no,
                doc_type="DRAWING",
                title=stem[:200] or doc_no,
            )
            db.add(doc)
            db.commit()
            made = doc.document_id
            taken.add(doc_no)
            r = _upload(db, user, doc.document_id, fname.lower(), data, "일괄 가져오기")
            if isinstance(r, JSONResponse):  # SERVER_BUSY
                raise ApiError(503, "SERVER_BUSY", "Server busy, retry later")
            d = r["data"]
            results.append(
                {"file": fname, "ok": True, "document_id": doc.document_id, "doc_no": doc_no}
                | {"revision_id": d["revision_id"], "warnings": d["warnings"]}
            )
        except (DxfError, ApiError) as e:
            _drop_empty(db, made)
            results.append({"file": fname, "ok": False, "code": e.code, "message": e.message})
        except IntegrityError:  # the same doc no. created meanwhile
            _drop_empty(db, made)
            results.append({"file": fname, "ok": False, "code": "DUPLICATE_KEY", "message": doc_no})
        except Exception:  # disk full, a database hiccup: this file fails, the others go on
            log.exception("batch import of one file failed")
            _drop_empty(db, made)
            results.append({"file": fname, "ok": False, "code": "IMPORT_FAILED", "message": ""})
    return _body({"items": results, "imported": sum(r["ok"] for r in results)})
