"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import AppShell, { useMe } from "@/components/shell/AppShell";
import { btn2, btnDanger, btnPrimary, card, Err, field, Field, link, Loading, td, th } from "@/components/ui";
import { can, download, errText } from "@/lib/api";
import { buildScurvePolyline, formatKrw, ganttBarRect } from "@/lib/cad/constructionSim";
import {
  createConstructionPlan,
  deleteConstructionPlan,
  listConstructionPlans,
  listConstructionTemplates,
  simulateConstructionPlanApi,
  updateConstructionPlan,
  type ConstructionPlan,
  type ConstructionTemplate,
  type DomainCode,
  type SimulationResult,
  type TaskRow,
  type WorkMode,
} from "@/lib/constructionSimApi";

const WORK_MODES: { value: WorkMode; label: string }[] = [
  { value: "NEW_BUILD", label: "신축 · 증축" },
  { value: "INSTALL", label: "신규 설치" },
  { value: "REPLACE", label: "철거 후 교체" },
  { value: "DEMOLISH", label: "해체 · 철거" },
  { value: "RENOVATE", label: "인테리어 리노베이션 · 개조" },
];

const STMT_LABELS: [string, string][] = [
  ["direct_material_krw", "직접재료비"],
  ["direct_labor_krw", "직접노무비"],
  ["indirect_labor_krw", "간접노무비"],
  ["direct_equipment_krw", "직접경비 (장비)"],
  ["indirect_expense_krw", "간접공사비"],
  ["net_construction_cost_krw", "순공사원가"],
  ["general_admin_krw", "일반관리비"],
  ["profit_krw", "이윤"],
  ["supply_amount_krw", "공급가액"],
  ["vat_krw", "부가가치세 (10%)"],
  ["grand_total_krw", "총공사비 (VAT 포함)"],
];

function Studio({ projectId }: { projectId: string }) {
  const me = useMe();
  const canEdit = can(me, "DESIGNER", "ESTIMATOR");
  const [templates, setTemplates] = useState<ConstructionTemplate[]>([]);
  const [plans, setPlans] = useState<ConstructionPlan[] | null>(null);
  const [active, setActive] = useState<ConstructionPlan | null>(null);
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [sim, setSim] = useState<SimulationResult | null>(null);
  const [expandedTask, setExpandedTask] = useState<string | null>(null);

  // Create form
  const [name, setName] = useState("냉난방기 교체 공사 1안");
  const [domain, setDomain] = useState<DomainCode>("MEP_HVAC");
  const [workMode, setWorkMode] = useState<WorkMode>("REPLACE");
  const [scaleQty, setScaleQty] = useState("4");

  const [useAi, setUseAi] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadAll = async () => {
    try {
      const [tpls, list] = await Promise.all([listConstructionTemplates(), listConstructionPlans(projectId)]);
      setTemplates(tpls);
      setPlans(list);
      if (list.length > 0 && !active) {
        selectPlan(list[0]);
      }
    } catch (e) {
      setError(errText(e));
    }
  };

  useEffect(() => {
    void loadAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const selectPlan = (p: ConstructionPlan) => {
    setActive(p);
    setTasks(p.tasks);
    setSim(p.last_simulation);
    setExpandedTask(null);
    setError(null);
  };

  const onCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    const qty = Number(scaleQty);
    if (!name.trim() || !Number.isFinite(qty) || qty <= 0) {
      return setError("계획명과 0보다 큰 규모(면적/대수)를 입력하세요.");
    }
    setBusy(true);
    setError(null);
    try {
      const created = await createConstructionPlan(projectId, {
        name: name.trim(),
        domain,
        work_mode: workMode,
        scale_qty: qty,
      });
      const res = await simulateConstructionPlanApi(created.plan_id, { use_ai: useAi });
      const updated: ConstructionPlan = { ...created, last_simulation: res };
      setPlans((prev) => [updated, ...(prev ?? [])]);
      selectPlan(updated);
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  };

  const onSaveAndSimulate = async () => {
    if (!active) return;
    setBusy(true);
    setError(null);
    try {
      if (canEdit) {
        await updateConstructionPlan(active.plan_id, { tasks });
      }
      const res = await simulateConstructionPlanApi(active.plan_id, { use_ai: useAi });
      setSim(res);
      setPlans((prev) =>
        (prev ?? []).map((p) => (p.plan_id === active.plan_id ? { ...p, tasks, last_simulation: res } : p)),
      );
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  };

  const onDelete = async (planId: number) => {
    setBusy(true);
    setError(null);
    try {
      await deleteConstructionPlan(planId);
      setPlans((prev) => {
        const next = (prev ?? []).filter((p) => p.plan_id !== planId);
        if (active?.plan_id === planId) {
          if (next[0]) selectPlan(next[0]);
          else {
            setActive(null);
            setTasks([]);
            setSim(null);
          }
        }
        return next;
      });
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  };

  const patchTask = (idx: number, patch: Partial<TaskRow>) => {
    setTasks((prev) => prev.map((t, i) => (i === idx ? { ...t, ...patch } : t)));
  };

  const activeTpl = templates.find((t) => t.domain === domain);
  const scurvePoints = sim ? buildScurvePolyline(sim.scurve, 520, 140) : "";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="space-y-0.5">
          <Link href={`/projects/${encodeURIComponent(projectId)}`} className={link}>
            ← 프로젝트 도면 목록
          </Link>
          <h1 className="text-xl font-semibold text-foreground">공사 공정 · 재료 소요량 · 5D 비용 시뮬레이션</h1>
          <p className="text-sm text-muted-foreground">
            건축·토목·조경·전기·배관·공조(냉난방기)·인테리어 리노베이션의 공정 절차, 재료 BOM, CPM 주공정, 3대 시나리오 및 몬테카를로 원가 리스크를 시뮬레이션합니다.
          </p>
        </div>
      </div>

      <Err text={error} />

      {canEdit && (
        <form onSubmit={onCreate} className={`${card} grid grid-cols-1 gap-3 md:grid-cols-5`}>
          <Field label="시뮬레이션 계획명">
            <input value={name} onChange={(e) => setName(e.target.value)} className={field} required />
          </Field>
          <Field label="공사 공종 (6대 표준)">
            <select
              value={domain}
              onChange={(e) => {
                const d = e.target.value as DomainCode;
                setDomain(d);
                if (d === "MEP_HVAC") setScaleQty("4");
                else if (scaleQty === "4") setScaleQty("100");
              }}
              className={field}
            >
              {templates.map((t) => (
                <option key={t.domain} value={t.domain}>
                  {t.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="작업 유형">
            <select value={workMode} onChange={(e) => setWorkMode(e.target.value as WorkMode)} className={field}>
              {WORK_MODES.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label={`규모 (${activeTpl?.default_unit ?? "m2"})`}>
            <input type="number" min="1" step="any" value={scaleQty} onChange={(e) => setScaleQty(e.target.value)} className={`${field} font-mono`} />
          </Field>
          <div className="flex items-end">
            <button type="submit" disabled={busy} className={`${btnPrimary} w-full`}>
              {busy ? "생성·시뮬레이션 중..." : "새 공정 계획 생성"}
            </button>
          </div>
        </form>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[240px_1fr]">
        <aside className={`${card} space-y-2`}>
          <h2 className="text-sm font-semibold text-foreground">저장된 공사 계획</h2>
          {plans === null ? (
            <Loading />
          ) : plans.length === 0 ? (
            <p className="text-xs text-muted-foreground">위에서 공종 템플릿을 선택해 새 계획을 생성하세요.</p>
          ) : (
            <ul className="space-y-1.5">
              {plans.map((p) => (
                <li
                  key={p.plan_id}
                  className={`flex items-center justify-between gap-1 rounded border p-2 text-xs ${
                    active?.plan_id === p.plan_id ? "border-[var(--color-primary)] bg-[var(--color-primary-light)]/40" : "border-line"
                  }`}
                >
                  <button type="button" onClick={() => selectPlan(p)} className="min-w-0 flex-1 text-left">
                    <p className="truncate font-medium text-foreground">{p.name}</p>
                    <p className="text-muted-foreground">
                      {p.domain} · {p.work_mode} · {p.scale_qty} {p.scale_unit}
                    </p>
                  </button>
                  {canEdit && (
                    <button type="button" onClick={() => void onDelete(p.plan_id)} className={`${btnDanger} px-1.5 py-0.5 text-[11px]`}>
                      삭제
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </aside>

        {active ? (
          <div className="space-y-4">
            {/* Control bar */}
            <div className={`${card} flex flex-wrap items-center justify-between gap-3`}>
              <div>
                <h2 className="text-base font-semibold text-foreground">{active.name}</h2>
                <p className="text-xs text-muted-foreground">
                  {active.domain} · {active.work_mode} · 규모 {active.scale_qty} {active.scale_unit}
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <label className="flex items-center gap-1.5 text-xs">
                  <input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} />
                  AI 리스크 조언 (Antigravity SDK)
                </label>
                <button type="button" disabled={busy} onClick={() => void onSaveAndSimulate()} className={btnPrimary}>
                  {busy ? "시뮬레이션 중..." : "저장 및 공정·비용 시뮬레이션 실행"}
                </button>
                <button
                  type="button"
                  onClick={() => void download(`/construction-plans/${active.plan_id}/export?format=csv`, `construction_${active.plan_id}.csv`)}
                  className={btn2}
                >
                  CSV 내역서
                </button>
                <button
                  type="button"
                  onClick={() => void download(`/construction-plans/${active.plan_id}/export?format=json`, `construction_${active.plan_id}.json`)}
                  className={btn2}
                >
                  JSON
                </button>
              </div>
            </div>

            {/* Editable WBS & Procedures */}
            <section className={`${card} space-y-2 overflow-x-auto`}>
              <h3 className="text-sm font-semibold text-foreground">1. 공정 프로세스 · 공사절차 · 투입비 (WBS 편집)</h3>
              <table className="w-full text-xs">
                <thead>
                  <tr>
                    <th className={th}>WBS</th>
                    <th className={th}>공정명</th>
                    <th className={th}>단계</th>
                    <th className={th}>기간(일)</th>
                    <th className={th}>선행</th>
                    <th className={th}>노무비(원)</th>
                    <th className={th}>장비경비(원)</th>
                    <th className={th}>절차</th>
                  </tr>
                </thead>
                <tbody>
                  {tasks.map((t, idx) => (
                    <tr key={t.task_id} className="border-t border-line">
                      <td className={`${td} font-mono`}>{t.wbs}</td>
                      <td className={td}>
                        <input
                          value={t.name}
                          disabled={!canEdit}
                          onChange={(e) => patchTask(idx, { name: e.target.value })}
                          className="w-full min-w-[160px] rounded border border-line bg-background px-1.5 py-0.5"
                        />
                        {expandedTask === t.task_id && (
                          <ol className="mt-1.5 list-decimal space-y-0.5 pl-4 text-[11px] text-muted-foreground">
                            {t.procedures.map((proc, pi) => (
                              <li key={pi}>{proc}</li>
                            ))}
                          </ol>
                        )}
                      </td>
                      <td className={td}>{t.phase}</td>
                      <td className={td}>
                        <input
                          type="number"
                          min="0.5"
                          step="0.5"
                          disabled={!canEdit}
                          value={t.duration_days}
                          onChange={(e) => patchTask(idx, { duration_days: Number(e.target.value) || 0.5 })}
                          className="w-16 rounded border border-line bg-background px-1 py-0.5 font-mono"
                        />
                      </td>
                      <td className={`${td} font-mono text-muted-foreground`}>{t.predecessors.join(", ") || "-"}</td>
                      <td className={td}>
                        <input
                          type="number"
                          min="0"
                          step="10000"
                          disabled={!canEdit}
                          value={t.labor_cost_krw}
                          onChange={(e) => patchTask(idx, { labor_cost_krw: Number(e.target.value) || 0 })}
                          className="w-28 rounded border border-line bg-background px-1 py-0.5 text-right font-mono"
                        />
                      </td>
                      <td className={td}>
                        <input
                          type="number"
                          min="0"
                          step="10000"
                          disabled={!canEdit}
                          value={t.equipment_cost_krw}
                          onChange={(e) => patchTask(idx, { equipment_cost_krw: Number(e.target.value) || 0 })}
                          className="w-24 rounded border border-line bg-background px-1 py-0.5 text-right font-mono"
                        />
                      </td>
                      <td className={td}>
                        <button
                          type="button"
                          onClick={() => setExpandedTask((cur) => (cur === t.task_id ? null : t.task_id))}
                          className={btn2}
                        >
                          {expandedTask === t.task_id ? "접기" : `절차(${t.procedures.length})`}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>

            {sim && (
              <>
                {/* 3-Scenario Comparison + Monte Carlo */}
                <section className="grid grid-cols-1 gap-3 md:grid-cols-3">
                  {sim.scenarios.map((sc) => (
                    <div key={sc.code} className={`${card} space-y-1`}>
                      <p className="text-xs font-semibold text-muted-foreground">{sc.name}</p>
                      <p className="text-lg font-bold font-mono text-foreground">{formatKrw(sc.grand_total_krw)}</p>
                      <p className="text-xs text-foreground">
                        총 공기 <strong>{sc.duration_days}일</strong>
                        {sc.delta_days !== 0 && ` (${sc.delta_days > 0 ? "+" : ""}${sc.delta_days}일)`}
                      </p>
                      {sc.delta_cost_krw !== 0 && (
                        <p className={`text-xs font-mono ${sc.delta_cost_krw > 0 ? "text-red-600" : "text-emerald-600"}`}>
                          기준 대비 {sc.delta_cost_krw > 0 ? "+" : ""}
                          {formatKrw(sc.delta_cost_krw)}
                        </p>
                      )}
                      <p className="text-[11px] text-muted-foreground">{sc.note}</p>
                    </div>
                  ))}
                </section>

                {/* Monte Carlo Risk & AI Advisor */}
                <section className={`${card} space-y-2`}>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h3 className="text-sm font-semibold text-foreground">
                      2. 몬테카를로 리스크 분포 ({sim.monte_carlo.trials.toLocaleString()}회) & 공정 조언
                    </h3>
                    <span className="font-mono text-xs text-muted-foreground">
                      주공정(Critical Path): {sim.critical_path.join(" → ")} ({sim.project_duration_days}일)
                    </span>
                  </div>
                  <div className="grid grid-cols-3 gap-3 text-xs">
                    <div className="rounded border border-line p-2">
                      <p className="text-muted-foreground">P50 (중앙값)</p>
                      <p className="font-mono text-sm font-semibold">{formatKrw(sim.monte_carlo.cost_p50_krw)}</p>
                      <p className="text-muted-foreground">{sim.monte_carlo.duration_p50_days}일</p>
                    </div>
                    <div className="rounded border border-line p-2">
                      <p className="text-muted-foreground">P80 (권장 예산)</p>
                      <p className="font-mono text-sm font-semibold">{formatKrw(sim.monte_carlo.cost_p80_krw)}</p>
                      <p className="text-muted-foreground">{sim.monte_carlo.duration_p80_days}일</p>
                    </div>
                    <div className="rounded border border-amber-500/40 bg-amber-500/10 p-2">
                      <p className="text-amber-900 dark:text-amber-200">P95 (보수적 상한)</p>
                      <p className="font-mono text-sm font-semibold">{formatKrw(sim.monte_carlo.cost_p95_krw)}</p>
                      <p className="text-muted-foreground">{sim.monte_carlo.duration_p95_days}일</p>
                    </div>
                  </div>
                  {sim.advisor && (
                    <p role="status" className="rounded-md bg-[var(--color-primary-light)]/50 p-2.5 text-xs text-foreground">
                      <strong>[{sim.advisor.provider}]</strong> {sim.advisor.advice}
                    </p>
                  )}
                </section>

                {/* CPM Gantt + S-Curve */}
                <section className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                  <div className={`${card} space-y-2`}>
                    <h3 className="text-sm font-semibold text-foreground">3. CPM 간트 타임라인 (주공정 빨강)</h3>
                    <div className="space-y-1.5">
                      {sim.tasks.map((t) => {
                        const rect = ganttBarRect(t.early_start ?? 0, t.early_finish ?? 1, sim.project_duration_days);
                        return (
                          <div key={t.task_id} className="grid grid-cols-[120px_1fr_54px] items-center gap-2 text-xs">
                            <span className="truncate font-medium" title={t.name}>
                              {t.wbs} {t.name}
                            </span>
                            <div className="relative h-4 rounded bg-muted/50">
                              <div
                                className={`absolute top-0.5 h-3 rounded ${t.is_critical ? "bg-red-500" : "bg-[var(--color-primary)]"}`}
                                style={{ left: `${rect.leftPct}%`, width: `${rect.widthPct}%` }}
                                title={`ES:${t.early_start} ~ EF:${t.early_finish} (여유 ${t.total_float}일)`}
                              />
                            </div>
                            <span className="text-right font-mono text-[11px] text-muted-foreground">
                              {t.early_start}~{t.early_finish}일
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  </div>

                  <div className={`${card} space-y-2`}>
                    <h3 className="text-sm font-semibold text-foreground">4. 누적 원가 S-Curve (일별 현금흐름)</h3>
                    <svg viewBox="0 0 520 140" className="h-36 w-full rounded border border-line bg-background">
                      <polyline fill="none" stroke="var(--color-primary, #2563eb)" strokeWidth="2.5" points={scurvePoints} />
                    </svg>
                    <p className="text-xs text-muted-foreground">
                      1일차 ~ {sim.scurve.length}일차 누적 {formatKrw(sim.cost_statement.grand_total_krw)} (100%)
                    </p>
                  </div>
                </section>

                {/* Korean Cost Statement + Material BOM */}
                <section className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                  <div className={`${card} space-y-2`}>
                    <h3 className="text-sm font-semibold text-foreground">5. 한국표준 공사원가계산서</h3>
                    <table className="w-full text-xs">
                      <tbody>
                        {STMT_LABELS.map(([k, lbl]) => (
                          <tr key={k} className={`border-t border-line ${k === "grand_total_krw" ? "font-bold text-foreground" : ""}`}>
                            <td className={td}>{lbl}</td>
                            <td className={`${td} text-right font-mono`}>{formatKrw(sim.cost_statement[k] ?? 0)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  <div className={`${card} space-y-2 overflow-x-auto`}>
                    <h3 className="text-sm font-semibold text-foreground">6. 재료 소요량 집계표 (BOM · 손실률 반영)</h3>
                    <table className="w-full text-xs">
                      <thead>
                        <tr>
                          <th className={th}>품명</th>
                          <th className={th}>단위</th>
                          <th className={th}>정미</th>
                          <th className={th}>할증포함</th>
                          <th className={th}>단가</th>
                          <th className={th}>금액</th>
                        </tr>
                      </thead>
                      <tbody>
                        {sim.material_bom.map((m) => (
                          <tr key={m.code} className="border-t border-line">
                            <td className={td}>{m.name}</td>
                            <td className={td}>{m.unit}</td>
                            <td className={`${td} text-right font-mono`}>{m.net_qty}</td>
                            <td className={`${td} text-right font-mono`}>{m.gross_qty}</td>
                            <td className={`${td} text-right font-mono`}>{m.unit_price_krw.toLocaleString()}</td>
                            <td className={`${td} text-right font-mono font-medium`}>{formatKrw(m.total_krw)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              </>
            )}
          </div>
        ) : (
          <div className={`${card} flex items-center justify-center p-10 text-sm text-muted-foreground`}>
            상단 폼에서 공종과 규모를 선택해 첫 번째 공정·비용 시뮬레이션을 생성하세요.
          </div>
        )}
      </div>
    </div>
  );
}

export default function ConstructionSimPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = use(params);
  return (
    <AppShell title="공사 공정 · 비용 시뮬레이션">
      <Studio projectId={projectId} />
    </AppShell>
  );
}
