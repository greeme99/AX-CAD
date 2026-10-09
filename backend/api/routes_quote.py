"""Quote metrics: FN-14 (2D, from a DXF revision) and FN-15 (3D, from a document's bodies).
Mapping rules come from the master data version in force today, else the engine defaults."""

import hashlib
import json
import os
import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from backend.api.auth import CurrentUser, Db, get_document, need
from backend.api.common import ApiError, body
from backend.api.routes_master import KST, active_version
from backend.db.models import Feature, MappingRule
from core.dxf.reader import run_isolated
from core.quote_engine.metrics2d import Rules, compute_metrics, rules_from_mapping

router = APIRouter()
ENGINE_VERSION = "metrics2d-1"  # bump when compute_metrics changes: invalidates the cache


def _rules(db: Db) -> tuple[Rules, int | None]:
    v = active_version(db, datetime.now(KST).date())
    if v is None:
        return Rules(), None
    rows = db.scalars(select(MappingRule).where(MappingRule.version_id == v.version_id)).all()
    return rules_from_mapping(
        [{"rule_type": r.rule_type, "target": r.target, "pattern": r.pattern} for r in rows]
    ), v.version_id


@router.get("/api/revisions/{revision_id}/metrics")
# sync def: parsing blocks, FastAPI runs it in the threadpool
def revision_metrics(revision_id: str, user: CurrentUser, db: Db) -> Any:
    from backend.api import main  # late: main imports this module

    need(user, "ESTIMATOR", "DESIGNER")
    rev, _ = main._revision(db, user, revision_id)
    src = main.VAR_DIR / "uploads" / f"{rev.revision_id}.dxf"
    if not src.is_file():
        raise ApiError(404, "REVISION_NOT_FOUND", "Revision not found")
    rules, version_id = _rules(db)
    key = hashlib.sha256((json.dumps(asdict(rules), sort_keys=True) + ENGINE_VERSION).encode())
    cache = main.VAR_DIR / "metrics" / f"{rev.revision_id}-{key.hexdigest()[:16]}.json"
    if cache.is_file():
        metrics = json.loads(cache.read_text(encoding="utf-8"))
    else:
        if not main.PARSE_SLOTS.acquire(timeout=main.SLOT_WAIT_S):
            raise ApiError(503, "SERVER_BUSY", "Server busy, retry later")
        try:
            metrics = run_isolated(
                compute_metrics, str(src), rules, fail=("METRIC_FAILED", "Metrics failed")
            )
        finally:
            main.PARSE_SLOTS.release()
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_name(f"{cache.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(metrics, allow_nan=False), encoding="utf-8")
        os.replace(tmp, cache)  # concurrent requests may both compute; last atomic write wins
    return body(
        {
            "revision_id": rev.revision_id,
            "master_version_id": version_id,
            "rules_source": "MASTER" if version_id else "DEFAULT",
            **metrics,
        }
    )


@router.get("/api/documents/{document_id}/metrics3d")
def document_metrics_3d(document_id: int, user: CurrentUser, db: Db) -> Any:
    """FN-15: one entry per body shown in the model (BOOLEAN inputs live inside their result)."""
    from backend.api.routes_model import _consumed, _export_name

    need(user, "ESTIMATOR", "DESIGNER")
    get_document(db, user, document_id)
    rows = db.scalars(
        select(Feature).where(Feature.document_id == document_id).order_by(Feature.seq)
    ).all()
    consumed = _consumed(db, document_id)
    bodies = []
    for f in rows:
        if f.feature_id in consumed or f.status != "OK" or not f.metrics:
            continue
        m = f.metrics
        bodies.append(
            {
                "feature_id": f.feature_id,
                "name": _export_name(f),
                "feature_type": f.feature_type,
                "volume_mm3": m["volume_mm3"],
                "surface_area_mm2": m["surface_area_mm2"],
                "bbox": m["bbox"],
                "parts": m.get("parts"),  # IMPORT only: per-part breakdown with instance counts
                "instance_count": m.get("instance_count", 1),
                "warnings": m.get("warnings", []),
            }
        )
    return body({"document_id": document_id, "bodies": bodies, "total": len(bodies)})
