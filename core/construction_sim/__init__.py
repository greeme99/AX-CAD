"""Construction Process, Material Takeoff, CPM Schedule & 5D Cost Simulation Engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

DomainCode = Literal[
    "BUILDING",
    "CIVIL_LANDSCAPE",
    "MEP_ELECTRICAL",
    "MEP_PLUMBING",
    "MEP_HVAC",
    "INTERIOR_RENO",
]
WorkMode = Literal["NEW_BUILD", "INSTALL", "REPLACE", "DEMOLISH", "RENOVATE"]


class ConstructionSimError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


@dataclass(frozen=True)
class CostRates:
    """Korean standard construction cost statement overhead ratios (공사원가계산서 요율)."""

    indirect_labor_rate: float = 0.12  # 간접노무비율 (직접노무비 기준)
    indirect_expense_rate: float = 0.08  # 간접공사비율 (재료비+직접노무비 기준)
    general_admin_rate: float = 0.06  # 일반관리비율 (순공사원가 기준)
    profit_rate: float = 0.10  # 이윤율 (노무비+경비+일반관리비 기준)
    vat_rate: float = 0.10  # 부가가치세


@dataclass
class MaterialSpec:
    code: str
    name: str
    unit: str
    qty: float
    loss_rate: float
    unit_price_krw: float


@dataclass
class TaskSpec:
    task_id: str
    wbs: str
    name: str
    phase: str
    duration_days: float
    predecessors: list[str] = field(default_factory=list)
    procedures: list[str] = field(default_factory=list)
    materials: list[MaterialSpec] = field(default_factory=list)
    labor_cost_krw: float = 0.0
    equipment_cost_krw: float = 0.0
    risk_low: float = 0.90
    risk_high: float = 1.25
