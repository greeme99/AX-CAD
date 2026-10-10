"""Quote metrics: FN-14 (2D, from a DXF revision) and FN-15 (3D, from a document's bodies).
Mapping rules come from the master data version in force today, else the engine defaults."""

import hashlib
import json
import os
import re
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select

from backend.api.auth import CurrentUser, Db, get_document, need
from backend.api.common import ApiError, body
from backend.api.routes_master import KST, active_version
from backend.db.models import (
    CostRatios,
    Document,
    Feature,
    MappingRule,
    MasterVersion,
    QuoteHeader,
    QuoteLine,
    QuoteLog,
    QuoteTrace,
    Revision,
)
from core.dxf.reader import DxfError, run_isolated
from core.quote_engine.cost import compute_quote, effective_amount, round_amount, summarize
from core.quote_engine.metrics2d import Rules, metrics_job, rules_from_mapping
from core.quote_engine.validate import validate_quote

router = APIRouter()
MATERIAL_RE = re.compile(r"^[A-Za-z0-9._\-]{1,40}$")
ENGINE_VERSION = "metrics2d-2"  # bump when compute_metrics changes: invalidates the cache
METRIC_SLOTS = threading.Semaphore(1)
SLOT_WAIT_S = 30
FAIL_TTL_S = 600  # failed drawings answer from the negative cache for 10 minutes


def _rules(db: Db, v: MasterVersion | None) -> tuple[Rules, int | None]:
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
    return body(_metrics2d(db, rev, active_version(db, datetime.now(KST).date())))


def _metrics2d(db: Db, rev: Revision, version: MasterVersion | None) -> dict[str, Any]:
    """2D metrics with the mapping rules of `version` (the caller fixes it once per request)."""
    from backend.api import main

    src = main.VAR_DIR / "uploads" / f"{rev.revision_id}.dxf"
    if not src.is_file():
        raise ApiError(404, "REVISION_NOT_FOUND", "Revision not found")
    rules, version_id = _rules(db, version)
    key = hashlib.sha256((json.dumps(asdict(rules), sort_keys=True) + ENGINE_VERSION).encode())
    cache = main.VAR_DIR / "metrics" / f"{rev.revision_id}-{key.hexdigest()[:16]}.json"
    failed = cache.with_suffix(".err.json")
    if failed.is_file() and time.time() - failed.stat().st_mtime < FAIL_TTL_S:
        err = json.loads(failed.read_text(encoding="utf-8"))  # a bomb is not re-parsed per request
        raise DxfError(err["code"], err["message"], 422)
    if cache.is_file():
        text = cache.read_text(encoding="utf-8")
    else:
        # own slots: a slow drawing must not starve uploads (main.PARSE_SLOTS)
        if not METRIC_SLOTS.acquire(timeout=SLOT_WAIT_S):
            raise ApiError(503, "SERVER_BUSY", "Server busy, retry later")
        try:
            text = run_isolated(
                metrics_job, str(src), rules, fail=("METRIC_FAILED", "Metrics failed")
            )
        except DxfError as e:
            cache.parent.mkdir(parents=True, exist_ok=True)
            failed.write_text(json.dumps({"code": e.code, "message": e.message}), encoding="utf-8")
            raise
        finally:
            METRIC_SLOTS.release()
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_name(f"{cache.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, cache)  # concurrent requests may both compute; last atomic write wins
    metrics = json.loads(text)
    # ponytail: cache files are never pruned (one per revision x rules version); add a TTL sweep
    # if var/metrics grows
    return {
        "revision_id": rev.revision_id,
        "master_version_id": version_id,
        "rules_source": "MASTER" if version_id else "DEFAULT",
        **metrics,
    }


@router.get("/api/documents/{document_id}/metrics3d")
def document_metrics_3d(document_id: int, user: CurrentUser, db: Db) -> Any:
    """FN-15: one entry per body shown in the model (BOOLEAN inputs live inside their result)."""
    need(user, "ESTIMATOR", "DESIGNER")
    get_document(db, user, document_id)
    bodies = _bodies(db, document_id)
    return body({"document_id": document_id, "bodies": bodies, "total": len(bodies)})


def _bodies(db: Db, document_id: int) -> list[dict[str, Any]]:
    from backend.api.routes_model import _consumed, _export_name

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
    return bodies


# --- quotes (FN-17 create, FN-18 trace read) ---------------------------------------------------


class QuoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")] | None = None  # 2D quote
    document_id: Annotated[int, Field(ge=1)] | None = None  # 3D quote of the model's bodies
    qty: Annotated[int, Field(ge=1, le=1_000_000)] | None = None  # default: title block qty
    material_code: Annotated[str, Field(pattern=r"^[A-Za-z0-9._\-]{1,40}$")] | None = None
    thickness_mm: (
        Annotated[Decimal, Field(gt=0, le=500, max_digits=8, decimal_places=3, allow_inf_nan=False)]
        | None
    ) = None

    @model_validator(mode="after")
    def _one_source(self) -> "QuoteCreate":
        if (self.revision_id is None) == (self.document_id is None):
            raise ValueError("give exactly one of revision_id / document_id")
        return self


TOTALS = (
    "material_cost",
    "labor_cost",
    "overhead_cost",
    "outsource_cost",
    "manufacturing_cost",
    "admin_cost",
    "total_cost",
    "profit",
    "supply_amount",
    "vat_amount",
    "total_amount",
)


def _plain(v: Any) -> Any:
    return str(v) if isinstance(v, Decimal) else v


def _cols(row: Any) -> list[str]:
    return [c.key for c in row.__table__.columns]


def _quote_out(db: Db, h: QuoteHeader) -> dict[str, Any]:
    lines = db.scalars(
        select(QuoteLine).where(QuoteLine.quote_id == h.quote_id).order_by(QuoteLine.line_no)
    ).all()
    traces: dict[int, list[dict[str, Any]]] = {}
    ids = [ln.quote_line_id for ln in lines]
    for t in db.scalars(
        select(QuoteTrace)
        .where(QuoteTrace.quote_line_id.in_(ids))
        .order_by(QuoteTrace.quote_trace_id)
    ):
        traces.setdefault(t.quote_line_id, []).append(
            {
                c: getattr(t, c)
                for c in (
                    "source_kind",
                    "sources",
                    "source_count",
                    "revision_id",
                    "rule_code",
                    "price_item_code",
                    "unit_price",
                    "inputs",
                    "formula_text",
                )
            }
        )
    logs = db.scalars(
        select(QuoteLog).where(QuoteLog.quote_id == h.quote_id).order_by(QuoteLog.log_id)
    )
    head = {c: _plain(getattr(h, c)) for c in _cols(h)}
    rows = [
        {
            **{c: _plain(getattr(ln, c)) for c in _cols(ln)},
            "traces": traces.get(ln.quote_line_id, []),
        }
        for ln in lines
    ]
    for r in rows:
        r["effective_amount"] = _plain(effective_amount(r))
    effective = summarize(rows, _ratios(db, h.master_version_id))
    return {
        **head,
        "lines": rows,
        # FN-19: header amounts stay what the engine computed; this is override ?? calculated
        "effective": None if effective is None else {k: _plain(v) for k, v in effective.items()},
        "logs": [{"severity": lg.severity, "code": lg.code, "message": lg.message} for lg in logs],
    }


def _ratios(db: Db, version_id: int) -> dict[str, Any]:
    r = db.get(CostRatios, version_id)
    assert r is not None  # an ACTIVE version always has cost ratios (activation gate)
    return {c: _plain(getattr(r, c)) for c in _cols(r)}


@router.post("/api/quotes")
# sync def: 2D metrics may parse the drawing in the isolated worker
def create_quote(req: QuoteCreate, user: CurrentUser, db: Db) -> Any:
    from backend.api import main
    from backend.api.routes_master import _bundle

    need(user, "ESTIMATOR")
    # one master version for the whole request: mapping rules and prices must agree (NFR-05)
    version = active_version(db, datetime.now(KST).date())
    if version is None:
        raise ApiError(409, "MASTER_NOT_ACTIVE", "No active master data: activate a version first")
    if req.revision_id:
        rev, doc = main._revision(db, user, req.revision_id)
        metrics = _metrics2d(db, rev, version)
        source = {"kind": "REVISION", "revision_id": rev.revision_id}
        title = metrics["title_block"]
        snapshot = {k: v for k, v in metrics.items() if k != "items"}  # handles live in the traces
    else:
        assert req.document_id is not None
        doc = get_document(db, user, req.document_id)
        metrics = {"bodies": _bodies(db, doc.document_id)}
        if not metrics["bodies"]:
            raise ApiError(409, "MODEL_EMPTY", "The document has no 3D bodies to quote")
        source, title, snapshot = (
            {"kind": "DOCUMENT_3D", "document_id": doc.document_id},
            {},
            metrics,
        )
    qty = req.qty or title.get("qty")
    if not qty:
        raise ApiError(
            422, "QUOTE_QTY_REQUIRED", "Quantity is required (not found in the title block)"
        )
    # drawing text is untrusted: a title-block material must look like a material code
    title_mat = title.get("material")
    if title_mat is not None and not MATERIAL_RE.fullmatch(str(title_mat)):
        title_mat = None
    material = req.material_code or title_mat
    thickness = req.thickness_mm
    if thickness is None and title.get("thickness_mm") is not None:
        thickness = Decimal(str(title["thickness_mm"]))
    result = compute_quote(
        metrics,
        _bundle(db, version),
        source,
        qty=int(qty),
        material_code=material,
        thickness_mm=thickness,
    )
    inputs = {
        "qty": int(qty),
        "material_code": material,
        "thickness_mm": _plain(thickness),
        # where each value came from: the estimator, or the drawing's title block
        "input_source": {
            "qty": "USER" if req.qty else "TITLE_BLOCK",
            "material_code": "USER" if req.material_code else ("TITLE_BLOCK" if material else None),
            "thickness_mm": "USER"
            if req.thickness_mm is not None
            else ("TITLE_BLOCK" if thickness is not None else None),
        },
    }
    qid = db.scalar(select(func.nextval("quote_headers_quote_id_seq")))  # unique, no retry needed
    h = QuoteHeader(
        quote_id=qid,
        quote_no=f"Q-{datetime.now(KST):%Y%m%d}-{qid:06d}",
        project_id=doc.project_id,
        source_kind=source["kind"],
        revision_id=source.get("revision_id"),
        document_id=doc.document_id,
        master_version_id=version.version_id,
        inputs=inputs,
        metrics=snapshot,
        has_errors=result["has_errors"],
        created_by=user.user_id,
        **result["totals"],
    )
    db.add(h)
    db.flush()
    for ln in result["lines"]:
        row = QuoteLine(quote_id=h.quote_id, **{k: v for k, v in ln.items() if k != "traces"})
        db.add(row)
        db.flush()
        db.add_all(QuoteTrace(quote_line_id=row.quote_line_id, **t) for t in ln["traces"])
    db.add_all(QuoteLog(quote_id=h.quote_id, **lg) for lg in result["logs"])
    db.commit()
    return body(_quote_out(db, h))


def _visible_quote(db: Db, user: Any, quote_id: int) -> QuoteHeader:
    h = db.get(QuoteHeader, quote_id)
    try:
        if h is None:
            raise ApiError(404, "QUOTE_NOT_FOUND", "Quote not found")
        get_document(db, user, h.document_id)  # project membership
    except ApiError:
        raise ApiError(404, "QUOTE_NOT_FOUND", "Quote not found") from None
    return h


@router.get("/api/quotes/{quote_id}")
def get_quote(quote_id: int, user: CurrentUser, db: Db) -> Any:
    need(user, "ESTIMATOR", "REVIEWER", "MANUFACTURING")  # FN-18 trace readers
    return body(_quote_out(db, _visible_quote(db, user, quote_id)))


@router.get("/api/documents/{document_id}/quotes")
def list_quotes(document_id: int, user: CurrentUser, db: Db) -> Any:
    need(user, "ESTIMATOR", "REVIEWER", "MANUFACTURING")
    get_document(db, user, document_id)
    rows = db.scalars(
        select(QuoteHeader)
        .where(QuoteHeader.document_id == document_id)
        .order_by(QuoteHeader.quote_id.desc())
        .limit(200)  # ponytail: newest 200, add paging when a document collects more quotes
    ).all()
    cols = (
        "quote_id",
        "quote_no",
        "source_kind",
        "revision_id",
        "master_version_id",
        "status",
        "has_errors",
        "supply_amount",
        "total_amount",
        "created_at",
    )
    return body(
        {"items": [{c: _plain(getattr(h, c)) for c in cols} for h in rows], "total": len(rows)}
    )


# --- FN-19 manual adjustment, FN-20 validation ----------------------------------------------


class OverrideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: Literal["qty", "unit_price", "amount"]
    value: Annotated[Decimal, Field(allow_inf_nan=False, max_digits=18, decimal_places=6)]
    reason: Annotated[str, Field(max_length=500)] = ""  # missing -> 422 below, like too short


def _editable_line(db: Db, user: Any, line_id: int) -> tuple[QuoteLine, QuoteHeader]:
    ln = db.scalar(select(QuoteLine).where(QuoteLine.quote_line_id == line_id).with_for_update())
    if ln is None:
        raise ApiError(404, "QUOTE_NOT_FOUND", "Quote line not found")
    h = _visible_quote(db, user, ln.quote_id)
    if h.status != "DRAFT":
        raise ApiError(
            409, "QUOTE_CONFIRMED", "A confirmed quote cannot be adjusted; make a new revision"
        )
    return ln, h


@router.patch("/api/quote-lines/{line_id}")
def override_line(line_id: int, req: OverrideIn, user: CurrentUser, db: Db) -> Any:
    need(user, "ESTIMATOR")
    ln, h = _editable_line(db, user, line_id)
    reason = req.reason.strip()
    if len(reason) < 5:
        raise ApiError(422, "QUOTE_OVERRIDE_INVALID", "조정 사유를 5자 이상 입력하세요")
    if req.value < 0:
        raise ApiError(422, "QUOTE_OVERRIDE_INVALID", "음수는 입력할 수 없습니다")
    limit = Decimal("1e11") if req.field == "qty" else Decimal("1e13")
    if req.value >= limit:
        raise ApiError(422, "QUOTE_OVERRIDE_INVALID", "값이 너무 큽니다")
    ratios = _ratios(db, h.master_version_id)

    def money(v: Decimal) -> Decimal:
        if ratios["rounding_scope"] == "LINE":
            return round_amount(v, ratios["rounding_rule"], int(ratios["rounding_unit"]))
        return v.quantize(Decimal("0.01"))

    qty = ln.override_qty if ln.override_qty is not None else ln.calculated_qty
    price = (
        ln.override_unit_price if ln.override_unit_price is not None else ln.calculated_unit_price
    )
    if req.field == "qty":
        if price is None:
            raise ApiError(
                422,
                "QUOTE_OVERRIDE_INVALID",
                "단가가 없어 수량만 조정할 수 없습니다. 단가나 금액을 조정하세요",
            )
        ln.override_qty, ln.override_amount = req.value, money(req.value * price)
    elif req.field == "unit_price":
        ln.override_unit_price, ln.override_amount = req.value, money(qty * req.value)
    else:
        ln.override_amount = req.value.quantize(Decimal("0.01"))
    ln.override_reason, ln.overridden_by, ln.overridden_at = reason, user.user_id, func.now()
    db.commit()  # the audit trigger records old and new values
    return body(_quote_out(db, h))


@router.delete("/api/quote-lines/{line_id}/override")
def clear_override(line_id: int, user: CurrentUser, db: Db) -> Any:
    need(user, "ESTIMATOR")
    ln, h = _editable_line(db, user, line_id)
    ln.override_qty = ln.override_unit_price = ln.override_amount = None
    ln.override_reason = None
    ln.overridden_by = None
    ln.overridden_at = None
    db.commit()
    return body(_quote_out(db, h))


@router.post("/api/quotes/{quote_id}/validate")
def validate(quote_id: int, user: CurrentUser, db: Db) -> Any:
    need(user, "ESTIMATOR", "REVIEWER")
    h = _visible_quote(db, user, quote_id)
    q = _quote_out(db, h)
    for ln in q["lines"]:  # duplicate detection key: same category/item over the same sources
        ln["source_key"] = sorted(s for t in ln["traces"] for s in t["sources"])
    doc = db.get(Document, h.document_id)
    logs = validate_quote(
        q,
        q["lines"],
        _ratios(db, h.master_version_id),
        q["logs"],
        doc.current_revision_id if doc else None,
    )
    return body(
        {
            "quote_id": quote_id,
            "logs": logs,
            "has_errors": any(lg["severity"] == "ERROR" for lg in logs),
        }
    )
