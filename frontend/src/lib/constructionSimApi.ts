import { api } from "@/lib/api";

export type DomainCode =
  | "BUILDING"
  | "CIVIL_LANDSCAPE"
  | "MEP_ELECTRICAL"
  | "MEP_PLUMBING"
  | "MEP_HVAC"
  | "INTERIOR_RENO";
export type WorkMode = "NEW_BUILD" | "INSTALL" | "REPLACE" | "DEMOLISH" | "RENOVATE";

export type ConstructionTemplate = {
  domain: DomainCode;
  name: string;
  default_unit: string;
  description: string;
  supported_modes: WorkMode[];
};

export type MaterialRow = {
  code: string;
  name: string;
  unit: string;
  qty: number;
  loss_rate: number;
  unit_price_krw: number;
};

export type TaskRow = {
  task_id: string;
  wbs: string;
  name: string;
  phase: string;
  duration_days: number;
  predecessors: string[];
  procedures: string[];
  materials: MaterialRow[];
  labor_cost_krw: number;
  equipment_cost_krw: number;
  risk_low?: number;
  risk_high?: number;
  // Populated after simulation
  sim_duration_days?: number;
  early_start?: number;
  early_finish?: number;
  late_start?: number;
  late_finish?: number;
  total_float?: number;
  is_critical?: boolean;
  material_krw?: number;
  labor_krw?: number;
  equipment_krw?: number;
  direct_total_krw?: number;
};

export type SimulationResult = {
  plan_id?: number;
  project_duration_days: number;
  critical_path: string[];
  tasks: TaskRow[];
  material_bom: {
    code: string;
    name: string;
    unit: string;
    net_qty: number;
    gross_qty: number;
    unit_price_krw: number;
    total_krw: number;
  }[];
  cost_statement: Record<string, number>;
  scurve: { day: number; daily_cost_krw: number; cumulative_cost_krw: number; progress_pct: number }[];
  scenarios: {
    code: string;
    name: string;
    duration_days: number;
    grand_total_krw: number;
    delta_cost_krw: number;
    delta_days: number;
    note: string;
  }[];
  monte_carlo: {
    trials: number;
    cost_p50_krw: number;
    cost_p80_krw: number;
    cost_p95_krw: number;
    duration_p50_days: number;
    duration_p80_days: number;
    duration_p95_days: number;
    top_risk_drivers: { task_id: string; name: string; cost_std_krw: number }[];
  };
  advisor?: { provider: string; ai_used: boolean; advice: string; note?: string };
};

export type ConstructionPlan = {
  plan_id: number;
  project_id: number;
  name: string;
  domain: DomainCode;
  work_mode: WorkMode;
  scale_qty: number;
  scale_unit: string;
  cost_rates: Record<string, number>;
  tasks: TaskRow[];
  last_simulation: SimulationResult | null;
  created_at: string;
  updated_at: string;
};

export const listConstructionTemplates = () =>
  api<{ templates: ConstructionTemplate[] }>("/construction-sim/templates").then((r) => r.templates);

export const listConstructionPlans = (projectId: string) =>
  api<{ items: ConstructionPlan[] }>(`/projects/${encodeURIComponent(projectId)}/construction-plans`).then((r) => r.items);

export const createConstructionPlan = (
  projectId: string,
  payload: { name: string; domain: DomainCode; work_mode: WorkMode; scale_qty: number },
) =>
  api<ConstructionPlan>(`/projects/${encodeURIComponent(projectId)}/construction-plans`, {
    method: "POST",
    json: payload,
  });

export const updateConstructionPlan = (
  planId: number,
  payload: { name?: string; tasks?: TaskRow[] },
) =>
  api<ConstructionPlan>(`/construction-plans/${planId}`, {
    method: "PUT",
    json: payload,
  });

export const deleteConstructionPlan = (planId: number) =>
  api<{ deleted: number }>(`/construction-plans/${planId}`, { method: "DELETE" });

export const simulateConstructionPlanApi = (
  planId: number,
  payload: { monte_carlo_trials?: number; use_ai?: boolean } = {},
) =>
  api<SimulationResult>(`/construction-plans/${planId}/simulate`, {
    method: "POST",
    json: payload,
  });
