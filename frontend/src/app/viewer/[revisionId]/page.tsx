"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, use, useEffect, useMemo, useRef, useState } from "react";
import MetricsDialog from "@/components/cad/MetricsDialog";
import CanvasViewport, { type RenderEntity } from "@/components/cad/CanvasViewport";
import CommandPrompt from "@/components/cad/CommandPrompt";
import PropertyInspector from "@/components/cad/PropertyInspector";
import ToolPalette from "@/components/cad/ToolPalette";
import { api, can, download, errText, type Doc, type User } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { diffEdits, isEmptyEdit } from "@/lib/cad/edits";
import { distToPaths, hitPaths, type Geom } from "@/lib/cad/geom";
import { initHistory, push, redo, undo, type History } from "@/lib/cad/history";
import { parseInput, preview, start, step, type Result, type Sel, type Tool, type ToolEvent, type ToolState } from "@/lib/cad/tools";
import type { SnapKind } from "@/lib/cad/snap";
import { effectiveGridStep } from "@/lib/cad/snap";
import type { Extents, Pt } from "@/lib/cad/view";

type Layer = { name: string; color: string | null; visible: boolean; locked: boolean };
type RenderData = {
  units: string;
  extents: Extents;
  layers: Layer[];
  parent_revision_id: string | null;
  entities: RenderEntity[];
  summary: { entity_count: number; layer_count: number; block_count: number; paperspace_layouts: number };
  warnings: string[];
};

const OBJ_SNAPS: { kind: Exclude<SnapKind, "GRID">; label: string }[] = [
  { kind: "END", label: "끝점" },
  { kind: "MID", label: "중점" },
  { kind: "CEN", label: "중심" },
];
const GRID_STEP = 10; // mm
const EMPTY_PATHS: Pt[][] = [];
// ponytail: module variable carries the save note across the router.replace remount, move to a query param/toast if it must survive reloads
let savedNote: string | null = null;

export default function ViewerPage({ params }: { params: Promise<{ revisionId: string }> }) {
  const { revisionId } = use(params);
  return (
    <Suspense>
      <ViewerRoute revisionId={revisionId} />
    </Suspense>
  );
}

function ViewerRoute({ revisionId }: { revisionId: string }) {
  const docId = useSearchParams().get("doc");
  return <Editor key={revisionId} revisionId={revisionId} docId={docId} />;
}

function Editor({ revisionId, docId }: { revisionId: string; docId: string | null }) {
  const router = useRouter();
  const me = useApi<User>("/auth/me").data;
  const doc = useApi<Doc>(docId ? `/documents/${encodeURIComponent(docId)}` : null).data;
  // read-only until the role is known; the server enforces DESIGNER/ADMIN anyway
  const canEdit = !!me && can(me, "DESIGNER");
  const canMetrics = !!me && can(me, "DESIGNER", "ESTIMATOR");
  const [metrics, setMetrics] = useState(false);
  const [data, setData] = useState<RenderData | null>(null);
  const [hist, setHist] = useState<History<RenderEntity[]> | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Set, not a plain object: layer names like "constructor" must not hit Object.prototype
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [cursor, setCursor] = useState<[number, number] | null>(null);
  const [scale, setScale] = useState(1);
  const [ts, setTs] = useState<ToolState>({ tool: "SELECT", pts: [] });
  const [prompt, setPrompt] = useState(() => start("SELECT", 0).prompt);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [layer, setLayer] = useState("");
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(() => {
    const t = savedNote;
    savedNote = null;
    return t ? { ok: true, text: t } : null;
  });
  const nextId = useRef(1);
  const [objSnaps, setObjSnaps] = useState<Set<SnapKind>>(new Set(["END", "MID", "CEN"]));
  const [objSnapOn, setObjSnapOn] = useState(true); // F3
  const [gridSnap, setGridSnap] = useState(false);
  const [showGrid, setShowGrid] = useState(true); // F7
  const snapKinds = useMemo(() => {
    const k = new Set<SnapKind>(objSnapOn ? objSnaps : []);
    if (gridSnap) k.add("GRID");
    return k;
  }, [objSnaps, objSnapOn, gridSnap]);
  const toggleSnap = (kind: SnapKind) =>
    setObjSnaps((s) => {
      const n = new Set(s);
      if (!n.delete(kind)) n.add(kind);
      return n;
    });

  useEffect(() => {
    let live = true;
    api<RenderData>(`/revisions/${encodeURIComponent(revisionId)}/render`)
      .then((d) => {
        if (!live) return;
        setData(d);
        setHist(initHistory(d.entities));
        setHidden(new Set(d.layers.filter((l) => !l.visible).map((l) => l.name)));
        setLayer((d.layers.find((l) => !l.locked && l.visible) ?? d.layers.find((l) => !l.locked))?.name ?? "");
      })
      .catch((e) => live && setError(errText(e)));
    return () => {
      live = false;
    };
  }, [revisionId]);

  const ents = hist?.present ?? EMPTY_ENTS;
  const locked = useMemo(() => new Set(data?.layers.filter((l) => l.locked).map((l) => l.name)), [data]);
  const selEntities = useMemo(() => ents.filter((e) => selected.has(e.handle)), [ents, selected]);
  const selEdit = useMemo<Sel[]>(
    () => selEntities.flatMap((e) => (e.geom && !locked.has(e.layer) ? [{ handle: e.handle, layer: e.layer, geom: e.geom }] : [])),
    [selEntities, locked],
  );
  const diff = useMemo(() => diffEdits(data?.entities ?? EMPTY_ENTS, ents), [data, ents]);
  const dirty = !isEmptyEdit(diff);
  const previewPaths = useMemo(() => (cursor && ts.tool !== "SELECT" ? preview(ts, cursor, selEdit) : EMPTY_PATHS), [ts, cursor, selEdit]);

  useEffect(() => {
    if (!dirty) return;
    const h = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [dirty]);

  const commit = (next: RenderEntity[]) => setHist((h) => h && push(h, next));

  const applyResults = (results: Result[]) => {
    if (!results.length) return;
    let next = ents;
    for (const r of results) {
      if (r.updated) {
        const m = new Map(r.updated.map((u) => [u.handle, u.geom]));
        next = next.map((e) => (m.has(e.handle) ? { ...e, geom: m.get(e.handle) } : e));
      }
      if (r.created) {
        const made = r.created.map((c): RenderEntity => {
          const ly = c.layer ?? layer;
          return { handle: `new-${nextId.current++}`, type: c.geom.type, layer: ly, color: data?.layers.find((l) => l.name === ly)?.color ?? null, paths: [], geom: c.geom };
        });
        next = [...next, ...made];
      }
    }
    commit(next);
  };

  const dispatch = (ev: ToolEvent) => {
    if (ts.tool === "SELECT") {
      if (ev.kind === "esc") setSelected(new Set());
      return;
    }
    // DIMRADIUS: the reducer gets the circle/arc under the pick instead of the selection
    const under = ts.tool === "DIMRADIUS" && ev.kind === "point" ? nearest(ev.p, scale, (e) => !!e.geom && !locked.has(e.layer)) : null;
    const out = step(ts, ev, under?.geom ? [{ handle: under.handle, layer: under.layer, geom: under.geom }] : ts.tool === "DIMRADIUS" ? [] : selEdit);
    setTs(out.state);
    setPrompt(out.prompt);
    applyResults(out.results);
  };

  const changeTool = (t: Tool) => {
    if (!canEdit && t !== "SELECT") return setPrompt("읽기 전용입니다");
    if (t !== "SELECT" && t !== "MOVE" && t !== "COPY" && !layer) return setPrompt("작도 가능한(잠기지 않은) 레이어가 없습니다");
    const out = start(t, selEdit.length);
    setTs(out.state);
    setPrompt(out.prompt + (out.state.tool !== "SELECT" && selEdit.length < selEntities.length ? "  ※ 잠금/읽기 전용 객체 제외" : ""));
  };

  const nearest = (p: Pt, sc: number, ok: (e: RenderEntity) => boolean = () => true) => {
    let best: RenderEntity | null = null;
    let bestD = 5 / sc; // 5 px
    for (const e of ents) {
      if (hidden.has(e.layer) || !ok(e)) continue;
      const d = distToPaths(hitPaths(e), p);
      if (d <= bestD) [best, bestD] = [e, d];
    }
    return best;
  };

  const onPick = (p: Pt, shift: boolean, sc: number, raw: Pt) => {
    // DIMRADIUS picks an entity: use the raw cursor, a CEN snap would land off the circle
    if (ts.tool !== "SELECT") return dispatch({ kind: "point", p: ts.tool === "DIMRADIUS" ? raw : p });
    const best = nearest(p, sc)?.handle;
    if (best) setSelected((s) => (shift ? new Set(s).add(best) : new Set([best])));
    else if (!shift) setSelected(new Set());
  };

  const del = () => {
    if (!canEdit) return;
    const targets = selEntities.filter((e) => e.geom && !locked.has(e.layer));
    if (!targets.length) return setPrompt(selEntities.length ? "삭제할 수 없습니다: 잠긴 레이어 또는 읽기 전용 객체" : "삭제할 객체를 선택하세요");
    const gone = new Set(targets.map((e) => e.handle));
    commit(ents.filter((e) => !gone.has(e.handle)));
    setSelected(new Set());
    setPrompt(`${targets.length}개 삭제` + (targets.length < selEntities.length ? " (잠금/읽기 전용 객체 제외)" : ""));
  };

  const applyPatch = (handle: string, patch: { layer?: string; geom?: Geom }) => {
    if (!canEdit) return;
    const e = ents.find((x) => x.handle === handle);
    if (!e || locked.has(e.layer)) return setPrompt("잠긴 레이어의 객체는 수정할 수 없습니다");
    if (patch.layer && locked.has(patch.layer)) return setPrompt("잠긴 레이어로는 이동할 수 없습니다");
    if (patch.geom && JSON.stringify(patch.geom) === JSON.stringify(e.geom)) return;
    const color = patch.layer ? (data?.layers.find((l) => l.name === patch.layer)?.color ?? e.color) : e.color;
    commit(ents.map((x) => (x.handle === handle ? { ...x, ...patch, color } : x)));
    setPrompt("속성을 적용했습니다");
  };

  const save = async () => {
    if (!canEdit || !dirty || saving) return;
    setSaving(true);
    setNote(null);
    try {
      const d = await api<{ revision_id: number; revision_no: number; entity_count: number; warnings: string[] }>(`/revisions/${encodeURIComponent(revisionId)}/edits`, { method: "POST", json: diff });
      savedNote = `새 리비전 ${d.revision_no} 저장됨 · 엔티티 ${d.entity_count}` + (d.warnings.length ? ` · 경고 ${d.warnings.length}` : "");
      router.replace("/viewer/" + encodeURIComponent(d.revision_id) + (docId ? `?doc=${encodeURIComponent(docId)}` : ""));
    } catch (e) {
      // 409 DOCUMENT_IN_REVIEW / REVISION_NOT_CURRENT get their own message via errText
      setNote({ ok: false, text: errText(e) });
    } finally {
      setSaving(false);
    }
  };

  const submit = (text: string) => {
    const p = parseInput(text, ts);
    if ("error" in p) setPrompt(p.error);
    else if ("tool" in p) changeTool(p.tool);
    else dispatch(p.ev);
  };

  const downloadDxf = () => download(`/revisions/${encodeURIComponent(revisionId)}/dxf`, `${doc?.doc_no ?? "revision-" + revisionId}.dxf`).catch((e) => setNote({ ok: false, text: errText(e) }));

  const doUndo = () => setHist((h) => h && undo(h));
  const doRedo = () => setHist((h) => h && redo(h));

  // global shortcuts (re-registered every render so handlers see fresh state)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      const k = e.key.toLowerCase();
      if (e.key === "F3" || e.key === "F7") {
        e.preventDefault();
        if (e.key === "F3") setObjSnapOn((v) => !v);
        else setShowGrid((v) => !v);
        return;
      }
      if (mod && k === "s") {
        e.preventDefault();
        void save();
        return;
      }
      const t = e.target;
      if (t instanceof HTMLElement && /^(INPUT|SELECT|TEXTAREA)$/.test(t.tagName)) return;
      if (mod) {
        if (k === "z" || k === "y") {
          e.preventDefault();
          if (k === "y" || e.shiftKey) doRedo();
          else doUndo();
        }
        return;
      }
      if (e.altKey) return;
      const tools: Record<string, Tool> = { v: "SELECT", l: "LINE", d: "DIMLINEAR", c: "CIRCLE", a: "ARC", t: "TEXT", m: "MOVE" };
      if (tools[k]) changeTool(tools[k]);
      else if (e.key === "Delete" || e.key === "Backspace") del();
      else if (e.key === "Escape") dispatch({ kind: "esc" });
      else if (e.key === "Enter" && !(t instanceof HTMLButtonElement)) dispatch({ kind: "enter" });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const one = selEntities.length === 1 ? selEntities[0] : null;

  return (
    <div className="flex h-screen flex-col bg-background">
      <header className="flex h-12 shrink-0 items-center gap-4 border-b border-line bg-card px-4 text-sm">
        <span className="font-semibold text-foreground">AX-CAD</span>
        <Link href={docId ? `/documents/${encodeURIComponent(docId)}` : "/projects"} className="text-[var(--color-primary)] hover:underline focus-visible:outline-2 focus-visible:outline-ring">
          {docId ? "← 도면 상세" : "← 프로젝트"}
        </Link>
        {doc && (
          <span className="text-foreground">
            <span className="font-mono">{doc.doc_no}</span> {doc.title}
          </span>
        )}
        {docId && (
          <Link href={`/model/${encodeURIComponent(docId)}`} className="text-[var(--color-primary)] hover:underline focus-visible:outline-2 focus-visible:outline-ring">
            Model
          </Link>
        )}
        {canMetrics && (
          <button type="button" onClick={() => setMetrics(true)} className="rounded-md border border-[var(--color-border-strong)] px-2 py-0.5 hover:bg-hover focus-visible:outline-2 focus-visible:outline-ring">
            견적 메트릭
          </button>
        )}
        {data && (
          <span className="font-mono text-muted-foreground">
            {doc?.current_revision_id === revisionId ? `Rev ${doc.current_revision_no} (현재) · ` : ""}리비전 {revisionId}
            {data.parent_revision_id ? ` (원본 ${data.parent_revision_id})` : ""} · {canEdit ? "" : "읽기 전용 · "}엔티티 {data.summary.entity_count} · 레이어 {data.summary.layer_count} · 블록 {data.summary.block_count}
          </span>
        )}
      </header>
      {metrics && (
        <MetricsDialog
          revisionId={revisionId}
          onClose={() => setMetrics(false)}
          onShow={(handles) => {
            setMetrics(false);
            setSelected(new Set(handles));
          }}
        />
      )}

      {error ? (
        <p role="alert" className="p-6 text-red-600">
          {error}
        </p>
      ) : !data || !hist ? (
        <p role="status" className="p-6 text-muted-foreground">
          불러오는 중...
        </p>
      ) : (
        <>
          <div className="flex min-h-0 flex-1">
            <aside aria-label="레이어" className="w-60 shrink-0 overflow-y-auto border-r border-line bg-card p-3">
              <h2 className="mb-2 text-sm font-semibold text-foreground">레이어</h2>
              <ul className="space-y-1">
                {data.layers.map((l) => (
                  <li key={l.name}>
                    <label className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-hover">
                      <input
                        type="checkbox"
                        checked={!hidden.has(l.name)}
                        onChange={(e) =>
                          setHidden((h) => {
                            const next = new Set(h);
                            if (e.target.checked) next.delete(l.name);
                            else next.add(l.name);
                            return next;
                          })
                        }
                      />
                      <span
                        aria-hidden
                        className="inline-block h-3 w-3 rounded-sm border border-line-strong"
                        style={{ background: l.color ?? "var(--canvas-fg)" }}
                      />
                      <span className="truncate text-body">
                        {l.name}
                        {l.locked ? " (잠금)" : ""}
                      </span>
                    </label>
                  </li>
                ))}
              </ul>
            </aside>
            <div className="flex min-w-0 flex-1 flex-col">
              {canEdit ? (
              <ToolPalette
                tool={ts.tool}
                onTool={changeTool}
                layers={data.layers}
                layer={layer}
                onLayer={setLayer}
                canDelete={selEntities.length > 0}
                onDelete={del}
                canUndo={hist.past.length > 0}
                onUndo={doUndo}
                canRedo={hist.future.length > 0}
                onRedo={doRedo}
                canSave={dirty}
                saving={saving}
                onSave={() => void save()}
                onDownload={downloadDxf}
              />
              ) : (
                <div className="flex items-center gap-2 border-b border-line bg-card px-2 py-1">
                  <button type="button" onClick={downloadDxf} className="rounded-md border border-line px-2 py-1 text-sm text-foreground hover:bg-hover focus-visible:outline-2 focus-visible:outline-ring">
                    DXF 다운로드
                  </button>
                </div>
              )}
              {note && (
                <p role={note.ok ? "status" : "alert"} className={`px-3 py-1 text-sm ${note.ok ? "text-snap" : "text-red-600"}`}>
                  {note.text}
                </p>
              )}
              <main className="min-h-0 flex-1">
                <CanvasViewport
                  entities={ents}
                  extents={data.extents}
                  hidden={hidden}
                  onCursor={setCursor}
                  onZoom={setScale}
                  selected={selected}
                  preview={previewPaths}
                  onPick={onPick}
                  snapOn={ts.tool !== "SELECT"}
                  snapKinds={snapKinds}
                  gridStep={GRID_STEP}
                  showGrid={showGrid}
                />
              </main>
              {canEdit && <CommandPrompt prompt={prompt} onSubmit={submit} />}
            </div>
            <PropertyInspector
              entity={one}
              count={selEntities.length}
              layers={data.layers}
              lockedLayer={!canEdit || (one ? locked.has(one.layer) : false)}
              onApply={applyPatch}
              onMessage={setPrompt}
            />
          </div>
          <footer className="flex h-8 shrink-0 items-center gap-6 border-t border-line bg-muted px-4 font-mono text-xs text-body">
            <span>
              X {cursor ? cursor[0].toFixed(2) : "--"} Y {cursor ? cursor[1].toFixed(2) : "--"} mm
            </span>
            <span>줌 {Math.round(scale * 100)}%</span>
            <span>격자 {effectiveGridStep(GRID_STEP, scale)} mm</span>
            <span role="group" aria-label="스냅" className="flex items-center gap-1">
              {OBJ_SNAPS.map((o) => (
                <SnapToggle key={o.kind} label={o.label} on={objSnaps.has(o.kind)} onClick={() => toggleSnap(o.kind)} />
              ))}
              <SnapToggle label="그리드스냅" on={gridSnap} onClick={() => setGridSnap((v) => !v)} />
              <SnapToggle label="객체스냅 (F3)" on={objSnapOn} onClick={() => setObjSnapOn((v) => !v)} />
              <SnapToggle label="격자 표시 (F7)" on={showGrid} onClick={() => setShowGrid((v) => !v)} />
            </span>
            <span>단위 {data.units}</span>
            <span>도구 {ts.tool}</span>
            <span>선택 {selEntities.length}</span>
            <span>엔티티 {ents.length}</span>
            <span title={data.warnings.join("\n")}>경고 {data.warnings.length}</span>
          </footer>
        </>
      )}
    </div>
  );
}

const EMPTY_ENTS: RenderEntity[] = [];

function SnapToggle({ label, on, onClick }: { label: string; on: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={`rounded border border-line px-1.5 py-0.5 hover:bg-hover focus-visible:outline-2 focus-visible:outline-ring ${on ? "bg-[var(--color-primary-light)] border-[var(--color-primary)]" : ""}`}
    >
      {label}
    </button>
  );
}
