"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, use, useEffect, useMemo, useRef, useState } from "react";
import ConstructionDialog from "@/components/cad/ConstructionDialog";
import CleanupDialog from "@/components/cad/CleanupDialog";
import MetricsDialog from "@/components/cad/MetricsDialog";
import DesktopOnly from "@/components/DesktopOnly";
import { parseSelect } from "@/lib/quote";
import CanvasViewport, { type RenderEntity, type Sheet } from "@/components/cad/CanvasViewport";
import ContextMenu, { type MenuItem } from "@/components/cad/ContextMenu";
import CommandPrompt from "@/components/cad/CommandPrompt";
import DynamicInput from "@/components/cad/DynamicInput";
import PropertyInspector from "@/components/cad/PropertyInspector";
import PlotDialog from "@/components/cad/PlotDialog";
import ToolPalette from "@/components/cad/ToolPalette";
import { CopilotToggle, useCopilotHandlers } from "@/components/copilot/CopilotProvider";
import { runInputs } from "@/lib/cad/script";
import { runScript } from "@/lib/cad/axs";
import { eventText, journalText, newJournal, note as journalNote, onPaths, record, toolText, type Journal } from "@/lib/cad/journal";
import { ParamBlockDialog, ScriptDialog, type ParamInitial, type Placed } from "@/components/cad/AutomationDialogs";
import { SaveAs } from "@/components/cad/DwgTools";
import { fireReactors, LispError, newSession, runLisp, type DialogRun, type Host, type InputKind, type Outcome, type ReactorEvent } from "@/lib/cad/lisp";
import { DrawingModel, NO_TABLES, withTableOps, type Changes, type DictOp, type DictTree, type LayerEdit, type ObjectIds, type TableOp, type Tables } from "@/lib/cad/objmodel";
import DclDialog from "@/components/cad/DclDialog";
import { eventHandlers as vbaEventHandlers, fireVbaEvent, macros as vbaMacros, parse as parseVba, runVba, VbaError, type VbaDocEvent } from "@/lib/cad/vba";
import { libraryFiles, lispSettings } from "@/lib/lispFiles";
import { libraryScript, libraryList, libraryPut, scriptName } from "@/lib/library";
import { runStartup } from "@/lib/cad/startup";
import { fillOutline, useUnderlays } from "@/components/cad/useUnderlays";
import { underlayOps } from "@/lib/cad/underlayEdits";
import type { LispRun } from "@/lib/copilot";
import { Modal } from "@/components/ui";
import { api, ApiError, can, download, errText, is3dNotice, warnText, type Doc, type User } from "@/lib/api";
import { useApi } from "@/lib/useApi";
import { ACI_COLORS, BYLAYER, dashFor, diffEdits, entProps, isEmptyEdit, sameEntities, type BlockDef, type LayerOp } from "@/lib/cad/edits";
import { toPrims } from "@/lib/cad/curves";
import { distToPaths, entityPaths, hitPaths, placeBlock, serverDrawn, toPaths, type Geom } from "@/lib/cad/geom";
import { initHistory, push, redo, undo, type History } from "@/lib/cad/history";
import { NOW_TOOLS, PICK_TOOLS, SEL_TOOLS, parseInput, preview, runNow, start, step, type Ctx, type Result, type Sel, type Tool, type ToolEvent, type ToolState } from "@/lib/cad/tools";
import { boxSelect } from "@/lib/cad/select";
import type { SnapKind } from "@/lib/cad/snap";
import { effectiveGridStep } from "@/lib/cad/snap";
import type { Extents, Pt } from "@/lib/cad/view";

type Layer = { name: string; color: string | null; visible: boolean; locked: boolean; aci?: number; linetype?: string; lwt?: number; plot?: boolean; off?: boolean; frozen?: boolean; description?: string };
type RenderData = {
  units: string;
  extents: Extents;
  layers: Layer[];
  current_layer?: string; // $CLAYER: AX-CAD blank drawings start on OUTLINE
  parent_revision_id: string | null;
  entities: RenderEntity[];
  sheets?: Sheet[]; // paper-space layouts (view only); absent in payloads stored before Track 8
  drawing?: Record<string, unknown> | null; // Track 8 P3: settings of a drawing made from 3D
  linetypes?: string[];
  linetype_dash?: Record<string, number[] | null>; // mm, for previews of linetype changes
  blocks?: { name: string; paths: Pt[][]; attdefs?: { tag: string; prompt: string; default: string }[]; param?: { template: string; values: Record<string, string>; state: string | null } }[]; // insertable blocks, content in block coordinates
  tables?: Tables; // W7b: linetypes, text/dimension styles, regapps, layouts (absent in older payloads)
  object_ids?: ObjectIds; // W4: handles of tables, records and spaces (Handle / ObjectID / OwnerID)
  dicts?: DictTree | null; // W6: user dictionaries of the named object dictionary (null: too many)
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
  const [cleanup, setCleanup] = useState(false); // B-4: drawing clean-up suggestions
  const [construction, setConstruction] = useState(false);
  const [data, setData] = useState<RenderData | null>(null);
  const [hist, setHist] = useState<History<RenderEntity[]> | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Set, not a plain object: layer names like "constructor" must not hit Object.prototype
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [newLayer, setNewLayer] = useState("");
  const [plotting, setPlotting] = useState(false);
  const [savingAs, setSavingAs] = useState(false);
  const [automation, setAutomation] = useState<null | "SCRIPT" | { initial?: ParamInitial; replace?: string }>(null);
  // ponytail: blocks made with BLOCK stay pending until saved, also when the command is undone (an unused block is harmless)
  const [newBlocks, setNewBlocks] = useState<BlockDef[]>([]);
  const [cursor, setCursor] = useState<[number, number] | null>(null);
  const [screenPos, setScreenPos] = useState<{ x: number; y: number } | null>(null);
  const [dynInput, setDynInput] = useState(true);
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
  // 2D automation A5: the library's start-up files (acad.pgp, acad.lsp, acaddoc.lsp, S::STARTUP) run once per screen
  const started = useRef(false);
  // 2D automation A4: the command journal being recorded (null: not recording), its line count
  // for the palette, and the script the dialog opens with (a finished journal)
  const journal = useRef<Journal | null>(null);
  const [journalLines, setJournalLines] = useState<number | null>(null);
  const [scriptInit, setScriptInit] = useState<string | undefined>(undefined);
  // 2D automation A1: the AX-LISP namespace and reactors of this screen; layers made by Layers.Add
  // wait for the next save; the DCL dialog a run is showing
  const lispSession = useRef(newSession());
  const [newLayers, setNewLayers] = useState<string[]>([]);
  // W7b: table records and layer properties changed by AX-LISP / VBA, saved with the next save
  const [pendingTables, setPendingTables] = useState<TableOp[]>([]);
  const [pendingLayerOps, setPendingLayerOps] = useState<LayerEdit[]>([]);
  const [pendingDicts, setPendingDicts] = useState<DictOp[]>([]); // W2: AX-LISP dictionary / XRecord changes
  // W6: blocks / groups / layouts changed by AX-LISP / VBA (Blocks.Add, Groups, Layouts), saved with the edits
  const [pendingW6, setPendingW6] = useState<Required<Pick<Changes, "blocks" | "groups" | "layouts">>>({ blocks: [], groups: [], layouts: [] });
  const w6Pending = pendingW6.blocks.length + pendingW6.groups.length + pendingW6.layouts.length;
  // review M2: AX-LISP / VBA runs chained after one another (events) read the latest pending changes,
  // not the ones of the render that started them; applyLisp updates this at once
  const pend = useRef({ newLayers, pendingTables, pendingLayerOps, pendingDicts, pendingW6 });
  pend.current = { newLayers, pendingTables, pendingLayerOps, pendingDicts, pendingW6 };
  const [dcl, setDcl] = useState<{ run: DialogRun; done: (status: number) => void; fail: (e: unknown) => void } | null>(null);
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number; world: [number, number] } | null>(null);
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
        setHist(initHistory(d.entities.map(fillOutline))); // underlays get their frame from the page size the server sends
        setHidden(new Set(d.layers.filter((l) => !l.visible).map((l) => l.name)));
        const open = (l: Layer) => !l.locked && l.visible;
        const current = d.layers.find((l) => l.name === d.current_layer && open(l)); // the layer the drawing was saved on
        setLayer((current ?? d.layers.find(open) ?? d.layers.find((l) => !l.locked))?.name ?? "");
        if (select.length) setSelected(new Set(select));
      })
      .catch((e) => live && setError(errText(e)));
    return () => {
      live = false;
    };
    // ?select applies once per load (the Editor is keyed by revision); later clicks own the selection
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revisionId]);

  // A5: start-up files, once the drawing is open (a signed-in designer; the library is the PC's own)
  useEffect(() => {
    if (started.current || !data || !me) return;
    started.current = true;
    void libraryList().then(
      (items) =>
        runStartup(items, lispSession.current, { layers: data.layers.filter((l) => !l.locked).map((l) => l.name), layer, files: libraryFiles }).then((notes) => {
          if (notes.length) setPrompt(notes.join(" · "));
        }),
      () => undefined, // no library (or no rights to read it): nothing to start
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, me]);

  const ents = hist?.present ?? EMPTY_ENTS;
  const sheet = sheetName === null ? undefined : data?.sheets?.find((s) => s.name === sheetName);
  const locked = useMemo(() => new Set(data?.layers.filter((l) => l.locked).map((l) => l.name)), [data]);
  const selEntities = useMemo(() => ents.filter((e) => selected.has(e.handle)), [ents, selected]);
  const selEdit = useMemo<Sel[]>(
    () => selEntities.flatMap((e) => (e.geom && !locked.has(e.layer) ? [{ handle: e.handle, layer: e.layer, geom: e.geom }] : [])),
    [selEntities, locked],
  );
  // B-4: what the clean-up looks at (visible, unlocked, editable); lines merge only within one style
  const cleanEnts = useMemo(
    () => (cleanup ? ents.flatMap((e) => (e.geom && !hidden.has(e.layer) && !locked.has(e.layer) ? [{ handle: e.handle, layer: e.layer, geom: e.geom, style: [e.aci, e.linetype, e.lwt, e.lts, e.tc, e.tr].join("|") }] : [])) : []),
    [cleanup, ents, hidden, locked],
  );
  // block content for previews: the drawing's blocks and the ones made in this session
  const blockPaths = useMemo(() => {
    const m = new Map((data?.blocks ?? []).map((b) => [b.name.toUpperCase(), b.paths]));
    for (const b of newBlocks) // in order: a block made earlier in the session may sit inside a later one (C-3)
      m.set(
        b.name.toUpperCase(),
        b.entities.flatMap((e) => (e.geom.type === "INSERT" ? placeBlock(m.get(e.geom.name.toUpperCase()) ?? [], e.geom) : toPaths(e.geom))).map((ln) => ln.map(([x, y]): Pt => [x - b.base[0], y - b.base[1]])),
      );
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
  const ulOps = useMemo(() => underlayOps(data?.entities ?? EMPTY_ENTS, ents), [data, ents]);
  // AX-LISP / VBA changes to tables, layers, dictionaries, blocks, groups and layouts are unsaved edits too
  const dirty = !isEmptyEdit(diff) || ulOps.length > 0 || pendingTables.length > 0 || pendingLayerOps.length > 0 || pendingDicts.length > 0 || w6Pending > 0;
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
    commit(resultsOn(ents, results));
  };

  // command results on a list of entities (applyResults commits; AX-LISP adds object changes first)
  const resultsOn = (base: RenderEntity[], results: Result[]): RenderEntity[] => {
    let next = base;
    for (const r of results) {
      if (r.block) {
        const { name, base, handles } = r.block;
        const parts = next.filter((e) => e.geom && handles.includes(e.handle));
        // C-3: a block reference goes in with its attribute values
        const def: BlockDef = { name, base, entities: parts.map((e) => ({ layer: e.layer, geom: e.geom!, ...(e.aci !== undefined ? { color: e.aci } : {}), ...(e.linetype ? { linetype: e.linetype } : {}), ...(e.geom!.type === "INSERT" && e.att ? { attributes: e.att } : {}) })) };
        setNewBlocks((bs) => [...bs, def]);
        blockPaths.set(name.toUpperCase(), parts.flatMap((e) => (e.geom!.type === "INSERT" ? placed(e.geom!) : toPaths(e.geom!))).map((ln) => ln.map(([x, y]): Pt => [x - base[0], y - base[1]])));
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
          const dash = dashFor(data?.linetype_dash, data?.layers ?? [], undefined, ly); // BYLAYER
          return { handle: `new-${nextId.current++}`, type: c.geom.type, layer: ly, color: data?.layers.find((l) => l.name === ly)?.color ?? null, paths: placed(c.geom), geom: c.geom, dash };
        });
        next = [...next, ...made];
      }
    }
    return next;
  };

  // 2D automation A1: entmake/entmod/entdel/vla- changes from the object model, styled like the
  // property inspector does (ACI colour or the layer's, the linetype's dashes)
  const styled = (e: RenderEntity): RenderEntity => {
    const own = ACI_COLORS.find(([a]) => a === e.aci);
    const layerColor = data?.layers.find((l) => l.name === e.layer)?.color ?? null;
    return { ...e, color: e.aci === undefined || e.aci === BYLAYER || !own ? layerColor : own[2], dash: dashFor(data?.linetype_dash, data?.layers ?? [], e.linetype, e.layer, e.lts) };
  };
  const changesOn = (base: RenderEntity[], ch: Changes | null): RenderEntity[] => {
    if (!ch || (!ch.created.length && !ch.updated.length && !ch.deleted.length)) return base;
    const gone = new Set(ch.deleted);
    const upd = new Map(ch.updated.map((u) => [u.handle, u]));
    const next = base.flatMap((e): RenderEntity[] => {
      if (gone.has(e.handle)) return [];
      const u = upd.get(e.handle);
      if (!u) return [e];
      const geomPart = serverDrawn(u.geom.type) ? { paths: placed(u.geom), text: undefined, fill: undefined } : {};
      return [styled({ ...e, ...geomPart, geom: u.geom, layer: u.layer, aci: u.aci, linetype: u.linetype, lwt: u.lwt, tc: u.tc, tr: u.tr, inv: u.inv, lts: u.lts, xd: u.xd, att: u.att })];
    });
    const made = ch.created.map((c): RenderEntity => styled({ handle: `new-${nextId.current++}`, type: c.geom.type, layer: c.layer, color: null, paths: placed(c.geom), geom: c.geom, aci: c.aci, linetype: c.linetype, ...entProps(c) }));
    return [...next, ...made];
  };

  // W2: what AX-LISP reads beyond the editable geometry (XDATA, dictionaries) comes from the saved revision
  // (one object per revision: AX-LISP caches its reads on it, and a revision never changes)
  const lispObjects = useMemo((): NonNullable<Host["objects"]> => ({
    entity: async (handle) => {
      try {
        return await api<{ type: string; tags: [number, unknown][] }>(`/revisions/${encodeURIComponent(revisionId)}/entities/${encodeURIComponent(handle)}`);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
    dict: async (path, owner) => {
      const q = new URLSearchParams(path.map((p) => ["path", p]));
      if (owner) q.set("handle", owner);
      try {
        return await api(`/revisions/${encodeURIComponent(revisionId)}/dictionaries?${q}`);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      }
    },
  }), [revisionId]);
  /** The drawing as objects for one AX-LISP run: editable entities on visible layers. */
  const lispModel = (base: RenderEntity[]) => {
    const m = new DrawingModel(
      base.flatMap((e) => (e.geom && !hidden.has(e.layer) ? [{ handle: e.handle, layer: e.layer, geom: e.geom, ...(e.aci !== undefined ? { aci: e.aci } : {}), ...(e.linetype ? { linetype: e.linetype } : {}), ...entProps(e), ...(e.xdict ? { xdict: true } : {}), ...(e.xdq ? { xdq: true } : {}), ...(e.xdtree ? { xdtree: e.xdtree } : {}) }] : [])),
      [...(data?.layers ?? []).map((l) => ({ name: l.name, color: l.aci ?? null, locked: l.locked, linetype: l.linetype, lwt: l.lwt, plot: l.plot, off: l.off, frozen: l.frozen, description: l.description })), ...pend.current.newLayers.map((name) => ({ name, color: 7, locked: false }))].map((l) => {
        const p = pend.current.pendingLayerOps.find((o) => o.name === l.name); // not yet saved changes of earlier runs
        return p ? { ...l, ...(p.color !== undefined ? { color: p.color } : {}), ...(p.locked !== undefined ? { locked: p.locked } : {}), ...(p.linetype ? { linetype: p.linetype } : {}), ...(p.lineweight !== undefined ? { lwt: p.lineweight } : {}), ...(p.plot !== undefined ? { plot: p.plot } : {}), ...(p.off !== undefined ? { off: p.off } : {}), ...(p.frozen !== undefined ? { frozen: p.frozen } : {}), ...(p.description !== undefined ? { description: p.description } : {}) } : l;
      }),
      layer,
      withTableOps(data?.tables ?? NO_TABLES, pend.current.pendingTables),
    );
    m.seedDicts(pend.current.pendingDicts); // W2 review M3: earlier runs' unsaved dictionary changes
    m.ids = data?.object_ids;
    m.dicts = data?.dicts;
    m.blockInfos = [...(data?.blocks ?? []).map((b) => ({ name: b.name, ...(b.attdefs ? { attdefs: b.attdefs } : {}) })), ...newBlocks.map((b) => ({ name: b.name }))];
    m.seedW6(pend.current.pendingW6);
    m.pickfirst = [...selected].filter((h) => !h.startsWith("new-"));
    return m;
  };

  /** Applies an AX-LISP outcome as one undo step: its command segments, then its object changes. */
  const applyLisp = (out: Outcome, base: RenderEntity[]): { applied: number; error: string | null; next: RenderEntity[] } => {
    const results: Result[] = [];
    for (const seg of out.segments) {
      const r = runInputs(seg.inputs, seg.sel, ctx);
      if (r.error) return { applied: 0, error: `'${r.error.input || "Enter"}' — ${r.error.message}`, next: base };
      const ly = seg.layer ?? layer;
      results.push(...r.results.map((x) => (x.created ? { ...x, created: x.created.map((c) => ({ ...c, layer: c.layer ?? ly })) } : x)));
    }
    const ch = out.changes;
    // W2 review M2: the server takes 1,000 dictionary operations per save — refused before anything is applied
    const P = pend.current;
    if (ch?.dictionaries?.length && P.pendingDicts.length + ch.dictionaries.length > 1000) return { applied: 0, error: "저장하지 않은 사전 변경이 1,000개를 넘습니다. 먼저 저장하세요", next: base };
    // W6 review M1: what the edit API takes in one save, refused before anything is applied
    const w6 = { blocks: [...P.pendingW6.blocks, ...(ch?.blocks ?? [])], groups: [...P.pendingW6.groups, ...(ch?.groups ?? [])], layouts: [...P.pendingW6.layouts, ...(ch?.layouts ?? [])] };
    const w6Err =
      newBlocks.length + w6.blocks.filter((b) => b.op === "create").length > 100 ? "블록 정의는 한 번 저장에 100개까지입니다"
      : w6.blocks.filter((b) => b.op !== "create").length + w6.layouts.filter((o) => o.op !== "create").length > 20 ? "블록·레이아웃 이름 변경·삭제는 한 번 저장에 20개까지입니다"
      : w6.groups.length > 1000 ? "그룹 변경은 한 번 저장에 1,000개까지입니다"
      : w6.layouts.length > 100 ? "레이아웃 변경은 한 번 저장에 100개까지입니다"
      : w6.blocks.reduce((n, b) => n + (b.entities?.length ?? 0) + (b.attdefs?.length ?? 0), 0) + w6.groups.reduce((n, g) => n + (g.handles?.length ?? 0), 0) > 10_000 ? "블록 내용·그룹 멤버는 한 번 저장에 합쳐 10,000개까지입니다"
      : null;
    if (w6Err) return { applied: 0, error: `${w6Err}. 먼저 저장하세요`, next: base };
    const next = changesOn(resultsOn(base, results), ch);
    const n = results.length + (ch ? ch.created.length + ch.updated.length + ch.deleted.length : 0);
    if (next !== base) commit(next);
    // the state for the next render, and pend at once for runs that follow before it (events)
    if (ch?.layers.length) setNewLayers((P.newLayers = [...P.newLayers, ...ch.layers.filter((l) => !P.newLayers.includes(l))]));
    if (ch?.tables.length) setPendingTables((P.pendingTables = [...P.pendingTables, ...ch.tables]));
    if (ch?.layerOps.length) setPendingLayerOps((P.pendingLayerOps = [...P.pendingLayerOps, ...ch.layerOps]));
    if (ch?.dictionaries?.length) setPendingDicts((P.pendingDicts = [...P.pendingDicts, ...ch.dictionaries]));
    if (ch?.blocks?.length || ch?.groups?.length || ch?.layouts?.length) setPendingW6((P.pendingW6 = w6));
    return { applied: n, error: null, next };
  };

  // reactors: after a command the user ran, :vlr-commandEnded and :vlr-modified / :vlr-erased run
  // (changes made by AX-LISP itself do not fire them: no loops)
  const react = async (tool: string, results: Result[], base: RenderEntity[]): Promise<RenderEntity[]> => {
    const live = lispSession.current.reactors.filter((r) => r.active);
    if (!live.length || !results.length) return base;
    const events: ReactorEvent[] = [{ kind: "command", name: ":VLR-COMMANDENDED", command: tool }];
    for (const r of results) {
      for (const u of r.updated ?? []) events.push({ kind: "object", name: ":VLR-MODIFIED", handle: u.handle });
      for (const h of r.deleted ?? []) events.push({ kind: "object", name: ":VLR-ERASED", handle: h });
    }
    let now = base;
    for (const ev of events.slice(0, 100)) { // W3 review M4: one user command wakes at most 100 reactions
      try {
        const out = await fireReactors(lispSession.current, ev, { model: lispModel(now), layer, layers: (data?.layers ?? []).filter((l) => !l.locked).map((l) => l.name) });
        if (!out) continue;
        const r = applyLisp(out, now);
        if (r.error) {
          setPrompt(`반응자 오류: ${r.error}`);
          return now;
        }
        now = r.next;
        if (out.log.length) setPrompt(out.log.at(-1)!);
      } catch (e) {
        setPrompt(`반응자 오류: ${e instanceof Error ? e.message : String(e)}`);
        return now;
      }
    }
    return now;
  };

  // W5: a VBA project with ThisDrawing_* handlers stays loaded (like VBALOAD) and hears the
  // document's events: after a user command, around an AX-LISP run (VBAUNLOAD drops it)
  const vbaProject = useRef<string | null>(null);
  const [vbaLoaded, setVbaLoaded] = useState<string[] | null>(null); // the handlers, shown with a 해제 button
  const unloadVba = (why?: string) => {
    vbaProject.current = null;
    setVbaLoaded(null);
    if (why) setPrompt(why);
  };
  useEffect(() => () => void (vbaProject.current = null), [revisionId]); // review L5: a project belongs to one revision
  const EVENT_BUDGET_MS = 3000; // review H1: all the events of one user command, together
  const fireVba = async (event: VbaDocEvent, args: (string | number | { t: "ent"; handle: string })[], now: RenderEntity[], until = Date.now() + EVENT_BUDGET_MS): Promise<RenderEntity[]> => {
    const src = vbaProject.current;
    if (!src || !canEdit || sheet) return now;
    try {
      const out = await fireVbaEvent(src, event, args, { model: lispModel(now) }, until - Date.now());
      if (!out) return now;
      const r = applyLisp({ segments: [], log: out.log, commands: [], value: [], changes: out.changes }, now);
      if (r.error) throw new Error(r.error);
      if (out.log.length) setPrompt(out.log.at(-1)!);
      return r.next;
    } catch (e) {
      // review H1: a failing or slow handler is not run again on the next command
      unloadVba(`VBA 이벤트 오류 (${event}): ${e instanceof Error ? e.message : String(e)} — 이벤트 처리기를 내렸습니다`);
      return now;
    }
  };
  const vbaReact = async (tool: string, results: Result[], base: RenderEntity[]) => {
    if (!vbaProject.current) return;
    const until = Date.now() + EVENT_BUDGET_MS;
    // AX-CAD hears a command when it ends: BeginCommand and EndCommand come together, objects between
    let now = await fireVba("BeginCommand", [tool], base, until);
    const made = results.reduce((n, r) => n + (r.created?.length ?? 0), 0);
    const events: [VbaDocEvent, string | number | { t: "ent"; handle: string }][] = [
      ...(made ? base.filter((e) => e.handle.startsWith("new-")).slice(-made) : []).map((e): [VbaDocEvent, { t: "ent"; handle: string }] => ["ObjectAdded", { t: "ent", handle: e.handle }]),
      ...results.flatMap((r) => (r.updated ?? []).map((u): [VbaDocEvent, { t: "ent"; handle: string }] => ["ObjectModified", { t: "ent", handle: u.handle }])),
      ...results.flatMap((r) => (r.deleted ?? []).map((h): [VbaDocEvent, number] => ["ObjectErased", /^[0-9A-F]+$/i.test(h) ? parseInt(h, 16) : 0])),
    ];
    for (const [ev, arg] of events.slice(0, 100)) {
      if (!vbaProject.current) return; // unloaded by an error on the way
      now = await fireVba(ev, [arg], now, until); // one command wakes at most 100
    }
    await fireVba("EndCommand", [tool], now, until);
  };
  // review M2: reactions run one after another, never two at once
  const eventChain = useRef<Promise<unknown>>(Promise.resolve());
  const userResults = (tool: string, results: Result[]) => {
    if (!results.length) return;
    const next = resultsOn(ents, results);
    commit(next);
    eventChain.current = eventChain.current.then(() => react(tool, results, next).then((now) => vbaReact(tool, results, now))).catch(() => undefined);
  };

  // Track 11 L2: an AX-LISP get*/entsel/ssget waits here for a click or a typed value
  type Ask = { kind: InputKind | "entity" | "select"; base?: Pt; resolve: (v: unknown) => void };
  const askRef = useRef<Ask | null>(null);
  const selRef = useRef<Sel[]>([]);
  selRef.current = selEdit;
  const ask = <X,>(kind: Ask["kind"], message: string, base?: Pt) =>
    new Promise<X>((resolve) => {
      setTs({ tool: "SELECT", pts: [] });
      askRef.current = { kind, base, resolve: resolve as (v: unknown) => void };
      setPrompt(`LISP: ${message || { point: "점", dist: "거리", real: "실수", int: "정수", string: "문자", angle: "각도(도)", entity: "객체 선택", select: "객체를 선택하고 Enter" }[kind]}  (Esc 취소)`);
    });
  const answer = (v: unknown) => {
    const a = askRef.current;
    askRef.current = null;
    setPrompt("");
    a?.resolve(v);
  };
  /** A click or typed text for the waiting question: true = it was used. */
  const answerWith = (p: Pt | null, text: string | null, raw?: Pt): boolean => {
    const a = askRef.current;
    if (!a) return false;
    if (a.kind === "select") return text !== null && text.trim() === "" ? (answer(selRef.current), true) : false; // clicks select as usual
    if (text !== null && text.trim() === "") return answer(null), true; // Enter: nil, like AutoLISP
    if (a.kind === "entity") {
      const e = p && nearest(raw ?? p, scale, (x) => !!x.geom && !locked.has(x.layer));
      if (!e?.geom) return setPrompt("LISP: 그 위치에 편집할 수 있는 객체가 없습니다"), true;
      return answer({ sel: { handle: e.handle, layer: e.layer, geom: e.geom }, at: p }), true;
    }
    let q = p;
    if (text !== null) {
      const m = /^\s*(@)?(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$/.exec(text);
      if (m) q = m[1] && a.base ? [a.base[0] + Number(m[2]), a.base[1] + Number(m[3])] : [Number(m[2]), Number(m[3])];
    }
    if (a.kind === "point") return q ? (answer(q), true) : (setPrompt("LISP: x,y 또는 @dx,dy로 입력하세요"), true);
    if (q && (a.kind === "dist" || a.kind === "angle")) {
      if (!a.base) return (a.base = q), setPrompt("LISP: 두 번째 점"), true;
      return answer(a.kind === "dist" ? Math.hypot(q[0] - a.base[0], q[1] - a.base[1]) : Math.atan2(q[1] - a.base[1], q[0] - a.base[0])), true;
    }
    if (text === null) return true; // a click while a number or text is asked: ignored
    if (a.kind === "string") return answer(text), true;
    const n = Number(text);
    if (!Number.isFinite(n) || (a.kind === "int" && !Number.isInteger(n))) return setPrompt(`LISP: ${a.kind === "int" ? "정수" : "숫자"}를 입력하세요`), true;
    return answer(a.kind === "angle" ? (n * Math.PI) / 180 : n), true;
  };

  // A4: one line per accepted input (a rejected one would be rejected on replay too), comments
  // for what a script cannot replay; a full journal stops and opens like a finished one
  const rec = (line: string) => {
    const j = journal.current;
    if (!j) return;
    if (!record(j, line)) return stopJournal("기록이 10,000줄에 닿아 멈췄습니다");
    setJournalLines(j.lines.length);
  };
  const jot = (text: string) => {
    const j = journal.current;
    if (!j) return;
    if (!journalNote(j, text)) return stopJournal("기록이 10,000줄에 닿아 멈췄습니다");
    setJournalLines(j.lines.length);
  };
  const startJournal = () => {
    if (!canEdit || sheet) return setPrompt("읽기 전용 화면입니다");
    if (ts.tool !== "SELECT") return setPrompt("진행 중인 명령을 마친 뒤 기록을 시작하세요");
    journal.current = newJournal();
    setJournalLines(0);
    setPrompt("명령 기록 시작 — 명령·클릭·키 입력을 .axs로 기록합니다 (끝내기: '기록 중지' 또는 JOURNAL)");
  };
  const cancelJournal = () => {
    if (!journal.current) return;
    journal.current = null; // FreeCAD's macro recording can be cancelled: the lines are dropped
    setJournalLines(null);
    setPrompt("명령 기록을 버렸습니다");
  };
  const stopJournal = (why?: string) => {
    const j = journal.current;
    if (!j) return;
    journal.current = null;
    setJournalLines(null);
    setScriptInit(journalText(j, `${doc?.doc_no ?? "도면"} · 리비전 ${revisionId}`));
    setAutomation("SCRIPT");
    setPrompt(why ?? `명령 기록 종료 · ${j.lines.length}줄`);
  };

  const dispatch = (ev: ToolEvent) => {
    if ((ev.kind === "esc" || ev.kind === "enter") && ul.handleKey(ev.kind)) return;
    if (ev.kind === "esc" && askRef.current) return answer(askRef.current.kind === "select" ? [] : null);
    if (ts.tool === "SELECT") {
      if (ev.kind === "esc") setSelected(new Set());
      return;
    }
    // pick tools get the editable entity under the pick instead of the selection (OFFSET only for its object pick)
    const picks = PICK_TOOLS.includes(ts.tool) || (ts.tool === "OFFSET" && ts.n !== undefined && !ts.target);
    const under = picks && ev.kind === "point" ? nearest(ev.p, scale, (e) => !!e.geom && !locked.has(e.layer)) : null;
    const out = step(ts, ev, under?.geom ? [{ handle: under.handle, layer: under.layer, geom: under.geom }] : picks ? [] : selEdit, ctx);
    if (journal.current && (out.state !== ts || out.results.length)) {
      // a pick is written onto the picked entity, so the replay picks the same one
      const line = eventText(under?.geom && ev.kind === "point" ? { kind: "point", p: onPaths(hitPaths({ geom: under.geom, paths: [] }), ev.p) } : ev);
      if (line === null) jot("이 문자열은 스크립트로 재생되지 않습니다 (; 로 시작하거나 앞뒤 공백·^C)");
      else rec(line);
    }
    setTs(out.state);
    setPrompt(out.prompt);
    userResults(ts.tool, out.results);
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
      if (now.results.length) {
        jot(`선택 ${selEdit.length}개 — 재생할 때의 선택에 적용`);
        rec(toolText(t));
        setSelected(new Set());
      }
      userResults(t, now.results);
      return;
    }
    const out = start(t, selEdit.length);
    // recorded when it started (or, for SELECT, when it ended a command), like a typed command
    if (out.state.tool !== "SELECT" || (t === "SELECT" && ts.tool !== "SELECT")) {
      if (SEL_TOOLS.includes(t)) jot(`선택 ${selEdit.length}개 — 재생할 때의 선택에 적용`);
      rec(toolText(t));
    }
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
    if (ul.handlePick(p)) return; // an underlay pick (clip, alignment, attach point) is open
    if (answerWith(p, null, raw)) return;
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
    const targets = selEntities.filter((e) => (e.geom || e.type === "UNDERLAY") && !locked.has(e.layer));
    if (!targets.length) return setPrompt(selEntities.length ? "삭제할 수 없습니다: 잠긴 레이어 또는 읽기 전용 객체" : "삭제할 객체를 선택하세요");
    const gone = new Set(targets.map((e) => e.handle));
    commit(ents.filter((e) => !gone.has(e.handle)));
    jot(`삭제 ${targets.length}개 (Delete 키)`);
    setSelected(new Set());
    setPrompt(`${targets.length}개 삭제` + (targets.length < selEntities.length ? " (잠금/읽기 전용 객체 제외)" : ""));
  };

  const applyPatch = (handle: string, patch: { layer?: string; geom?: Geom; aci?: number; linetype?: string }) => {
    if (!canEdit) return;
    const e = ents.find((x) => x.handle === handle);
    if (!e || locked.has(e.layer)) return setPrompt("잠긴 레이어의 객체는 수정할 수 없습니다");
    if (patch.layer && locked.has(patch.layer)) return setPrompt("잠긴 레이어로는 이동할 수 없습니다");
    if (patch.geom && JSON.stringify(patch.geom) === JSON.stringify(e.geom)) return;
    const aci = patch.aci ?? e.aci;
    const own = ACI_COLORS.find(([a]) => a === aci);
    const layerColor = data?.layers.find((l) => l.name === (patch.layer ?? e.layer))?.color ?? null;
    const color = aci === BYLAYER ? layerColor : own ? own[2] : patch.layer ? (layerColor ?? e.color) : e.color;
    // a new linetype (or a BYLAYER entity's new layer) shows at once, not only after saving
    const restyle = patch.linetype !== undefined || patch.layer !== undefined;
    const dash = restyle ? dashFor(data?.linetype_dash, data?.layers ?? [], patch.linetype ?? e.linetype, patch.layer ?? e.layer, e.lts) : e.dash;
    commit(ents.map((x) => (x.handle === handle ? { ...x, ...patch, color, dash } : x)));
    jot("속성 창에서 객체 속성 변경");
    setPrompt("속성을 적용했습니다");
  };

  // layer changes save at once, together with any pending entity edits
  const save = async (ops?: LayerOp[]) => {
    if (!canEdit || (!dirty && !ops && !newLayers.length && !pendingTables.length && !pendingLayerOps.length && !pendingDicts.length && !w6Pending) || saving) return;
    setSaving(true);
    setNote(null);
    // layers made by AX-LISP (Layers.Add) go first, so the entities on them have their layer
    const later = pendingLayerOps.map((o): LayerOp => o); // AX-LISP / VBA changes to layer records
    const layers = newLayers.length || ops || later.length ? [...newLayers.map((name): LayerOp => ({ name, create: true })), ...later, ...(ops ?? [])] : undefined;
    try {
      const json = { ...diff, ...(layers ? { layers } : {}), ...(newBlocks.length || pendingW6.blocks.length ? { blocks: [...newBlocks, ...pendingW6.blocks] } : {}), ...(pendingW6.groups.length ? { groups: pendingW6.groups } : {}), ...(pendingW6.layouts.length ? { layouts: pendingW6.layouts } : {}), ...(ulOps.length ? { underlays: ulOps } : {}), ...(pendingTables.length ? { tables: pendingTables } : {}), ...(pendingDicts.length ? { dictionaries: pendingDicts } : {}) };
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
    if (answerWith(null, text)) return;
    // 2D underlay: a typed point answers an open pick; Enter ends a polygon clip; PDFATTACH, UCLIP, UALIGN, UOSNAP...
    const pt = /^\s*([-+]?\d*\.?\d+)\s*,\s*([-+]?\d*\.?\d+)\s*$/.exec(text);
    if (ul.picking && pt) return void ul.handlePick([Number(pt[1]), Number(pt[2])]);
    if (ul.picking && !text.trim() && ul.handleKey("enter")) return;
    if (ul.command(text)) return;
    // A4: JOURNAL starts / ends the command journal (the name NX gives its recorder)
    if (/^-?JOURNAL\s+(C|CANCEL)$/i.test(text.trim()) && ts.tool === "SELECT") return journal.current ? cancelJournal() : setPrompt("기록 중이 아닙니다");
    if (/^-?JOURNAL$/i.test(text.trim()) && ts.tool === "SELECT") return journal.current ? stopJournal() : startJournal();
    // A5: SCRIPT <name> runs a recorded/written .axs of the PC library (AutoCAD's -SCRIPT)
    const scr = /^-?SCRIPT\s+(\S{1,60})$/i.exec(text.trim());
    if (scr && ts.tool === "SELECT") {
      void libraryScript(scr[1]).then(
        (f) => {
          if (!f) return setPrompt(`라이브러리에 없는 스크립트: ${scr[1]}`);
          const err = runScriptText(f.source);
          setPrompt(err ? `${f.name}: ${err}` : `${f.name} 실행`);
        },
        (e) => setPrompt(errText(e)),
      );
      return;
    }
    const p = parseInput(text, ts);
    // A2: VBARUN <macro> runs a Sub of a .bas file in the PC library (AutoCAD's -VBARUN)
    if (/^-?VBAUNLOAD$/i.test(text.trim()) && ts.tool === "SELECT") {
      unloadVba("VBA 프로젝트를 내렸습니다 (이벤트 처리기 해제)"); // W5: the loaded project stops hearing events
      return;
    }
    const vbarun = /^-?VBARUN\s+([\w가-힣.]{1,60})$/i.exec(text.trim());
    if (vbarun && ts.tool === "SELECT") {
      const want = vbarun[1].replace(/^.*\./, ""); // "Module1.Plate" -> "Plate"
      void api<{ items: { name: string; source: string }[] }>("/lisp").then(
        (lib) => {
          const file = lib.items.find((f) => {
            if (!f.name.toLowerCase().endsWith(".bas")) return false;
            try {
              return vbaMacros(parseVba(f.source)).some((m) => m.toLowerCase() === want.toLowerCase());
            } catch {
              return false;
            }
          });
          if (!file) return setPrompt(`라이브러리에 없는 VBA 매크로: ${want}`);
          const classes = lib.items.filter((f) => f.name.toLowerCase().endsWith(".cls")).map((f) => f.source); // W5: class modules
          void runVbaText(file.source, want, classes).then((r) => setPrompt(r.error ? `${want}: ${r.error}` : `${want} 실행 · 변경 ${r.applied}건${r.log.length ? ` · ${r.log.at(-1)}` : ""}`));
        },
        (e) => setPrompt(errText(e)),
      );
      return;
    }
    // Track 11 L5: a c: command of this PC's LISP library, typed by name like a built-in command
    const name = text.trim().toUpperCase();
    if ("error" in p && ts.tool === "SELECT" && /^[A-Z0-9_-]{1,40}$/.test(name)) {
      const unknown = p.error;
      void api<{ items: { source: string; commands: string[] }[] }>("/lisp").then(
        (lib) => {
          const file = lib.items.find((f) => f.commands.includes(name));
          if (!file) return setPrompt(unknown);
          void runLispText(file.source, name).then((r) => setPrompt(r.error ? `${name}: ${r.error}` : `${name} 실행 · 변경 ${r.applied}건${r.log.length ? ` · ${r.log.at(-1)}` : ""}`));
        },
        () => setPrompt(unknown),
      );
      return;
    }
    if ("error" in p) setPrompt(p.error);
    else if ("tool" in p) changeTool(p.tool);
    else dispatch(p.ev);
  };

  // 2D underlay U3-U5: PDF/DWFx references (attach, edit, calibrate, clip, snap, import as objects)
  const ensureLayer = (name: string): string => {
    const known = [...(data?.layers.map((l) => l.name) ?? []), ...newLayers].find((n) => n.toUpperCase() === name.toUpperCase());
    if (known) return known;
    setNewLayers((ls) => [...ls, name]);
    return name;
  };
  const ul = useUnderlays({
    ents,
    baseEnts: data?.entities ?? EMPTY_ENTS,
    commit,
    nextId,
    projectId: doc?.project_id,
    extents: data?.extents ?? { min: [0, 0], max: [1, 1] },
    layer,
    layers: [...(data?.layers ?? []).map((l) => ({ name: l.name, locked: l.locked })), ...newLayers.map((name) => ({ name, locked: false }))],
    hidden,
    locked,
    canEdit,
    selected,
    setSelected,
    setPrompt,
    note: (t) => jot(t),
    ensureLayer,
    applyCreated: (items) => applyResults([{ created: items }]),
  });

  // Track 9 Q4: a .axs script, like a Copilot command, is one undo step; errors name the line
  const runScriptText = (text: string): string | null => {
    if (!canEdit || sheet) return "읽기 전용 화면입니다";
    if (!layer) return "작도 가능한(잠기지 않은) 레이어가 없습니다";
    // A4: the handles applyResults will give new entities, so a later pick in the script can name them
    const out = runScript(text, selEdit, { ...ctx, nextHandle: nextId.current, layer });
    if (out.error) return out.line ? `${out.line}번째 줄 '${out.error.input || "Enter"}' — ${out.error.message}` : out.error.message;
    jot("스크립트(.axs) 실행 — 그 내용은 기록되지 않음");
    setTs(out.state);
    setPrompt(out.prompt || "스크립트를 실행했습니다");
    applyResults(out.results);
    return null;
  };
  // a parametric block: a new block for new values (reused when the same values exist), then
  // the reference — replacing the selected one when its values were edited
  const placeParamBlock = (b: Placed, at: [number, number], replace?: string) => {
    const known = new Set([...(data?.blocks ?? []), ...newBlocks].map((x) => x.name.toUpperCase()));
    if (!known.has(b.name.toUpperCase())) {
      setNewBlocks((bs) => [...bs, { name: b.name, base: [0, 0], entities: b.geoms.map((geom) => ({ layer, geom })), description: b.description }]);
      blockPaths.set(b.name.toUpperCase(), b.geoms.flatMap((g) => toPaths(g)));
    }
    const ins: Geom = { type: "INSERT", name: b.name, insert: at, xscale: 1, yscale: 1, rotation: 0 };
    applyResults([{ created: [{ geom: ins }], ...(replace ? { deleted: [replace] } : {}) }]);
    if (replace) setSelected(new Set());
    jot(`파라미터 블록 ${b.name} 삽입 — 스크립트로 재생되지 않음`);
    setPrompt(`${b.name} 삽입`);
  };
  const paramOf = (name: string) => {
    const fromFile = data?.blocks?.find((x) => x.name.toUpperCase() === name.toUpperCase())?.param;
    if (fromFile) return fromFile;
    const desc = newBlocks.find((x) => x.name.toUpperCase() === name.toUpperCase())?.description;
    try {
      const d = desc ? JSON.parse(desc) : null;
      return d?.axcad_pb === 1 ? { template: d.template as string, values: d.values as Record<string, string>, state: (d.state ?? null) as string | null } : undefined;
    } catch {
      return undefined;
    }
  };
  const openParamBlock = () => {
    const one = selEdit.length === 1 && selEdit[0].geom.type === "INSERT" ? selEdit[0] : null;
    const p = one && one.geom.type === "INSERT" ? paramOf(one.geom.name) : undefined;
    if (one && one.geom.type === "INSERT" && p) setAutomation({ initial: { ...p, at: one.geom.insert }, replace: one.handle });
    else setAutomation({});
  };

  // Track 9: a Copilot command runs through the same reducer as typing, applied as one undo step
  // Track 11: an AX-LISP run is applied like a script — every segment, then one undo step
  const runLispText = async (src: string, command?: string): Promise<LispRun> => {
    const fail = (error: string): LispRun => ({ error, log: [], commands: [], applied: 0 });
    if (!canEdit || sheet) return fail("읽기 전용 화면입니다");
    if (!layer) return fail("작도 가능한(잠기지 않은) 레이어가 없습니다");
    const open = (data?.layers ?? []).filter((l) => !l.locked).map((l) => l.name);
    const base = await fireVba("BeginLisp", [command ?? "(AX-LISP)"], ents); // review L4: a name, never the source
    let out;
    try {
      out = await runLisp(
        src,
        {
          layers: [...open, ...newLayers],
          layer,
          model: lispModel(base),
          files: libraryFiles,
          settings: lispSettings,
          objects: lispObjects,
          input: (kind, message, b) => ask(kind, message, b),
          entsel: (message) => ask("entity", message),
          ssget: async (mode) => (selRef.current.length && mode !== "X" ? selRef.current : ask<Sel[]>("select", "")),
          dialog: (run) => new Promise<number>((done, fail) => setDcl({ run, done, fail })).finally(() => setDcl(null)),
        },
        command,
        lispSession.current,
      );
    } catch (e) {
      void fireVba("LispCancelled", [], base);
      return fail(e instanceof LispError ? (e.line ? `${e.line}번째 줄: ${e.message}` : e.message) : String(e));
    }
    const r = applyLisp(out, base);
    if (r.error) return { ...fail(r.error), log: out.log, commands: out.commands };
    void fireVba("EndLisp", [], r.next);
    if (r.applied) setPrompt("LISP를 실행했습니다");
    if (r.applied) jot(`AX-LISP 실행${command ? ` (${command})` : ""} — 변경 ${r.applied}건, 스크립트로 재생되지 않음`);
    return { error: null, log: out.log, commands: out.commands, applied: r.applied };
  };

  // 2D automation A2: a VBA macro on the same object model, applied the same way (one undo step)
  const runVbaText = async (src: string, macro?: string, classes: string[] = []): Promise<LispRun> => {
    const fail = (error: string): LispRun => ({ error, log: [], commands: [], applied: 0 });
    if (!canEdit || sheet) return fail("읽기 전용 화면입니다");
    if (!layer) return fail("작도 가능한(잠기지 않은) 레이어가 없습니다");
    const base = ents;
    try {
      const out = await runVba(
        src,
        {
          model: lispModel(base),
          input: (kind, message, b) => ask(kind, message, b),
          entsel: async (message) => {
            const r = await ask<{ sel: Sel; at: Pt } | null>("entity", message);
            return r ? { handle: r.sel.handle, at: r.at } : null;
          },
        },
        macro,
        classes,
      );
      const r = applyLisp({ segments: [], log: out.log, commands: [], value: [], changes: out.changes }, base);
      // W5: a project with ThisDrawing_* handlers stays loaded for its events, when the user says so (review H1)
      const handlers = r.error || vbaProject.current === src ? [] : vbaEventHandlers(src);
      if (handlers.length && window.confirm(`이 VBA 프로젝트에 도면 이벤트 처리기가 있습니다 (${handlers.join(", ")}).\n이 도면을 여는 동안 적재해 명령마다 실행할까요? (VBAUNLOAD로 해제)`)) {
        vbaProject.current = src;
        setVbaLoaded(handlers);
      }
      if (r.applied) setPrompt("VBA 매크로를 실행했습니다");
      if (r.applied) jot(`VBA 매크로 실행${macro ? ` (${macro})` : ""} — 변경 ${r.applied}건, 스크립트로 재생되지 않음`);
      return { error: r.error, log: out.log, commands: out.macros, applied: r.applied };
    } catch (e) {
      return fail(e instanceof VbaError ? (e.line ? `${e.line}번째 줄: ${e.message}` : e.message) : String(e));
    }
  };

  useCopilotHandlers({
    vba: runVbaText,
    lisp: runLispText,
    cad2d: (inputs) => {
      if (!canEdit || sheet) return "읽기 전용 화면입니다";
      if (!layer) return "작도 가능한(잠기지 않은) 레이어가 없습니다";
      const out = runInputs(inputs, selEdit, ctx);
      if (out.error) return `'${out.error.input || "Enter"}' — ${out.error.message}`;
      setTs(out.state);
      setPrompt(out.prompt || "Copilot 명령을 실행했습니다");
      // the Copilot's inputs are command line inputs: they replay as they are
      jot("AI Copilot 명령");
      if (ts.tool !== "SELECT") rec("^C"); // the Copilot's inputs start with no command running
      for (const line of inputs) rec(line.trim());
      applyResults(out.results);
      return null;
    },
  });

  // Track 8 P3: redraw a drawing made from 3D with its stored settings (the user's entities stay)
  const regenerate = () => {
    if (!docId) return;
    setNote(null);
    api<{ revision_id: string }>(`/documents/${encodeURIComponent(docId)}/drawing`, { method: "POST", json: {} }).then(
      (r) => router.push(`/viewer/${r.revision_id}?doc=${encodeURIComponent(docId)}`),
      (e) => setNote({ ok: false, text: errText(e) }),
    );
  };
  const downloadDxf = () => download(`/revisions/${encodeURIComponent(revisionId)}/dxf`, `${doc?.doc_no ?? "revision-" + revisionId}.dxf`).catch((e) => setNote({ ok: false, text: errText(e) }));

  const doUndo = () => {
    if (hist?.past.length) jot("실행 취소 — 재생되지 않음: 앞의 줄을 지우거나 고치세요");
    setHist((h) => h && undo(h));
  };
  const doRedo = () => {
    if (hist?.future.length) jot("다시 실행 — 재생되지 않음");
    setHist((h) => h && redo(h));
  };

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
      else if (e.key === "F12") { e.preventDefault(); setDynInput((d) => !d); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const one = selEntities.length === 1 ? selEntities[0] : null;

  return (
    <div data-mode="drawing" className="flex h-screen flex-col bg-background">
      <header className="flex h-12 shrink-0 items-center gap-4 border-b border-line bg-card px-4 text-sm" style={{ borderBottomColor: "var(--color-mode-accent, var(--color-border))" }}>
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
        {vbaLoaded && (
          <span className="rounded-md border border-[var(--color-border-strong)] px-2 py-0.5" title={`ThisDrawing_${vbaLoaded.join(", ThisDrawing_")}`}>
            VBA 이벤트 적재 {vbaLoaded.length}개{" "}
            <button type="button" onClick={() => unloadVba("VBA 프로젝트를 내렸습니다 (이벤트 처리기 해제)")} className="text-[var(--color-primary)] hover:underline focus-visible:outline-2 focus-visible:outline-ring">
              해제
            </button>
          </span>
        )}
        {w6Pending > 0 && (
          <span className="rounded-md border border-[var(--color-border-strong)] px-2 py-0.5" title="AX-LISP·VBA로 바꾼 블록·그룹·레이아웃 (저장할 때 함께 보냅니다)">
            블록·그룹 변경 {w6Pending}건 대기{" "}
            <button type="button" onClick={() => setPendingW6({ blocks: [], groups: [], layouts: [] })} className="text-[var(--color-primary)] hover:underline focus-visible:outline-2 focus-visible:outline-ring">
              버리기
            </button>
          </span>
        )}
        {docId && canEdit && data?.drawing && (
          <button type="button" disabled={dirty} title={dirty ? "먼저 저장하세요" : "3D 형상이 바뀌었으면 저장된 설정으로 도면을 다시 그립니다 (직접 그린 객체는 유지)"} onClick={regenerate} className="rounded-md border border-[var(--color-border-strong)] px-2 py-0.5 hover:bg-hover focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-50">
            3D에서 다시 생성
          </button>
        )}
        {canEdit && (
          <button type="button" onClick={() => setCleanup(true)} title="끊긴 끝점·겹친 선 찾기 (제안만, 고른 것만 적용)" className="rounded-md border border-[var(--color-border-strong)] px-2 py-0.5 hover:bg-hover focus-visible:outline-2 focus-visible:outline-ring">
            도면 정리
          </button>
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
        <span className="flex-1" />
        <CopilotToggle />
      </header>
      {cleanup && (
        <CleanupDialog
          ents={cleanEnts}
          onClose={() => setCleanup(false)}
          onShow={(handles) => {
            setCleanup(false);
            setSelected(new Set(handles));
          }}
          onApply={(r, note) => {
            setCleanup(false);
            applyResults([r]);
            setSelected(new Set(r.updated?.map((u) => u.handle)));
            setPrompt(note);
          }}
        />
      )}
      {metrics && (
        <MetricsDialog
          revisionId={revisionId}
          canQuote={!!me && can(me, "ESTIMATOR")}
          fromModel={!!data?.drawing}
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
                onSaveAs={() => setSavingAs(true)}
                onPlot={() => setPlotting(true)}
                onScript={() => {
                  setScriptInit(undefined);
                  setAutomation("SCRIPT");
                }}
                journalLines={journalLines}
                onJournal={() => (journal.current ? stopJournal() : startJournal())}
                onJournalCancel={cancelJournal}
                onParamBlock={openParamBlock}
              />
              ) : (
                <div className="flex items-center gap-2 border-b border-line bg-card px-2 py-1">
                  <button type="button" onClick={downloadDxf} className="rounded-md border border-line px-2 py-1 text-sm text-foreground hover:bg-hover focus-visible:outline-2 focus-visible:outline-ring">
                    DXF 다운로드
                  </button>
                  <button type="button" onClick={() => setSavingAs(true)} className="rounded-md border border-line px-2 py-1 text-sm text-foreground hover:bg-hover focus-visible:outline-2 focus-visible:outline-ring">
                    다른 이름으로 저장
                  </button>
                  <button type="button" onClick={() => setPlotting(true)} className="rounded-md border border-line px-2 py-1 text-sm text-foreground hover:bg-hover focus-visible:outline-2 focus-visible:outline-ring">
                    PDF 출력
                  </button>
                </div>
              )}
              {automation === "SCRIPT" && (
                <ScriptDialog
                  initial={scriptInit}
                  fileName={`${doc?.doc_no ?? "drawing"}-journal.axs`}
                  onRun={runScriptText}
                  onLibrary={async (name, source) => {
                    const n = scriptName(name);
                    if (!n) throw new Error("이름은 글자·숫자·공백·.-만, 60자까지입니다");
                    await libraryPut({ name: n, source });
                    return n;
                  }}
                  onClose={() => {
                    setAutomation(null);
                    setScriptInit(undefined);
                  }}
                />
              )}
              {automation && automation !== "SCRIPT" && (
                <ParamBlockDialog
                  initial={automation.initial}
                  onPlace={(b, at) => placeParamBlock(b, at, automation.replace)}
                  onClose={() => setAutomation(null)}
                />
              )}
              {dcl && <DclDialog run={dcl.run} onDone={dcl.done} onError={dcl.fail} />}
              {savingAs && (
                <Modal title="다른 이름으로 저장" onClose={() => setSavingAs(false)}>
                  {dirty && <p className="mb-2 text-sm text-body">저장하지 않은 편집은 포함되지 않습니다. 먼저 저장(Ctrl+S)하세요.</p>}
                  <SaveAs revisionId={revisionId} name={doc?.doc_no ?? "revision-" + revisionId} />
                </Modal>
              )}
              {plotting && (
                <PlotDialog revisionId={revisionId} filename={`${doc?.doc_no ?? "revision-" + revisionId}.pdf`} hidden={hidden} dirty={dirty} sheet={sheet?.name} onClose={() => setPlotting(false)} />
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
              <main className="relative min-h-0 flex-1">
                <DynamicInput
                  enabled={dynInput && canEdit}
                  screen={screenPos}
                  cursor={cursor}
                  base={ts.pts.length ? ts.pts[ts.pts.length - 1] : null}
                  onSubmit={submit}
                  tool={ts.tool}
                />
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
                  snapOn={ts.tool !== "SELECT" || ul.picking}
                  underlay={sheet ? undefined : ul.canvas}
                  snapExtra={sheet ? null : ul.snapExtra}
                  snapKinds={snapKinds}
                  snapFrom={ts.pts.length ? ts.pts[ts.pts.length - 1] : undefined}
                  gridStep={GRID_STEP}
                  showGrid={showGrid}
                  onRightClick={(s, w) => setCtxMenu({ x: s.x, y: s.y, world: w as [number, number] })}
                  onScreenCursor={setScreenPos}
                />
              </main>
              {ctxMenu && canEdit && (
                <ContextMenu
                  x={ctxMenu.x}
                  y={ctxMenu.y}
                  items={[
                    { label: `좌표 복사 (${ctxMenu.world[0].toFixed(2)}, ${ctxMenu.world[1].toFixed(2)})`, key: "copy-coord" },
                    { label: "실행 취소", key: "undo", disabled: !hist || hist.past.length === 0 },
                    { label: "다시 실행", key: "redo", disabled: !hist || hist.future.length === 0 },
                    { label: "선택 삭제", key: "delete", disabled: selected.size === 0 },
                  ] satisfies MenuItem[]}
                  onSelect={(key) => {
                    if (key === "copy-coord") navigator.clipboard.writeText(`${ctxMenu.world[0].toFixed(4)},${ctxMenu.world[1].toFixed(4)}`);
                    else if (key === "undo") doUndo();
                    else if (key === "redo") doRedo();
                    else if (key === "delete") del();
                  }}
                  onClose={() => setCtxMenu(null)}
                />
              )}
              {canEdit && <CommandPrompt prompt={prompt} onSubmit={submit} />}
            </div>
            {ul.panel ?? (
            <PropertyInspector
              entity={one}
              count={selEntities.length}
              layers={data.layers}
              linetypes={data.linetypes ?? []}
              lockedLayer={!canEdit || (one ? locked.has(one.layer) : false)}
              onApply={applyPatch}
              onMessage={setPrompt}
            />
            )}
            {ul.dialogs}
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
