"use client";

import { useEffect, useMemo, useRef, useState, type MutableRefObject, type ReactNode } from "react";
import { api, errText } from "@/lib/api";
import { buildSnapIndex, type SnapEntity, type SnapIndex } from "@/lib/cad/snap";
import { aligned, defaultGeom, outline, scaledAbout, scaleFactorFor, toPage, type UnderlayGeom, type UnderlayRec } from "@/lib/cad/underlay";
import { underlayOps, type UnderlayOp } from "@/lib/cad/underlayEdits";
import { importObjects, snapEntities, type Imported, type ImportResult, type PageVec } from "@/lib/cad/underlayVec";
import { UnderlayView, type DrawItem } from "@/lib/cad/underlayView";
import type { Extents, Pt, View } from "@/lib/cad/view";
import { listUnderlays, pageVectors, underlayFormat, type UnderlayFile } from "@/lib/underlayApi";
import type { RenderEntity } from "./CanvasViewport";
import { AttachDialog, ImportDialog, type ImportUi } from "./UnderlayDialogs";
import UnderlayPanel, { pickPrompt, type PanelCtl, type PickKind } from "./UnderlayPanel";

// 2D underlay U3-U5: everything the 2D screen does with PDF/DWFx references, kept out of the
// page: placing, editing (one undo step each, like any entity), the look, clip, two-point
// calibration, snapping (UOSNAP / PDFOSNAP / DWFOSNAP) and importing a page as objects.

/** The selectable frame of an underlay entity (the server tells the page size when it sends the drawing). */
export const fillOutline = (e: RenderEntity): RenderEntity => (e.type === "UNDERLAY" && e.underlay?.size ? { ...e, paths: outline(e.underlay, e.underlay.size) } : e);

const SWITCH = { uosnap: "ax.uosnap", pdfosnap: "ax.pdfosnap", dwfosnap: "ax.dwfosnap" } as const;
const readSwitch = (k: string) => {
  try {
    return localStorage.getItem(k) !== "0";
  } catch {
    return true; // storage blocked: on
  }
};
const writeSwitch = (k: string, v: boolean) => {
  try {
    localStorage.setItem(k, v ? "1" : "0");
  } catch {
    /* only a convenience */
  }
};

type Args = {
  ents: RenderEntity[];
  baseEnts: RenderEntity[];
  commit: (next: RenderEntity[]) => void;
  nextId: MutableRefObject<number>;
  projectId: number | undefined;
  extents: Extents;
  layer: string;
  layers: { name: string; locked: boolean }[];
  hidden: Set<string>;
  locked: Set<string>;
  canEdit: boolean;
  selected: Set<string>;
  setSelected: (s: Set<string>) => void;
  setPrompt: (s: string) => void;
  note: (text: string) => void; // a comment for the command journal
  ensureLayer: (name: string) => string; // the drawing layer for imported objects (made when missing)
  applyCreated: (items: Imported) => void; // new entities as one undo step
};
type Pick = { kind: PickKind | "attach" | "box"; handle?: string; pts: Pt[]; file?: UnderlayFile; page?: number };
type Vecs = Record<string, PageVec | string>;
const LOADING = "loading";

export function useUnderlays(a: Args) {
  const [version, setVersion] = useState(0);
  const [uview] = useState(() => new UnderlayView(() => setVersion((v) => v + 1)));
  useEffect(() => () => uview.clear(), [uview]);
  const [files, setFiles] = useState<UnderlayFile[] | null>(null);
  const [vecs, setVecs] = useState<Vecs>({});
  const asked = useRef(new Set<string>());
  const [hiddenPdf, setHiddenPdf] = useState<Record<string, string[]>>({});
  const [calibrated, setCalibrated] = useState<Set<string>>(new Set());
  const [pick, setPick] = useState<Pick | null>(null);
  const [measured, setMeasured] = useState<{ handle: string; mm: number; at: Pt } | null>(null);
  const [dialog, setDialog] = useState<null | "attach" | "import">(null);
  const [sw, setSw] = useState(() => ({ uosnap: readSwitch(SWITCH.uosnap), pdfosnap: readSwitch(SWITCH.pdfosnap), dwfosnap: readSwitch(SWITCH.dwfosnap) }));
  const [iui, setIui] = useState<ImportUi>({ crossing: true, text: true, fills: true, tol: "0.01", layer: "PDF-IMPORT", region: "page", boxSet: false });
  const [box, setBox] = useState<{ min: Pt; max: Pt } | null>(null);

  const uents = useMemo(() => a.ents.filter((e): e is RenderEntity & { underlay: UnderlayRec } => e.type === "UNDERLAY" && !!e.underlay), [a.ents]);
  const sel = a.selected.size === 1 ? uents.find((e) => a.selected.has(e.handle)) : undefined;

  // --- files and vectors ---------------------------------------------------------------------------
  const loadFiles = () => {
    if (a.projectId !== undefined) void listUnderlays(a.projectId).then(setFiles, () => setFiles([]));
  };
  const vkey = (r: UnderlayRec) => `${r.file_id}:${r.page}`;
  const loadVec = (r: UnderlayRec) => {
    if (!r.file_id) return;
    const k = vkey(r);
    if (asked.current.has(k)) return;
    asked.current.add(k);
    setVecs((v) => ({ ...v, [k]: LOADING }));
    pageVectors(r.file_id, r.page).then(
      (vec) => setVecs((v) => ({ ...v, [k]: vec })),
      (e) => setVecs((v) => ({ ...v, [k]: errText(e) })),
    );
  };
  const snapOnFor = (r: UnderlayRec) => sw.uosnap && (r.format === "pdf" ? sw.pdfosnap : sw.dwfosnap) && r.flags.on && r.linked;

  // the vectors of every underlay that can be snapped to, and of the selected one (its panel)
  const sig = useMemo(() => JSON.stringify(uents.map((e) => [e.handle, e.layer, e.underlay])), [uents]);
  useEffect(() => {
    for (const e of uents) if ((snapOnFor(e.underlay) && !a.hidden.has(e.layer)) || e === sel) loadVec(e.underlay);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig, sw, a.hidden, sel?.handle]);

  // --- drawing ---------------------------------------------------------------------------------------
  const items = useMemo<DrawItem[]>(
    () =>
      uents
        .filter((e) => e.underlay.linked && e.underlay.file_id && e.underlay.size && !a.hidden.has(e.layer))
        .map((e) => ({ handle: e.handle, g: e.underlay, size: e.underlay.size!, fileId: e.underlay.file_id!, hidden: hiddenPdf[e.handle] ?? [] })),
    [uents, a.hidden, hiddenPdf],
  );
  const canvas = useMemo(
    () =>
      items.length
        ? {
            version,
            draw: (ctx: CanvasRenderingContext2D, view: View, dark: boolean) => uview.draw(ctx, items, view, dark),
            onView: (view: View, vp: { w: number; h: number }) => uview.requestSettled(items, view, vp),
          }
        : undefined,
    [items, version, uview],
  );

  const snapExtra = useMemo<SnapIndex | null>(() => {
    const all: SnapEntity[] = [];
    for (const e of uents) {
      const r = e.underlay;
      if (!snapOnFor(r) || a.hidden.has(e.layer) || a.locked.has(e.layer)) continue; // hidden: off; locked: shown but not snapped to
      const v = vecs[vkey(r)];
      if (typeof v !== "object") continue;
      const off = new Set((hiddenPdf[e.handle] ?? []).map((n) => v.layers.indexOf(n)).filter((i) => i >= 0));
      all.push(...snapEntities(v, r, e.handle, off));
    }
    if (!all.length) return null;
    return buildSnapIndex(all, Math.hypot(a.extents.max[0] - a.extents.min[0], a.extents.max[1] - a.extents.min[1]) / 200 || 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig, vecs, hiddenPdf, sw, a.hidden, a.locked, a.extents]);

  // --- editing ------------------------------------------------------------------------------------------
  const update = (handle: string, fn: (r: UnderlayRec) => UnderlayRec | string, why: string) => {
    const e = uents.find((x) => x.handle === handle);
    if (!e) return;
    if (!a.canEdit) return a.setPrompt("읽기 전용입니다");
    if (a.locked.has(e.layer)) return a.setPrompt("잠긴 레이어의 언더레이는 수정할 수 없습니다");
    if (!e.underlay.linked) return a.setPrompt("연결되지 않은 언더레이는 수정할 수 없습니다");
    const r = fn(e.underlay);
    if (typeof r === "string") return a.setPrompt(r);
    if (JSON.stringify(r) === JSON.stringify(e.underlay)) return;
    a.commit(a.ents.map((x) => (x.handle === handle ? fillOutline({ ...x, underlay: r }) : x)));
    a.note(`언더레이 ${why}`);
  };

  const change = (e: RenderEntity & { underlay: UnderlayRec }, patch: Partial<UnderlayGeom>) => {
    const r = e.underlay;
    if (patch.page !== undefined && patch.page !== r.page) {
      const page = patch.page;
      const apply = (f: UnderlayFile) => {
        const p = f.pages[page];
        if (!p) return a.setPrompt("없는 페이지입니다");
        update(e.handle, (x) => ({ ...x, page, clip: null, size: [p.width, p.height] }), `페이지 ${page + 1}`);
      };
      const known = files?.find((f) => f.underlay_id === r.file_id);
      if (known) apply(known);
      else if (r.file_id) void api<UnderlayFile>(`/underlays/${r.file_id}`).then(apply, (er) => a.setPrompt(errText(er)));
      return;
    }
    if (patch.scale !== undefined) {
      const k = scaledAbout({ ...r }, [r.insert[0], r.insert[1]], patch.scale / r.scale);
      if (typeof k === "string") return a.setPrompt(k);
    }
    update(e.handle, (x) => ({ ...x, ...patch }), "속성 변경");
    if (patch.scale !== undefined) setCalibrated((c) => new Set(c).add(e.handle));
  };

  const attachAt = useRef<((at: Pt) => void) | null>(null); // the click that finishes an attach
  const attach = (f: UnderlayFile, page: number, insert: Pt | null) => {
    const p = f.pages[page];
    if (!p) return a.setPrompt("없는 페이지입니다");
    const make = (at: Pt) => {
      const g = defaultGeom({ format: underlayFormat(f), sha256: f.sha256, name: f.name }, page, at);
      g.flags = { ...g.flags, adjust: true }; // light paper on a dark canvas is hard to work over
      const rec: UnderlayRec = { ...g, linked: true, uniform: true, file_id: f.underlay_id, page_count: f.page_count, size: [p.width, p.height] };
      const ent = fillOutline({ handle: `new-${a.nextId.current++}`, type: "UNDERLAY", layer: a.layer, color: null, paths: [], underlay: rec });
      a.commit([...a.ents, ent]);
      a.setSelected(new Set([ent.handle]));
      a.note(`언더레이 부착: ${f.name} (${page + 1}쪽)`);
      a.setPrompt(`${f.name} 부착 — 축척 1(인쇄 크기). 두 점 맞춤이나 길이로 축척을 보정하세요`);
    };
    if (insert) make(insert);
    else {
      attachAt.current = make;
      setPick({ kind: "attach", pts: [], file: f, page });
      a.setPrompt("부착: 삽입점(페이지의 왼쪽 아래 모서리)을 클릭하세요");
    }
  };
  const remove = (handle: string) => {
    if (!a.canEdit) return a.setPrompt("읽기 전용입니다");
    const e = uents.find((x) => x.handle === handle);
    if (!e || a.locked.has(e.layer)) return a.setPrompt("잠긴 레이어의 언더레이는 지울 수 없습니다");
    a.commit(a.ents.filter((x) => x.handle !== handle));
    a.setSelected(new Set());
    a.note("언더레이 삭제");
  };

  const moveLayer = (handle: string, name: string) => {
    if (!a.canEdit || a.locked.has(name)) return a.setPrompt("그 레이어로는 옮길 수 없습니다");
    a.commit(a.ents.map((x) => (x.handle === handle ? { ...x, layer: name } : x)));
    a.note("언더레이 레이어 변경");
  };

  const replaceFile = (e: RenderEntity & { underlay: UnderlayRec }, f: UnderlayFile) => {
    const p = f.pages[0];
    update(e.handle, (x) => ({ ...x, sha256: f.sha256, name: f.name, format: underlayFormat(f), page: 0, clip: null, file_id: f.underlay_id, page_count: f.page_count, size: [p.width, p.height], linked: true }), `파일 바꿈: ${f.name}`);
    uview.clear();
  };

  // --- interactive picks (clip, alignment, length, attach point, import window) --------------------
  const startPick = (kind: PickKind | "box", handle?: string) => {
    if (!a.canEdit) return a.setPrompt("읽기 전용입니다");
    setPick({ kind, handle, pts: [] });
    a.setPrompt(kind === "box" ? "가져올 영역: 첫 모서리를 클릭" : pickPrompt(kind, 0));
  };
  const cancel = (why = "취소했습니다") => {
    if (pick?.kind === "box") setDialog("import");
    setPick(null);
    attachAt.current = null;
    a.setPrompt(why);
  };
  const finishPoly = (p: Pick) => {
    const e = uents.find((x) => x.handle === p.handle);
    if (!e || p.pts.length < 3) return cancel("다각형 클립은 점 3개 이상이 필요합니다");
    const pts = p.pts.map((q) => toPage(e.underlay, q));
    update(e.handle, (x) => ({ ...x, clip: { kind: "poly", points: pts } }), "다각형 클립");
    setPick(null);
  };

  /** A click while a pick is open. Returns true when it was used. */
  const handlePick = (p: Pt): boolean => {
    if (!pick) return false;
    const pts = [...pick.pts, p];
    const e = uents.find((x) => x.handle === pick.handle);
    switch (pick.kind) {
      case "attach":
        attachAt.current?.(p);
        attachAt.current = null;
        setPick(null);
        return true;
      case "box":
        if (pts.length < 2) {
          setPick({ ...pick, pts });
          a.setPrompt("반대쪽 모서리를 클릭");
        } else {
          setBox({ min: [Math.min(pts[0][0], pts[1][0]), Math.min(pts[0][1], pts[1][1])], max: [Math.max(pts[0][0], pts[1][0]), Math.max(pts[0][1], pts[1][1])] });
          setIui((u) => ({ ...u, region: "box", boxSet: true }));
          setPick(null);
          setDialog("import");
        }
        return true;
      case "clip-rect": {
        if (!e) return cancel(), true;
        if (pts.length < 2) {
          setPick({ ...pick, pts });
          a.setPrompt(pickPrompt("clip-rect", 1));
          return true;
        }
        const [pa, pb] = pts.map((q) => toPage(e.underlay, q));
        if (Math.abs(pa[0] - pb[0]) < 1e-6 || Math.abs(pa[1] - pb[1]) < 1e-6) return cancel("두 모서리가 같은 가로·세로선 위에 있습니다"), true;
        update(e.handle, (x) => ({ ...x, clip: { kind: "rect", points: [pa, pb] } }), "사각형 클립");
        setPick(null);
        return true;
      }
      case "clip-poly":
        setPick({ ...pick, pts });
        a.setPrompt(`다각형 클립: 점 ${pts.length}개 (Enter 완료, Esc 취소)`);
        return true;
      case "length":
        if (!e) return cancel(), true;
        if (pts.length < 2) {
          setPick({ ...pick, pts });
          a.setPrompt(pickPrompt("length", 1));
        } else {
          setMeasured({ handle: e.handle, mm: Math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]), at: pts[0] });
          setPick(null);
          a.setPrompt("패널에 실제 길이(mm)를 입력하세요");
        }
        return true;
      case "align": {
        if (!e) return cancel(), true;
        if (pts.length < 4) {
          setPick({ ...pick, pts });
          a.setPrompt(pickPrompt("align", pts.length));
          return true;
        }
        update(e.handle, (x) => {
          const r = aligned(x, pts[0], pts[1], pts[2], pts[3]);
          return typeof r === "string" ? r : { ...x, ...r };
        }, "두 점 보정");
        setCalibrated((c) => new Set(c).add(e.handle));
        setPick(null);
        return true;
      }
    }
  };
  /** Esc / Enter while a pick is open. */
  const handleKey = (key: "esc" | "enter"): boolean => {
    if (!pick) return false;
    if (key === "esc") cancel();
    else if (pick.kind === "clip-poly") finishPoly(pick);
    return true;
  };

  const applyLength = (real: number) => {
    const m = measured;
    const e = m && uents.find((x) => x.handle === m.handle);
    if (!m || !e) return;
    const k = scaleFactorFor(m.mm, real);
    if (typeof k === "string") return a.setPrompt(k);
    update(e.handle, (x) => scaledAbout(x, m.at, k) as UnderlayRec | string, `길이로 축척 (${m.mm.toFixed(2)} → ${real} mm)`);
    setCalibrated((c) => new Set(c).add(e.handle));
    setMeasured(null);
  };

  // --- import as objects ----------------------------------------------------------------------------------
  const importVec = sel ? vecs[vkey(sel.underlay)] : undefined;
  const result = useMemo<ImportResult | null>(() => {
    if (dialog !== "import" || !sel || typeof importVec !== "object") return null;
    const off = new Set((hiddenPdf[sel.handle] ?? []).map((n) => importVec.layers.indexOf(n)).filter((i) => i >= 0));
    const tol = Number(iui.tol);
    return importObjects(importVec, sel.underlay, { region: iui.region === "box" ? box : null, crossing: iui.crossing, hidden: off, tol: tol > 0 ? tol : 0.01, text: iui.text, fills: iui.fills });
  }, [dialog, sel, importVec, iui, box, hiddenPdf]);
  const doImport = () => {
    if (!result || result.error || !result.items.length) return;
    const name = a.ensureLayer(iui.layer.trim());
    a.applyCreated(result.items.map((i) => ({ ...i, layer: name })));
    a.note(`PDF 객체로 가져오기: ${result.items.length}개`);
    a.setPrompt(`객체 ${result.items.length}개를 ${name} 레이어로 가져왔습니다`);
    setDialog(null);
  };

  // --- command line ---------------------------------------------------------------------------------------
  const openAttach = () => {
    if (!a.canEdit) return a.setPrompt("읽기 전용입니다");
    if (a.projectId === undefined) return a.setPrompt("프로젝트를 알 수 없습니다");
    setDialog("attach");
    loadFiles();
  };
  const setSwitch = (k: keyof typeof SWITCH, v: boolean) => {
    writeSwitch(SWITCH[k], v);
    setSw((s) => ({ ...s, [k]: v }));
  };
  /** UNDERLAY / PDFATTACH / UCLIP / UALIGN / PDFIMPORT / UOSNAP...; true when the text was one of them. */
  const command = (text: string): boolean => {
    const t = text.trim();
    if (/^-?(UNDERLAY|PDFATTACH|DWFATTACH|UATTACH)$/i.test(t)) return openAttach(), true;
    const s = /^(UOSNAP|PDFOSNAP|DWFOSNAP)(?:\s+([01]))?$/i.exec(t);
    if (s) {
      const k = s[1].toLowerCase() as keyof typeof SWITCH;
      if (s[2] === undefined) a.setPrompt(`${s[1].toUpperCase()} = ${sw[k] ? 1 : 0} (UOSNAP이 0이면 PDF·DWF 모두 스냅하지 않습니다)`);
      else {
        setSwitch(k, s[2] === "1");
        a.setPrompt(`${s[1].toUpperCase()} = ${s[2]}`);
      }
      return true;
    }
    const verb = /^(UCLIP|UALIGN|PDFIMPORT|UIMPORT|ULENGTH)(?:\s+(R|P|D|O|OFF|DELETE|RECT|POLY))?$/i.exec(t);
    if (!verb) return false;
    if (!sel) return a.setPrompt("먼저 언더레이의 테두리를 클릭해 하나 선택하세요"), true;
    const v = verb[1].toUpperCase();
    const opt = (verb[2] ?? "").toUpperCase();
    if (v === "UALIGN") startPick("align", sel.handle);
    else if (v === "ULENGTH") startPick("length", sel.handle);
    else if (v === "UCLIP") {
      if (opt === "D" || opt === "DELETE" || opt === "OFF") update(sel.handle, (x) => ({ ...x, clip: null }), "클립 해제");
      else startPick(opt === "P" || opt === "POLY" ? "clip-poly" : "clip-rect", sel.handle);
    } else {
      setDialog("import");
      loadVec(sel.underlay);
    }
    return true;
  };

  // --- what the page renders ------------------------------------------------------------------------------
  const ops: UnderlayOp[] = useMemo(() => underlayOps(a.baseEnts, a.ents), [a.baseEnts, a.ents]);
  const vec = sel ? (vecs[vkey(sel.underlay)] ?? null) : null;
  const ctl: PanelCtl | null = sel
    ? {
        rec: sel.underlay,
        layer: sel.layer,
        layers: a.layers,
        readOnly: !a.canEdit || a.locked.has(sel.layer) || !sel.underlay.linked,
        files,
        vec,
        imageError: uview.error(sel.handle),
        hiddenPdf: hiddenPdf[sel.handle] ?? [],
        calibrated: calibrated.has(sel.handle),
        picking: pick && pick.handle === sel.handle && pick.kind !== "attach" && pick.kind !== "box" ? { kind: pick.kind, step: pick.pts.length } : null,
        measured: measured && measured.handle === sel.handle ? measured.mm : null,
        snapOn: snapOnFor(sel.underlay),
        onChange: (p) => change(sel, p),
        onLayer: (n) => moveLayer(sel.handle, n),
        onTogglePdfLayer: (n) => setHiddenPdf((h) => ({ ...h, [sel.handle]: (h[sel.handle] ?? []).includes(n) ? (h[sel.handle] ?? []).filter((x) => x !== n) : [...(h[sel.handle] ?? []), n] })),
        onReplace: (f) => replaceFile(sel, f),
        onPick: (k) => startPick(k, sel.handle),
        onCancelPick: () => cancel(),
        onClearClip: () => update(sel.handle, (x) => ({ ...x, clip: null }), "클립 해제"),
        onApplyLength: applyLength,
        onImport: () => {
          setDialog("import");
          loadVec(sel.underlay);
        },
        onDelete: () => remove(sel.handle),
        onLoadFiles: () => files === null && loadFiles(),
      }
    : null;
  const panel: ReactNode = ctl ? <UnderlayPanel ctl={ctl} /> : null;
  const dialogs: ReactNode = (
    <>
      {dialog === "attach" && a.projectId !== undefined && (
        <AttachDialog
          projectId={a.projectId}
          files={files}
          onUploaded={(f) => setFiles((fs) => [...(fs ?? []).filter((x) => x.underlay_id !== f.underlay_id), f])}
          onAttach={(f, page, at) => {
            setDialog(null);
            attach(f, page, at);
          }}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === "import" && sel && (
        <ImportDialog ui={iui} setUi={setIui} result={result} layers={a.layers.map((l) => l.name)} onPickBox={() => (setDialog(null), startPick("box", sel.handle))} onImport={doImport} onClose={() => setDialog(null)} />
      )}
    </>
  );

  return { canvas, snapExtra, panel, dialogs, ops, hasOps: ops.length > 0, picking: pick !== null, selected: sel, handlePick, handleKey, command, openAttach, uview };
}
