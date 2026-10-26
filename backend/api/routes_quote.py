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
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
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
    Project,
    QtoObject,
    QuoteHeader,
    QuoteLine,
    QuoteLog,
    QuoteTrace,
    Revision,
    WorkItem,
)
from core.dxf.reader import DxfError, run_isolated
from core.quote_engine.construction import compute_construction
from core.quote_engine.cost import (
    CENT,
    MAX_AMOUNT,
    compute_quote,
    effective_amount,
    round_amount,
    summarize,
)
from core.quote_engine.metrics2d import Rules, metrics_job, rules_from_mapping
from core.quote_engine.qto import layers_from_mapping, qto_job
from core.quote_engine.report import render_pdf, render_scenarios_xlsx, render_xlsx
from core.quote_engine.validate import validate_quote

router = APIRouter()
MATERIAL_RE = re.compile(r"^[A-Za-z0-9._\-]{1,40}$")
ENGINE_VERSION = "metrics2d-3"  # bump when compute_metrics changes: invalidates the cache
QTO_ENGINE = "qto-3"  # same for compute_qto
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
    text = _cached_job(cache, metrics_job, (str(src), rules), ("METRIC_FAILED", "Metrics failed"))
    metrics = json.loads(text)
    # ponytail: cache files are never pruned (one per revision x rules version); add a TTL sweep
    # if var/metrics grows
    return {
        "revision_id": rev.revision_id,
        "master_version_id": version_id,
        "rules_source": "MASTER" if version_id else "DEFAULT",
        **metrics,
    }


def _cached_job(cache: Path, job: Any, args: tuple[Any, ...], fail: tuple[str, str]) -> str:
    """Run a drawing job in the isolated worker once per cache key; failures are cached too."""
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
            text = run_isolated(job, *args, fail=fail)
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
    return text


def _qto(
    db: Db,
    user: Any,
    rev: Revision,
    doc: Document,
    base_id: str | None,
    version: MasterVersion | None,
) -> dict[str, Any]:
    """Track 6-2 quantity take-off of a revision, split against `base_id` (the existing building)
    when given. Layer roles come from the master's QTO_LAYER rows, else the defaults."""
    from backend.api import main

    rows, objects = [], []
    if version is not None:
        objects = [
            {
                **{k: getattr(o, k) for k in ("code", "layer_pattern", "block_pattern")},
                **{k: getattr(o, k) for k in ("attribute", "quantity_type")},
                "factor": str(o.factor),
                "waste_rate": None if o.waste_rate is None else str(o.waste_rate),
            }
            for o in db.scalars(
                select(QtoObject)
                .where(QtoObject.version_id == version.version_id)
                .order_by(QtoObject.code)
            )
        ]
        rows = [
            {"rule_type": r.rule_type, "target": r.target, "pattern": r.pattern}
            for r in db.scalars(
                select(MappingRule).where(
                    MappingRule.version_id == version.version_id,
                    MappingRule.rule_type == "QTO_LAYER",
                )
            )
        ]
    layers = layers_from_mapping(rows)
    paths = [main.VAR_DIR / "uploads" / f"{rev.revision_id}.dxf"]
    if base_id:
        base, base_doc = main._revision(db, user, base_id)  # membership checked there
        if base_doc.project_id != doc.project_id:
            raise ApiError(
                422, "QTO_BASE_INVALID", "기존 도면은 같은 프로젝트의 리비전이어야 합니다"
            )
        if base.revision_id == rev.revision_id:
            raise ApiError(422, "QTO_BASE_INVALID", "기존 도면과 변경 도면이 같습니다")
        paths.append(main.VAR_DIR / "uploads" / f"{base.revision_id}.dxf")
    if not all(p.is_file() for p in paths):
        raise ApiError(404, "REVISION_NOT_FOUND", "Revision not found")
    key = hashlib.sha256(
        (json.dumps([layers, objects], sort_keys=True) + (base_id or "") + QTO_ENGINE).encode()
    ).hexdigest()[:16]
    cache = main.VAR_DIR / "metrics" / f"{rev.revision_id}-qto-{key}.json"
    base_path = str(paths[1]) if base_id else None
    text = _cached_job(
        cache,
        qto_job,
        (str(paths[0]), layers, base_path, objects),
        ("QTO_FAILED", "QTO failed"),
    )
    return {
        "revision_id": rev.revision_id,
        "base_revision_id": base_id,
        "master_version_id": version.version_id if version else None,
        "rules_source": "MASTER" if rows or objects else "DEFAULT",
        **json.loads(text),
    }


@router.get("/api/revisions/{revision_id}/qto")
# sync def: parsing blocks, FastAPI runs it in the threadpool
def revision_qto(
    revision_id: str,
    user: CurrentUser,
    db: Db,
    base_revision_id: Annotated[str, Query(pattern=r"^[0-9a-f]{32}$")] | None = None,
) -> Any:
    from backend.api import main

    need(user, "ESTIMATOR", "DESIGNER")
    rev, doc = main._revision(db, user, revision_id)
    version = active_version(db, datetime.now(KST).date())
    return body(_qto(db, user, rev, doc, base_revision_id, version))


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
    by_id = {f.feature_id: f for f in rows}
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
                **_machining(by_id, f),
            }
        )
    return bodies


def _standard_parts(db: Db, document_id: int) -> list[dict[str, Any]]:
    """Track 8 P4-5: the document's purchased standard parts (STANDARD features, not suppressed)."""
    from core.bom.standard import item_code, label

    rows = db.scalars(
        select(Feature)
        .where(Feature.document_id == document_id, Feature.feature_type == "STANDARD")
        .order_by(Feature.seq)
    ).all()
    return [
        {
            "feature_id": f.feature_id,
            **f.params,
            "code": item_code(f.params),
            "label": label(f.params),
        }
        for f in rows
        if not f.suppressed
    ]


HANDLE_RE = re.compile(r"^[0-9A-F]{1,16}$")


def _made_from_3d(revision_id: str) -> dict[str, Any] | None:
    """The settings of a drawing generated from 3D (P3), or None for any other drawing. They come
    from the file: only a plain material text and well-formed callout handles are kept."""
    from backend.api import main

    if not main.REVISION_RE.fullmatch(revision_id):
        return None
    cfg = (main._read_json(revision_id) or {}).get("drawing")
    if not isinstance(cfg, dict):
        return None
    raw = cfg.get("callouts")
    callouts = {
        k: v
        for k, v in (raw.items() if isinstance(raw, dict) else ())
        if isinstance(k, str) and k.isdigit() and isinstance(v, str) and HANDLE_RE.fullmatch(v)
    }
    material = cfg.get("material")
    return {"material": material if isinstance(material, str) else None, "callouts": callouts}


def _machining(by_id: dict[int, Feature], body: Feature) -> dict[str, Any]:
    """Track 8 P4-1: what a machining quote reads off a body's history: the holes drilled into it
    (each HOLE feature, for its trace) and the turning axis when the body started as a full
    revolution or a cylinder on a world axis (stock = round bar)."""
    from backend.api.routes_model import _base, _inputs, _params

    holes, seen, stack = [], set(), [body.feature_id]
    while stack:
        g = by_id.get(stack.pop())
        if g is None or g.feature_id in seen:
            continue
        seen.add(g.feature_id)
        h = (g.metrics or {}).get("holes") if g.feature_type == "HOLE" else None
        if h and not g.suppressed:
            holes.append(
                {
                    "feature_id": g.feature_id,
                    "count": h["count"],
                    "diameter": h["diameter"],
                    "drill_length": h.get("drill_length"),  # all of this feature's holes
                    "depth": h.get("depth"),
                    "thread_length": (h.get("thread") or {}).get("length"),
                    "threaded": "thread" in h,
                    "head": (h.get("head") or {}).get("kind"),
                }
            )
        stack += _inputs(_params(g.feature_type, g.params))
    root = body
    while (b := _base(_params(root.feature_type, root.params))) is not None and b in by_id:
        root = by_id[b]
    return {"holes": holes, "turn_axis": _turn_axis(by_id, root)}


def _turn_axis(by_id: dict[int, Feature], root: Feature) -> str | None:
    p = root.params
    if root.feature_type == "PRIMITIVE":
        return str(p.get("axis", "Z")) if p.get("kind") == "CYLINDER" else None
    if root.feature_type != "REVOLVE" or p.get("angle_deg") != 360:
        return None
    sk = by_id.get(p.get("sketch_feature_id") or 0)
    plane = sk.params["plane"] if sk is not None else {"normal": [0, 0, 1], "xdir": [1, 0, 0]}
    n, x = plane["normal"], plane["xdir"]
    y = [n[1] * x[2] - n[2] * x[1], n[2] * x[0] - n[0] * x[2], n[0] * x[1] - n[1] * x[0]]
    u, v = p["axis_dir"]
    w = [u * x[i] + v * y[i] for i in range(3)]
    size = max(abs(c) for c in w)
    axis = [i for i in range(3) if abs(abs(w[i]) - size) < 1e-9 * size]
    if len(axis) != 1 or any(abs(w[i]) > 1e-9 * size for i in range(3) if i != axis[0]):
        return None  # ponytail: an oblique turning axis is quoted as block stock
    return "XYZ"[axis[0]]


# --- quotes (FN-17 create, FN-18 trace read) ---------------------------------------------------


Factor = Annotated[Decimal, Field(gt=0, le=10, max_digits=8, decimal_places=6, allow_inf_nan=False)]
RevisionId = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
FactorCode = Annotated[str, Field(pattern=r"^[\w.\-]{1,40}$")]


class ScenarioIn(BaseModel):
    """Track 6-4 what-if: unit prices of a cost category and every quantity times a factor."""

    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)] = (
        "시나리오"
    )
    material: Factor = Decimal(1)
    labor: Factor = Decimal(1)
    overhead: Factor = Decimal(1)
    outsource: Factor = Decimal(1)
    qty: Factor = Decimal(1)


class PartIn(BaseModel):
    """Track 8 P4-3: a 3D body's own material and quantity (else the quote's)."""

    model_config = ConfigDict(extra="forbid")
    feature_id: Annotated[int, Field(ge=1)]
    material_code: Annotated[str, Field(pattern=r"^[A-Za-z0-9._\-]{1,40}$")] | None = None
    qty: Annotated[int, Field(ge=1, le=1_000_000)] | None = None


Plain = Annotated[str, Field(pattern=r"^[^\x00-\x1f\x7f]{1,100}$")]  # jsonb refuses NUL


class ElementIn(BaseModel):
    """Track 8 P4-6d: one model element's quantity from Revit / IFC (other keys are ignored)."""

    model_config = ConfigDict(extra="ignore")
    element_id: Annotated[str, Field(pattern=r"^[^\x00-\x1f\x7f]{1,64}$")]
    category: Plain | None = None
    quantity_type: Literal["LENGTH", "AREA", "VOLUME", "COUNT", "WEIGHT"] | None = None
    quantity: Annotated[
        Decimal, Field(ge=0, le=1e9, max_digits=18, decimal_places=6, allow_inf_nan=False)
    ]
    unit: Annotated[str, Field(pattern=r"^[^\x00-\x1f\x7f]{1,20}$")]
    work_item_code: FactorCode


class QuantitySetIn(BaseModel):
    """External quantity take-off: work quantities of the elements given per 공종 code."""

    model_config = ConfigDict(extra="ignore")
    source_file: Plain
    elements: Annotated[list[ElementIn], Field(min_length=1, max_length=5_000)]

    @model_validator(mode="after")
    def _once(self) -> "QuantitySetIn":
        keys = [(e.work_item_code, e.element_id) for e in self.elements]
        if len(set(keys)) != len(keys):  # an element listed twice would be counted twice
            raise ValueError("an element is listed twice for the same work item")
        return self

    def sha256(self) -> str:
        """Fingerprint of the quantities as priced (source_file is only what the sender says)."""
        text = json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(text.encode()).hexdigest()


class QuoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["PART", "CONSTRUCTION"] = "PART"
    revision_id: RevisionId | None = None  # 2D quote (PART) or the plan to measure (CONSTRUCTION)
    document_id: Annotated[int, Field(ge=1)] | None = None  # 3D quote of the model's bodies
    qty: Annotated[int, Field(ge=1, le=1_000_000)] | None = None  # default: title block qty
    material_code: Annotated[str, Field(pattern=r"^[A-Za-z0-9._\-]{1,40}$")] | None = None
    thickness_mm: (
        Annotated[Decimal, Field(gt=0, le=500, max_digits=8, decimal_places=3, allow_inf_nan=False)]
        | None
    ) = None

    # construction: the existing building (demolition/new/keep split) and the ceiling height
    base_revision_id: RevisionId | None = None
    height_mm: (
        Annotated[
            Decimal, Field(gt=0, le=100_000, max_digits=9, decimal_places=3, allow_inf_nan=False)
        ]
        | None
    ) = None
    region: FactorCode | None = None  # master adjust factors (both kinds)
    escalation: FactorCode | None = None
    scenario: ScenarioIn | None = None
    parts: Annotated[list[PartIn], Field(max_length=500)] = []  # 3D: per-body material / qty
    quantities: QuantitySetIn | None = None  # construction: Revit / IFC quantities (P4-6d)

    @model_validator(mode="after")
    def _one_source(self) -> "QuoteCreate":
        if (self.revision_id is None) == (self.document_id is None):
            raise ValueError("give exactly one of revision_id / document_id")
        if self.kind == "CONSTRUCTION" and self.revision_id is None:
            raise ValueError("a construction quote measures a drawing revision")
        if self.kind == "PART" and (self.base_revision_id or self.height_mm):
            raise ValueError("base_revision_id / height_mm are for construction quotes")
        if self.quantities and self.kind != "CONSTRUCTION":
            raise ValueError("quantities are for construction quotes")
        if self.parts and self.document_id is None:
            raise ValueError("parts are for 3D quotes")
        if len({p.feature_id for p in self.parts}) != len(self.parts):
            raise ValueError("each part once")
        return self

    def adjust(self) -> dict[str, Any]:
        sc = self.scenario.model_dump(mode="json") if self.scenario else None
        return {"region": self.region, "escalation": self.escalation, "scenario": sc}


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
    effective = summarize(rows, _ratios(db, h.master_version_id), construction=_is_c(h))
    return {
        **head,
        "lines": rows,
        # FN-19: header amounts stay what the engine computed; this is override ?? calculated
        "effective": None if effective is None else {k: _plain(v) for k, v in effective.items()},
        "logs": [{"severity": lg.severity, "code": lg.code, "message": lg.message} for lg in logs],
    }


def _is_c(h: QuoteHeader) -> bool:
    return h.source_kind == "CONSTRUCTION"


def _ratios(db: Db, version_id: int) -> dict[str, Any]:
    r = db.get(CostRatios, version_id)
    if r is None:  # an ACTIVE version always has cost ratios (activation gate)
        raise ApiError(409, "MASTER_INVALID", "Master data version has no cost ratios")
    return {c: _plain(getattr(r, c)) for c in _cols(r)}


@router.post("/api/quotes")
# sync def: 2D metrics may parse the drawing in the isolated worker
def create_quote(req: QuoteCreate, user: CurrentUser, db: Db) -> Any:
    need(user, "ESTIMATOR")
    return body(_quote_out(db, _new_quote(db, user, req)))


def _new_quote(
    db: Db,
    user: Any,
    req: QuoteCreate,
) -> QuoteHeader:
    """FN-17 computation."""
    from backend.api import main
    from backend.api.routes_master import _bundle

    # one master version for the whole request: mapping rules and prices must agree (NFR-05)
    version = active_version(db, datetime.now(KST).date())
    if version is None:
        raise ApiError(409, "MASTER_NOT_ACTIVE", "No active master data: activate a version first")
    today = datetime.now(KST).date()
    bundle = _bundle(db, version)
    if req.kind == "CONSTRUCTION":
        assert req.revision_id is not None
        rev, doc = main._revision(db, user, req.revision_id)
        qto = _qto(db, user, rev, doc, req.base_revision_id, version)
        if req.quantities:  # kept in the snapshot, so scenarios use the same quantities
            qto["external"] = {
                **req.quantities.model_dump(mode="json"),
                "sha256": req.quantities.sha256(),
            }
        source = {
            "kind": "CONSTRUCTION",
            "revision_id": rev.revision_id,
            "base_revision_id": req.base_revision_id,
        }
        height = None if req.height_mm is None else req.height_mm / 1000
        result = compute_construction(
            qto, bundle, source, height_m=height, adjust=req.adjust(), on=today
        )
        inputs = {
            "kind": "CONSTRUCTION",
            "height_mm": _plain(req.height_mm),
            "base_revision_id": req.base_revision_id,
            "quantities_file": req.quantities.source_file if req.quantities else None,
            "quantities_sha256": req.quantities.sha256() if req.quantities else None,
            **req.adjust(),
        }
        snapshot = {k: v for k, v in qto.items() if k != "items"}  # handles live in the traces
        return _store(db, user, doc, source, version, inputs, snapshot, result)
    made: dict[str, Any] | None = None
    if req.revision_id:
        rev, doc = main._revision(db, user, req.revision_id)
        made = _made_from_3d(rev.revision_id)
    if req.revision_id and made is None:
        metrics = _metrics2d(db, rev, version)
        source = {"kind": "REVISION", "revision_id": rev.revision_id}
        title = metrics["title_block"]
        snapshot = {k: v for k, v in metrics.items() if k != "items"}  # handles live in the traces
    elif req.revision_id and made is not None:
        # Track 8 P4-4: a drawing made from 3D is quoted from the model it shows (its projected
        # views would count every edge three times); traces also point into the drawing
        metrics = {
            "bodies": _bodies(db, doc.document_id),
            "callouts": made["callouts"],
            "standard_parts": _standard_parts(db, doc.document_id),
        }
        if not metrics["bodies"]:
            raise ApiError(409, "MODEL_EMPTY", "The document has no 3D bodies to quote")
        source = {
            "kind": "DOCUMENT_3D",
            "document_id": doc.document_id,
            "revision_id": rev.revision_id,
        }
        title, snapshot = {"material": made["material"]}, metrics
    else:
        assert req.document_id is not None
        doc = get_document(db, user, req.document_id)
        metrics = {
            "bodies": _bodies(db, doc.document_id),
            "standard_parts": _standard_parts(db, doc.document_id),
        }
        if not metrics["bodies"]:
            raise ApiError(409, "MODEL_EMPTY", "The document has no 3D bodies to quote")
        given = {p.feature_id: p for p in req.parts}
        if set(given) - {b["feature_id"] for b in metrics["bodies"]}:
            raise ApiError(422, "QUOTE_PART_UNKNOWN", "A part is not a body shown in the model")
        for b in metrics["bodies"]:  # kept in the snapshot, so scenarios price them the same
            p = given.get(b["feature_id"])
            b["material_code"] = p.material_code if p else None
            b["qty"] = p.qty if p else None
        source, title, snapshot = (
            {"kind": "DOCUMENT_3D", "document_id": doc.document_id},
            {},
            metrics,
        )
    qty = req.qty or title.get("qty")
    if not qty and req.parts and all(b["qty"] for b in metrics.get("bodies", [])):
        qty = 1  # every part has its own quantity
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
        bundle,
        source,
        qty=int(qty),
        material_code=material,
        thickness_mm=thickness,
        adjust=req.adjust(),
        on=today,
    )
    inputs = {
        **req.adjust(),
        "qty": int(qty),
        "parts": [p.model_dump() for p in req.parts],
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
    return _store(db, user, doc, source, version, inputs, snapshot, result)


def _store(
    db: Db,
    user: Any,
    doc: Document,
    source: dict[str, Any],
    version: MasterVersion,
    inputs: dict[str, Any],
    snapshot: dict[str, Any],
    result: dict[str, Any],
) -> QuoteHeader:
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
    return h


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
        "revision_no",
        "parent_quote_id",
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
    quote_id = db.scalar(select(QuoteLine.quote_id).where(QuoteLine.quote_line_id == line_id))
    if quote_id is None:
        raise ApiError(404, "QUOTE_NOT_FOUND", "Quote not found")
    _visible_quote(db, user, quote_id)  # membership before any lock
    # lock order header -> line, like every status change: a review request or confirm can
    # never land between this status check and the override commit
    h = _locked_quote(db, quote_id)
    ln = db.scalar(select(QuoteLine).where(QuoteLine.quote_line_id == line_id).with_for_update())
    assert ln is not None
    if h.status != "DRAFT":  # only quotes confirmed under the removed approval flow (Track 7)
        raise ApiError(
            409, f"QUOTE_{h.status}", "Only a draft quote can be adjusted; recalculate a new one"
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
        return v.quantize(CENT, rounding=ROUND_HALF_UP)  # same HALF_UP as the engine

    value = req.value if req.field == "qty" else req.value.quantize(CENT, rounding=ROUND_HALF_UP)
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
        amount = money(value * price)
        ln.override_qty = value
    elif req.field == "unit_price":
        amount = money(qty * value)  # from the stored (cent) price: amount = qty x what is shown
        ln.override_unit_price = value
    else:
        amount = value
        ln.override_qty = ln.override_unit_price = None  # a lump sum replaces qty x price
    if amount >= MAX_AMOUNT:  # qty x price can outgrow numeric(18,2)
        raise ApiError(422, "QUOTE_OVERRIDE_INVALID", "조정 결과 금액이 너무 큽니다")
    ln.override_amount = amount
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


def _validation(db: Db, h: QuoteHeader) -> list[dict[str, Any]]:
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
    active = active_version(db, datetime.now(KST).date())
    if active is None or active.version_id != h.master_version_id:  # e.g. a COPY revision
        logs.append(
            {
                "severity": "WARN",
                "code": "QUOTE_MASTER_OUTDATED",
                "message": "현재 적용 중인 기준정보(단가)가 아닌 이전 버전으로 산출된 견적입니다",
                "line_no": None,
            }
        )
    return logs


@router.post("/api/quotes/{quote_id}/validate")
def validate(quote_id: int, user: CurrentUser, db: Db) -> Any:
    need(user, "ESTIMATOR", "REVIEWER")
    logs = _validation(db, _visible_quote(db, user, quote_id))
    return body(
        {
            "quote_id": quote_id,
            "logs": logs,
            "has_errors": any(lg["severity"] == "ERROR" for lg in logs),
        }
    )


# --- FN-22 quote approval: DRAFT -> IN_REVIEW -> CONFIRMED | (rejected) DRAFT ----------------


class ScenariosIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenarios: Annotated[list[ScenarioIn], Field(max_length=10)] = []
    sensitivity: Annotated[
        Decimal, Field(gt=0, le=Decimal("0.5"), max_digits=4, decimal_places=3)
    ] = Decimal("0.1")


def _recompute(h: QuoteHeader, bundle: dict[str, Any], scenario: dict[str, Any] | None) -> Any:
    """The quote's engine result under another scenario (same master version, drawing snapshot,
    region and escalation). Manual overrides are not part of a what-if."""
    p = h.inputs
    adjust = {"region": p.get("region"), "escalation": p.get("escalation"), "scenario": scenario}
    if h.source_kind == "CONSTRUCTION":
        height = p.get("height_mm")
        src = {
            "kind": "CONSTRUCTION",
            "revision_id": h.revision_id,
            "base_revision_id": p.get("base_revision_id"),
        }
        r = compute_construction(
            {**h.metrics, "items": []},
            bundle,
            src,
            height_m=None if height is None else Decimal(str(height)) / 1000,
            adjust=adjust,
        )
    else:
        thickness = p.get("thickness_mm")
        r = compute_quote(
            h.metrics,
            bundle,
            {"kind": h.source_kind, "revision_id": h.revision_id, "document_id": h.document_id},
            qty=int(p["qty"]),
            material_code=p.get("material_code"),
            thickness_mm=None if thickness is None else Decimal(str(thickness)),
            adjust=adjust,
        )
    return r


SENSITIVE = ("material", "labor", "overhead", "outsource", "qty")


@router.post("/api/quotes/{quote_id}/scenarios")
def quote_scenarios(
    quote_id: int,
    req: ScenariosIn,
    user: CurrentUser,
    db: Db,
    response: Response,
    format: Literal["json", "xlsx"] = "json",
) -> Any:
    """Track 6-4: compare what-if scenarios against the quote and rank each factor's sensitivity
    (total change for ±`sensitivity` on that factor alone). Nothing is stored."""
    from backend.api.routes_master import _bundle

    need(user, "ESTIMATOR", "REVIEWER")
    h = _visible_quote(db, user, quote_id)
    version = db.get(MasterVersion, h.master_version_id)
    assert version is not None
    bundle = _bundle(db, version)  # once: up to 21 engine runs below
    db.close()  # pure CPU from here on: give the connection back before waiting for a slot
    if not _render_slots.acquire(timeout=30):
        raise ApiError(503, "SERVER_BUSY", "Server busy, retry later")
    response.headers["Cache-Control"] = "no-store"  # internal cost breakdown
    try:
        return _scenarios(h, bundle, req, format)
    finally:
        _render_slots.release()


def _scenarios(h: QuoteHeader, bundle: dict[str, Any], req: ScenariosIn, format: str) -> Any:
    own = h.inputs.get("scenario") or {}
    base = _recompute(h, bundle, own or None)["totals"]
    total = base["total_amount"]

    def row(name: str, sc: dict[str, Any]) -> dict[str, Any]:
        r = _recompute(h, bundle, sc)
        t = r["totals"]
        delta = t["total_amount"] - total
        return {
            "name": name,
            # a factor that pushes a line or the total out of range zeroes it: flag, not a delta
            "errors": sorted({lg["code"] for lg in r["logs"] if lg["severity"] == "ERROR"}),
            "factors": {k: str(sc.get(k, 1)) for k in SENSITIVE},
            "totals": {k: _plain(v) for k, v in t.items()},
            "delta": _plain(delta),
            "delta_pct": None if not total else _plain((delta / total * 100).quantize(CENT)),
        }

    scenarios = [row(s.name, s.model_dump(mode="json")) for s in req.scenarios]
    sensitivity = []
    for k in SENSITIVE:
        for sign in (1, -1):
            f = Decimal(str(own.get(k, 1))) * (1 + sign * req.sensitivity)
            sensitivity.append(
                {"factor": k, "change": _plain(sign * req.sensitivity)} | row(k, {**own, k: str(f)})
            )
    out = {
        "quote_id": h.quote_id,
        "quote_no": h.quote_no,
        "basis": "CALCULATED",  # engine values: manual overrides are not simulated
        "base": {
            "factors": {k: str(own.get(k, 1)) for k in SENSITIVE},
            "totals": {k: _plain(v) for k, v in base.items()},
        },
        "scenarios": scenarios,
        "sensitivity": sensitivity,
        "construction": _is_c(h),
    }
    if format == "xlsx":
        return Response(
            render_scenarios_xlsx(out),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="{h.quote_no}-scenarios.xlsx"',
                "Cache-Control": "no-store",  # internal cost breakdown
                "X-Content-Type-Options": "nosniff",
            },
        )
    return body(out)


def _locked_quote(db: Db, quote_id: int) -> QuoteHeader:
    """Header row lock: every status change and line adjustment takes it first. populate_existing:
    the visibility check already loaded the row, and the status must be read after the lock."""
    h = db.scalar(
        select(QuoteHeader)
        .where(QuoteHeader.quote_id == quote_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert h is not None  # callers checked visibility; quotes are never deleted
    return h


SUPPLIER_FILE = Path(
    os.environ.get("AXCAD_SUPPLIER_FILE")
    or Path(__file__).resolve().parents[1] / "config" / "supplier.json"
)
REPORT_MEDIA = {
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
SUPPLIER_KEYS = ("company", "business_no", "ceo", "address", "phone", "email")
TERM_KEYS = ("delivery", "payment", "note")
BUSINESS_NO = re.compile(r"^\d{3}-\d{2}-\d{5}$")
MAX_REPORT_LINES = 500  # the PDF builds a table cell per value: bound the work per request
_render_slots = threading.BoundedSemaphore(2)  # CPU-bound renders, the API stays responsive


def _supplier() -> tuple[dict[str, Any], bool]:
    """supplier.json, checked -> (supplier, is_real). A sample still renders, watermarked."""
    try:
        sup = json.loads(SUPPLIER_FILE.read_text(encoding="utf-8"))
        days = sup["validity_days"]
        ok = (
            all(isinstance(sup[k], str) and 0 < len(sup[k]) <= 200 for k in SUPPLIER_KEYS)
            and all(isinstance(sup[k], str) and len(sup[k]) <= 500 for k in TERM_KEYS)
            and isinstance(days, int)
            and 1 <= days <= 365
        )
    except (OSError, ValueError, KeyError, TypeError):
        ok = False
    if not ok:
        raise ApiError(409, "SUPPLIER_NOT_CONFIGURED", "공급자 설정 파일이 올바르지 않습니다")
    real = not (
        "_sample" in sup
        or not BUSINESS_NO.match(sup["business_no"])
        or sup["business_no"] == "000-00-00000"
    )
    return sup, real


def _report_model(db: Db, h: QuoteHeader, basis: bool) -> dict[str, Any]:
    q = _quote_out(db, h)
    eff = q["effective"]
    if eff is None:
        raise ApiError(409, "QUOTE_AMOUNT_OVERFLOW", "조정 반영 합계가 허용 범위를 넘습니다")
    doc, project = db.get(Document, h.document_id), db.get(Project, h.project_id)
    assert doc is not None and project is not None
    issued = datetime.now(UTC).astimezone(KST).date()
    sup, real = _supplier()
    if real and any(
        lg["severity"] == "ERROR" for lg in _validation(db, h)
    ):  # was the approval gate
        raise ApiError(409, "QUOTE_HAS_ERRORS", "검증 ERROR가 있는 견적은 견적서로 낼 수 없습니다")
    if len(q["lines"]) > MAX_REPORT_LINES:
        raise ApiError(422, "REPORT_TOO_LARGE", "라인이 너무 많아 견적서를 만들 수 없습니다")
    tb = h.metrics.get("title_block") or {}
    material, thickness = h.inputs.get("material_code"), h.inputs.get("thickness_mm")
    spec = [tb.get("part_no") or doc.doc_no, material, f"t{thickness}" if thickness else None]
    if h.revision_id:
        rev = db.get(Revision, h.revision_id)
        kind = "공사 도면" if _is_c(h) else "2D 도면"
        source = f"{kind} {doc.doc_no} Rev {rev.revision_no if rev else '?'}"
    else:
        source = f"3D 모델 {doc.doc_no}"
    qty, supply = Decimal(h.inputs.get("qty") or 1), Decimal(eff["supply_amount"])
    lines = [
        {
            "line_no": ln["line_no"],
            "cost_category": ln["cost_category"],
            "item_name": ln["item_name"],
            "unit": ln["unit"],
            "qty": ln["calculated_qty"] if ln["override_qty"] is None else ln["override_qty"],
            "unit_price": ln["calculated_unit_price"]
            if ln["override_unit_price"] is None
            else ln["override_unit_price"],
            # ratio line (식): the exact rate, shown as % like the quote screen (unitPriceText)
            "rate": ln["traces"][0]["unit_price"]
            if ln["unit"] == "식" and ln["override_unit_price"] is None and ln["traces"]
            else None,
            "amount": ln["effective_amount"],
            "manual": ln["override_amount"] is not None,
            "override_reason": ln["override_reason"],
            "basis": " / ".join(f"{t['rule_code']}: {t['formula_text']}" for t in ln["traces"]),
        }
        for ln in q["lines"]
    ]
    item = {
        "name": tb.get("part_name") or doc.title,
        "spec": " / ".join(x for x in spec if x),
        "qty": qty,
        "unit": "EA",
    }
    construction = None
    if _is_c(h):  # one lump-sum line; the BOQ and cost statement below carry the detail
        item = {"name": doc.title, "spec": "별첨 내역서 참조", "qty": qty, "unit": "식"}
        construction = _construction_report(db, h, q["lines"], eff)
    return {
        "quote_no": h.quote_no,
        "issued": issued.isoformat(),
        "valid_until": (issued + timedelta(days=sup["validity_days"])).isoformat(),
        "official": real,  # Track 7: no approval; only a sample supplier is watermarked
        "basis": basis,
        "master_version_id": h.master_version_id,
        "source": source,
        "manual_count": sum(ln["manual"] for ln in lines),
        "supplier": {k: sup[k] for k in SUPPLIER_KEYS},
        "customer": {
            "name": project.customer_name or "(고객명 미지정)",
            "project": project.project_name,
        },
        "item": {
            **item,
            "unit_price": (supply / qty).quantize(CENT, rounding=ROUND_HALF_UP),
            "supply": supply,
            "vat": Decimal(eff["vat_amount"]),
        },
        "terms": {k: sup[k] for k in TERM_KEYS},
        "totals": eff,
        "lines": lines,
        "construction": construction,
    }


RATIO_LINES = ("INDIRECT_LABOR", "INDIRECT_COST")


def _construction_report(
    db: Db, h: QuoteHeader, lines: list[dict[str, Any]], eff: dict[str, Any]
) -> dict[str, Any]:
    """Track 6-5: 공종별 내역서 (BOQ, one row per work item), 공사원가계산서 and 실별 면적표,
    from the effective (override ?? calculated) line values."""
    works = {
        w.item_code: w
        for w in db.scalars(select(WorkItem).where(WorkItem.version_id == h.master_version_id))
    }
    boq: dict[str, dict[str, Any]] = {}
    unit_costs: dict[str, dict[str, Any]] = {}  # P4-6 일위대가표: per costbook work item
    for ln in lines:
        code = ln["item_code"].split("/", 1)[0]  # a costbook resource line: work/resource
        if code in RATIO_LINES:
            continue
        w = works.get(code)
        row = boq.setdefault(
            code,
            {
                "work_category": w.work_category if w else "",
                "item_name": w.item_name if w else ln["item_name"],
                "spec": (w.spec if w else None) or "",
                "unit": ln["unit"],
                "qty": None,
                "loss_rate": _plain(w.loss_rate) if w and w.loss_rate else None,
                "total": Decimal(0),
            },
        )
        qty = ln["calculated_qty"] if ln["override_qty"] is None else ln["override_qty"]
        price = (
            ln["calculated_unit_price"]
            if ln["override_unit_price"] is None
            else ln["override_unit_price"]
        )
        amt = Decimal(ln["effective_amount"] or 0)
        part = {"MATERIAL": "material", "LABOR": "labor", "OVERHEAD": "expense"}[
            ln["cost_category"]
        ]
        cb = next((t for t in ln["traces"] if t["rule_code"].startswith("COSTBOOK:")), None)
        if cb:  # 일위대가: resources add up per part; unit price = part amount / work quantity
            work = Decimal(cb["inputs"]["work_qty"])
            row["qty"] = _plain(work)
            row[f"{part}_amount"] = _plain(Decimal(row.get(f"{part}_amount") or 0) + amt)
            row[f"{part}_price"] = _plain((Decimal(row[f"{part}_amount"]) / work).quantize(CENT))
            row["total"] += amt
            uc = unit_costs.setdefault(
                code,
                {
                    "item_name": row["item_name"],
                    "unit": row["unit"],
                    "costbook_item": cb["inputs"]["costbook_item"],
                    "notice_no": cb["inputs"].get("notice_no"),
                    "page_no": cb["inputs"].get("page_no"),
                    "resources": [],
                },
            )
            uc["resources"].append(
                {
                    "name": ln["item_name"].split(" · ", 1)[-1],
                    "category": ln["cost_category"],
                    "unit": ln["unit"],
                    "qty_per_unit": _plain(Decimal(str(qty)) / work),
                    "unit_price": price,
                    "amount_per_unit": _plain((amt / work).quantize(CENT)),
                }
            )
            continue
        row[f"{part}_price"], row[f"{part}_amount"] = price, _plain(amt)
        if part != "material" or row["qty"] is None:  # net quantity: material carries the loss
            row["qty"] = qty
        row["total"] += amt
    rows = sorted(boq.values(), key=lambda r: (r["work_category"], r["item_name"]))
    for r in rows:
        r["total"] = _plain(r["total"])

    def sum_of(code: str) -> Decimal:
        return sum(
            (Decimal(ln["effective_amount"] or 0) for ln in lines if ln["item_code"] == code),
            Decimal(0),
        )

    il, ic = sum_of("INDIRECT_LABOR"), sum_of("INDIRECT_COST")
    labor, overhead = Decimal(eff["labor_cost"]), Decimal(eff["overhead_cost"])
    statement = [
        ("재료비", eff["material_cost"]),
        ("  직접노무비", _plain(labor - il)),
        ("  간접노무비", _plain(il)),
        ("노무비 계", eff["labor_cost"]),
        ("  직접경비", _plain(overhead - ic)),
        ("  간접공사비", _plain(ic)),
        ("경비 계", eff["overhead_cost"]),
        ("순공사원가", eff["manufacturing_cost"]),
        ("일반관리비", eff["admin_cost"]),
        ("이윤", eff["profit"]),
        ("공급가액", eff["supply_amount"]),
        ("부가가치세", eff["vat_amount"]),
        ("도급액 합계", eff["total_amount"]),
    ]
    rooms = [
        {k: r.get(k) for k in ("name", "area_m2", "perimeter_m", "status")}
        for r in (h.metrics.get("rooms") or [])[:MAX_REPORT_LINES]
    ]
    return {
        "boq": rows,
        "statement": statement,
        "rooms": rooms,
        "unit_costs": list(unit_costs.values()),
    }


def _render(m: dict[str, Any], format: str) -> bytes:
    if not _render_slots.acquire(timeout=30):
        raise ApiError(503, "REPORT_BUSY", "견적서 생성 요청이 많습니다. 잠시 후 다시 시도하세요")
    try:
        return render_pdf(m) if format == "pdf" else render_xlsx(m)
    finally:
        _render_slots.release()


@router.get("/api/quotes/{quote_id}/report")
# sync def: rendering is CPU work, keep it off the event loop
def quote_report(
    quote_id: int,
    user: CurrentUser,
    db: Db,
    format: Literal["pdf", "xlsx"] = "pdf",
    basis: bool = False,
) -> Response:
    """Track 7: no approval step, so one kind of document, rendered on demand from the quote's
    current values; `basis` adds the internal cost basis (산출근거)."""
    need(user, "ESTIMATOR", "REVIEWER")
    h = _visible_quote(db, user, quote_id)
    m = _report_model(db, h, basis)
    db.commit()  # give the connection back before the CPU-bound render
    data = _render(m, format)
    name = f"{h.quote_no}.{format}"
    return Response(
        data,
        media_type=REPORT_MEDIA[format],
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",  # internal costs may be inside
            "X-Content-Type-Options": "nosniff",
        },
    )
