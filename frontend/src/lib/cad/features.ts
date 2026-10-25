// FN-10/11 form logic for the model workbench, kept free of React for tests
import type { BBox, Sig } from "./mesh";
export type { Sig } from "./mesh";
import type { Plane, SketchParams, V3 } from "./sketch";
import type { Extents, Pt } from "./view";

export type Direction = "+Z" | "-Z";
export type BooleanOp = "FUSE" | "CUT" | "COMMON";
// profile: DXF entities of a revision, or a SKETCH feature (Track 8 P2-1)
type Sketch = { source_revision_id?: string | null; handles?: string[] | null; sketch_feature_id?: number | null };
// Track 8 P2-2 Pad / Pocket lengths (FreeCAD PartDesign); up_to is a planar face picked in 3D
export type PadMode = "LENGTH" | "SYMMETRIC" | "TWO_SIDED" | "UP_TO_FACE" | "THROUGH_ALL";
export type UpTo = { origin: [number, number, number]; normal: [number, number, number] };
type PadOpts = { mode?: PadMode; distance?: number | null; distance2?: number | null; direction: Direction; taper_deg?: number; up_to?: UpTo | null };
export type ExtrudeParams = Sketch & PadOpts;
export type PocketParams = ExtrudeParams & { target_feature_id: number };
export type PrimKind = "BOX" | "CYLINDER" | "CONE" | "SPHERE" | "TORUS";
export type PrimitiveParams = { kind: PrimKind; length?: number | null; width?: number | null; height?: number | null; radius?: number | null; radius2?: number | null; origin: [number, number, number]; axis: "X" | "Y" | "Z" };
export type RevolveParams = Sketch & { axis_point: [number, number]; axis_dir: [number, number]; angle_deg: number };
export type BooleanParams = { op: BooleanOp; target_feature_id: number; tool_feature_id: number };
export type ImportParams = { file_key: string; format: "STEP" | "IGES"; filename: string };
// machining features on one earlier body (FreeCAD PartDesign style); the body is consumed
export type EdgeSet = "VERTICAL" | "TOP" | "BOTTOM" | "ALL";
export type MachKind = "FILLET" | "CHAMFER" | "HOLE" | "PATTERN" | "MIRROR" | "SHELL" | "DRAFT";
export type OpenFace = "TOP" | "BOTTOM" | "BOTH" | "NONE";
type OnBody = { target_feature_id: number };
type EdgePick = { edges?: EdgeSet | null; edge_refs?: Sig[] | null; face_refs?: Sig[] | null };
export type Fit = "FINE" | "MEDIUM" | "COARSE";
export type Head = "NONE" | "COUNTERBORE" | "COUNTERSINK";
export type HoleSpec = { size: string; kind: "TAPPED" | "CLEARANCE"; pitch?: number | null; fit?: Fit; head?: Head; head_diameter?: number | null; head_depth?: number | null; thread_depth?: number | null };
export type HoleParams = OnBody & { positions?: [number, number][] | null; sketch_feature_id?: number | null; plane?: Plane | null; diameter?: number | null; spec?: HoleSpec | null; depth: number | null };
export type MachParams =
  | (OnBody & EdgePick & { radius: number })
  | (OnBody & EdgePick & { distance: number })
  | HoleParams
  | (OnBody & { kind: "LINEAR" | "POLAR"; copies: number; step?: number[] | null; center?: number[] | null; angle_deg?: number })
  | (OnBody & { plane: "YZ" | "ZX" | "XY"; offset: number })
  | (OnBody & { thickness: number; open_face: OpenFace; outward?: boolean; join?: "ARC" | "INTERSECTION"; face_refs?: Sig[] | null })
  | (OnBody & { angle_deg: number; pull: Direction; neutral: "TOP" | "BOTTOM"; face_refs?: Sig[] | null });
// Track 8 P2-3 bodies made from sketches (FreeCAD AdditivePipe / AdditiveLoft)
export type SweepParams = { profile_sketch_id: number; path_sketch_id: number; frenet: boolean };
export type LoftParams = { sketch_ids: number[]; ruled: boolean };
type Measure = { volume_mm3: number; surface_area_mm2: number; bbox: BBox & { size: [number, number, number] } };
export type Part = Measure & { part_key: string; name: string; instance_count: number; color?: string | null }; // "#rrggbb" from the file
export type TreeNode = { name: string; kind: "PART"; part_key: string } | { name: string; kind: "ASSEMBLY"; children: TreeNode[] };
// FN-15: IMPORT features also carry the per-part breakdown
// HOLE: what a quote needs (count, sizes, the thread for tapping) and the centres for the cosmetic thread
export type HoleInfo = { count: number; diameter: number; depth: number | null; size?: string; kind?: string; head?: { kind: Head; diameter: number; depth?: number }; thread?: { label: string; major: number; pitch: number; fine: boolean; length: number | null }; centers: V3[]; axis: V3 };
type Metrics = Measure & { parts?: Part[]; part_count?: number; instance_count?: number; assembly_tree?: TreeNode[]; warnings?: string[]; holes?: HoleInfo };
export type Feature = {
  feature_id: number;
  seq: number;
  status: "OK" | "ERROR";
  error_code: string | null;
  metrics: Metrics | null;
  created_at: string;
  updated_at: string;
  visible: boolean; // false = consumed by a BOOLEAN, a machining feature or (a sketch) an Extrude/Revolve
  inputs: number[];
} & (
  | { feature_type: "SKETCH"; params: SketchParams }
  | { feature_type: "POCKET"; params: PocketParams }
  | { feature_type: "PRIMITIVE"; params: PrimitiveParams }
  | { feature_type: "SWEEP"; params: SweepParams }
  | { feature_type: "LOFT"; params: LoftParams }
  | { feature_type: "EXTRUDE"; params: ExtrudeParams }
  | { feature_type: "REVOLVE"; params: RevolveParams }
  | { feature_type: "BOOLEAN"; params: BooleanParams }
  | { feature_type: "IMPORT"; params: ImportParams }
  | { feature_type: MachKind; params: MachParams }
);

export const MACH_LABEL: Record<MachKind, string> = { FILLET: "필렛", CHAMFER: "모따기", HOLE: "구멍", PATTERN: "패턴", MIRROR: "대칭", SHELL: "쉘(두께)", DRAFT: "구배" };
export const isMach = (f: Feature): f is Feature & { feature_type: MachKind; params: MachParams } => f.feature_type in MACH_LABEL;

export const OP_SYMBOL: Record<BooleanOp, string> = { FUSE: "+", CUT: "−", COMMON: "∩" };
const cap = (s: string) => s[0] + s.slice(1).toLowerCase();
export const featureName = (f: Feature) =>
  `${cap(f.feature_type === "BOOLEAN" ? (f.params as BooleanParams).op : f.feature_type === "PRIMITIVE" ? (f.params as PrimitiveParams).kind : f.feature_type)}${String(f.seq).padStart(3, "0")}`;

export type AxisDraft = { px: string; py: string; dx: string; dy: string; angle: string };

const fin = (s: string) => (s.trim() === "" ? NaN : Number(s));

/** Revolve params from the form strings, or a Korean error message. */
export function revolveParams(d: AxisDraft): { axis_point: Pt; axis_dir: Pt; angle_deg: number } | string {
  const [px, py, dx, dy, angle] = [d.px, d.py, d.dx, d.dy, d.angle].map(fin);
  if (![px, py, dx, dy].every(Number.isFinite)) return "축 좌표와 방향을 숫자로 입력하세요.";
  if (dx === 0 && dy === 0) return "축 방향은 (0, 0)일 수 없습니다.";
  if (!(angle > 0 && angle <= 360)) return "각도는 0 초과 360 이하여야 합니다.";
  return { axis_point: [px, py], axis_dir: [dx, dy], angle_deg: angle };
}

/** Axis line through point along dir, long enough to cross the drawing extents (2D preview). */
export function axisLine(d: AxisDraft, ext: Extents): Pt[] {
  const [px, py, dx, dy] = [d.px, d.py, d.dx, d.dy].map(fin);
  const len = Math.hypot(dx, dy);
  if (![px, py].every(Number.isFinite) || !(len > 0)) return [];
  const span = Math.hypot(ext.max[0] - ext.min[0], ext.max[1] - ext.min[1]) + Math.hypot(px - ext.min[0], py - ext.min[1]) + 1;
  const [ux, uy] = [(dx / len) * span, (dy / len) * span];
  return [
    [px - ux, py - uy],
    [px + ux, py + uy],
  ];
}

/** Bodies a BOOLEAN may take: built, not consumed (except by `own`), earlier than `own`. */
export function booleanCandidates(features: Feature[], own?: Feature): Feature[] {
  const mine = own?.feature_type === "BOOLEAN" ? [own.params.target_feature_id, own.params.tool_feature_id] : own && (isMach(own) || own.feature_type === "POCKET") ? [own.params.target_feature_id] : [];
  return features.filter((f) => f.feature_type !== "SKETCH" && f.status === "OK" && f !== own && (f.visible || mine.includes(f.feature_id)) && (!own || f.seq < own.seq));
}

export const PAD_MODE_LABEL: Record<PadMode, string> = { LENGTH: "길이", SYMMETRIC: "대칭(평면 양쪽)", TWO_SIDED: "양방향 두 길이", UP_TO_FACE: "면까지", THROUGH_ALL: "관통" };
/** Form strings of the Pad / Pocket fields. */
export type PadDraft = { mode: PadMode; distance: string; distance2: string; direction: Direction; taper: string; upTo: UpTo | null };
export const PAD_DEFAULT: PadDraft = { mode: "LENGTH", distance: "10", distance2: "5", direction: "+Z", taper: "0", upTo: null };

export const padDraftOf = (p: PadOpts): PadDraft => ({
  mode: p.mode ?? "LENGTH",
  distance: p.distance == null ? "10" : String(p.distance),
  distance2: p.distance2 == null ? "5" : String(p.distance2),
  direction: p.direction,
  taper: String(p.taper_deg ?? 0),
  upTo: p.up_to ?? null,
});

/** API length options for the draft, or a Korean error message. */
export function padParams(d: PadDraft): PadOpts | string {
  const [distance, distance2, taper] = [d.distance, d.distance2, d.taper].map(fin);
  const ok = (v: number) => v > 0 && v <= 1e6;
  if ((d.mode === "LENGTH" || d.mode === "SYMMETRIC" || d.mode === "TWO_SIDED") && !ok(distance)) return "거리는 0보다 커야 합니다.";
  if (d.mode === "TWO_SIDED" && !ok(distance2)) return "두 번째 거리는 0보다 커야 합니다.";
  if (d.mode === "UP_TO_FACE" && !d.upTo) return "도달할 면을 3D 화면에서 고르세요.";
  if (!(Math.abs(taper) < 60)) return "테이퍼 각도는 -60° 초과 60° 미만이어야 합니다.";
  return {
    mode: d.mode,
    direction: d.direction,
    taper_deg: taper,
    distance: ok(distance) && d.mode !== "UP_TO_FACE" && d.mode !== "THROUGH_ALL" ? distance : null,
    distance2: d.mode === "TWO_SIDED" ? distance2 : null,
    up_to: d.mode === "UP_TO_FACE" ? d.upTo : null,
  };
}

export const PRIM_LABEL: Record<PrimKind, string> = { BOX: "상자", CYLINDER: "원통", CONE: "원뿔", SPHERE: "구", TORUS: "토러스" };
// the dimensions each primitive asks for, in form order (FreeCAD Part primitives)
export const PRIM_DIMS: Record<PrimKind, [keyof PrimitiveParams, string][]> = {
  BOX: [["length", "가로 X"], ["width", "세로 Y"], ["height", "높이 Z"]],
  CYLINDER: [["radius", "반지름"], ["height", "높이"]],
  CONE: [["radius", "아래 반지름"], ["radius2", "위 반지름 (0 = 뾰족)"], ["height", "높이"]],
  SPHERE: [["radius", "반지름"]],
  TORUS: [["radius", "링 반지름"], ["radius2", "관 반지름"]],
};
export type PrimDraft = { kind: PrimKind; dims: Record<string, string>; origin: string; axis: "X" | "Y" | "Z" };
export const PRIM_DEFAULT: PrimDraft = { kind: "BOX", dims: { length: "40", width: "30", height: "10", radius: "10", radius2: "5" }, origin: "0,0,0", axis: "Z" };
export const primDraftOf = (p: PrimitiveParams): PrimDraft => ({
  kind: p.kind,
  dims: Object.fromEntries(Object.entries(PRIM_DEFAULT.dims).map(([k, v]) => [k, p[k as keyof PrimitiveParams] == null ? v : String(p[k as keyof PrimitiveParams])])),
  origin: p.origin.join(","),
  axis: p.axis,
});

/** API params for a primitive, or a Korean error message. */
export function primitiveParams(d: PrimDraft): PrimitiveParams | string {
  const origin = nums(d.origin, 3);
  if (!origin) return '기준점을 "x,y,z"로 입력하세요.';
  const out: PrimitiveParams = { kind: d.kind, origin: origin as [number, number, number], axis: d.axis };
  for (const [k, label] of PRIM_DIMS[d.kind]) {
    const v = fin(d.dims[k] ?? "");
    const zeroOk = k === "radius2" && d.kind === "CONE";
    if (!(zeroOk ? v >= 0 : v > 0) || v > 1e6) return `${label}은(는) ${zeroOk ? "0 이상" : "0보다 커야"} 합니다.`;
    (out as Record<string, unknown>)[k] = v;
  }
  if (d.kind === "CONE" && out.radius === out.radius2) return "원뿔의 두 반지름은 달라야 합니다 (같으면 원통).";
  if (d.kind === "TORUS" && !(out.radius2! < out.radius!)) return "관 반지름은 링 반지름보다 작아야 합니다.";
  return out;
}

/** Form strings of the machining dialog (all kinds share one draft so switching kind keeps values). */
export type MachDraft = {
  kind: MachKind;
  target: string;
  size: string; // radius / distance / diameter / thickness
  edges: EdgeSet;
  positions: string; // "x,y; x,y"
  depth: string; // blank = through
  patKind: "LINEAR" | "POLAR";
  copies: string;
  step: string; // "dx,dy,dz"
  center: string; // "x,y"
  angle: string;
  plane: "YZ" | "ZX" | "XY";
  offset: string;
  openFace: OpenFace;
  outward: boolean;
  join: "ARC" | "INTERSECTION";
  pull: Direction; // draft pull direction
  neutral: "TOP" | "BOTTOM"; // draft neutral plane
  // Track 8 P2-4: picked edges / faces instead of a rule
  edgeSrc: "RULE" | "EDGES" | "FACES"; // fillet / chamfer
  faceSrc: "RULE" | "FACES"; // thickness: open faces, draft: side faces
  refs: Sig[];
  // hole wizard
  holeBy: "DIAMETER" | "SPEC";
  holeSize: string; // "M6"
  holeKind: "TAPPED" | "CLEARANCE";
  pitch: string; // "" = coarse
  fit: Fit;
  head: Head;
  headDia: string; // blank = standard
  headDepth: string;
  threadDepth: string; // blank = hole depth
  posBy: "XY" | "FACE" | "SKETCH"; // positions on the top (XY), on a picked face, or a sketch's points
  holePlane: Plane | null;
  holeSketch: string;
};

// ponytail: mirrors core/geometry/holes_std.py COARSE/FINE for the pitch list; the server checks again
export const COARSE: Record<string, number> = { "M1.6": 0.35, M2: 0.4, "M2.5": 0.45, M3: 0.5, M4: 0.7, M5: 0.8, M6: 1, M8: 1.25, M10: 1.5, M12: 1.75, M14: 2, M16: 2, M18: 2.5, M20: 2.5, M22: 2.5, M24: 3, M27: 3, M30: 3.5, M33: 3.5, M36: 4, M39: 4, M42: 4.5, M45: 4.5, M48: 5, M52: 5, M56: 5.5, M60: 5.5, M64: 6 };
const FINE: Record<string, number[]> = { M8: [1], M10: [1.25, 1], M12: [1.5, 1.25], M14: [1.5], M16: [1.5], M18: [2, 1.5], M20: [2, 1.5], M22: [2, 1.5], M24: [2], M27: [2], M30: [2], M33: [2], M36: [3], M39: [3], M42: [3], M45: [3], M48: [3], M52: [4], M56: [4], M60: [4], M64: [4] };
export const pitches = (size: string) => [COARSE[size], ...(FINE[size] ?? [])].filter((x) => x != null);
/** Text of a hole spec: M8x1 탭, Ø9 볼트 구멍 + 카운터보어 */
export const holeText = (h: HoleInfo) => `${h.thread ? `${h.thread.label} 탭` : `Ø${h.diameter}`}${h.head ? (h.head.kind === "COUNTERBORE" ? " + 카운터보어" : " + 접시") : ""} ×${h.count}`;

const nums = (s: string, n: number) => {
  const v = s.split(",").map(fin);
  return v.length === n && v.every(Number.isFinite) ? v : null;
};

/** API params for the draft, or a Korean error message. */
export function machiningParams(d: MachDraft): MachParams | string {
  const target_feature_id = Number(d.target);
  if (!Number.isInteger(target_feature_id) || target_feature_id < 1) return "대상 형상을 선택하세요.";
  const size = fin(d.size);
  const positive = size > 0 && size <= 1e6;
  switch (d.kind) {
    case "FILLET":
    case "CHAMFER": {
      if (!positive) return d.kind === "FILLET" ? "반지름은 0보다 커야 합니다." : "거리는 0보다 커야 합니다.";
      if (d.edgeSrc !== "RULE" && !d.refs.length) return d.edgeSrc === "EDGES" ? "모서리를 3D 화면에서 고르세요." : "면을 3D 화면에서 고르세요.";
      const pick = d.edgeSrc === "RULE" ? { edges: d.edges } : d.edgeSrc === "EDGES" ? { edge_refs: d.refs } : { face_refs: d.refs };
      return d.kind === "FILLET" ? { target_feature_id, radius: size, ...pick } : { target_feature_id, distance: size, ...pick };
    }
    case "HOLE":
      return holeParams(d, target_feature_id);
    case "PATTERN": {
      const copies = fin(d.copies);
      if (!Number.isInteger(copies) || copies < 2 || copies > 100) return "개수는 2~100 정수입니다 (원본 포함).";
      if (d.patKind === "LINEAR") {
        const step = nums(d.step, 3);
        if (!step || step.every((v) => v === 0)) return '간격을 "dx,dy,dz"로 입력하세요 (0이 아닌 값).';
        return { target_feature_id, kind: "LINEAR", copies, step };
      }
      const center = nums(d.center, 2);
      const angle = fin(d.angle);
      if (!center) return '회전 중심을 "x,y"로 입력하세요.';
      if (!(angle > 0 && angle <= 360)) return "각도는 0 초과 360 이하여야 합니다.";
      return { target_feature_id, kind: "POLAR", copies, center, angle_deg: angle };
    }
    case "MIRROR": {
      const offset = fin(d.offset);
      return Number.isFinite(offset) ? { target_feature_id, plane: d.plane, offset } : "평면 위치를 숫자로 입력하세요.";
    }
    case "SHELL": {
      if (!positive) return "두께는 0보다 커야 합니다.";
      if (d.faceSrc === "FACES" && !d.refs.length) return "열어 둘 면을 3D 화면에서 고르세요.";
      const open = d.faceSrc === "FACES" ? { open_face: "NONE" as const, face_refs: d.refs } : { open_face: d.openFace };
      return { target_feature_id, thickness: size, ...open, outward: d.outward, join: d.join };
    }
    case "DRAFT": {
      const angle = fin(d.angle);
      if (!(angle !== 0 && Math.abs(angle) <= 45)) return "구배 각도는 0이 아닌 -45~45°입니다.";
      if (d.faceSrc === "FACES" && !d.refs.length) return "기울일 옆면을 3D 화면에서 고르세요.";
      return { target_feature_id, angle_deg: angle, pull: d.pull, neutral: d.neutral, ...(d.faceSrc === "FACES" ? { face_refs: d.refs } : {}) };
    }
  }
}

/** Hole: diameter or KS/ISO spec, at x,y positions (top face or a picked face) or a sketch's points. */
function holeParams(d: MachDraft, target_feature_id: number): HoleParams | string {
  const depth = d.depth.trim() === "" ? null : fin(d.depth);
  if (depth !== null && !(depth > 0)) return "깊이는 0보다 커야 합니다 (비우면 관통).";
  let where: Partial<HoleParams>;
  if (d.posBy === "SKETCH") {
    const id = Number(d.holeSketch);
    if (!Number.isInteger(id) || id < 1) return "구멍 위치 스케치를 고르세요.";
    where = { sketch_feature_id: id };
  } else {
    const positions = d.positions.split(/[;\n]/).map((t) => t.trim()).filter(Boolean).map((t) => nums(t, 2));
    if (!positions.length || positions.some((q) => !q)) return '구멍 위치를 "x,y; x,y" 형식으로 입력하세요.';
    if (positions.length > 500) return "구멍은 500개까지입니다.";
    if (d.posBy === "FACE" && !d.holePlane) return "구멍을 뚫을 면을 3D 화면에서 고르세요.";
    where = { positions: positions as [number, number][], ...(d.posBy === "FACE" ? { plane: d.holePlane } : {}) };
  }
  if (d.holeBy === "DIAMETER") {
    const size = fin(d.size);
    return size > 0 && size <= 1e6 ? { target_feature_id, ...where, diameter: size, depth } : "직경은 0보다 커야 합니다.";
  }
  const opt = (s: string) => (s.trim() === "" ? null : fin(s));
  const [headDia, headDepth, threadDepth] = [d.headDia, d.headDepth, d.threadDepth].map(opt);
  if ([headDia, headDepth, threadDepth].some((v) => v !== null && !(v > 0))) return "머리 지름·깊이와 나사 깊이는 0보다 커야 합니다 (비우면 표준값).";
  if (threadDepth !== null && depth !== null && threadDepth > depth) return "나사 깊이는 구멍 깊이보다 깊을 수 없습니다.";
  const spec: HoleSpec = { size: d.holeSize, kind: d.holeKind, pitch: d.holeKind === "TAPPED" && d.pitch ? Number(d.pitch) : null, fit: d.fit, head: d.head, head_diameter: d.head === "NONE" ? null : headDia, head_depth: d.head === "COUNTERBORE" ? headDepth : null, thread_depth: d.holeKind === "TAPPED" ? threadDepth : null };
  return { target_feature_id, ...where, spec, depth };
}

/** Draft filled from a stored feature (properties panel). */
export function machDraftOf(kind: MachKind, p: MachParams): MachDraft {
  const q = p as Record<string, unknown>;
  const pair = (v: unknown) => (Array.isArray(v) ? v.join(",") : "");
  const size = q.radius ?? q.distance ?? q.diameter ?? q.thickness ?? "";
  return {
    ...MACH_DEFAULT,
    kind,
    target: String(p.target_feature_id),
    size: String(size),
    edges: (q.edges as EdgeSet) ?? "VERTICAL",
    positions: Array.isArray(q.positions) ? (q.positions as number[][]).map((x) => x.join(",")).join("; ") : "",
    depth: q.depth == null ? "" : String(q.depth),
    patKind: (q.kind as "LINEAR" | "POLAR") ?? "LINEAR",
    copies: q.copies == null ? "3" : String(q.copies),
    step: pair(q.step) || MACH_DEFAULT.step,
    center: pair(q.center) || MACH_DEFAULT.center,
    angle: q.angle_deg == null ? "360" : String(q.angle_deg),
    plane: (q.plane as MachDraft["plane"]) ?? "YZ",
    offset: q.offset == null ? "0" : String(q.offset),
    openFace: (q.open_face as MachDraft["openFace"]) ?? "TOP",
    outward: q.outward === true,
    join: (q.join as MachDraft["join"]) ?? "ARC",
    pull: (q.pull as Direction) ?? "+Z",
    neutral: (q.neutral as MachDraft["neutral"]) ?? "BOTTOM",
    edgeSrc: q.edge_refs ? "EDGES" : q.face_refs && (kind === "FILLET" || kind === "CHAMFER") ? "FACES" : "RULE",
    faceSrc: q.face_refs && (kind === "SHELL" || kind === "DRAFT") ? "FACES" : "RULE",
    refs: ((q.edge_refs ?? q.face_refs) as Sig[] | null) ?? [],
    ...holeDraftOf(q as Partial<HoleParams>),
  };
}

const holeDraftOf = (h: Partial<HoleParams>): Partial<MachDraft> => {
  const s = h.spec;
  const str = (v: number | null | undefined) => (v == null ? "" : String(v));
  return {
    posBy: h.sketch_feature_id ? "SKETCH" : h.plane ? "FACE" : "XY",
    holeSketch: str(h.sketch_feature_id),
    holePlane: h.plane ?? null,
    holeBy: s || h.diameter == null ? "SPEC" : "DIAMETER",
    ...(s ? { holeSize: s.size, holeKind: s.kind, pitch: str(s.pitch), fit: s.fit ?? "MEDIUM", head: s.head ?? "NONE", headDia: str(s.head_diameter), headDepth: str(s.head_depth), threadDepth: str(s.thread_depth) } : {}),
  };
};

export const MACH_DEFAULT: MachDraft = {
  kind: "FILLET",
  target: "",
  size: "2",
  edges: "VERTICAL",
  positions: "10,10",
  depth: "",
  patKind: "LINEAR",
  copies: "3",
  step: "50,0,0",
  center: "0,0",
  angle: "360",
  plane: "YZ",
  offset: "0",
  openFace: "TOP",
  outward: false,
  join: "ARC",
  pull: "+Z",
  neutral: "BOTTOM",
  edgeSrc: "RULE",
  faceSrc: "RULE",
  refs: [],
  holeBy: "SPEC",
  holeSize: "M6",
  holeKind: "TAPPED",
  pitch: "",
  fit: "MEDIUM",
  head: "NONE",
  headDia: "",
  headDepth: "",
  threadDepth: "",
  posBy: "XY",
  holePlane: null,
  holeSketch: "",
};

/** feature_id of the dependent BOOLEAN whose regeneration failed (whole PATCH rolled back). */
export function failedDependent(details: unknown): number | null {
  const id = (details as { feature_id?: unknown } | null | undefined)?.feature_id;
  return typeof id === "number" ? id : null;
}
