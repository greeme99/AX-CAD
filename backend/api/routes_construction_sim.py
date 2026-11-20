"""Construction Process, Material Takeoff & 5D Cost Simulation API routes (TASK-025)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from backend.api.auth import CurrentUser, Db, get_project, need
from backend.api.common import ApiError, body
from backend.db.models import ConstructionPlan
from core.construction_sim import ConstructionSimError, CostRates, DomainCode, WorkMode
from core.construction_sim.ai_advisor import advise_construction_simulation
from core.construction_sim.simulator import export_simulation_csv, simulate_construction_plan
from core.construction_sim.templates import DOMAIN_META, generate_template_tasks, list_templates

router = APIRouter(tags=["construction-sim"])


class CostRatesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    indirect_labor_rate: float = Field(default=0.12, ge=0.0, le=1.0)
    indirect_expense_rate: float = Field(default=0.08, ge=0.0, le=1.0)
    general_admin_rate: float = Field(default=0.06, ge=0.0, le=0.5)
    profit_rate: float = Field(default=0.10, ge=0.0, le=0.5)
    vat_rate: float = Field(default=0.10, ge=0.0, le=0.3)


class PlanCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    domain: DomainCode = "BUILDING"
    work_mode: WorkMode = "INSTALL"
    scale_qty: float = Field(default=100.0, gt=0.0, le=1e6)
    scale_unit: str | None = Field(default=None, max_length=20)
    cost_rates: CostRatesIn = Field(default_factory=CostRatesIn)
    tasks: list[dict[str, Any]] | None = None


class PlanUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    work_mode: WorkMode | None = None
    scale_qty: float | None = Field(default=None, gt=0.0, le=1e6)
    cost_rates: CostRatesIn | None = None
    tasks: list[dict[str, Any]] | None = None


class SimulateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    monte_carlo_trials: int = Field(default=1000, ge=100, le=5000)
    seed: int = Field(default=42, ge=0, le=1_000_000)
    use_ai: bool = False


def _plan_dict(row: ConstructionPlan) -> dict[str, Any]:
    return {
        "plan_id": row.plan_id,
        "project_id": row.project_id,
        "name": row.name,
        "domain": row.domain,
        "work_mode": row.work_mode,
        "scale_qty": float(row.scale_qty),
        "scale_unit": row.scale_unit,
        "cost_rates": row.cost_rates or CostRatesIn().model_dump(),
        "tasks": row.tasks or [],
        "last_simulation": row.last_simulation,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _get_plan(db: Db, user: Any, plan_id: int) -> ConstructionPlan:
    row = db.get(ConstructionPlan, plan_id)
    if row is None:
        raise ApiError(404, "CONSIM_PLAN_NOT_FOUND", "공사 시뮬레이션 계획을 찾을 수 없습니다")
    get_project(db, user, row.project_id)
    return row


@router.get("/api/construction-sim/templates")
def get_templates(user: CurrentUser) -> dict[str, Any]:
    return body({"templates": list_templates()})


@router.post("/api/projects/{project_id}/construction-plans", status_code=201)
def create_plan(
    project_id: int, payload: PlanCreateIn, user: CurrentUser, db: Db
) -> dict[str, Any]:
    get_project(db, user, project_id)
    need(user, "DESIGNER", "ESTIMATOR")
    tasks = (
        payload.tasks
        if payload.tasks is not None and len(payload.tasks) > 0
        else generate_template_tasks(payload.domain, payload.work_mode, payload.scale_qty)
    )
    unit = payload.scale_unit or DOMAIN_META[payload.domain]["default_unit"]
    row = ConstructionPlan(
        project_id=project_id,
        name=payload.name.strip(),
        domain=payload.domain,
        work_mode=payload.work_mode,
        scale_qty=Decimal(str(round(payload.scale_qty, 2))),
        scale_unit=unit,
        cost_rates=payload.cost_rates.model_dump(),
        tasks=tasks,
        created_by=user.user_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return body(_plan_dict(row))


@router.get("/api/projects/{project_id}/construction-plans")
def list_plans(project_id: int, user: CurrentUser, db: Db) -> dict[str, Any]:
    get_project(db, user, project_id)
    rows = db.scalars(
        select(ConstructionPlan)
        .where(ConstructionPlan.project_id == project_id)
        .order_by(ConstructionPlan.updated_at.desc(), ConstructionPlan.plan_id.desc())
    ).all()
    return body({"items": [_plan_dict(r) for r in rows]})


@router.put("/api/construction-plans/{plan_id}")
def update_plan(plan_id: int, payload: PlanUpdateIn, user: CurrentUser, db: Db) -> dict[str, Any]:
    row = _get_plan(db, user, plan_id)
    need(user, "DESIGNER", "ESTIMATOR")
    if payload.name is not None:
        row.name = payload.name.strip()
    if payload.work_mode is not None:
        row.work_mode = payload.work_mode
    if payload.scale_qty is not None:
        row.scale_qty = Decimal(str(round(payload.scale_qty, 2)))
    if payload.cost_rates is not None:
        row.cost_rates = payload.cost_rates.model_dump()
    if payload.tasks is not None:
        row.tasks = payload.tasks
    row.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(row)
    return body(_plan_dict(row))


@router.delete("/api/construction-plans/{plan_id}")
def delete_plan(plan_id: int, user: CurrentUser, db: Db) -> dict[str, Any]:
    row = _get_plan(db, user, plan_id)
    need(user, "DESIGNER", "ESTIMATOR")
    db.delete(row)
    db.commit()
    return body({"deleted": plan_id})


@router.post("/api/construction-plans/{plan_id}/simulate")
def run_simulation(
    plan_id: int,
    user: CurrentUser,
    db: Db,
    payload: SimulateIn | None = None,
) -> dict[str, Any]:
    row = _get_plan(db, user, plan_id)
    p = payload or SimulateIn()
    cr_raw = row.cost_rates or {}
    rates = CostRates(
        indirect_labor_rate=float(cr_raw.get("indirect_labor_rate", 0.12)),
        indirect_expense_rate=float(cr_raw.get("indirect_expense_rate", 0.08)),
        general_admin_rate=float(cr_raw.get("general_admin_rate", 0.06)),
        profit_rate=float(cr_raw.get("profit_rate", 0.10)),
        vat_rate=float(cr_raw.get("vat_rate", 0.10)),
    )
    try:
        sim = simulate_construction_plan(
            row.tasks or [],
            rates=rates,
            monte_carlo_trials=p.monte_carlo_trials,
            seed=p.seed,
        )
    except ConstructionSimError as exc:
        raise ApiError(exc.http_status, exc.code, exc.message) from None

    advice = advise_construction_simulation(row.domain, row.work_mode, sim, use_ai=p.use_ai)
    sim["advisor"] = advice
    row.last_simulation = sim
    row.updated_at = datetime.now(UTC)
    db.commit()
    return body({"plan_id": row.plan_id, **sim})


@router.get("/api/construction-plans/{plan_id}/export")
def export_plan(
    plan_id: int,
    user: CurrentUser,
    db: Db,
    format: Annotated[Literal["csv", "json"], Query()] = "csv",
) -> Response:
    row = _get_plan(db, user, plan_id)
    sim = row.last_simulation
    if not sim:
        sim = simulate_construction_plan(row.tasks or [])
    if format == "json":
        import json

        return Response(
            json.dumps(
                {"plan": _plan_dict(row), "simulation": sim},
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            media_type="application/json; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="construction_plan_{plan_id}.json"'
            },
        )
    csv_bytes = export_simulation_csv(row.name, sim)
    return Response(
        csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="construction_plan_{plan_id}.csv"'},
    )
