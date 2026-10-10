"""FN-16 master data: versioned bundles (materials, price items, process rules, cost ratios,
mapping rules). ADMIN edits a DRAFT bundle as a whole; ACTIVE bundles are frozen (DB trigger),
so a change is always a new version and old quotes stay reproducible (NFR-05)."""

import json
import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal, get_args
from zoneinfo import ZoneInfo

from fastapi import APIRouter, File, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import delete, func, select

from backend.api.auth import CurrentUser, Db, need
from backend.api.common import ApiError, body
from backend.db.models import (
    CostRatios,
    MappingRule,
    MasterVersion,
    Material,
    PriceItem,
    ProcessRule,
)
from core.dxf.reader import DxfError, run_isolated
from core.quote_engine import formula
from core.quote_engine.g1 import build_template, parse_g1_job

router = APIRouter()
MAX_XLSX_BYTES = 5 * 1024**2
MAX_REPORTED = 30  # error lines returned at once
KST = ZoneInfo("Asia/Seoul")  # business dates (effective_from) are Korean calendar days

Code = Annotated[str, Field(pattern=r"^[A-Za-z0-9._\-]{1,40}$")]
# price items are named by the shop's own labour/machine groups (often Korean, e.g. 레이저)
ItemCode = Annotated[str, Field(pattern=r"^[\w.\-]{1,40}$")]
Text100 = Annotated[str, Field(min_length=1, max_length=100)]
Money = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2, allow_inf_nan=False)]
Rate = Annotated[Decimal, Field(ge=0, le=1, max_digits=7, decimal_places=6, allow_inf_nan=False)]
Mm = Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=3, allow_inf_nan=False)]
Param = Annotated[float, Field(allow_inf_nan=False)]
# metric names produced by FN-14 (2D) and FN-15 (3D); process rules may only consume these
Metric = Literal[
    "cutting_length_mm",
    "hole_count",
    "punch_hole_count",
    "bend_count",
    "bend_length_mm",
    "net_area_mm2",
    "surface_area_mm2",
    "volume_mm3",
    "part_qty",
]
METRIC_NAMES = (*get_args(Metric), "thickness_mm")  # a formula may read any metric
TITLE_FIELDS = ("part_no", "part_name", "material", "thickness_mm", "qty")
LAYER_TARGETS = ("CUT", "BEND", "IGNORE")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MaterialIn(_Strict):
    material_code: Code
    material_name: Text100 | None = None
    thickness_min_mm: Mm | None = None
    thickness_max_mm: Mm | None = None
    density_g_cm3: Annotated[
        Decimal, Field(gt=0, le=30, max_digits=10, decimal_places=4, allow_inf_nan=False)
    ]
    unit_price_per_kg: Money | None = None
    scrap_rate: Rate | None = None

    @model_validator(mode="after")
    def _range(self) -> "MaterialIn":
        lo, hi = self.thickness_min_mm, self.thickness_max_mm
        if lo is not None and hi is not None and hi < lo:
            raise ValueError("thickness_max_mm < thickness_min_mm")
        return self


class PriceItemIn(_Strict):
    item_code: ItemCode
    item_type: Literal["LABOR", "MACHINE", "OUTSOURCE"]
    unit: Annotated[str, Field(min_length=1, max_length=20)]
    unit_price: Money | None = None


class ProcessRuleIn(_Strict):
    rule_code: Code
    process_code: Code
    process_name: Text100 | None = None
    input_metric: Metric
    # ponytail: stored as text, evaluated by the S9 rule engine (safe AST evaluator), not here
    formula_text: Annotated[str, Field(min_length=1, max_length=500)]
    params: Annotated[dict[Code, Param], Field(max_length=50)] = {}
    labor_item_code: ItemCode | None = None
    machine_item_code: ItemCode | None = None

    @model_validator(mode="after")
    def _formula(self) -> "ProcessRuleIn":
        try:
            unknown = formula.names(self.formula_text) - set(self.params) - set(METRIC_NAMES)
        except formula.FormulaError as e:
            raise ValueError(str(e)) from None
        if unknown:
            raise ValueError(f"unknown names in formula: {', '.join(sorted(unknown))}")
        return self


class CostRatiosIn(_Strict):
    overhead_basis: Literal["MACHINE_HOUR", "LABOR_RATIO"]
    overhead_rate: Rate | None = None
    admin_rate: Rate | None = None
    profit_rate: Rate | None = None
    vat_rate: Rate = Decimal("0.1")
    rounding_rule: Literal["FLOOR", "HALF_UP", "CEILING"]
    rounding_unit: Literal[1, 10, 100, 1000]
    rounding_scope: Literal["LINE", "TOTAL"]
    material_basis: Literal["NET", "BBOX"] = "NET"  # plate weight: net area or bounding rect


class MappingRuleIn(_Strict):
    rule_type: Literal["LAYER", "LINETYPE", "TITLE_TAG", "PUNCH_MAX_DIA"]
    target: Annotated[str, Field(min_length=1, max_length=40)]
    pattern: Annotated[str, Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def _target(self) -> "MappingRuleIn":
        if self.rule_type in ("LAYER", "LINETYPE") and self.target not in LAYER_TARGETS:
            raise ValueError(f"target must be one of {LAYER_TARGETS}")
        if self.rule_type == "TITLE_TAG" and self.target not in TITLE_FIELDS:
            raise ValueError(f"target must be one of {TITLE_FIELDS}")
        if self.rule_type == "PUNCH_MAX_DIA":
            if self.target != "HOLE":
                raise ValueError("target must be HOLE")
            try:
                ok = 0 < float(self.pattern) < 1000
            except ValueError:
                ok = False
            if not ok:
                raise ValueError("pattern must be a diameter in mm")
        return self


class BundleIn(_Strict):
    materials: Annotated[list[MaterialIn], Field(max_length=2000)] = []
    price_items: Annotated[list[PriceItemIn], Field(max_length=2000)] = []
    process_rules: Annotated[list[ProcessRuleIn], Field(max_length=2000)] = []
    cost_ratios: CostRatiosIn | None = None
    mapping_rules: Annotated[list[MappingRuleIn], Field(max_length=2000)] = []


class VersionCreate(_Strict):
    version_code: Code
    effective_from: date
    note: Annotated[str, Field(max_length=500)] | None = None
    copy_from: Annotated[int, Field(ge=1)] | None = None


def _plain(v: Any) -> Any:
    return str(v) if isinstance(v, Decimal) else v  # amounts never pass through float


def _row(obj: Any, skip: tuple[str, ...] = ("version_id",)) -> dict[str, Any]:
    cols = obj.__table__.columns.keys()
    return {c: _plain(getattr(obj, c)) for c in cols if c not in skip}


def _version_out(v: MasterVersion) -> dict[str, Any]:
    return _row(v, ())


CHILD_MODELS: tuple[tuple[str, Any, str], ...] = (
    ("materials", Material, "material_id"),
    ("price_items", PriceItem, "price_item_id"),
    ("process_rules", ProcessRule, "process_rule_id"),
    ("mapping_rules", MappingRule, "mapping_rule_id"),
)


def _bundle(db: Db, v: MasterVersion) -> dict[str, Any]:
    out = _version_out(v)
    for key, model, pk in CHILD_MODELS:
        rows = db.scalars(select(model).where(model.version_id == v.version_id).order_by(pk))
        out[key] = [_row(r, ("version_id", pk)) for r in rows]
    ratios = db.get(CostRatios, v.version_id)
    out["cost_ratios"] = _row(ratios) if ratios else None
    return out


def _get(db: Db, version_id: int, lock: bool = False) -> MasterVersion:
    q = select(MasterVersion).where(MasterVersion.version_id == version_id)
    v = db.scalar(q.with_for_update() if lock else q)
    if v is None:
        raise ApiError(404, "MASTER_NOT_FOUND", "Master data version not found")
    return v


def _draft(db: Db, version_id: int) -> MasterVersion:
    v = _get(db, version_id, lock=True)
    if v.status != "DRAFT":
        raise ApiError(
            409, "MASTER_FROZEN", "Active master data cannot change; create a new version"
        )
    return v


def _replace(db: Db, version_id: int, b: BundleIn) -> None:
    for model in (Material, PriceItem, ProcessRule, MappingRule, CostRatios):
        db.execute(delete(model).where(model.version_id == version_id))
    for key, model, _ in CHILD_MODELS:
        db.add_all(model(version_id=version_id, **r.model_dump()) for r in getattr(b, key))
    if b.cost_ratios:
        db.add(CostRatios(version_id=version_id, **b.cost_ratios.model_dump()))


def _missing(bundle: dict[str, Any]) -> list[str]:
    """What blocks activation: every price a quote could need must be filled in."""
    out: list[str] = []
    r = bundle["cost_ratios"]
    if r is None:
        out.append("cost_ratios")
    else:
        out += [f"cost_ratios.{k}" for k in ("admin_rate", "profit_rate") if r[k] is None]
        if r["overhead_basis"] == "LABOR_RATIO" and r["overhead_rate"] is None:
            out.append("cost_ratios.overhead_rate")
    for m in bundle["materials"]:
        out += [
            f"materials.{m['material_code']}.{k}"
            for k in ("unit_price_per_kg", "scrap_rate")
            if m[k] is None
        ]
    items = {p["item_code"] for p in bundle["price_items"]}
    out += [
        f"price_items.{p['item_code']}.unit_price"
        for p in bundle["price_items"]
        if p["unit_price"] is None
    ]
    for rule in bundle["process_rules"]:
        for k in ("labor_item_code", "machine_item_code"):
            if rule[k] and rule[k] not in items:
                out.append(f"process_rules.{rule['rule_code']}.{k}")
    if not bundle["materials"]:
        out.append("materials")
    return out


@router.get("/api/master-versions")
def list_versions(user: CurrentUser, db: Db) -> Any:
    need(user, "ESTIMATOR")
    rows = db.scalars(select(MasterVersion).order_by(MasterVersion.effective_from.desc())).all()
    return body({"items": [_version_out(v) for v in rows], "total": len(rows)})


@router.get("/api/master-versions/current")
def current_version(user: CurrentUser, db: Db, on: date | None = None) -> Any:
    need(user, "ESTIMATOR")
    v = active_version(db, on or datetime.now(KST).date())
    if v is None:
        raise ApiError(404, "MASTER_NOT_FOUND", "No active master data for that date")
    return body(_bundle(db, v))


def active_version(db: Db, on: date) -> MasterVersion | None:
    q = (
        select(MasterVersion)
        .where(MasterVersion.status == "ACTIVE", MasterVersion.effective_from <= on)
        .order_by(MasterVersion.effective_from.desc(), MasterVersion.version_id.desc())
        .limit(1)
    )
    return db.scalar(q)


@router.post("/api/master-versions")
def create_version(req: VersionCreate, user: CurrentUser, db: Db) -> Any:
    need(user)  # ADMIN only
    v = MasterVersion(
        version_code=req.version_code,
        effective_from=req.effective_from,
        note=req.note,
        created_by=user.user_id,
    )
    db.add(v)
    db.flush()
    if req.copy_from:
        src = _bundle(db, _get(db, req.copy_from))
        _replace(
            db, v.version_id, BundleIn.model_validate({k: src[k] for k in BundleIn.model_fields})
        )
    db.commit()
    return body(_bundle(db, v))


@router.get("/api/master-versions/{version_id}")
def get_version(version_id: int, user: CurrentUser, db: Db) -> Any:
    need(user, "ESTIMATOR")
    return body(_bundle(db, _get(db, version_id)))


@router.put("/api/master-versions/{version_id}")
def put_version(version_id: int, req: BundleIn, user: CurrentUser, db: Db) -> Any:
    need(user)
    v = _draft(db, version_id)
    _replace(db, version_id, req)
    db.commit()
    return body(_bundle(db, v))


@router.post("/api/master-versions/{version_id}/activate")
def activate_version(version_id: int, user: CurrentUser, db: Db) -> Any:
    need(user)
    v = _draft(db, version_id)
    missing = _missing(_bundle(db, v))
    if missing:
        raise ApiError(422, "MASTER_INCOMPLETE", "Missing: " + ", ".join(missing[:50]))
    v.status, v.activated_at = "ACTIVE", func.now()
    db.commit()
    db.refresh(v)
    return body(_version_out(v))


@router.delete("/api/master-versions/{version_id}")
def delete_version(version_id: int, user: CurrentUser, db: Db) -> Any:
    need(user)
    db.delete(_draft(db, version_id))
    db.commit()
    return body({"version_id": version_id})


LIST_NAMES = {
    "materials": "1_재질",
    "price_items": "3_임률/2_공정 기계경비",
    "process_rules": "2_공정",
    "mapping_rules": "5_레이어/6_표제란",
    "cost_ratios": "4_원가비율",
}


def _validation_lines(e: ValidationError) -> list[str]:
    out = []
    for err in e.errors():
        loc = [str(x) for x in err["loc"]]
        where = LIST_NAMES.get(loc[0], loc[0]) if loc else ""
        if len(loc) > 1 and loc[1].isdigit():
            where += f" {int(loc[1]) + 1}번째 항목"
        field = loc[-1] if len(loc) > 2 or (len(loc) == 2 and not loc[1].isdigit()) else ""
        out.append(f"{where} {field}: {err['msg']}".strip())
    return out


@router.post("/api/master-versions/{version_id}/import-xlsx")
# sync def: parsing runs in an isolated process; FastAPI keeps it off the event loop
def import_xlsx(
    version_id: int,
    user: CurrentUser,
    db: Db,
    file: UploadFile = File(...),  # noqa: B008
) -> Any:
    """G1 workbook (sheets 1-6) replaces the content of a DRAFT bundle."""
    need(user)
    _draft(db, version_id)
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise ApiError(422, "G1_INVALID", "G1 양식(.xlsx) 파일만 가져올 수 있습니다")
    data = file.file.read(MAX_XLSX_BYTES + 1)
    if len(data) > MAX_XLSX_BYTES:
        raise ApiError(413, "FILE_TOO_LARGE", "File exceeds 5 MB")
    if not data.startswith(b"PK\x03\x04"):
        raise ApiError(422, "G1_INVALID", "엑셀(.xlsx) 파일이 아닙니다")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "g1.xlsx"
        path.write_bytes(data)
        try:
            text = run_isolated(
                parse_g1_job, str(path), fail=("G1_INVALID", "엑셀을 읽을 수 없습니다")
            )
        except DxfError as e:
            raise ApiError(422, "G1_INVALID", e.message) from None
    result = json.loads(text)
    errors: list[str] = result["errors"]
    if not errors:
        try:
            bundle = BundleIn.model_validate(result["bundle"])
        except ValidationError as e:
            errors = _validation_lines(e)
    if errors:
        more = f"\n… 외 {len(errors) - MAX_REPORTED}건" if len(errors) > MAX_REPORTED else ""
        raise ApiError(422, "G1_INVALID", "\n".join(errors[:MAX_REPORTED]) + more)
    v = _draft(db, version_id)
    _replace(db, version_id, bundle)
    db.commit()
    return body({**_bundle(db, v), "warnings": result["warnings"]})


@router.get("/api/master-data/g1-template.xlsx")  # own prefix: /master-versions/{id} shadows it
def g1_template(user: CurrentUser) -> Any:
    """Blank G1 workbook, generated from the same definitions the importer reads."""
    need(user, "ESTIMATOR")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "g1.xlsx"
        build_template(str(path))
        data = path.read_bytes()
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="G1_input.xlsx"'},
    )
