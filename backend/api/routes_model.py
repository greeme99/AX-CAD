"""3D features (sketch -> Extrude/Revolve, Boolean, STEP/IGES import), metrics (FN-15), mesh (FN-13),
STEP/IGES export (FN-12)."""

import contextlib
import hashlib
import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal

import OCP
from fastapi import APIRouter, File, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import func, select

from backend.api.auth import CurrentUser, Db, get_document, need
from backend.api.common import ApiError, body, pick
from backend.db.models import Document, Feature, Revision, User
from core.geometry.errors import GeomError
from core.geometry.exchange import check_file, export_job, import_job
from core.geometry.features import MAX_DISTANCE_MM, boolean_job, extrude_job, revolve_job
from core.geometry.mesh import mesh_job
from core.geometry.worker import run_kernel

router = APIRouter()
Handle = Annotated[str, Field(pattern=r"^[0-9A-Fa-f]{1,16}$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Sketch(_Strict):
    source_revision_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    handles: Annotated[list[Handle], Field(min_length=1, max_length=1000)]

    @model_validator(mode="after")
    def _unique(self) -> "_Sketch":
        if len(set(self.handles)) != len(self.handles):
            raise ValueError("duplicate handles")
        return self


class ExtrudeParams(_Sketch):
    distance: Annotated[float, Field(allow_inf_nan=False, gt=0, le=MAX_DISTANCE_MM)]
    direction: Literal["+Z", "-Z"]


Coord = Annotated[float, Field(allow_inf_nan=False, ge=-MAX_DISTANCE_MM, le=MAX_DISTANCE_MM)]
Vec2 = Annotated[list[Coord], Field(min_length=2, max_length=2)]
FeatureId = Annotated[int, Field(ge=1)]


class RevolveParams(_Sketch):
    axis_point: Vec2
    axis_dir: Vec2
    angle_deg: Annotated[float, Field(allow_inf_nan=False, gt=0, le=360)]

    @model_validator(mode="after")
    def _axis(self) -> "RevolveParams":
        if self.axis_dir == [0, 0]:
            raise ValueError("zero axis direction")
        return self


class BooleanParams(_Strict):
    # ponytail: inputs live only in params (source of truth); "consumed" is derived by scanning the
    # document's BOOLEAN features, no extra column. Add a consumed_by FK if lists get large.
    op: Literal["FUSE", "CUT", "COMMON"]
    target_feature_id: FeatureId
    tool_feature_id: FeatureId


class ImportParams(_Strict):
    # created only by POST /imports, never from client JSON: file_key points at a stored upload
    file_key: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    format: Literal["STEP", "IGES"]
    filename: Annotated[str, Field(min_length=1, max_length=200)]


Params = ExtrudeParams | RevolveParams | BooleanParams | ImportParams
PARAMS: dict[str, type[Params]] = {
    "EXTRUDE": ExtrudeParams,
    "REVOLVE": RevolveParams,
    "BOOLEAN": BooleanParams,
    "IMPORT": ImportParams,
}
MAX_CHAIN_DEPTH = 64
MAX_IMPORT_BYTES = 100 * 1024**2  # FN-12
EXCHANGE_TIMEOUT_S = 120
Fmt = Literal["STEP", "IGES"]
EXTENSIONS: dict[str, Fmt] = {".step": "STEP", ".stp": "STEP", ".iges": "IGES", ".igs": "IGES"}
SUFFIX = {"STEP": "step", "IGES": "igs"}
MEDIA = {"STEP": "model/step", "IGES": "model/iges"}
IMPORT_SLOTS = threading.Semaphore(1)
SLOT_WAIT_S = 60


class FeatureCreate(_Strict):
    feature_type: Literal["EXTRUDE", "REVOLVE", "BOOLEAN"]
    params: dict[str, Any]


class FeatureUpdate(_Strict):
    params: dict[str, Any]


def _params(ftype: str, raw: dict[str, Any]) -> Params:
    try:
        return PARAMS[ftype].model_validate(raw)
    except ValidationError:
        raise GeomError("GEOM_INVALID_PARAM", "Invalid feature parameters") from None


def _var() -> Path:
    from backend.api import main  # late: main imports this module, tests patch main.VAR_DIR

    return main.VAR_DIR


def _brep_path(key: str) -> Path:
    return _var() / "brep" / f"{key}.brep"


def _inputs(p: Params) -> list[int]:
    return [p.target_feature_id, p.tool_feature_id] if isinstance(p, BooleanParams) else []


def _key(db: Db, p: Params) -> str:
    # chain-aware: a BOOLEAN key includes its inputs' keys, so a changed input changes the output key
    ins = [db.get(Feature, i) for i in _inputs(p)]
    raw = json.dumps(p.model_dump(), sort_keys=True) + OCP.__version__
    raw += "".join(i.brep_key or "" for i in ins if i)
    return hashlib.sha256(raw.encode()).hexdigest()


def _import_path(key: str, fmt: str) -> Path:
    return _var() / "imports" / f"{key}.{SUFFIX[fmt]}"


def _lock_import(db: Db, p: "ImportParams") -> None:
    """Serialise write/build/commit and delete/count/unlink of one shared upload file, across
    documents (the document row lock does not cover two documents importing the same file)."""
    db.execute(select(func.pg_advisory_xact_lock(func.hashtext(p.file_key))))


def _exchange(fn: Any, *args: Any) -> Any:
    try:
        return run_kernel(fn, *args, timeout_s=EXCHANGE_TIMEOUT_S)
    except GeomError as e:
        if e.code == "GEOM_KERNEL_TIMEOUT":
            raise GeomError("STEP_TIMEOUT", "STEP/IGES processing timed out", 504) from None
        raise


def _store_brep(key: str, brep: bytes) -> None:
    path = _brep_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(brep)


def _feature_brep(db: Db, f: Feature, mem: dict[int, bytes], depth: int = 0) -> bytes:
    """Body of a feature: this request's fresh builds, else the disk cache, else rebuilt from params."""
    if f.feature_id in mem:
        return mem[f.feature_id]
    path = _brep_path(f.brep_key or "")
    if f.brep_key and path.is_file():
        return path.read_bytes()
    doc = db.get(Document, f.document_id)
    assert doc is not None
    brep, _ = _build(db, doc, f.feature_type, _params(f.feature_type, f.params), mem, depth + 1)
    if f.brep_key:
        _store_brep(f.brep_key, brep)
    return brep


def _build(
    db: Db, doc: Document, ftype: str, p: Params, mem: dict[int, bytes], depth: int = 0
) -> tuple[bytes, dict[str, Any]]:
    """Resolve sketch handles / input bodies and run the kernel in the worker pool."""
    from backend.api import main

    if depth > MAX_CHAIN_DEPTH:
        raise GeomError("GEOM_INVALID_PARAM", "Feature chain too deep")
    if isinstance(p, ImportParams):
        path = _import_path(p.file_key, p.format)
        if not path.is_file():
            raise GeomError("STEP_READ_FAILED", "Imported source file is missing")
        return _exchange(import_job, str(path), p.format)  # type: ignore[no-any-return]
    if isinstance(p, BooleanParams):
        bodies = []
        for i in _inputs(p):
            inp = db.get(Feature, i)
            if inp is None or inp.document_id != doc.document_id:
                raise GeomError("GEOM_INVALID_PARAM", "Input feature not found in this document")
            bodies.append(_feature_brep(db, inp, mem, depth))
        return run_kernel(boolean_job, p.op, *bodies)

    rev = db.get(Revision, p.source_revision_id)
    payload = main._read_json(p.source_revision_id) if rev else None
    if rev is None or rev.document_id != doc.document_id or payload is None:
        raise GeomError("GEOM_INVALID_PARAM", "Source revision not found in this document")
    geoms = {e["handle"]: e["geom"] for e in payload["entities"] if "geom" in e}
    missing = [h for h in p.handles if h not in geoms]
    if missing:
        raise GeomError(
            "GEOM_INVALID_PARAM",
            "Handles not found or not editable",
            details={"handles": missing},
        )
    profile = [geoms[h] for h in p.handles]
    if isinstance(p, RevolveParams):
        return run_kernel(revolve_job, profile, tuple(p.axis_point), tuple(p.axis_dir), p.angle_deg)
    assert isinstance(p, ExtrudeParams)
    return run_kernel(extrude_job, profile, p.distance, p.direction)


def _consumed(db: Db, document_id: int, exclude: int | None = None) -> dict[int, int]:
    """body feature_id -> the BOOLEAN feature_id that consumed it."""
    out: dict[int, int] = {}
    rows = db.scalars(
        select(Feature).where(Feature.document_id == document_id, Feature.feature_type == "BOOLEAN")
    )
    for g in rows:
        if g.feature_id != exclude:
            for i in _inputs(BooleanParams.model_validate(g.params)):
                out[i] = g.feature_id
    return out


def _check_boolean(db: Db, doc: Document, p: BooleanParams, seq: int, own: int | None) -> None:
    def bad(msg: str) -> GeomError:
        return GeomError("GEOM_INVALID_PARAM", msg)

    if p.target_feature_id == p.tool_feature_id:
        raise bad("target and tool must differ")
    taken = _consumed(db, doc.document_id, own)
    for i in _inputs(p):
        inp = db.get(Feature, i)
        if inp is None or inp.document_id != doc.document_id:
            raise bad("Input feature not found in this document")
        if inp.seq >= seq:
            raise bad("Input feature must be earlier than the BOOLEAN")
        if i in taken:
            raise bad("Input feature is already consumed by another BOOLEAN")


def _out(f: Feature, consumed: dict[int, int]) -> dict[str, Any]:
    cols = ("feature_id", "seq", "feature_type", "params", "status", "error_code", "metrics")
    ins = _inputs(_params(f.feature_type, f.params))
    return {
        **pick(f, *cols),
        **pick(f, "created_at", "updated_at"),
        "visible": f.feature_id not in consumed,
        "inputs": ins,
    }


def _feature(db: Db, user: User, feature_id: int, lock: bool = False) -> tuple[Feature, Document]:
    f = db.get(Feature, feature_id)
    if f is None:
        raise ApiError(404, "FEATURE_NOT_FOUND", "Feature not found")
    try:
        return f, get_document(db, user, f.document_id, lock)
    except ApiError:
        raise ApiError(404, "FEATURE_NOT_FOUND", "Feature not found") from None


def _check_editable(doc: Document) -> None:
    if doc.status == "IN_REVIEW":
        raise ApiError(409, "DOCUMENT_IN_REVIEW", "Document is in review")


@router.get("/api/documents/{document_id}/features")
def list_features(document_id: int, user: CurrentUser, db: Db) -> Any:
    get_document(db, user, document_id)
    rows = db.scalars(
        select(Feature).where(Feature.document_id == document_id).order_by(Feature.seq)
    ).all()
    consumed = _consumed(db, document_id)
    return body({"items": [_out(f, consumed) for f in rows], "total": len(rows)})


@router.post("/api/documents/{document_id}/features")
def create_feature(document_id: int, req: FeatureCreate, user: CurrentUser, db: Db) -> Any:
    doc = get_document(db, user, document_id, lock=True)
    need(user, "DESIGNER")
    _check_editable(doc)
    p = _params(req.feature_type, req.params)
    seq = (
        db.scalar(select(func.max(Feature.seq)).where(Feature.document_id == document_id)) or 0
    ) + 1  # race-free: the document row lock is held
    if isinstance(p, BooleanParams):
        _check_boolean(db, doc, p, seq, None)
    brep, metrics = _build(db, doc, req.feature_type, p, {})
    key = _key(db, p)
    f = Feature(
        document_id=document_id,
        seq=seq,
        feature_type=req.feature_type,
        params=p.model_dump(),
        brep_key=key,
        metrics=metrics,
        created_by=user.user_id,
    )
    db.add(f)
    db.flush()
    _store_brep(key, brep)
    db.commit()
    return body(_out(f, _consumed(db, document_id)))


@router.patch("/api/features/{feature_id}")
def update_feature(feature_id: int, req: FeatureUpdate, user: CurrentUser, db: Db) -> Any:
    f, doc = _feature(db, user, feature_id, lock=True)
    need(user, "DESIGNER")
    _check_editable(doc)
    if f.feature_type == "IMPORT":
        raise ApiError(409, "FEATURE_NOT_EDITABLE", "Imported bodies have no parameters to edit")
    p = _params(f.feature_type, req.params)
    if isinstance(p, BooleanParams):
        _check_boolean(db, doc, p, f.seq, f.feature_id)
    mem: dict[int, bytes] = {}
    brep, metrics = _build(db, doc, f.feature_type, p, mem)  # failure raises before any row change
    mem[f.feature_id] = brep
    f.params, f.brep_key, f.metrics = p.model_dump(), _key(db, p), metrics
    f.updated_at = func.now()
    # regenerate dependent BOOLEANs in seq order; any failure rolls the whole request back
    changed = {f.feature_id}
    later = db.scalars(
        select(Feature)
        .where(
            Feature.document_id == doc.document_id,
            Feature.feature_type == "BOOLEAN",
            Feature.seq > f.seq,
        )
        .order_by(Feature.seq)
    ).all()
    for g in later:
        gp = BooleanParams.model_validate(g.params)
        if not changed.intersection(_inputs(gp)):
            continue
        try:
            mem[g.feature_id], g.metrics = _build(db, doc, "BOOLEAN", gp, mem)
        except GeomError as e:
            db.rollback()
            raise GeomError(
                e.code, e.message, e.http, {**(e.details or {}), "feature_id": g.feature_id}
            ) from None
        g.brep_key = _key(db, gp)
        g.updated_at = func.now()
        changed.add(g.feature_id)
    for fid in changed:
        row = f if fid == f.feature_id else db.get(Feature, fid)
        assert row is not None and row.brep_key is not None
        _store_brep(row.brep_key, mem[fid])
    db.commit()
    return body(_out(f, _consumed(db, doc.document_id)))


@router.delete("/api/features/{feature_id}")
def delete_feature(feature_id: int, user: CurrentUser, db: Db) -> Any:
    f, doc = _feature(db, user, feature_id, lock=True)
    need(user, "DESIGNER")
    _check_editable(doc)
    if f.feature_id in _consumed(db, doc.document_id):
        raise ApiError(409, "FEATURE_IN_USE", "Feature is an input of a BOOLEAN; delete that first")
    key = f.brep_key
    src = _params(f.feature_type, f.params)
    if isinstance(src, ImportParams):
        _lock_import(db, src)
    db.delete(f)
    db.flush()
    # BREP files are a content-addressed cache: keep it while another feature still uses the key
    if key and db.scalar(select(func.count()).where(Feature.brep_key == key)) == 0:
        _brep_path(key).unlink(missing_ok=True)
    if isinstance(src, ImportParams):
        _drop_import(db, src)
    db.commit()
    return body({"feature_id": feature_id})


@router.get("/api/features/{feature_id}/mesh")
def feature_mesh(feature_id: int, user: CurrentUser, db: Db) -> Any:
    f, _ = _feature(db, user, feature_id)
    if f.metrics is None or f.brep_key is None:
        raise ApiError(409, "FEATURE_NOT_BUILT", "Feature has no geometry")
    brep = _feature_brep(db, f, {})  # cache miss: params JSON is the source of truth
    mesh = run_kernel(mesh_job, brep)
    return body({**mesh, "bbox": f.metrics["bbox"]})


def _drop_import(db: Db, p: ImportParams) -> None:
    """Uploads are content-addressed too: remove the file once no IMPORT feature points at it."""
    users = select(func.count()).where(
        Feature.feature_type == "IMPORT", Feature.params["file_key"].astext == p.file_key
    )
    if db.scalar(users) == 0:
        _import_path(p.file_key, p.format).unlink(missing_ok=True)


@router.post("/api/documents/{document_id}/imports")
# sync def: the kernel call blocks; FastAPI runs it in the threadpool
def import_file(
    document_id: int,
    user: CurrentUser,
    db: Db,
    file: UploadFile = File(...),  # noqa: B008
) -> Any:
    doc = get_document(db, user, document_id, lock=True)
    need(user, "DESIGNER")
    _check_editable(doc)
    name = re.sub(r"[\x00-\x1f\x7f]", "", Path(file.filename or "").name)  # ends up in exports
    fmt = EXTENSIONS.get(Path(name).suffix.lower())
    if fmt is None or not 1 <= len(name) <= 200:
        raise GeomError("STEP_INVALID_FILE", "Only .step/.stp/.iges/.igs files are accepted")
    data = file.file.read(MAX_IMPORT_BYTES + 1)
    if len(data) > MAX_IMPORT_BYTES:
        raise ApiError(413, "FILE_TOO_LARGE", "File exceeds 100 MB")
    check_file(data, fmt)  # cheap magic/external-reference check before the kernel
    p = ImportParams(file_key=hashlib.sha256(data).hexdigest(), format=fmt, filename=name)
    # one import at a time: each holds up to 100 MB and a kernel worker for up to 120 s
    # ponytail: the document row lock + DB connection stay held during the build; move the
    # kernel call out of the transaction if imports of one document start queueing
    if not IMPORT_SLOTS.acquire(timeout=SLOT_WAIT_S):
        raise ApiError(503, "SERVER_BUSY", "Server busy, retry later")
    try:
        _lock_import(db, p)
        path = _import_path(p.file_key, fmt)
        if not path.is_file():  # content-addressed: an existing file has these exact bytes
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)  # atomic: readers never see a partial file
        del data
        try:
            brep, metrics = _build(db, doc, "IMPORT", p, {})
        except BaseException:
            with contextlib.suppress(Exception):  # never mask the original error
                _drop_import(db, p)
            raise
    finally:
        IMPORT_SLOTS.release()
    seq = (
        db.scalar(select(func.max(Feature.seq)).where(Feature.document_id == document_id)) or 0
    ) + 1  # race-free: the document row lock is held
    key = _key(db, p)
    f = Feature(
        document_id=document_id,
        seq=seq,
        feature_type="IMPORT",
        params=p.model_dump(),
        brep_key=key,
        metrics=metrics,
        created_by=user.user_id,
    )
    db.add(f)
    db.flush()
    _store_brep(key, brep)
    db.commit()
    return body(_out(f, _consumed(db, document_id)))


def _export_name(f: Feature) -> str:
    p = _params(f.feature_type, f.params)
    if isinstance(p, ImportParams):
        return Path(p.filename).stem
    kind = p.op if isinstance(p, BooleanParams) else f.feature_type
    return f"{kind.title()}{f.seq:03d}"


@router.get("/api/documents/{document_id}/model")
def export_file(
    document_id: int,
    user: CurrentUser,
    db: Db,
    format: Annotated[Literal["STEP", "IGES"], Query()] = "STEP",
) -> Any:
    doc = get_document(db, user, document_id)
    # same rule as DXF export: designers always, others only once the document is approved
    if not {"ADMIN", "DESIGNER"} & set(user.role_codes) and doc.status not in (
        "APPROVED",
        "RELEASED",
    ):
        raise ApiError(403, "EXPORT_NOT_APPROVED", "Only approved documents can be exported")
    rows = db.scalars(
        select(Feature).where(Feature.document_id == document_id).order_by(Feature.seq)
    ).all()
    consumed = _consumed(db, document_id)
    shown = [f for f in rows if f.feature_id not in consumed and f.status == "OK" and f.metrics]
    if not shown:
        raise ApiError(409, "MODEL_EMPTY", "The document has no 3D bodies to export")
    # ponytail: an IMPORT body is written as one product (its sub-assembly is flattened)
    items = [(_export_name(f), _feature_brep(db, f, {})) for f in shown]
    data = _exchange(export_job, items, format)
    fname = f"{doc.doc_no}.{SUFFIX[format]}"
    return Response(
        data,
        media_type=MEDIA[format],
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
