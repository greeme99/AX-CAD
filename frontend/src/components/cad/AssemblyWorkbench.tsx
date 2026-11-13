"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import ThreeViewport from "@/components/cad/ThreeViewport";
import { CopilotToggle, useCopilotHandlers } from "@/components/copilot/CopilotProvider";
import { btn2, btnDanger, btnPrimary, Err, field, Field, link, Loading, Modal } from "@/components/ui";
import { api, can, download, errText, type Doc, type User } from "@/lib/api";
import {
  MATE_LABEL,
  matesFor,
  placementMatrix,
  type AssemblyState,
  type Candidate,
  type Component,
  type Interference,
  type MateType,
  type Placement,
} from "@/lib/cad/assembly";
import type { MeshJson, Sig } from "@/lib/cad/mesh";
import { useApi } from "@/lib/useApi";

type Side = { component_id: number | null; face: Sig | null };
type Picking = { side: "a" | "b"; component_id: number } | null;
const fmt = (v: number) => (Math.abs(v) < 1e-9 ? "0" : v.toFixed(3).replace(/\.?0+$/, ""));

// Track 9 Q2: assembly document — place part bodies, mate faces, check interference (plan.md §15.3)
export default function AssemblyWorkbench({ doc }: { doc: Doc }) {
  const docId = doc.document_id;
  const me = useApi<User>("/auth/me").data;
  const canEdit = !!me && can(me, "DESIGNER");
  const st = useApi<AssemblyState>(`/documents/${docId}/assembly`);
  const [state, setState] = useState<AssemblyState | null>(null);
  useEffect(() => {
    if (st.data) setState(st.data);
  }, [st.data]);
  const [selected, setSelected] = useState<number | null>(null);
  const [meshes, setMeshes] = useState<Record<number, MeshJson>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(false);
  const [mating, setMating] = useState<{ a: Side; b: Side; type: MateType | ""; value: string; flip: boolean } | null>(null);
  const [picking, setPicking] = useState<Picking>(null);
  const [clash, setClash] = useState<Interference | null>(null);

  const comps = useMemo(() => state?.components ?? [], [state]);
  const byId = useMemo(() => new Map(comps.map((c) => [c.component_id, c])), [comps]);
  const sel = selected != null ? byId.get(selected) : undefined;

  // one mesh per part body, shared by every instance
  useEffect(() => {
    const want = [...new Set(comps.flatMap((c) => (c.body_feature_id ? [c.body_feature_id] : [])))].filter((id) => !meshes[id]);
    for (const id of want)
      api<MeshJson>(`/features/${id}/mesh`).then(
        (m) => setMeshes((x) => ({ ...x, [id]: m })),
        (e) => setError(errText(e)),
      );
  }, [comps, meshes]);

  const shown = useMemo(
    () =>
      comps.flatMap((c) => {
        const m = c.body_feature_id ? meshes[c.body_feature_id] : undefined;
        if (!m || (picking && picking.component_id !== c.component_id)) return [];
        return [{ id: c.component_id, mesh: m, matrix: placementMatrix(c.placement) }];
      }),
    [comps, meshes, picking],
  );

  const run = async (fn: () => Promise<AssemblyState | Component | unknown>) => {
    setBusy(true);
    setError(null);
    try {
      const r = await fn();
      if (r && typeof r === "object" && "components" in r) setState(r as AssemblyState);
      else st.reload();
      return true;
    } catch (e) {
      setError(errText(e));
      return false;
    } finally {
      setBusy(false);
    }
  };

  useCopilotHandlers({
    target: (t) => {
      if (t.kind === "feature" || t.kind === "entity") setSelected(Number(t.id));
    },
  });

  const name = (id: number) => byId.get(id)?.name ?? `#${id}`;
  const pickSide = (s: "a" | "b") => mating?.[s].component_id != null && setPicking({ side: s, component_id: mating[s].component_id! });
  const refPick =
    picking && mating
      ? {
          bodyId: picking.component_id,
          mode: "FACE" as const,
          selected: mating[picking.side].face ? [mating[picking.side].face!] : [],
          onToggle: (s: Sig) => {
            setMating((m) => m && { ...m, [picking.side]: { ...m[picking.side], face: s } });
            setPicking(null);
          },
        }
      : null;
  const types = mating ? matesFor(mating.a.face, mating.b.face) : [];

  if (st.error) return <Err text={errText(st.error)} />;
  if (!state) return <Loading />;
  return (
    <div data-mode="assembly" className="flex h-screen flex-col bg-background">
      <header className="flex h-12 shrink-0 items-center gap-4 border-b border-line bg-card px-4 text-sm" style={{ borderBottomColor: "var(--color-mode-accent, var(--color-border))" }}>
        <span className="font-semibold text-foreground">AX-CAD</span>
        <Link href={`/documents/${docId}`} className={link}>
          ← 도면 상세
        </Link>
        <span className="text-foreground">
          <span className="font-mono">{doc.doc_no}</span> {doc.title}
        </span>
        <span className="rounded-md bg-[var(--color-primary-light)] px-3 py-1 font-medium text-[var(--color-primary-hover)]">Assembly</span>
        {me && !canEdit && <span className="text-muted-foreground">읽기 전용</span>}
        <span className="flex-1" />
        <CopilotToggle />
      </header>
      <div className="flex min-h-0 flex-1">
        <aside className="w-72 shrink-0 space-y-3 overflow-y-auto border-r border-line bg-card p-3 text-sm" aria-label="조립 구성">
          <div className="flex flex-wrap gap-2">
            {canEdit && (
              <button type="button" className={btn2} onClick={() => setAdding(true)}>
                부품 추가
              </button>
            )}
            {canEdit && comps.length >= 2 && (
              <button type="button" className={btn2} onClick={() => setMating({ a: { component_id: selected, face: null }, b: { component_id: null, face: null }, type: "", value: "0", flip: false })}>
                메이트 추가
              </button>
            )}
            <button type="button" className={btn2} disabled={busy || comps.length < 2} onClick={() => void run(async () => setClash(await api<Interference>(`/documents/${docId}/interference`, { method: "POST", json: { clearance: 0.5 } })))}>
              간섭 검사
            </button>
            {canEdit && !!state.mates.length && (
              <button type="button" className={btn2} disabled={busy} onClick={() => void run(() => api<AssemblyState>(`/documents/${docId}/assembly/solve`, { method: "POST" }))}>
                다시 풀기
              </button>
            )}
            <button type="button" className={btn2} disabled={!comps.length} onClick={() => download(`/documents/${docId}/assembly/export?format=STEP`, `${doc.doc_no}.step`).catch((e) => setError(errText(e)))}>
              STEP
            </button>
            <Link href={`/documents/${docId}/bom`} className={btn2}>
              BOM
            </Link>
          </div>
          {busy && <p className="text-xs text-muted-foreground">계산 중…</p>}
          <Err text={error} />
          <section aria-label="부품">
            <h2 className="mb-1 font-medium text-foreground">부품 {comps.length}</h2>
            <ul className="space-y-1">
              {comps.map((c) => (
                <li key={c.component_id}>
                  <button
                    type="button"
                    aria-pressed={selected === c.component_id}
                    onClick={() => setSelected(c.component_id)}
                    className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-hover ${selected === c.component_id ? "bg-[var(--color-primary-light)]" : ""}`}
                  >
                    <span className="min-w-0 flex-1 truncate">{c.name}</span>
                    {c.fixed && <span className="rounded bg-[var(--color-bg-subtle)] px-1 text-xs">고정</span>}
                    {c.status === "MISSING" && <span className="rounded bg-red-500/15 px-1 text-xs text-red-700 dark:text-red-300">형상 없음</span>}
                  </button>
                </li>
              ))}
              {!comps.length && <li className="text-muted-foreground">부품 추가로 같은 프로젝트의 부품을 배치하세요.</li>}
            </ul>
          </section>
          <section aria-label="메이트">
            <h2 className="mb-1 font-medium text-foreground">
              메이트 {state.mates.length}
              {state.dof != null && <span className="ml-2 text-xs font-normal text-muted-foreground">남은 자유도 {state.dof}</span>}
            </h2>
            <ul className="space-y-1">
              {state.mates.map((m) => (
                <li key={m.mate_id} className="flex items-center gap-2 rounded-md border border-line px-2 py-1">
                  <span className="min-w-0 flex-1 truncate" title={MATE_LABEL[m.mate_type]}>
                    {m.mate_type} · {name(m.a.component_id)} ↔ {name(m.b.component_id)}
                    {m.value != null && ` (${m.value})`}
                  </span>
                  <span className={`text-xs ${m.status === "FAILED" ? "text-red-600" : "text-[var(--color-success-text)]"}`}>{m.status === "FAILED" ? "충돌" : "충족"}</span>
                  {canEdit && (
                    <button type="button" aria-label="메이트 삭제" className="text-xs text-muted-foreground hover:text-red-600" onClick={() => void run(() => api<AssemblyState>(`/mates/${m.mate_id}`, { method: "DELETE" }))}>
                      ✕
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </section>
          {clash && (
            <section aria-label="간섭 결과" className="space-y-1">
              <h2 className="font-medium text-foreground">간섭 {clash.clashes.length}건 · 근접 {clash.near.length}건</h2>
              {clash.clashes.map((c, i) => (
                <button key={i} type="button" onClick={() => setSelected(c.b)} className="block w-full rounded-md bg-red-500/10 px-2 py-1 text-left text-red-700 dark:text-red-300">
                  {name(c.a)} × {name(c.b)} — {c.volume_mm3.toFixed(1)} mm³
                </button>
              ))}
              {clash.near.map((c, i) => (
                <p key={i} className="px-2 text-xs text-muted-foreground">
                  {name(c.a)} · {name(c.b)} 거리 {fmt(c.distance_mm)} mm {c.distance_mm < 1e-6 ? "(접촉)" : ""}
                </p>
              ))}
              {!clash.clashes.length && <p className="text-[var(--color-success-text)]">간섭 없음 (검사 {clash.pairs_checked}쌍)</p>}
              {clash.truncated && <p className="text-xs text-amber-700 dark:text-amber-300">겹치는 쌍이 많아 {clash.pairs_checked}쌍까지만 검사했습니다(부분 결과). 부품을 나눠 확인하세요.</p>}
            </section>
          )}
        </aside>
        <main className="relative min-w-0 flex-1">
          <ThreeViewport meshes={shown} selectedId={selected} onSelect={setSelected} refPick={refPick} />
          {picking && (
            <p role="status" className="absolute top-2 left-1/2 -translate-x-1/2 rounded-md bg-card px-3 py-1 text-sm shadow">
              {name(picking.component_id)}의 면을 클릭하세요 ·{" "}
              <button type="button" className={link} onClick={() => setPicking(null)}>
                취소
              </button>
            </p>
          )}
        </main>
        {sel && !mating && <PlacementPanel key={`${sel.component_id}-${JSON.stringify(sel.placement)}`} c={sel} canEdit={canEdit} busy={busy} run={run} />}
        {mating && !picking && (
          <aside className="w-80 shrink-0 space-y-3 overflow-y-auto border-l border-line bg-card p-3 text-sm" aria-label="메이트 추가">
            <h2 className="font-medium text-foreground">메이트 추가</h2>
            {(["a", "b"] as const).map((s) => (
              <div key={s} className="space-y-1">
                <Field label={`부품 ${s.toUpperCase()}`}>
                  <select
                    className={field}
                    value={mating[s].component_id ?? ""}
                    onChange={(e) => setMating({ ...mating, [s]: { component_id: e.target.value ? Number(e.target.value) : null, face: null }, type: "" })}
                  >
                    <option value="">선택</option>
                    {comps.map((c) => (
                      <option key={c.component_id} value={c.component_id}>
                        {c.name}
                      </option>
                    ))}
                  </select>
                </Field>
                <button type="button" className={btn2} disabled={mating[s].component_id == null} onClick={() => pickSide(s)}>
                  {mating[s].face ? `면: ${mating[s].face!.kind === "PLANE" ? "평면" : mating[s].face!.kind === "CYLINDER" ? "원통" : mating[s].face!.kind}` : "면 선택"}
                </button>
              </div>
            ))}
            <Field label="종류">
              <select className={field} value={mating.type} onChange={(e) => setMating({ ...mating, type: e.target.value as MateType })}>
                <option value="">{types.length ? "선택" : "두 면을 먼저 고르세요 (평면·평면 또는 원통·원통)"}</option>
                {types.map((t) => (
                  <option key={t} value={t}>
                    {MATE_LABEL[t]}
                  </option>
                ))}
              </select>
            </Field>
            {(mating.type === "DISTANCE" || mating.type === "ANGLE") && (
              <Field label={mating.type === "DISTANCE" ? "거리 (mm)" : "각도 (deg)"}>
                <input className={field} inputMode="decimal" value={mating.value} onChange={(e) => setMating({ ...mating, value: e.target.value })} />
              </Field>
            )}
            {(mating.type === "COINCIDENT" || mating.type === "DISTANCE") && mating.a.face?.kind === "PLANE" && (
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={mating.flip} onChange={(e) => setMating({ ...mating, flip: e.target.checked })} />
                같은 방향 (기본: 면끼리 마주봄)
              </label>
            )}
            <div className="flex justify-end gap-2">
              <button type="button" className={btn2} onClick={() => setMating(null)}>
                취소
              </button>
              <button
                type="button"
                className={btnPrimary}
                disabled={busy || !mating.type || !mating.a.face || !mating.b.face}
                onClick={() =>
                  void run(() =>
                    api<AssemblyState>(`/documents/${docId}/mates`, {
                      method: "POST",
                      json: {
                        mate_type: mating.type,
                        a: { component_id: mating.a.component_id, face: mating.a.face },
                        b: { component_id: mating.b.component_id, face: mating.b.face },
                        value: mating.type === "DISTANCE" || mating.type === "ANGLE" ? Number(mating.value) : null,
                        flip: mating.flip,
                      },
                    }),
                  ).then((ok) => ok && setMating(null))
                }
              >
                추가하고 풀기
              </button>
            </div>
          </aside>
        )}
      </div>
      {adding && <AddDialog docId={docId} onClose={() => setAdding(false)} run={run} />}
    </div>
  );
}

function PlacementPanel({ c, canEdit, busy, run }: { c: Component; canEdit: boolean; busy: boolean; run: (fn: () => Promise<unknown>) => Promise<boolean> }) {
  const [v, setV] = useState({ t: c.placement.t.map(fmt), r: c.placement.r.map(fmt), fixed: c.fixed, name: c.name });
  const [confirm, setConfirm] = useState(false);
  const nums = (xs: string[]) => xs.map(Number) as Placement["t"];
  const valid = [...v.t, ...v.r].every((x) => x.trim() !== "" && Number.isFinite(Number(x)));
  return (
    <aside className="w-72 shrink-0 space-y-3 overflow-y-auto border-l border-line bg-card p-3 text-sm" aria-label="부품 배치">
      <Field label="이름">
        <input className={field} value={v.name} disabled={!canEdit} maxLength={200} onChange={(e) => setV({ ...v, name: e.target.value })} />
      </Field>
      <p className="text-xs text-muted-foreground">원본: {c.source_doc_no} · 형상 #{c.body_feature_id ?? "없음"}</p>
      {(["t", "r"] as const).map((k) => (
        <fieldset key={k} className="space-y-1">
          <legend className="font-medium text-foreground">{k === "t" ? "위치 (mm)" : "회전 X→Y→Z (deg)"}</legend>
          <div className="grid grid-cols-3 gap-1">
            {["X", "Y", "Z"].map((ax, i) => (
              <label key={ax} className="text-xs">
                {ax}
                <input
                  className={field}
                  inputMode="decimal"
                  disabled={!canEdit}
                  value={v[k][i]}
                  onChange={(e) => setV({ ...v, [k]: v[k].map((x, j) => (j === i ? e.target.value : x)) })}
                />
              </label>
            ))}
          </div>
        </fieldset>
      ))}
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={v.fixed} disabled={!canEdit} onChange={(e) => setV({ ...v, fixed: e.target.checked })} />
        고정 (메이트가 움직이지 않음)
      </label>
      {canEdit && (
        <div className="flex gap-2">
          <button
            type="button"
            className={btnPrimary}
            disabled={busy || !valid || !v.name.trim()}
            onClick={() => void run(() => api(`/components/${c.component_id}`, { method: "PATCH", json: { name: v.name.trim(), fixed: v.fixed, placement: { t: nums(v.t), r: nums(v.r) } } }))}
          >
            적용
          </button>
          <button type="button" className={btnDanger} onClick={() => setConfirm(true)}>
            삭제
          </button>
        </div>
      )}
      {confirm && (
        <Modal title="부품 삭제" onClose={() => setConfirm(false)}>
          <p className="mb-4 text-sm">{c.name}과(와) 이 부품의 메이트를 삭제합니다.</p>
          <div className="flex justify-end gap-2">
            <button type="button" className={btn2} onClick={() => setConfirm(false)}>
              취소
            </button>
            <button type="button" className={btnDanger} onClick={() => (setConfirm(false), void run(() => api(`/components/${c.component_id}`, { method: "DELETE" })))}>
              삭제
            </button>
          </div>
        </Modal>
      )}
    </aside>
  );
}

function AddDialog({ docId, onClose, run }: { docId: number; onClose: () => void; run: (fn: () => Promise<unknown>) => Promise<boolean> }) {
  const cands = useApi<{ items: Candidate[]; total: number }>(`/documents/${docId}/assembly/candidates`);
  const [pick, setPick] = useState<number | null>(null);
  const [t, setT] = useState(["0", "0", "0"]);
  return (
    <Modal title="부품 추가" onClose={onClose} wide>
      {cands.error && <Err text={errText(cands.error)} />}
      {!cands.data && !cands.error && <Loading />}
      {cands.data && !cands.data.items.length && <p className="text-sm text-muted-foreground">같은 프로젝트에 3D 형상이 있는 부품 문서가 없습니다.</p>}
      <ul className="max-h-72 space-y-1 overflow-y-auto">
        {cands.data?.items.map((c) => (
          <li key={c.feature_id}>
            <label className={`flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 hover:bg-hover ${pick === c.feature_id ? "bg-[var(--color-primary-light)]" : ""}`}>
              <input type="radio" name="cand" checked={pick === c.feature_id} onChange={() => setPick(c.feature_id)} />
              <span className="font-mono">{c.doc_no}</span>
              <span className="min-w-0 flex-1 truncate">
                {c.title} · {c.name}
              </span>
              <span className="text-xs text-muted-foreground">{c.bbox.min.map((m, i) => fmt(c.bbox.max[i] - m)).join("×")} mm</span>
            </label>
          </li>
        ))}
      </ul>
      <div className="mt-3 grid grid-cols-3 gap-2">
        {["X", "Y", "Z"].map((ax, i) => (
          <Field key={ax} label={`위치 ${ax} (mm)`}>
            <input className={field} inputMode="decimal" value={t[i]} onChange={(e) => setT(t.map((x, j) => (j === i ? e.target.value : x)))} />
          </Field>
        ))}
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <button type="button" className={btn2} onClick={onClose}>
          취소
        </button>
        <button
          type="button"
          className={btnPrimary}
          disabled={pick == null || t.some((x) => !Number.isFinite(Number(x)) || x.trim() === "")}
          onClick={() =>
            void run(() => api(`/documents/${docId}/components`, { method: "POST", json: { source_feature_id: pick, placement: { t: t.map(Number), r: [0, 0, 0] } } })).then(
              (ok) => ok && onClose(),
            )
          }
        >
          추가
        </button>
      </div>
    </Modal>
  );
}
