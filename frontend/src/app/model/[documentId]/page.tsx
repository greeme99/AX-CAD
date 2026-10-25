"use client";

import DesktopOnly from "@/components/DesktopOnly";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, use, useEffect, useMemo, useState } from "react";
import CanvasViewport, { type RenderEntity } from "@/components/cad/CanvasViewport";
import FeatureTree from "@/components/cad/FeatureTree";
import { useSketchEditor, type SketchSession } from "@/components/cad/SketchEditor";
import ThreeViewport, { type Overlay } from "@/components/cad/ThreeViewport";
import { btn2, btnDanger, btnPrimary, Err, field, Field, link, Loading, Modal } from "@/components/ui";
import { api, ApiError, can, download, errText, type Doc, type List, type User } from "@/lib/api";
import {
  axisLine,
  booleanCandidates,
  failedDependent,
  featureName,
  isMach,
  MACH_DEFAULT,
  MACH_LABEL,
  machDraftOf,
  machiningParams,
  OP_SYMBOL,
  PAD_DEFAULT,
  PAD_MODE_LABEL,
  padDraftOf,
  padParams,
  PRIM_DEFAULT,
  PRIM_DIMS,
  PRIM_LABEL,
  primDraftOf,
  primitiveParams,
  revolveParams,
  type AxisDraft,
  type BooleanOp,
  type Direction,
  type Feature,
  type MachDraft,
  type MachKind,
  type PadDraft,
  type PadMode,
  type PrimDraft,
  type PrimKind,
  type TreeNode,
  type UpTo,
} from "@/lib/cad/features";
import { distToPaths, hitPaths, toPaths } from "@/lib/cad/geom";
import type { MeshJson } from "@/lib/cad/mesh";
import { facePlane, geomOf, planeName, PLANES, type PlaneName, type V3 } from "@/lib/cad/sketch";
import type { Extents, Pt } from "@/lib/cad/view";
import { parseSelect } from "@/lib/quote";
import { useApi } from "@/lib/useApi";

type RenderData = { extents: Extents; layers: { name: string; visible: boolean }[]; entities: RenderEntity[] };
type SketchKind = "EXTRUDE" | "REVOLVE";
type BoolDraft = { op: BooleanOp; target: string; tool: string };

const PROFILE_TYPES = ["LINE", "ARC", "CIRCLE", "LWPOLYLINE"];
// STEP/IGES keep names and assembly; STL (3D print, CAM) and BREP (OCCT native) carry the bodies only
const EXPORT_EXT = { STEP: "step", IGES: "igs", STL: "stl", BREP: "brep" } as const;
type ExportFmt = keyof typeof EXPORT_EXT;
const NOOP = () => {};
const NO_SNAPS = new Set<never>();
const MAX_IMPORT_BYTES = 100 * 1024 ** 2;
const AXIS_Y: AxisDraft = { px: "0", py: "0", dx: "0", dy: "1", angle: "360" };
const num = (n: number) => n.toLocaleString("ko-KR", { maximumFractionDigits: 2 });
const dangling = (e: unknown): Pt[] => {
  const d = e instanceof ApiError && e.code === "GEOM_OPEN_WIRE" ? (e.details as { dangling?: unknown } | undefined)?.dangling : null;
  return Array.isArray(d) ? d.filter((p): p is Pt => Array.isArray(p) && p.length >= 2 && p.every(Number.isFinite)) : [];
};
// Track 8 P2-2: an up-to face is picked on a body in the 3D view (the dialog hides meanwhile)
type PickFace = (pick: (u: UpTo) => void, cancel: () => void) => void;
const booleanOf = (b: BoolDraft) =>
  !b.target || !b.tool ? "대상과 도구 형상을 선택하세요." : b.target === b.tool ? "대상과 도구는 서로 달라야 합니다." : { op: b.op, target_feature_id: Number(b.target), tool_feature_id: Number(b.tool) };
const axisOf = (p: { axis_point: Pt; axis_dir: Pt; angle_deg: number }): AxisDraft => ({ px: String(p.axis_point[0]), py: String(p.axis_point[1]), dx: String(p.axis_dir[0]), dy: String(p.axis_dir[1]), angle: String(p.angle_deg) });

/** API error text; a dependent BOOLEAN failure names that feature (the whole change was rolled back). */
function failText(e: unknown, features: Feature[]) {
  const dep = e instanceof ApiError ? failedDependent(e.details) : null;
  const f = features.find((x) => x.feature_id === dep);
  return f ? `${featureName(f)} 재생성 실패로 변경이 취소되었습니다: ${errText(e)}` : errText(e);
}

// FreeCAD Pad / Pocket length options; up-to-face only where a face can be picked, through-all for pockets
function PadFields({ v, onChange, pocket, onPickFace }: { v: PadDraft; onChange: (v: PadDraft) => void; pocket?: boolean; onPickFace?: (pick: (u: UpTo) => void) => void }) {
  const modes = (Object.keys(PAD_MODE_LABEL) as PadMode[]).filter((m) => (m !== "THROUGH_ALL" || pocket) && (m !== "UP_TO_FACE" || onPickFace));
  const num = (k: "distance" | "distance2" | "taper", label: string) => (
    <Field label={label}>
      <input type="number" step="any" value={v[k]} onChange={(e) => onChange({ ...v, [k]: e.target.value })} className={`${field} font-mono`} />
    </Field>
  );
  return (
    <div className="space-y-2">
      <div className="flex gap-3">
        <Field label="길이 방식">
          <select value={v.mode} onChange={(e) => onChange({ ...v, mode: e.target.value as PadMode })} className={field}>
            {modes.map((m) => (
              <option key={m} value={m}>
                {PAD_MODE_LABEL[m]}
              </option>
            ))}
          </select>
        </Field>
        <Field label="방향">
          <select value={v.direction} onChange={(e) => onChange({ ...v, direction: e.target.value as Direction })} className={field}>
            <option value="+Z">+Z</option>
            <option value="-Z">−Z</option>
          </select>
        </Field>
      </div>
      {(v.mode === "LENGTH" || v.mode === "SYMMETRIC" || v.mode === "TWO_SIDED") && (
        <div className="flex gap-3">
          {num("distance", v.mode === "SYMMETRIC" ? "전체 거리 (mm)" : "거리 (mm)")}
          {v.mode === "TWO_SIDED" && num("distance2", "반대쪽 거리 (mm)")}
        </div>
      )}
      {v.mode === "UP_TO_FACE" && onPickFace && (
        <div className="flex items-center gap-2 text-sm">
          <button type="button" onClick={() => onPickFace((u) => onChange({ ...v, upTo: u }))} className={btn2}>
            {v.upTo ? "면 다시 고르기" : "3D 화면에서 면 고르기"}
          </button>
          <span className="font-mono text-xs text-muted-foreground">{v.upTo ? `점 ${v.upTo.origin.map(num1).join(", ")}` : "면이 선택되지 않았습니다"}</span>
        </div>
      )}
      {num("taper", "테이퍼 각도 (°, + 는 좁아짐)")}
    </div>
  );
}
const num1 = (n: number) => String(Math.round(n * 10) / 10);

function PrimFields({ v, onChange, kindFixed }: { v: PrimDraft; onChange: (v: PrimDraft) => void; kindFixed?: boolean }) {
  return (
    <div className="space-y-2">
      <div className="flex gap-3">
        <Field label="형상">
          <select value={v.kind} disabled={kindFixed} onChange={(e) => onChange({ ...v, kind: e.target.value as PrimKind })} className={field}>
            {(Object.keys(PRIM_LABEL) as PrimKind[]).map((k) => (
              <option key={k} value={k}>
                {PRIM_LABEL[k]}
              </option>
            ))}
          </select>
        </Field>
        {v.kind !== "BOX" && (
          <Field label="축">
            <select value={v.axis} onChange={(e) => onChange({ ...v, axis: e.target.value as PrimDraft["axis"] })} className={field}>
              <option value="X">X</option>
              <option value="Y">Y</option>
              <option value="Z">Z</option>
            </select>
          </Field>
        )}
      </div>
      <div className="grid grid-cols-2 gap-3">
        {PRIM_DIMS[v.kind].map(([k, label]) => (
          <Field key={k} label={`${label} (mm)`}>
            <input type="number" min="0" step="any" value={v.dims[k] ?? ""} onChange={(e) => onChange({ ...v, dims: { ...v.dims, [k]: e.target.value } })} className={`${field} font-mono`} />
          </Field>
        ))}
      </div>
      <Field label={v.kind === "BOX" ? "기준 모서리 x,y,z" : v.kind === "SPHERE" || v.kind === "TORUS" ? "중심 x,y,z" : "밑면 중심 x,y,z"}>
        <input value={v.origin} onChange={(e) => onChange({ ...v, origin: e.target.value })} className={`${field} font-mono`} />
      </Field>
    </div>
  );
}

// Track 8 P2-2: FreeCAD Part primitives as new bodies
function PrimitiveDialog({ docId, onClose, onDone }: { docId: string; onClose: () => void; onDone: (id: number) => void }) {
  const [v, setV] = useState<PrimDraft>(PRIM_DEFAULT);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const p = primitiveParams(v);
    if (typeof p === "string") return setError(p);
    setBusy(true);
    setError(null);
    try {
      onDone((await createFeature(docId, "PRIMITIVE", p)).feature_id);
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title="원시 형상" onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        <PrimFields v={v} onChange={setV} />
        <Err text={error} />
        <Actions busy={busy} onClose={onClose} />
      </form>
    </Modal>
  );
}

function AxisAngle({ v, onChange }: { v: AxisDraft; onChange: (v: AxisDraft) => void }) {
  const input = (k: keyof AxisDraft, label: string) => (
    <Field label={label}>
      <input type="number" step="any" value={v[k]} onChange={(e) => onChange({ ...v, [k]: e.target.value })} className={`${field} font-mono`} />
    </Field>
  );
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-3">
        {input("px", "축 기준점 X (mm)")}
        {input("py", "축 기준점 Y (mm)")}
        {input("dx", "축 방향 X")}
        {input("dy", "축 방향 Y")}
      </div>
      <div className="flex items-end gap-3">
        {input("angle", "각도 (°)")}
        <button type="button" onClick={() => onChange({ ...v, dx: "1", dy: "0" })} className={`${btn2} whitespace-nowrap`}>
          X축
        </button>
        <button type="button" onClick={() => onChange({ ...v, dx: "0", dy: "1" })} className={`${btn2} whitespace-nowrap`}>
          Y축
        </button>
      </div>
    </div>
  );
}

function BooleanFields({ v, candidates, onChange }: { v: BoolDraft; candidates: Feature[]; onChange: (v: BoolDraft) => void }) {
  const pick = (k: "target" | "tool", label: string) => (
    <Field label={label}>
      <select value={v[k]} onChange={(e) => onChange({ ...v, [k]: e.target.value })} className={field}>
        <option value="">선택</option>
        {candidates.map((f) => (
          <option key={f.feature_id} value={f.feature_id}>
            {featureName(f)}
          </option>
        ))}
      </select>
    </Field>
  );
  return (
    <div className="space-y-3">
      <Field label="연산">
        <select value={v.op} onChange={(e) => onChange({ ...v, op: e.target.value as BooleanOp })} className={field}>
          <option value="FUSE">합집합 (Fuse {OP_SYMBOL.FUSE})</option>
          <option value="CUT">차집합 (Cut {OP_SYMBOL.CUT})</option>
          <option value="COMMON">교집합 (Common {OP_SYMBOL.COMMON})</option>
        </select>
      </Field>
      <div className="flex gap-3">
        {pick("target", "대상 형상")}
        {pick("tool", "도구 형상")}
      </div>
    </div>
  );
}

const EDGE_LABEL = { VERTICAL: "수직 모서리 (외곽 코너)", TOP: "윗면 모서리", BOTTOM: "아랫면 모서리", ALL: "모든 모서리" } as const;
const SIZE_LABEL: Record<MachKind, string> = { FILLET: "반지름 (mm)", CHAMFER: "거리 (mm)", HOLE: "직경 (mm)", PATTERN: "", MIRROR: "", SHELL: "벽 두께 (mm)" };

// FreeCAD PartDesign-style machining on one body: the fields of the chosen kind
function MachiningFields({ v, candidates, onChange, kindFixed }: { v: MachDraft; candidates: Feature[]; onChange: (v: MachDraft) => void; kindFixed?: boolean }) {
  const set = (k: keyof MachDraft) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => onChange({ ...v, [k]: e.target.value });
  return (
    <div className="space-y-3">
      <div className="flex gap-3">
        {!kindFixed && (
          <Field label="가공">
            <select value={v.kind} onChange={set("kind")} className={field}>
              {(Object.keys(MACH_LABEL) as MachKind[]).map((k) => (
                <option key={k} value={k}>
                  {MACH_LABEL[k]}
                </option>
              ))}
            </select>
          </Field>
        )}
        <Field label="대상 형상">
          <select value={v.target} onChange={set("target")} className={field}>
            <option value="">선택</option>
            {candidates.map((f) => (
              <option key={f.feature_id} value={f.feature_id}>
                {featureName(f)}
              </option>
            ))}
          </select>
        </Field>
      </div>
      {SIZE_LABEL[v.kind] && (
        <Field label={SIZE_LABEL[v.kind]}>
          <input inputMode="decimal" value={v.size} onChange={set("size")} className={field} />
        </Field>
      )}
      {(v.kind === "FILLET" || v.kind === "CHAMFER") && (
        <Field label="모서리">
          <select value={v.edges} onChange={set("edges")} className={field}>
            {(Object.keys(EDGE_LABEL) as (keyof typeof EDGE_LABEL)[]).map((k) => (
              <option key={k} value={k}>
                {EDGE_LABEL[k]}
              </option>
            ))}
          </select>
        </Field>
      )}
      {v.kind === "HOLE" && (
        <>
          <Field label="위치 x,y (여러 개는 ; 로 구분, 윗면에서 아래로 뚫음)">
            <input value={v.positions} onChange={set("positions")} className={field} />
          </Field>
          <Field label="깊이 (mm, 비우면 관통)">
            <input inputMode="decimal" value={v.depth} onChange={set("depth")} className={field} />
          </Field>
        </>
      )}
      {v.kind === "PATTERN" && (
        <>
          <div className="flex gap-3">
            <Field label="종류">
              <select value={v.patKind} onChange={set("patKind")} className={field}>
                <option value="LINEAR">직선 패턴</option>
                <option value="POLAR">원형 패턴 (Z축)</option>
              </select>
            </Field>
            <Field label="개수 (원본 포함)">
              <input inputMode="numeric" value={v.copies} onChange={set("copies")} className={field} />
            </Field>
          </div>
          {v.patKind === "LINEAR" ? (
            <Field label="간격 dx,dy,dz (mm)">
              <input value={v.step} onChange={set("step")} className={field} />
            </Field>
          ) : (
            <div className="flex gap-3">
              <Field label="회전 중심 x,y">
                <input value={v.center} onChange={set("center")} className={field} />
              </Field>
              <Field label="전체 각도 (°)">
                <input inputMode="decimal" value={v.angle} onChange={set("angle")} className={field} />
              </Field>
            </div>
          )}
        </>
      )}
      {v.kind === "MIRROR" && (
        <div className="flex gap-3">
          <Field label="대칭 평면">
            <select value={v.plane} onChange={set("plane")} className={field}>
              <option value="YZ">YZ (x = 위치)</option>
              <option value="ZX">ZX (y = 위치)</option>
              <option value="XY">XY (z = 위치)</option>
            </select>
          </Field>
          <Field label="위치 (mm)">
            <input inputMode="decimal" value={v.offset} onChange={set("offset")} className={field} />
          </Field>
        </div>
      )}
      {v.kind === "SHELL" && (
        <Field label="여는 면">
          <select value={v.openFace} onChange={set("openFace")} className={field}>
            <option value="TOP">윗면</option>
            <option value="BOTTOM">아랫면</option>
          </select>
        </Field>
      )}
    </div>
  );
}

function Actions({ busy, disabled, onClose }: { busy: boolean; disabled?: boolean; onClose: () => void }) {
  return (
    <div className="flex justify-end gap-2">
      <button type="button" onClick={onClose} className={btn2}>
        취소
      </button>
      <button type="submit" disabled={busy || disabled} aria-busy={busy} className={btnPrimary}>
        {busy ? "생성 중..." : "생성"}
      </button>
    </div>
  );
}

async function createFeature(docId: string, feature_type: Feature["feature_type"], params: object) {
  return api<Feature>(`/documents/${docId}/features`, { method: "POST", json: { feature_type, params } });
}

// FN-10: pick a closed profile on the current revision, then POST the EXTRUDE / REVOLVE feature
function SketchDialog({ kind, docId, revisionId, onClose, onDone }: { kind: SketchKind; docId: string; revisionId: string; onClose: () => void; onDone: (id: number) => void }) {
  const render = useApi<RenderData>(`/revisions/${revisionId}/render`);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [pad, setPad] = useState<PadDraft>(PAD_DEFAULT);
  const [axis, setAxis] = useState(AXIS_Y);
  const [error, setError] = useState<string | null>(null);
  const [markers, setMarkers] = useState<Pt[]>([]);
  const [busy, setBusy] = useState(false);
  const d = render.data;
  const hidden = useMemo(() => new Set(d?.layers.filter((l) => !l.visible).map((l) => l.name)), [d]);
  const preview = useMemo(() => (kind === "REVOLVE" && d ? [axisLine(axis, d.extents)] : []), [kind, axis, d]);
  const pickable = (e: RenderEntity) => !!e.geom && PROFILE_TYPES.includes(e.geom.type) && !hidden.has(e.layer);

  const onPick = (_p: Pt, _shift: boolean, scale: number, raw: Pt) => {
    let best: string | null = null;
    let bestD = 5 / scale; // 5 px, same as the 2D viewer
    for (const e of d?.entities ?? []) {
      const dist = pickable(e) ? distToPaths(hitPaths(e), raw) : Infinity;
      if (dist <= bestD) [best, bestD] = [e.handle, dist];
    }
    if (!best) return;
    setMarkers([]);
    setPicked((s) => {
      const n = new Set(s);
      if (!n.delete(best)) n.add(best);
      return n;
    });
  };

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!picked.size) return setError("프로파일 엔티티를 하나 이상 선택하세요.");
    const p = kind === "EXTRUDE" ? padParams(pad) : revolveParams(axis);
    if (typeof p === "string") return setError(p);
    setBusy(true);
    setError(null);
    setMarkers([]);
    try {
      const f = await createFeature(docId, kind, { source_revision_id: revisionId, handles: [...picked], ...p });
      onDone(f.feature_id);
    } catch (err) {
      setError(errText(err));
      setMarkers(dangling(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={kind === "EXTRUDE" ? "Extrude" : "Revolve"} onClose={onClose} wide>
      <form onSubmit={submit} className="space-y-3">
        <p className="text-sm text-muted-foreground">
          폐곡선을 이루는 엔티티를 클릭해 선택하세요. (선, 호, 원, 폴리라인) 선택됨: {picked.size}개{kind === "REVOLVE" && " · 회전축은 파란 선으로 표시되며 프로파일을 가로지르면 안 됩니다."}
        </p>
        <div className="h-80 overflow-hidden rounded-md border border-line">
          {d ? (
            <CanvasViewport entities={d.entities} extents={d.extents} hidden={hidden} onCursor={NOOP} onZoom={NOOP} selected={picked} preview={preview} onPick={onPick} snapOn={false} snapKinds={NO_SNAPS} gridStep={10} showGrid={false} markers={markers} />
          ) : (
            <div className="p-3">{render.error ? <Err text={errText(render.error)} /> : <Loading />}</div>
          )}
        </div>
        {kind === "EXTRUDE" ? <PadFields v={pad} onChange={setPad} /> : <AxisAngle v={axis} onChange={setAxis} />}
        <Err text={error} />
        <Actions busy={busy} disabled={!d} onClose={onClose} />
      </form>
    </Modal>
  );
}

type SketchFeature = Extract<Feature, { feature_type: "SKETCH" }>;

type FromSketch = SketchKind | "POCKET";
const FROM_LABEL: Record<FromSketch, string> = { EXTRUDE: "Extrude", POCKET: "Pocket", REVOLVE: "Revolve" };

// Track 8 P2-1/P2-2: Pad / Pocket / Revolve a sketch (FreeCAD PartDesign); +Z is the sketch plane's normal
function FromSketchDialog({ kind, sketch, docId, features, onPickFace, onClose, onDone }: { kind: FromSketch; sketch: SketchFeature; docId: string; features: Feature[]; onPickFace: PickFace; onClose: () => void; onDone: (id: number) => void }) {
  // FreeCAD Pocket default: 5 mm into the material, against the sketch normal
  const [pad, setPad] = useState<PadDraft>(kind === "POCKET" ? { ...PAD_DEFAULT, distance: "5", direction: "-Z" } : PAD_DEFAULT);
  const bodies = booleanCandidates(features);
  const [target, setTarget] = useState(String(bodies.at(-1)?.feature_id ?? ""));
  const [axis, setAxis] = useState(AXIS_Y);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [picking, setPicking] = useState(false);
  const pickFace = (pick: (u: UpTo) => void) => {
    setPicking(true);
    onPickFace(
      (u) => (setPicking(false), pick(u)),
      () => setPicking(false),
    );
  };

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const p = kind === "REVOLVE" ? revolveParams(axis) : padParams(pad);
    if (typeof p === "string") return setError(p);
    if (kind === "POCKET" && !target) return setError("깎을 형상을 선택하세요.");
    setBusy(true);
    setError(null);
    try {
      const extra = kind === "POCKET" ? { target_feature_id: Number(target) } : {};
      onDone((await createFeature(docId, kind, { sketch_feature_id: sketch.feature_id, ...p, ...extra })).feature_id);
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  }
  if (picking) return null; // the 3D view takes the click; the footer says what to do
  return (
    <Modal title={`${FROM_LABEL[kind]} · ${featureName(sketch)}`} onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        <p className="text-sm text-muted-foreground">
          {kind === "REVOLVE" ? "회전축은 스케치 좌표로 입력합니다 (스케치 Y축: 기준점 0,0 · 방향 0,1)." : `+Z는 스케치 평면의 법선 방향입니다.${kind === "POCKET" ? " 포켓은 프로파일만큼 형상을 깎아 냅니다." : ""}`} 바깥 윤곽 안의 닫힌 윤곽은 구멍이 되고, 보조선은 형상에 쓰이지 않습니다.
        </p>
        {kind === "POCKET" && <BodyPick value={target} candidates={bodies} onChange={setTarget} />}
        {kind === "REVOLVE" ? <AxisAngle v={axis} onChange={setAxis} /> : <PadFields v={pad} onChange={setPad} pocket={kind === "POCKET"} onPickFace={pickFace} />}
        <Err text={error} />
        <Actions busy={busy} onClose={onClose} />
      </form>
    </Modal>
  );
}

// Track 8 P2-1: where the new sketch lies (FreeCAD base planes, or a planar face of a body)
function PlaneDialog({ hasBodies, onPlane, onFace, onClose }: { hasBodies: boolean; onPlane: (p: PlaneName) => void; onFace: () => void; onClose: () => void }) {
  return (
    <Modal title="스케치 평면" onClose={onClose}>
      <div className="space-y-3">
        <div className="flex gap-2">
          {(Object.keys(PLANES) as PlaneName[]).map((k) => (
            <button key={k} type="button" onClick={() => onPlane(k)} className={btn2}>
              {k} 평면
            </button>
          ))}
          <button type="button" disabled={!hasBodies} title={hasBodies ? undefined : "표시 중인 형상이 없습니다"} onClick={onFace} className={btn2}>
            부품 면 선택
          </button>
        </div>
        <p className="text-sm text-muted-foreground">XY는 위에서, XZ는 앞에서, YZ는 오른쪽에서 본 평면입니다. 부품 면을 고르면 그 평평한 면 위에 스케치합니다.</p>
      </div>
    </Modal>
  );
}

function SketchWorkspace({ session, docId, meshes, others, onSaved, onCancel }: { session: SketchSession; docId: string; meshes: { id: number; mesh: MeshJson }[]; others: Overlay[]; onSaved: (id: number) => void; onCancel: () => void }) {
  const sk = useSketchEditor(session, onSaved, docId);
  const overlays = useMemo(() => [...others, sk.overlay], [others, sk.overlay]);
  return (
    <>
      <main className="min-w-0 flex-1">
        <ThreeViewport meshes={meshes} selectedId={null} onSelect={NOOP} overlays={overlays} sketchPlane={session.plane} onSketch={sk.onSketch} />
      </main>
      <aside aria-label="스케치" className="w-80 shrink-0 space-y-3 overflow-y-auto border-l border-line bg-card p-3">
        {sk.panel}
        <button type="button" onClick={onCancel} className={`${btn2} w-full`}>
          취소 (변경 버림)
        </button>
      </aside>
    </>
  );
}

function BodyPick({ value, candidates, onChange }: { value: string; candidates: Feature[]; onChange: (v: string) => void }) {
  return (
    <Field label="깎을 형상">
      <select value={value} onChange={(e) => onChange(e.target.value)} className={field}>
        <option value="">선택</option>
        {candidates.map((f) => (
          <option key={f.feature_id} value={f.feature_id}>
            {featureName(f)}
          </option>
        ))}
      </select>
    </Field>
  );
}

// FN-11: combine two visible bodies; the inputs become hidden (consumed)
function BooleanDialog({ docId, features, onClose, onDone }: { docId: string; features: Feature[]; onClose: () => void; onDone: (id: number) => void }) {
  const candidates = booleanCandidates(features);
  const [v, setV] = useState<BoolDraft>({ op: "CUT", target: String(candidates[0]?.feature_id ?? ""), tool: String(candidates[1]?.feature_id ?? "") });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const p = booleanOf(v);
    if (typeof p === "string") return setError(p);
    setBusy(true);
    setError(null);
    try {
      onDone((await createFeature(docId, "BOOLEAN", p)).feature_id);
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Boolean" onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        <p className="text-sm text-muted-foreground">대상에서 도구 형상으로 연산합니다. 입력 형상은 결과에 포함되어 화면에서 숨겨집니다.</p>
        <BooleanFields v={v} candidates={candidates} onChange={setV} />
        <Err text={error} />
        <Actions busy={busy} onClose={onClose} />
      </form>
    </Modal>
  );
}

// Track 5-3: machining feature on one visible body; the body is consumed like a BOOLEAN input
function MachiningDialog({ docId, features, onClose, onDone }: { docId: string; features: Feature[]; onClose: () => void; onDone: (id: number) => void }) {
  const candidates = booleanCandidates(features);
  const [v, setV] = useState<MachDraft>({ ...MACH_DEFAULT, target: String(candidates[candidates.length - 1]?.feature_id ?? "") });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const p = machiningParams(v);
    if (typeof p === "string") return setError(p);
    setBusy(true);
    setError(null);
    try {
      onDone((await createFeature(docId, v.kind, p)).feature_id);
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="가공 Feature" onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        <p className="text-sm text-muted-foreground">선택한 형상에 필렛·모따기·구멍·패턴·대칭·쉘을 적용합니다. 원래 형상은 결과에 포함되어 화면에서 숨겨집니다.</p>
        <MachiningFields v={v} candidates={candidates} onChange={setV} />
        <Err text={error} />
        <Actions busy={busy} onClose={onClose} />
      </form>
    </Modal>
  );
}

const IMPORT_WARN: Record<string, string> = {
  SEWN: "곡면 모델을 봉합해 솔리드로 변환했습니다. 형상을 확인하세요.",
  OPEN_SHELLS: "닫히지 않은 셸이 있어 부피 값을 신뢰할 수 없습니다.",
  TREE_TRUNCATED: "조립 트리가 커서 일부만 표시합니다. 파트 목록은 전체입니다.",
};

function Tree({ nodes }: { nodes: TreeNode[] }) {
  return (
    <ul className="ml-3 space-y-0.5 border-l border-line pl-2">
      {nodes.map((n, i) => (
        <li key={i}>
          <span className={n.kind === "ASSEMBLY" ? "font-medium text-foreground" : "text-body"}>{n.name}</span>
          {n.kind === "ASSEMBLY" && <Tree nodes={n.children} />}
        </li>
      ))}
    </ul>
  );
}

// FN-12/15: imported STEP/IGES — source file, warnings, parts with instance counts, assembly tree
function ImportInfo({ f }: { f: Extract<Feature, { feature_type: "IMPORT" }> }) {
  const m = f.metrics;
  return (
    <div className="space-y-3 text-sm">
      <p className="break-all text-body">
        <span className="font-mono text-xs text-muted-foreground">{f.params.format}</span> {f.params.filename}
      </p>
      {m?.warnings?.map((w) => (
        <p key={w} role="status" className="rounded-md bg-amber-50 px-2 py-1 text-amber-800">
          ⚠ {IMPORT_WARN[w] ?? w}
        </p>
      ))}
      {m?.parts && (
        <table className="w-full text-xs">
          <caption className="mb-1 text-left text-sm font-semibold text-foreground">
            파트 {m.part_count}종 · 인스턴스 {m.instance_count}개
          </caption>
          <thead>
            <tr className="text-muted-foreground">
              <th className="text-left font-medium">이름</th>
              <th className="text-right font-medium">수량</th>
              <th className="text-right font-medium">부피 mm³</th>
            </tr>
          </thead>
          <tbody>
            {m.parts.map((p) => (
              <tr key={p.part_key} className="border-t border-line">
                <td className="max-w-32 truncate py-0.5" title={p.color ? `${p.name} (${p.color})` : p.name}>
                  {p.color && /^#[0-9a-f]{6}$/.test(p.color) && <span aria-hidden className="mr-1 inline-block size-2.5 rounded-sm border border-line align-middle" style={{ background: p.color }} />}
                  {p.name}
                </td>
                <td className="text-right font-mono">{p.instance_count}</td>
                <td className="text-right font-mono">{num(p.volume_mm3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {!!m?.assembly_tree?.length && (
        <section aria-label="조립 구조">
          <h3 className="mb-1 font-semibold text-foreground">조립 구조</h3>
          <Tree nodes={m.assembly_tree} />
        </section>
      )}
    </div>
  );
}

function Properties({ f, features, canEdit, onChanged, onDeleted, onSketch, onPickFace }: { f: Feature; features: Feature[]; canEdit: boolean; onChanged: () => void; onDeleted: () => void; onSketch: (action: "EDIT" | FromSketch, f: SketchFeature) => void; onPickFace: PickFace }) {
  const padded = f.feature_type === "EXTRUDE" || f.feature_type === "POCKET";
  const [pad, setPad] = useState<PadDraft>(padded ? padDraftOf(f.params) : PAD_DEFAULT);
  const [target, setTarget] = useState(f.feature_type === "POCKET" ? String(f.params.target_feature_id) : "");
  const [prim, setPrim] = useState<PrimDraft>(f.feature_type === "PRIMITIVE" ? primDraftOf(f.params) : PRIM_DEFAULT);
  const [axis, setAxis] = useState(f.feature_type === "REVOLVE" ? axisOf(f.params) : AXIS_Y);
  const [bool, setBool] = useState<BoolDraft>(f.feature_type === "BOOLEAN" ? { op: f.params.op, target: String(f.params.target_feature_id), tool: String(f.params.tool_feature_id) } : { op: "CUT", target: "", tool: "" });
  const [mach, setMach] = useState<MachDraft>(isMach(f) ? machDraftOf(f.feature_type, f.params) : MACH_DEFAULT);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);

  async function run(fn: () => Promise<unknown>, done: () => void) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      done();
    } catch (e) {
      setError(failText(e, features));
      onChanged(); // a failed regenerate may still flip the feature to ERROR
    } finally {
      setBusy(false);
    }
  }
  const regen = () => {
    if (f.feature_type === "IMPORT" || f.feature_type === "SKETCH") return;
    if (isMach(f)) {
      const m = machiningParams(mach);
      if (typeof m === "string") return setError(m);
      return void run(() => api(`/features/${f.feature_id}`, { method: "PATCH", json: { params: m } }), onChanged);
    }
    if (f.feature_type === "PRIMITIVE") {
      const q = primitiveParams(prim);
      if (typeof q === "string") return setError(q);
      return void run(() => api(`/features/${f.feature_id}`, { method: "PATCH", json: { params: q } }), onChanged);
    }
    const p = padded ? padParams(pad) : f.feature_type === "REVOLVE" ? revolveParams(axis) : booleanOf(bool);
    if (typeof p === "string") return setError(p);
    const extra = f.feature_type === "POCKET" ? { target_feature_id: Number(target) } : {};
    void run(() => api(`/features/${f.feature_id}`, { method: "PATCH", json: { params: { ...f.params, ...p, ...extra } } }), onChanged);
  };

  return (
    <div className="space-y-3">
      <h2 className="text-sm font-semibold text-foreground">{featureName(f)}</h2>
      {f.status === "ERROR" && <Err text={`⚠ ${f.error_code ? errText(new ApiError(f.error_code, f.error_code)) : "재생성 오류"}`} />}
      {!f.visible && <p className="text-sm text-muted-foreground">다른 Feature(돌출·회전·Boolean·가공)의 입력으로 사용 중이어서 화면에 표시되지 않습니다.</p>}
      {f.feature_type === "SKETCH" && (
        <p className="text-sm text-body">
          {planeName(f.params.plane)} · 요소 {f.params.entities.length}개 · 구속 {f.params.constraints.length}개
        </p>
      )}
      <fieldset disabled={!canEdit || busy} className="space-y-3">
        {f.feature_type === "POCKET" && <BodyPick value={target} candidates={booleanCandidates(features, f)} onChange={setTarget} />}
        {padded && <PadFields v={pad} onChange={setPad} pocket={f.feature_type === "POCKET"} onPickFace={(pick) => onPickFace(pick, NOOP)} />}
        {f.feature_type === "PRIMITIVE" && <PrimFields v={prim} onChange={setPrim} kindFixed />}
        {f.feature_type === "REVOLVE" && <AxisAngle v={axis} onChange={setAxis} />}
        {f.feature_type === "BOOLEAN" && <BooleanFields v={bool} candidates={booleanCandidates(features, f)} onChange={setBool} />}
        {f.feature_type === "IMPORT" && <ImportInfo f={f} />}
        {isMach(f) && <MachiningFields v={mach} candidates={booleanCandidates(features, f)} onChange={setMach} kindFixed />}
        {canEdit && f.feature_type === "SKETCH" && (
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={() => onSketch("EDIT", f)} className={btnPrimary}>
              스케치 편집
            </button>
            {(["EXTRUDE", "POCKET", "REVOLVE"] as const).map((k) => {
              const why = !f.visible ? "이미 다른 Feature에 쓰인 스케치입니다" : k === "POCKET" && !booleanCandidates(features).length ? "깎을 형상이 없습니다" : undefined;
              return (
                <button key={k} type="button" disabled={!!why} title={why} onClick={() => onSketch(k, f)} className={btn2}>
                  {FROM_LABEL[k]}
                </button>
              );
            })}
          </div>
        )}
        {canEdit && (
          <div className="flex gap-2">
            {f.feature_type !== "IMPORT" && f.feature_type !== "SKETCH" && (
              <button type="button" onClick={regen} className={btnPrimary}>
                재생성
              </button>
            )}
            <button type="button" onClick={() => setConfirm(true)} className={btn2}>
              삭제
            </button>
          </div>
        )}
      </fieldset>
      <Err text={error} />
      {confirm && (
        <Modal title="Feature 삭제" onClose={() => setConfirm(false)}>
          <p className="mb-4 text-sm">
            {featureName(f)}을(를) 삭제할까요?{!!f.inputs.length && " 입력 형상(스케치)이 다시 표시됩니다."}
          </p>
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => setConfirm(false)} className={btn2}>
              취소
            </button>
            <button
              type="button"
              onClick={() => {
                setConfirm(false);
                void run(() => api(`/features/${f.feature_id}`, { method: "DELETE" }), onDeleted);
              }}
              className={btnDanger}
            >
              삭제
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

const Metric = ({ label, value }: { label: string; value: string }) => (
  <div className="flex justify-between gap-2">
    <dt className="text-muted-foreground">{label}</dt>
    <dd className="text-right font-mono text-foreground">{value}</dd>
  </div>
);

function Workbench({ docId, initial = null }: { docId: string; initial?: number | null }) {
  const me = useApi<User>("/auth/me").data;
  const doc = useApi<Doc>(`/documents/${encodeURIComponent(docId)}`);
  const feats = useApi<List<Feature>>(`/documents/${encodeURIComponent(docId)}/features`);
  const [selectedId, setSelectedId] = useState<number | null>(initial);
  const [dialog, setDialog] = useState<SketchKind | "BOOLEAN" | "MACH" | "PLANE" | "PRIMITIVE" | null>(null);
  const [session, setSession] = useState<SketchSession | null>(null);
  // a click on a planar face goes to `pick` (new sketch plane, or an up-to face); Esc runs `cancel`
  const [facePick, setFacePick] = useState<{ pick: (point: V3, normal: V3) => void; cancel: () => void; hint: string } | null>(null);
  const [fromSketch, setFromSketch] = useState<{ kind: FromSketch; sketch: SketchFeature } | null>(null);
  const pickUpTo: PickFace = (pick, cancel) => setFacePick({ pick: (origin, normal) => pick({ origin, normal }), cancel, hint: "돌출이 멈출 평평한 면을 클릭하세요 (Esc 취소)" });
  const [meshes, setMeshes] = useState<Record<string, MeshJson>>({});
  const [meshError, setMeshError] = useState<string | null>(null);
  const canEdit = !!me && can(me, "DESIGNER");
  const features = useMemo(() => feats.data?.items.toSorted((a, b) => a.seq - b.seq) ?? [], [feats.data]);
  const key = (f: Feature) => `${f.feature_id}:${f.updated_at}`;
  const drawn = (f: Feature) => f.feature_type !== "SKETCH" && f.status === "OK" && f.visible; // consumed BOOLEAN inputs live inside the result

  useEffect(() => {
    let live = true;
    for (const f of features) {
      if (!drawn(f) || meshes[key(f)]) continue;
      api<MeshJson>(`/features/${f.feature_id}/mesh`).then(
        (m) => live && setMeshes((s) => ({ ...s, [key(f)]: m })),
        (e) => live && setMeshError(errText(e)),
      );
    }
    return () => {
      live = false;
    };
    // ponytail: meshes cache never pruned and a re-run may refetch in flight ids, key by updated_at keeps it correct; add eviction if sessions get long
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [features]);

  const shown = useMemo(() => features.filter((f) => drawn(f) && meshes[key(f)]).map((f) => ({ id: f.feature_id, mesh: meshes[key(f)] })), [features, meshes]);
  // visible sketches are drawn as lines on their planes (the edited one comes from the editor)
  const sketches = useMemo(
    (): Overlay[] =>
      features
        .filter((f): f is SketchFeature => f.feature_type === "SKETCH" && f.visible && f.feature_id !== session?.featureId)
        .map((f) => ({ plane: f.params.plane, paths: f.params.entities.filter((e) => !e.construction).flatMap((e) => toPaths(geomOf(e)).map((pts) => ({ pts, color: f.feature_id === selectedId ? "selected" : "shown" }))) })),
    [features, selectedId, session],
  );
  useEffect(() => {
    if (!facePick) return;
    const esc = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      facePick.cancel();
      setFacePick(null);
    };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [facePick]);
  const tris = shown.reduce((s, m) => s + m.mesh.triangle_count, 0);
  const sel = features.find((f) => f.feature_id === selectedId) ?? null;
  const m = sel?.metrics;
  const d = doc.data;
  const revId = d?.current_revision_id;
  const canBoolean = booleanCandidates(features).length >= 2;
  const [io, setIo] = useState<{ busy: boolean; error: string | null }>({ busy: false, error: null });

  async function importFile(file: File | undefined) {
    if (!file) return;
    if (!/\.(step|stp|iges|igs)$/i.test(file.name)) return setIo({ busy: false, error: ".step/.stp/.iges/.igs 파일만 가져올 수 있습니다." });
    if (file.size > MAX_IMPORT_BYTES) return setIo({ busy: false, error: "파일 크기는 100MB 이하여야 합니다." });
    const body = new FormData();
    body.append("file", file);
    setIo({ busy: true, error: null });
    try {
      done((await api<Feature>(`/documents/${encodeURIComponent(docId)}/imports`, { method: "POST", body })).feature_id);
      setIo({ busy: false, error: null });
    } catch (e) {
      setIo({ busy: false, error: errText(e) });
    }
  }
  async function exportFile(format: ExportFmt) {
    setIo({ busy: true, error: null });
    try {
      await download(`/documents/${encodeURIComponent(docId)}/model?format=${format}`, `${d?.doc_no ?? "model"}.${EXPORT_EXT[format]}`);
      setIo({ busy: false, error: null });
    } catch (e) {
      setIo({ busy: false, error: errText(e) });
    }
  }
  const done = (id: number) => {
    setDialog(null);
    setFromSketch(null);
    setSession(null);
    setSelectedId(id);
    feats.reload();
  };

  return (
    <div className="flex h-screen flex-col bg-background">
      <header className="flex h-12 shrink-0 items-center gap-4 border-b border-line bg-card px-4 text-sm">
        <span className="font-semibold text-foreground">AX-CAD</span>
        <Link href={`/documents/${encodeURIComponent(docId)}`} className={link}>
          ← 도면 상세
        </Link>
        {d && (
          <span className="text-foreground">
            <span className="font-mono">{d.doc_no}</span> {d.title}
          </span>
        )}
        <nav aria-label="작업 화면" className="flex gap-1">
          {revId ? (
            <Link href={`/viewer/${revId}?doc=${encodeURIComponent(docId)}`} className="rounded-md px-3 py-1 text-body hover:bg-hover focus-visible:outline-2 focus-visible:outline-ring">
              Draft
            </Link>
          ) : (
            <span className="px-3 py-1 text-muted-foreground">Draft</span>
          )}
          <span aria-current="page" className="rounded-md bg-[var(--color-primary-light)] px-3 py-1 font-medium text-[var(--color-primary-hover)]">
            Model
          </span>
        </nav>
        {me && !canEdit && <span className="text-muted-foreground">읽기 전용</span>}
      </header>

      <div className="flex min-h-0 flex-1">
        <aside aria-label="Feature" className="w-64 shrink-0 space-y-3 overflow-y-auto border-r border-line bg-card p-3">
          <h2 className="text-sm font-semibold text-foreground">Feature</h2>
          {canEdit && (
            <div className="flex flex-wrap gap-1">
              <button type="button" disabled={!!session} title="평면이나 부품 면에 구속 스케치를 그립니다" onClick={() => setDialog("PLANE")} className={btn2}>
                Sketch
              </button>
              <button type="button" title="상자·원통·원뿔·구·토러스" onClick={() => setDialog("PRIMITIVE")} className={btn2}>
                원시 형상
              </button>
              {(["EXTRUDE", "REVOLVE"] as const).map((k) => (
                <button key={k} type="button" disabled={!revId} title={revId ? undefined : "리비전이 없습니다"} onClick={() => setDialog(k)} className={btn2}>
                  {k === "EXTRUDE" ? "Extrude" : "Revolve"}
                </button>
              ))}
              <button type="button" disabled={!canBoolean} title={canBoolean ? undefined : "표시 중인 형상이 2개 이상 필요합니다"} onClick={() => setDialog("BOOLEAN")} className={btn2}>
                Boolean
              </button>
              <button type="button" disabled={!booleanCandidates(features).length} title="필렛·모따기·구멍·패턴·대칭·쉘" onClick={() => setDialog("MACH")} className={btn2}>
                가공
              </button>
              <label className={`${btn2} cursor-pointer has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-50`}>
                가져오기
                <input
                  type="file"
                  accept=".step,.stp,.iges,.igs"
                  disabled={io.busy}
                  className="sr-only"
                  onChange={(e) => {
                    void importFile(e.target.files?.[0]);
                    e.target.value = "";
                  }}
                />
              </label>
            </div>
          )}
          <div className="flex items-center gap-1">
            <span className="text-xs text-muted-foreground">내보내기</span>
            {(Object.keys(EXPORT_EXT) as ExportFmt[]).map((fmt) => (
              <button key={fmt} type="button" disabled={io.busy || !shown.length} onClick={() => void exportFile(fmt)} className={btn2}>
                {fmt}
              </button>
            ))}
          </div>
          {io.busy && <Loading />}
          <Err text={io.error} />
          <Err text={(feats.error && errText(feats.error)) || (doc.error && errText(doc.error)) || meshError} />
          {!feats.data && !feats.error ? <Loading /> : <FeatureTree features={features} selectedId={selectedId} onSelect={setSelectedId} />}
        </aside>

        {session ? (
          <SketchWorkspace key={session.featureId ?? "new"} session={session} docId={docId} meshes={shown} others={sketches} onSaved={done} onCancel={() => setSession(null)} />
        ) : (
          <main className="min-w-0 flex-1">
            <ThreeViewport
              meshes={shown}
              selectedId={selectedId}
              onSelect={setSelectedId}
              overlays={sketches}
              onFacePick={
                facePick
                  ? (point, normal) => {
                      setFacePick(null);
                      facePick.pick(point, normal);
                    }
                  : null
              }
            />
          </main>
        )}

        {!session && (
        <aside aria-label="속성" className="w-80 shrink-0 space-y-4 overflow-y-auto border-l border-line bg-card p-3">
          {sel ? (
            <>
              <Properties
                key={key(sel)}
                f={sel}
                features={features}
                canEdit={canEdit}
                onChanged={feats.reload}
                onDeleted={() => {
                  setSelectedId(null);
                  feats.reload();
                }}
                onPickFace={pickUpTo}
                onSketch={(action, f) => (action === "EDIT" ? setSession({ featureId: f.feature_id, plane: f.params.plane, entities: f.params.entities, constraints: f.params.constraints }) : setFromSketch({ kind: action, sketch: f }))}
              />
              <section aria-label="계측" className="space-y-1 border-t border-line pt-3 text-sm">
                <h3 className="font-semibold text-foreground">계측</h3>
                {m ? (
                  <dl className="space-y-1">
                    <Metric label="부피" value={`${num(m.volume_mm3)} mm³`} />
                    <Metric label="표면적" value={`${num(m.surface_area_mm2)} mm²`} />
                    <Metric label="BBox" value={`${m.bbox.size.map(num).join(" × ")} mm`} />
                  </dl>
                ) : (
                  <p className="text-muted-foreground">계측값이 없습니다.</p>
                )}
              </section>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">Feature를 선택하세요.</p>
          )}
        </aside>
        )}
      </div>

      <footer className="flex h-8 shrink-0 items-center gap-6 border-t border-line bg-muted px-4 font-mono text-xs text-body">
        <span>선택: {sel ? featureName(sel) : "-"}</span>
        <span>삼각형 {tris.toLocaleString("ko-KR")}</span>
        <span>단위 mm</span>
        <span role="status" className="ml-auto font-sans text-body">
          {facePick ? facePick.hint : session ? "스케치 모드: 좌 클릭 작도·선택 · 우 드래그 이동 · 휠 확대/축소" : "좌 드래그 회전 · 우 드래그 이동 · 휠 확대/축소"}
        </span>
      </footer>

      {(dialog === "EXTRUDE" || dialog === "REVOLVE") && revId && <SketchDialog kind={dialog} docId={docId} revisionId={revId} onClose={() => setDialog(null)} onDone={done} />}
      {dialog === "BOOLEAN" && <BooleanDialog docId={docId} features={features} onClose={() => setDialog(null)} onDone={done} />}
      {dialog === "PLANE" && (
        <PlaneDialog
          hasBodies={!!shown.length}
          onClose={() => setDialog(null)}
          onPlane={(k) => {
            setDialog(null);
            setSession({ featureId: null, plane: PLANES[k], entities: [], constraints: [] });
          }}
          onFace={() => {
            setDialog(null);
            setFacePick({ pick: (point, normal) => setSession({ featureId: null, plane: facePlane(point, normal), entities: [], constraints: [] }), cancel: NOOP, hint: "스케치할 평평한 면을 클릭하세요 (Esc 취소)" });
          }}
        />
      )}
      {fromSketch && <FromSketchDialog kind={fromSketch.kind} sketch={fromSketch.sketch} docId={docId} features={features} onPickFace={pickUpTo} onClose={() => setFromSketch(null)} onDone={done} />}
      {dialog === "PRIMITIVE" && <PrimitiveDialog docId={docId} onClose={() => setDialog(null)} onDone={done} />}
      {dialog === "MACH" && <MachiningDialog docId={docId} features={features} onClose={() => setDialog(null)} onDone={done} />}
    </div>
  );
}

export default function ModelPage({ params }: { params: Promise<{ documentId: string }> }) {
  const { documentId } = use(params);
  return (
    <DesktopOnly back={`/documents/${encodeURIComponent(documentId)}`}>
      <Suspense>
        <ModelRoute docId={documentId} />
      </Suspense>
    </DesktopOnly>
  );
}

function ModelRoute({ docId }: { docId: string }) {
  // FN-18 "도면에서 보기" from a 3D quote line: ?select=featureId
  const first = parseSelect(useSearchParams().get("select"), "feature")[0];
  return <Workbench docId={docId} initial={first ? Number(first) : null} />;
}
