"""3D features (S5: sketch -> Extrude), their metrics (FN-15) and mesh (FN-13)."""

import hashlib
import json
from pathlib import Path
from typing import Annotated, Any, Literal

import OCP
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import func, select

from backend.api.auth import CurrentUser, Db, get_document, need
from backend.api.common import ApiError, body, pick
from backend.db.models import Document, Feature, Revision, User
from core.geometry.errors import GeomError
from core.geometry.features import MAX_DISTANCE_MM, extrude_job
from core.geometry.mesh import mesh_job
from core.geometry.worker import run_kernel

router = APIRouter()
Handle = Annotated[str, Field(pattern=r"^[0-9A-Fa-f]{1,16}$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExtrudeParams(_Strict):
    source_revision_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    handles: Annotated[list[Handle], Field(min_length=1, max_length=1000)]
    distance: Annotated[float, Field(allow_inf_nan=False, gt=0, le=MAX_DISTANCE_MM)]
    direction: Literal["+Z", "-Z"]

    @model_validator(mode="after")
    def _unique(self) -> "ExtrudeParams":
        if len(set(self.handles)) != len(self.handles):
            raise ValueError("duplicate handles")
        return self


class FeatureCreate(_Strict):
    feature_type: Literal["EXTRUDE"]
    params: dict[str, Any]


class FeatureUpdate(_Strict):
    params: dict[str, Any]


def _params(raw: dict[str, Any]) -> ExtrudeParams:
    try:
        return ExtrudeParams.model_validate(raw)
    except ValidationError:
        raise GeomError("GEOM_INVALID_PARAM", "Invalid feature parameters") from None


def _var() -> Path:
    from backend.api import main  # late: main imports this module, tests patch main.VAR_DIR

    return main.VAR_DIR


def _brep_path(key: str) -> Path:
    return _var() / "brep" / f"{key}.brep"


def _key(p: ExtrudeParams) -> str:
    # ponytail: one body per feature (no feature chaining yet); S6 Boolean introduces inputs/chains
    raw = json.dumps(p.model_dump(), sort_keys=True) + OCP.__version__
    return hashlib.sha256(raw.encode()).hexdigest()


def _build(db: Db, doc: Document, p: ExtrudeParams) -> tuple[bytes, dict[str, Any]]:
    """Resolve handles from the source revision and run the kernel in the worker pool."""
    from backend.api import main

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
    brep, metrics = run_kernel(extrude_job, [geoms[h] for h in p.handles], p.distance, p.direction)
    return brep, metrics


def _store_brep(key: str, brep: bytes) -> None:
    path = _brep_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(brep)


def _out(f: Feature) -> dict[str, Any]:
    cols = ("feature_id", "seq", "feature_type", "params", "status", "error_code", "metrics")
    return {**pick(f, *cols), **pick(f, "created_at", "updated_at")}


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
    return body({"items": [_out(f) for f in rows], "total": len(rows)})


@router.post("/api/documents/{document_id}/features")
def create_feature(document_id: int, req: FeatureCreate, user: CurrentUser, db: Db) -> Any:
    doc = get_document(db, user, document_id, lock=True)
    need(user, "DESIGNER")
    _check_editable(doc)
    p = _params(req.params)
    brep, metrics = _build(db, doc, p)
    key = _key(p)
    seq = db.scalar(select(func.max(Feature.seq)).where(Feature.document_id == document_id))
    f = Feature(  # seq is race-free: the document row lock is held
        document_id=document_id,
        seq=(seq or 0) + 1,
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
    return body(_out(f))


@router.patch("/api/features/{feature_id}")
def update_feature(feature_id: int, req: FeatureUpdate, user: CurrentUser, db: Db) -> Any:
    f, doc = _feature(db, user, feature_id, lock=True)
    need(user, "DESIGNER")
    _check_editable(doc)
    p = _params(req.params)
    brep, metrics = _build(db, doc, p)  # failure raises before any row change
    f.params, f.brep_key, f.metrics = p.model_dump(), _key(p), metrics
    f.updated_at = func.now()
    _store_brep(f.brep_key, brep)
    db.commit()
    return body(_out(f))


@router.delete("/api/features/{feature_id}")
def delete_feature(feature_id: int, user: CurrentUser, db: Db) -> Any:
    f, doc = _feature(db, user, feature_id, lock=True)
    need(user, "DESIGNER")
    _check_editable(doc)
    key = f.brep_key
    db.delete(f)
    db.flush()
    # BREP files are a content-addressed cache: keep it while another feature still uses the key
    if key and db.scalar(select(func.count()).where(Feature.brep_key == key)) == 0:
        _brep_path(key).unlink(missing_ok=True)
    db.commit()
    return body({"feature_id": feature_id})


@router.get("/api/features/{feature_id}/mesh")
def feature_mesh(feature_id: int, user: CurrentUser, db: Db) -> Any:
    f, doc = _feature(db, user, feature_id)
    if f.metrics is None or f.brep_key is None:
        raise ApiError(409, "FEATURE_NOT_BUILT", "Feature has no geometry")
    path = _brep_path(f.brep_key)
    if path.is_file():
        brep = path.read_bytes()
    else:  # cache miss: params JSON is the source of truth
        brep, _ = _build(db, doc, _params(f.params))
        _store_brep(f.brep_key, brep)
    mesh = run_kernel(mesh_job, brep)
    return body({**mesh, "bbox": f.metrics["bbox"]})
