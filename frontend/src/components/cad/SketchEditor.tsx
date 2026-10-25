"use client";

// Track 8 P2-1 sketch mode in the 3D view (FreeCAD Sketcher): draw on the plane with snaps that
// become constraints, add constraints to the selection, the server solves after every change.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Overlay, OverlayColor } from "@/components/cad/ThreeViewport";
import { btn2, btnPrimary, Err } from "@/components/ui";
import { api, errText } from "@/lib/api";
import { toPaths } from "@/lib/cad/geom";
import {
  addConstraint,
  build,
  C_LABEL,
  constraintLabel,
  geomOf,
  hasProfile,
  makeConstraint,
  pickRef,
  planeName,
  pointOf,
  preview,
  refAt,
  removeEntities,
  snapEntities,
  TOOL_PICKS,
  VALUED,
  type CType,
  type Pick,
  type Plane,
  type SketchParams,
  type SkConstraint,
  type SkEntity,
  type SkRef,
  type SkTool,
  type SolveResult,
} from "@/lib/cad/sketch";
import { buildSnapIndex, querySnap, type SnapKind } from "@/lib/cad/snap";
import type { Pt } from "@/lib/cad/view";

export type SketchSession = {
  featureId: number | null;
  plane: Plane;
  entities: SkEntity[];
  constraints: SkConstraint[];
};
type Draft = { entities: SkEntity[]; constraints: SkConstraint[] };

const TOOLS: [SkTool, string, string][] = [
  ["LINE", "선", "L"],
  ["RECT", "사각형", "R"],
  ["CIRCLE", "원", "C"],
  ["ARC", "호", "A"],
  ["POINT", "점", "P"],
];
const CMDS: CType[] = ["COINCIDENT", "POINT_ON_OBJECT", "HORIZONTAL", "VERTICAL", "PARALLEL", "PERPENDICULAR", "TANGENT", "CONCENTRIC", "EQUAL", "SYMMETRIC", "FIXED", "DISTANCE", "DISTANCE_X", "DISTANCE_Y", "RADIUS", "DIAMETER", "ANGLE"];
const SNAPS = new Set<SnapKind>(["END", "CEN", "NEA"]);
const SNAP_CELL = 2; // mm buckets for the shared snap index
const MAX_UNDO = 100;
const sameRef = (a: SkRef, b: SkRef) => a.e === b.e && a.p === b.p;

function askValue(c: SkConstraint): number | null {
  const unit = c.type === "ANGLE" ? "°" : "mm";
  const raw = window.prompt(`${C_LABEL[c.type]} (${unit})`, String(c.value ?? 0));
  if (raw === null) return null;
  const v = Number(raw);
  return Number.isFinite(v) && Math.abs(v) <= 1e6 && (c.type === "DISTANCE_X" || c.type === "DISTANCE_Y" || c.type === "ANGLE" || v >= 0) ? v : NaN;
}

/** State and handlers of one sketch editing session; `overlay`/`onSketch` go to the 3D viewport. */
export function useSketchEditor(session: SketchSession, onSaved: (featureId: number) => void, docId: string) {
  const [draft, setDraft] = useState<Draft>({
    entities: session.entities,
    constraints: session.constraints,
  });
  const [undo, setUndo] = useState<Draft[]>([]);
  const [tool, setTool] = useState<SkTool | null>(null);
  const [picks, setPicks] = useState<Pick[]>([]);
  const [cursor, setCursor] = useState<Pt | null>(null);
  const [selection, setSelection] = useState<SkRef[]>([]);
  const [construction, setConstruction] = useState(false);
  const [result, setResult] = useState<SolveResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const seq = useRef(0);
  const { entities, constraints } = draft;

  const solve = useCallback((d: Draft) => {
    const n = ++seq.current; // only the latest answer counts
    api<SolveResult>("/sketches/solve", { method: "POST", json: d }).then(
      (r) => {
        if (n !== seq.current) return;
        setResult(r);
        if (r.status !== "CONFLICT") setDraft({ entities: r.entities, constraints: d.constraints });
      },
      (e) => n === seq.current && setError(errText(e)),
    );
  }, []);
  useEffect(() => solve({ entities: session.entities, constraints: session.constraints }), [solve, session]);

  const commit = (d: Draft) => {
    setUndo((u) => [...u.slice(-MAX_UNDO + 1), draft]);
    setDraft(d);
    setError(null);
    solve(d);
  };
  const back = () => {
    const prev = undo.at(-1);
    if (!prev) return;
    setUndo((u) => u.slice(0, -1));
    setDraft(prev);
    setSelection([]);
    solve(prev);
  };
  const cancelTool = () => {
    setPicks([]);
    setTool(null);
  };

  const index = useMemo(() => buildSnapIndex(snapEntities(entities), SNAP_CELL), [entities]);
  const snap = (p: Pt, tol: number) => {
    const hit = querySnap(index, p, tol, SNAPS, 0);
    return { p: hit ? ([hit.x, hit.y] as Pt) : p, ...refAt(entities, hit) };
  };

  const onSketch = (kind: "move" | "click", raw: Pt, tol: number) => {
    if (!tool) {
      if (kind !== "click") return;
      const r = pickRef(entities, raw, tol);
      setSelection((s) => (!r ? [] : s.some((x) => sameRef(x, r)) ? s.filter((x) => !sameRef(x, r)) : [...s, r]));
      return;
    }
    const pick = snap(raw, tol);
    setCursor(pick.p);
    if (kind !== "click") return;
    const all = [...picks, pick];
    if (all.length < TOOL_PICKS[tool]) return setPicks(all);
    const made = build(tool, all, entities, construction);
    if (!made) return setPicks([]);
    commit({
      entities: [...entities, ...made.entities],
      constraints: [...constraints, ...made.constraints],
    });
    const last = made.entities.at(-1)!;
    // polyline: the next line starts where this one ended (FreeCAD continuous mode)
    setPicks(tool === "LINE" && last.type === "LINE" ? [{ p: last.end, at: { e: last.id, p: "end" } }] : []);
  };

  const constrain = (type: CType) => {
    const c = makeConstraint(type, selection, entities);
    if (typeof c === "string") return setError(c);
    if (VALUED.includes(type)) {
      const v = askValue(c);
      if (v === null) return;
      if (Number.isNaN(v)) return setError("값을 숫자로 입력하세요 (거리·반지름은 0 이상).");
      c.value = v;
    }
    commit({ entities, constraints: addConstraint(constraints, c) });
    setSelection([]);
  };
  const editValue = (i: number) => {
    const v = askValue(constraints[i]);
    if (v === null) return;
    if (Number.isNaN(v)) return setError("값을 숫자로 입력하세요 (거리·반지름은 0 이상).");
    commit({
      entities,
      constraints: constraints.map((c, k) => (k === i ? { ...c, value: v } : c)),
    });
  };
  const dropConstraint = (i: number) => commit({ entities, constraints: constraints.filter((_, k) => k !== i) });
  const dropSelected = () => {
    const ids = new Set(selection.filter((r) => r.p === "edge" || entities.find((e) => e.id === r.e)?.type === "POINT").map((r) => r.e));
    if (!ids.size) return setError("지울 선·원·호·점을 선택하세요 (끝점이 아니라 선 자체를 클릭).");
    commit(removeEntities(draft, ids));
    setSelection([]);
  };
  const toggleConstruction = () => {
    const ids = new Set(selection.filter((r) => r.p === "edge").map((r) => r.e));
    if (!ids.size) return setConstruction((c) => !c); // no selection: the mode for new geometry
    commit({
      entities: entities.map((e) => (ids.has(e.id) ? { ...e, construction: !e.construction || undefined } : e)),
      constraints,
    });
  };

  async function save() {
    setBusy(true);
    setError(null);
    const params: SketchParams = {
      plane: session.plane,
      entities,
      constraints,
    };
    try {
      const f = session.featureId
        ? await api<{ feature_id: number }>(`/features/${session.featureId}`, {
            method: "PATCH",
            json: { params },
          })
        : await api<{ feature_id: number }>(`/documents/${encodeURIComponent(docId)}/features`, { method: "POST", json: { feature_type: "SKETCH", params } });
      onSaved(f.feature_id);
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  // keyboard: tool letters, Esc steps back out, Delete removes, Ctrl+Z undoes
  const keys = useRef<(e: KeyboardEvent) => void>(() => {});
  keys.current = (e: KeyboardEvent) => {
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") return (e.preventDefault(), back());
    if (e.key === "Escape") return picks.length ? setPicks([]) : tool ? cancelTool() : setSelection([]);
    if (e.key === "Delete") return dropSelected();
    const t = TOOLS.find(([, , k]) => k === e.key.toUpperCase());
    if (t && !e.ctrlKey && !e.metaKey && !e.altKey) {
      setTool(t[0]);
      setPicks([]);
    }
  };
  useEffect(() => {
    const h = (e: KeyboardEvent) => keys.current(e);
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  const conflictIds = useMemo(() => new Set((result?.conflicting ?? []).flatMap((i) => constraints[i]?.refs.map((r) => r.e) ?? [])), [result, constraints]);
  const full = result?.status === "OK" && result.dof === 0 && entities.length > 0;
  const overlay = useMemo((): Overlay => {
    const color = (e: SkEntity): OverlayColor => (selection.some((r) => r.e === e.id && r.p === "edge") ? "selected" : conflictIds.has(e.id) ? "conflict" : e.construction ? "construction" : full ? "done" : "line");
    const paths = entities.flatMap((e) => toPaths(geomOf(e)).map((pts) => ({ pts, color: color(e) })));
    if (tool && cursor) paths.push(...preview(tool, picks, cursor).flatMap((g) => toPaths(g).map((pts) => ({ pts, color: "preview" as const }))));
    const points: Overlay["points"] = [
      {
        p: [0, 0],
        color: selection.some((r) => r.e === -1 && r.p === "start") ? "selected" : "shown",
      },
    ];
    for (const e of entities)
      for (const p of e.type === "LINE" ? (["start", "end"] as const) : e.type === "ARC" ? (["start", "end", "center"] as const) : (["center"] as const)) {
        const q = pointOf(entities, { e: e.id, p });
        if (q)
          points.push({
            p: q,
            color: selection.some((r) => r.e === e.id && r.p === p) ? "selected" : e.construction ? "construction" : full ? "done" : "line",
          });
      }
    if (tool && cursor) points.push({ p: cursor, color: "preview" });
    // the axes, as FreeCAD draws them: long thin lines through the origin
    const L = 1e4;
    paths.push({
      pts: [
        [-L, 0],
        [L, 0],
      ],
      color: selection.some((r) => r.e === -1 && r.p === "edge") ? "selected" : "shown",
    });
    paths.push({
      pts: [
        [0, -L],
        [0, L],
      ],
      color: selection.some((r) => r.e === -2 && r.p === "edge") ? "selected" : "shown",
    });
    return { plane: session.plane, paths, points };
  }, [entities, selection, conflictIds, full, tool, cursor, picks, session.plane]);

  const status = !result ? "풀이 중..." : !entities.length ? "빈 스케치" : result.status === "CONFLICT" ? "구속 충돌" : full ? "완전 구속" : `자유도 ${result.dof}${result.status === "REDUNDANT" ? " · 중복 구속 있음" : ""}`;
  const badge = !result ? "bg-muted text-muted-foreground" : result.status === "CONFLICT" ? "bg-red-100 text-red-700" : full ? "bg-snap/15 text-[var(--color-success-text)]" : "bg-amber-50 text-amber-800";

  const panel = (
    <div className="space-y-3 text-sm">
      <div className="flex items-center justify-between gap-2">
        <h2 className="font-semibold text-foreground">스케치 편집</h2>
        <span role="status" className={`rounded-full px-2 py-0.5 text-xs ${badge}`}>
          {status}
        </span>
      </div>
      <p className="text-xs text-muted-foreground">{planeName(session.plane)} · 우 드래그 이동 · 휠 확대/축소 · Esc 취소 · Delete 삭제 · Ctrl+Z 실행 취소</p>
      <section aria-label="작도 도구" className="space-y-1">
        <div className="flex flex-wrap gap-1">
          <button type="button" aria-pressed={!tool} onClick={cancelTool} className={btn2}>
            선택
          </button>
          {TOOLS.map(([t, label, k]) => (
            <button key={t} type="button" aria-pressed={tool === t} title={`${label} (${k})`} onClick={() => (setTool(t), setPicks([]))} className={`${btn2} ${tool === t ? "ring-2 ring-[var(--color-primary)]" : ""}`}>
              {label}
            </button>
          ))}
          <button
            type="button"
            aria-pressed={construction}
            title="선택한 요소를 보조선으로, 선택이 없으면 새로 그릴 요소를 보조선으로"
            onClick={toggleConstruction}
            className={`${btn2} ${construction ? "ring-2 ring-[var(--color-primary)]" : ""}`}
          >
            보조선
          </button>
        </div>
        <p className="text-xs text-muted-foreground">
          {tool
            ? `${TOOLS.find((t) => t[0] === tool)![1]}: ${tool === "ARC" ? "중심 → 시작점 → 끝점(반시계)" : tool === "CIRCLE" ? "중심 → 반지름 점" : tool === "RECT" ? "모서리 → 반대 모서리" : tool === "LINE" ? "점을 차례로 클릭 (Esc로 끊기)" : "위치 클릭"} · 끝점·중심에 붙으면 일치, 선 위면 점-선 위 구속이 붙습니다`
            : `선택됨 ${selection.length}개 — 요소를 클릭해 고른 뒤 구속을 누르세요`}
        </p>
      </section>
      <section aria-label="구속" className="flex flex-wrap gap-1">
        {CMDS.map((c) => (
          <button key={c} type="button" disabled={!selection.length} onClick={() => constrain(c)} className={`${btn2} px-2 text-xs`}>
            {C_LABEL[c]}
          </button>
        ))}
      </section>
      <div className="flex gap-1">
        <button type="button" disabled={!undo.length} onClick={back} className={btn2}>
          실행 취소
        </button>
        <button type="button" disabled={!selection.length} onClick={dropSelected} className={btn2}>
          선택 삭제
        </button>
      </div>
      <section aria-label="구속 목록" className="space-y-1">
        <h3 className="font-semibold text-foreground">구속 {constraints.length}개</h3>
        <ul className="max-h-64 space-y-0.5 overflow-y-auto">
          {constraints.map((c, i) => {
            const bad = result?.conflicting.includes(i);
            const dup = result?.redundant.includes(i);
            return (
              <li key={i} className={`flex items-center gap-1 rounded px-1 text-xs ${bad ? "bg-red-50 text-red-700" : dup ? "bg-amber-50 text-amber-800" : "text-body"}`}>
                <button
                  type="button"
                  onClick={() => setSelection(c.refs)}
                  onDoubleClick={() => c.value !== undefined && editValue(i)}
                  title={c.value !== undefined ? "더블클릭: 값 수정" : undefined}
                  className="min-w-0 flex-1 truncate text-left"
                >
                  {bad ? "⚠ " : dup ? "≡ " : ""}
                  {constraintLabel(c)}
                </button>
                <button type="button" aria-label={`${constraintLabel(c)} 삭제`} onClick={() => dropConstraint(i)} className="px-1 text-muted-foreground hover:text-red-600">
                  ×
                </button>
              </li>
            );
          })}
        </ul>
      </section>
      <Err text={error} />
      <div className="flex justify-end gap-2 border-t border-line pt-3">
        <button
          type="button"
          disabled={busy || result?.status === "CONFLICT" || !hasProfile(entities)}
          title={result?.status === "CONFLICT" ? "충돌하는 구속을 먼저 정리하세요" : undefined}
          onClick={() => void save()}
          className={btnPrimary}
        >
          {busy ? "저장 중..." : "스케치 닫기(저장)"}
        </button>
      </div>
    </div>
  );
  return { overlay, onSketch, panel };
}
