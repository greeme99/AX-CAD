"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, use, useEffect, useMemo, useRef, useState } from "react";
import ConstructionDialog from "@/components/cad/ConstructionDialog";
import MetricsDialog from "@/components/cad/MetricsDialog";
import DesktopOnly from "@/components/DesktopOnly";
import { parseSelect } from "@/lib/quote";
import CanvasViewport, { type RenderEntity, type Sheet } from "@/components/cad/CanvasViewport";
import CommandPrompt from "@/components/cad/CommandPrompt";
import PropertyInspector from "@/components/cad/PropertyInspector";
import PlotDialog from "@/components/cad/PlotDialog";
import ToolPalette from "@/components/cad/ToolPalette";
import { api, can, download, errText, is3dNotice, warnText, type Doc, type User } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { ACI_COLORS, BYLAYER, diffEdits, isEmptyEdit, sameEntities, type BlockDef, type LayerOp } from "@/lib/cad/edits";
import { toPrims } from "@/lib/cad/curves";
import { distToPaths, entityPaths, hitPaths, placeBlock, serverDrawn, toPaths, type Geom } from "@/lib/cad/geom";
import { initHistory, push, redo, undo, type History } from "@/lib/cad/history";
import { NOW_TOOLS, PICK_TOOLS, SEL_TOOLS, parseInput, preview, runNow, start, step, type Ctx, type Result, type Sel, type Tool, type ToolEvent, type ToolState } from "@/lib/cad/tools";
import { boxSelect } from "@/lib/cad/select";
import type { SnapKind } from "@/lib/cad/snap";
import { effectiveGridStep } from "@/lib/cad/snap";
import type { Extents, Pt } from "@/lib/cad/view";

type Layer = { name: string; color: string | null; visible: boolean; locked: boolean; aci?: number; linetype?: string };
type RenderData = {
  units: string;
  extents: Extents;
  layers: Layer[];
  parent_revision_id: string | null;
  entities: RenderEntity[];
  sheets?: Sheet[]; // paper-space layouts (view only); absent in payloads stored before Track 8
  linetypes?: string[];
  blocks?: { name: string; paths: Pt[][] }[]; // insertable blocks, content in block coordinates
  summary: { entity_count: number; layer_count: number; block_count: number; paperspace_layouts: number };
  warnings: string[];
};

const OBJ_SNAPS: { kind: Exclude<SnapKind, "GRID">; label: string }[] = [
  { kind: "END", label: "끝점" },
  { kind: "MID", label: "중점" },
  { kind: "CEN", label: "중심" },
  { kind: "QUA", label: "사분점" },
  { kind: "INT", label: "교차점" },
  { kind: "PERP", label: "수직" },
  { kind: "ORTHO", label: "직교" },
  { kind: "NEA", label: "근접점" },
];
const GRID_STEP = 10; // mm
const layerBtn = "rounded border border-line px-1.5 py-0.5 text-xs text-foreground hover:bg-hover disabled:opacity-40";
const EMPTY_PATHS: Pt[][] = [];
// ponytail: module variable carries the save note across the router.replace remount, move to a query param/toast if it must survive reloads
let savedNote: string | null = null;

export default function ViewerPage({ params }: { params: Promise<{ revisionId: string }> }) {
  const { revisionId } = use(params);
  return (
    <DesktopOnly back="/projects">
      <Suspense>
        <ViewerRoute revisionId={revisionId} />
      </Suspense>
    </DesktopOnly>
  );
}

function ViewerRoute({ revisionId }: { revisionId: string }) {
  const sp = useSearchParams();
  // FN-18 "도면에서 보기": ?select=h1,h2 highlights the quote line's source entities
  return <Editor key={revisionId} revisionId={revisionId} docId={sp.get("doc")} select={parseSelect(sp.get("select"), "handle")} />;
}

function Editor({ revisionId, docId, select = [] }: { revisionId: string; docId: string | null; select?: string[] }) {
  const router = useRouter();
  const me = useApi<User>("/auth/me").data;
  const doc = useApi<Doc>(docId ? `/documents/${encodeURIComponent(docId)}` : null).data;
  const [sheetName, setSheetName] = useState<string | null>(null); // null = model space
  // read-only until the role is known (the server enforces DESIGNER/ADMIN anyway); sheets are view-only
  const canEdit = !!me && can(me, "DESIGNER") && sheetName === null;
  const canMetrics = !!me && can(me, "DESIGNER", "ESTIMATOR");
  const [metrics, setMetrics] = useState(false);
  const [construction, setConstruction] = useState(false);
  const [data, setData] = useState<RenderData | null>(null);
  const [hist, setHist] = useState<History<RenderEntity[]> | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Set, not a plain object: layer names like "constructor" must not hit Object.prototype
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [newLayer, setNewLayer] = useState("");
  const [plotting, setPlotting] = useState(false);
  // ponytail: blocks made with BLOCK stay pending until saved, also when the command is undone (an unused block is harmless)
  const [newBlocks, setNewBlocks] = useState<BlockDef[]>([]);
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
  const [objSnaps, setObjSnaps] = useState<Set<SnapKind>>(new Set(["END", "MID", "CEN", "QUA", "INT"]));
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
        if (select.length) setSelected(new Set(select));
      })
      .catch((e) => live && setError(errText(e)));
    return () => {
      live = false;
    };
    // ?select applies once per load (the Editor is keyed by revision); later clicks own the selection
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revisionId]);

  const ents = hist?.present ?? EMPTY_ENTS;
  const sheet = sheetName === null ? undefined : data?.sheets?.find((s) => s.name === sheetName);
  const locked = useMemo(() => new Set(data?.layers.filter((l) => l.locked).map((l) => l.name)), [data]);
  const selEntities = useMemo(() => ents.filter((e) => selected.has(e.handle)), [ents, selected]);
  const selEdit = useMemo<Sel[]>(
    () => selEntities.flatMap((e) => (e.geom && !locked.has(e.layer) ? [{ handle: e.handle, layer: e.layer, geom: e.geom }] : [])),
    [selEntities, locked],
  );
  // block content for previews: the drawing's blocks and the ones made in this session
  const blockPaths = useMemo(() => {
    const m = new Map((data?.blocks ?? []).map((b) => [b.name.toUpperCase(), b.paths]));
    for (const b of newBlocks)
      m.set(b.name.toUpperCase(), b.entities.flatMap((e) => toPaths(e.geom)).map((ln) => ln.map(([x, y]): Pt => [x - b.base[0], y - b.base[1]])));
    return m;
  }, [data, newBlocks]);
  const placed = (g: Geom): Pt[][] => (g.type === "INSERT" ? placeBlock(blockPaths.get(g.name.toUpperCase()) ?? [], g) : []);
  // trim/extend boundaries: every visible curve, editable or not
  const ctx = useMemo<Ctx>(() => {
    const ex = data?.extents;
    const reach = ex ? 4 * Math.hypot(ex.max[0] - ex.min[0], ex.max[1] - ex.min[1]) + 1 : 1;
    return {
      reach,
      bounds: ents.flatMap((e) => (hidden.has(e.layer) ? [] : toPrims(e.handle, e.geom, entityPaths(e)))),
      // STRETCH picks its own entities with a crossing window: visible, editable, unlocked ones
      editable: ents.flatMap((e) => (e.geom && !hidden.has(e.layer) && !locked.has(e.layer) ? [{ handle: e.handle, layer: e.layer, geom: e.geom }] : [])),
      blocks: [...(data?.blocks ?? []), ...newBlocks].map((b) => b.name),
    };
  }, [ents, hidden, data, locked, newBlocks]);
  const diff = useMemo(() => diffEdits(data?.entities ?? EMPTY_ENTS, ents), [data, ents]);
  const dirty = !isEmptyEdit(diff);
  const previewPaths = useMemo(() => (cursor && ts.tool !== "SELECT" ? preview(ts, cursor, selEdit) : EMPTY_PATHS), [ts, cursor, selEdit]);

  useEffect(() => {
    if (!dirty) return;
    const h = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [dirty]);

  const commit = (next: RenderEntity[]) => setHist((h) => h && push(h, next, sameEntities));

  const applyResults = (results: Result[]) => {
    if (!results.length) return;
    let next = ents;
    for (const r of results) {
      if (r.block) {
        const { name, base, handles } = r.block;
        const parts = next.filter((e) => e.geom && handles.includes(e.handle));
        const def: BlockDef = { name, base, entities: parts.map((e) => ({ layer: e.layer, geom: e.geom!, ...(e.aci !== undefined ? { color: e.aci } : {}), ...(e.linetype ? { linetype: e.linetype } : {}) })) };
        setNewBlocks((bs) => [...bs, def]);
        blockPaths.set(name.toUpperCase(), parts.flatMap((e) => toPaths(e.geom!)).map((ln) => ln.map(([x, y]): Pt => [x - base[0], y - base[1]])));
      }
      if (r.deleted) {
        const gone = new Set(r.deleted);
        next = next.filter((e) => !gone.has(e.handle));
      }
      if (r.updated) {
        const m = new Map(r.updated.map((u) => [u.handle, u.geom]));
        // an edited dimension or hatch drops its server-drawn pieces and shows the local preview
        next = next.flatMap((e) => {
          const g = m.get(e.handle);
          if (!g) return [e];
          if (!serverDrawn(g.type)) return [{ ...e, geom: g }];
          return e.geom ? [{ ...e, geom: g, paths: placed(g), text: undefined, fill: undefined }] : [];
        });
      }
      if (r.created) {
        const made = r.created.map((c): RenderEntity => {
          const ly = c.layer ?? layer;
          return { handle: `new-${nextId.current++}`, type: c.geom.type, layer: ly, color: data?.layers.find((l) => l.name === ly)?.color ?? null, paths: placed(c.geom), geom: c.geom };
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
    // pick tools get the editable entity under the pick instead of the selection (OFFSET only for its object pick)
    const picks = PICK_TOOLS.includes(ts.tool) || (ts.tool === "OFFSET" && ts.n !== undefined && !ts.target);
    const under = picks && ev.kind === "point" ? nearest(ev.p, scale, (e) => !!e.geom && !locked.has(e.layer)) : null;
    const out = step(ts, ev, under?.geom ? [{ handle: under.handle, layer: under.layer, geom: under.geom }] : picks ? [] : selEdit, ctx);
    setTs(out.state);
    setPrompt(out.prompt);
    applyResults(out.results);
  };

  const changeTool = (t: Tool) => {
    if (!canEdit && t !== "SELECT") return setPrompt("읽기 전용입니다");
    // only tools that draw new shapes need a target layer; the others keep their source's layer
    const draws = !SEL_TOOLS.includes(t) && !PICK_TOOLS.includes(t) && t !== "SELECT" && t !== "OFFSET" && t !== "STRETCH";
    if (draws && !layer) return setPrompt("작도 가능한(잠기지 않은) 레이어가 없습니다");
    if (NOW_TOOLS.includes(t) && selEdit.length) {
      const now = runNow(t, selEdit); // EXPLODE / JOIN act on the selection at once
      setTs(now.state);
      setPrompt(now.prompt);
      if (now.results.length) setSelected(new Set());
      applyResults(now.results);
      return;
    }
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
    // entity picks use the raw cursor: a snap (CEN, QUA...) can land off the clicked curve or on the wrong side
    const rawPick = PICK_TOOLS.includes(ts.tool) || (ts.tool === "OFFSET" && !ts.target);
    if (ts.tool !== "SELECT") return dispatch({ kind: "point", p: rawPick ? raw : p });
    const best = nearest(p, sc)?.handle;
    if (best) setSelected((s) => (shift ? new Set(s).add(best) : new Set([best])));
    else if (!shift) setSelected(new Set());
  };

  const onBox = (a: Pt, b: Pt, crossing: boolean, shift: boolean) => {
    const hs = boxSelect(ents, a, b, crossing, hidden);
    setSelected((s) => (shift ? new Set([...s, ...hs]) : new Set(hs)));
    setPrompt(`${crossing ? "걸침" : "창"} 선택 ${hs.length}개`);
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

  const applyPatch = (handle: string, patch: { layer?: string; geom?: Geom; aci?: number; linetype?: string }) => {
    if (!canEdit) return;
    const e = ents.find((x) => x.handle === handle);
    if (!e || locked.has(e.layer)) return setPrompt("잠긴 레이어의 객체는 수정할 수 없습니다");
    if (patch.layer && locked.has(patch.layer)) return setPrompt("잠긴 레이어로는 이동할 수 없습니다");
    if (patch.geom && JSON.stringify(patch.geom) === JSON.stringify(e.geom)) return;
    // ponytail: a new linetype shows after saving (the server draws the dash pattern)
    const aci = patch.aci ?? e.aci;
    const own = ACI_COLORS.find(([a]) => a === aci);
    const layerColor = data?.layers.find((l) => l.name === (patch.layer ?? e.layer))?.color ?? null;
    const color = aci === BYLAYER ? layerColor : own ? own[2] : patch.layer ? (layerColor ?? e.color) : e.color;
    commit(ents.map((x) => (x.handle === handle ? { ...x, ...patch, color } : x)));
    setPrompt("속성을 적용했습니다");
  };

  // layer changes save at once, together with any pending entity edits
  const save = async (layers?: LayerOp[]) => {
    if (!canEdit || (!dirty && !layers) || saving) return;
    setSaving(true);
    setNote(null);
    try {
      const json = { ...diff, ...(layers ? { layers } : {}), ...(newBlocks.length ? { blocks: newBlocks } : {}) };
      const d = await api<{ revision_id: number; revision_no: number; entity_count: number; warnings: string[] }>(`/revisions/${encodeURIComponent(revisionId)}/edits`, { method: "POST", json });
      savedNote = `새 리비전 ${d.revision_no} 저장됨 · 엔티티 ${d.entity_count}` + (d.warnings.length ? ` · 경고 ${d.warnings.length}` : "");
      router.replace("/viewer/" + encodeURIComponent(d.revision_id) + (docId ? `?doc=${encodeURIComponent(docId)}` : ""));
    } catch (e) {
      // 409 REVISION_NOT_CURRENT gets its own message via errText
      setNote({ ok: false, text: errText(e) });
    } finally {
      setSaving(false);
    }
  };

  const renameLayer = (name: string) => {
    const to = window.prompt(`'${name}' 레이어의 새 이름`, name)?.trim();
    if (!to || to === name) return;
    if (data?.layers.some((l) => l.name.toUpperCase() === to.toUpperCase() && l.name !== name)) return setPrompt("같은 이름의 레이어가 있습니다");
    void save([{ name, rename: to }]);
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
        if (k === "a" && ts.tool === "SELECT" && !sheet) {
          e.preventDefault(); // select all visible
          setSelected(new Set(ents.filter((x) => !hidden.has(x.layer)).map((x) => x.handle)));
          return;
        }
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
        {!!me && can(me, "ESTIMATOR") && (
          <button type="button" onClick={() => setConstruction(true)} className="rounded-md border border-[var(--color-border-strong)] px-2 py-0.5 hover:bg-hover focus-visible:outline-2 focus-visible:outline-ring">
            공사 견적
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
          canQuote={!!me && can(me, "ESTIMATOR")}
          onClose={() => setMetrics(false)}
          onShow={(handles) => {
            setMetrics(false);
            setSelected(new Set(handles));
          }}
        />
      )}

      {construction && (
        <ConstructionDialog
          revisionId={revisionId}
          docId={docId}
          onClose={() => setConstruction(false)}
          onShow={(handles) => {
            setConstruction(false);
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
                    {canEdit && (
                      <div className="ml-6 flex items-center gap-1 pb-1">
                        <button type="button" className={layerBtn} disabled={saving} aria-label={`${l.name} ${l.locked ? "잠금 해제" : "잠금"}`} onClick={() => void save([{ name: l.name, locked: !l.locked }])}>
                          {l.locked ? "해제" : "잠금"}
                        </button>
                        <button type="button" className={layerBtn} disabled={saving} aria-label={`${l.name} 이름 변경`} onClick={() => renameLayer(l.name)}>
                          이름
                        </button>
                        <select
                          aria-label={`${l.name} 색상`}
                          className={layerBtn}
                          disabled={saving}
                          value={ACI_COLORS.some(([a]) => a === l.aci) ? l.aci : ""}
                          onChange={(e) => void save([{ name: l.name, color: Number(e.target.value) }])}
                        >
                          {!ACI_COLORS.some(([a]) => a === l.aci) && <option value="">색상</option>}
                          {ACI_COLORS.map(([aci, label]) => (
                            <option key={aci} value={aci}>
                              {label}
                            </option>
                          ))}
                        </select>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
              {canEdit && (
                <form
                  className="mt-3 flex gap-1"
                  onSubmit={(e) => {
                    e.preventDefault();
                    const name = newLayer.trim();
                    if (!name) return;
                    if (data.layers.some((l) => l.name.toUpperCase() === name.toUpperCase())) return setPrompt("같은 이름의 레이어가 있습니다");
                    void save([{ name, create: true }]).then(() => setNewLayer(""));
                  }}
                >
                  <input aria-label="새 레이어 이름" placeholder="새 레이어" maxLength={255} value={newLayer} onChange={(e) => setNewLayer(e.target.value)} className="w-full min-w-0 rounded-md border border-line bg-background px-2 py-1 text-sm text-foreground" />
                  <button type="submit" className={`${layerBtn} shrink-0`} disabled={saving}>
                    추가
                  </button>
                </form>
              )}
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
                onPlot={() => setPlotting(true)}
              />
              ) : (
                <div className="flex items-center gap-2 border-b border-line bg-card px-2 py-1">
                  <button type="button" onClick={downloadDxf} className="rounded-md border border-line px-2 py-1 text-sm text-foreground hover:bg-hover focus-visible:outline-2 focus-visible:outline-ring">
                    DXF 다운로드
                  </button>
                  <button type="button" onClick={() => setPlotting(true)} className="rounded-md border border-line px-2 py-1 text-sm text-foreground hover:bg-hover focus-visible:outline-2 focus-visible:outline-ring">
                    PDF 출력
                  </button>
                </div>
              )}
              {plotting && (
                <PlotDialog revisionId={revisionId} filename={`${doc?.doc_no ?? "revision-" + revisionId}.pdf`} hidden={hidden} dirty={dirty} onClose={() => setPlotting(false)} />
              )}
              {note && (
                <p role={note.ok ? "status" : "alert"} className={`px-3 py-1 text-sm ${note.ok ? "text-snap" : "text-red-600"}`}>
                  {note.text}
                </p>
              )}
              {data.warnings.filter(is3dNotice).map((w) => (
                <p key={w} role="status" className="px-3 py-1 text-sm text-body">
                  {warnText(w)}
                </p>
              ))}
              {!!data.sheets?.length && (
                <div role="tablist" aria-label="도면 공간" className="flex gap-1 border-b border-line bg-card px-2 py-1">
                  {[null, ...data.sheets.map((s) => s.name)].map((name) => (
                    <button
                      key={name ?? "\0model"}
                      type="button"
                      role="tab"
                      aria-selected={sheetName === name}
                      onClick={() => {
                        changeTool("SELECT");
                        setSelected(new Set());
                        setSheetName(name);
                      }}
                      className={`rounded px-2 py-0.5 text-sm focus-visible:outline-2 focus-visible:outline-ring ${sheetName === name ? "bg-hover font-semibold text-foreground" : "text-body hover:bg-hover"}`}
                    >
                      {name ?? "모델"}
                    </button>
                  ))}
                </div>
              )}
              <main className="min-h-0 flex-1">
                <CanvasViewport
                  key={sheetName ?? "\0model"}
                  entities={sheet ? sheet.entities : ents}
                  extents={sheet ? sheet.extents : data.extents}
                  sheet={sheet ? { paper: sheet.paper, viewports: sheet.viewports, model: ents } : undefined}
                  hidden={hidden}
                  onCursor={setCursor}
                  onZoom={setScale}
                  selected={selected}
                  preview={previewPaths}
                  onPick={sheet ? NO_PICK : onPick}
                  onBox={sheet ? undefined : onBox}
                  snapOn={ts.tool !== "SELECT"}
                  snapKinds={snapKinds}
                  snapFrom={ts.pts.length ? ts.pts[ts.pts.length - 1] : undefined}
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
              linetypes={data.linetypes ?? []}
              lockedLayer={!canEdit || (one ? locked.has(one.layer) : false)}
              onApply={applyPatch}
              onMessage={setPrompt}
            />
          </div>
          <footer className="flex h-8 shrink-0 items-center gap-6 overflow-x-auto whitespace-nowrap border-t border-line bg-muted px-4 font-mono text-xs text-body">
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
            <span title={data.warnings.map(warnText).join("\n")}>경고 {data.warnings.length}</span>
          </footer>
        </>
      )}
    </div>
  );
}

const EMPTY_ENTS: RenderEntity[] = [];
const NO_PICK = () => {}; // sheets are view-only

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
