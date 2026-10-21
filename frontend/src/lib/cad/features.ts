// FN-10/11 form logic for the model workbench, kept free of React for tests
import type { BBox } from "./mesh";
import type { Extents, Pt } from "./view";

export type Direction = "+Z" | "-Z";
export type BooleanOp = "FUSE" | "CUT" | "COMMON";
type Sketch = { source_revision_id: string; handles: string[] };
export type ExtrudeParams = Sketch & { distance: number; direction: Direction };
export type RevolveParams = Sketch & { axis_point: [number, number]; axis_dir: [number, number]; angle_deg: number };
export type BooleanParams = { op: BooleanOp; target_feature_id: number; tool_feature_id: number };
export type ImportParams = { file_key: string; format: "STEP" | "IGES"; filename: string };
// machining features on one earlier body (FreeCAD PartDesign style); the body is consumed
export type EdgeSet = "VERTICAL" | "TOP" | "BOTTOM" | "ALL";
export type MachKind = "FILLET" | "CHAMFER" | "HOLE" | "PATTERN" | "MIRROR" | "SHELL";
type OnBody = { target_feature_id: number };
export type MachParams =
  | (OnBody & { radius: number; edges: EdgeSet })
  | (OnBody & { distance: number; edges: EdgeSet })
  | (OnBody & { positions: [number, number][]; diameter: number; depth: number | null })
  | (OnBody & { kind: "LINEAR" | "POLAR"; copies: number; step?: number[] | null; center?: number[] | null; angle_deg?: number })
  | (OnBody & { plane: "YZ" | "ZX" | "XY"; offset: number })
  | (OnBody & { thickness: number; open_face: "TOP" | "BOTTOM" });
type Measure = { volume_mm3: number; surface_area_mm2: number; bbox: BBox & { size: [number, number, number] } };
export type Part = Measure & { part_key: string; name: string; instance_count: number; color?: string | null }; // "#rrggbb" from the file
export type TreeNode = { name: string; kind: "PART"; part_key: string } | { name: string; kind: "ASSEMBLY"; children: TreeNode[] };
// FN-15: IMPORT features also carry the per-part breakdown
type Metrics = Measure & { parts?: Part[]; part_count?: number; instance_count?: number; assembly_tree?: TreeNode[]; warnings?: string[] };
export type Feature = {
  feature_id: number;
  seq: number;
  status: "OK" | "ERROR";
  error_code: string | null;
  metrics: Metrics | null;
  created_at: string;
  updated_at: string;
  visible: boolean; // false = consumed by a BOOLEAN
  inputs: number[];
} & (
  | { feature_type: "EXTRUDE"; params: ExtrudeParams }
  | { feature_type: "REVOLVE"; params: RevolveParams }
  | { feature_type: "BOOLEAN"; params: BooleanParams }
  | { feature_type: "IMPORT"; params: ImportParams }
  | { feature_type: MachKind; params: MachParams }
);

export const MACH_LABEL: Record<MachKind, string> = { FILLET: "필렛", CHAMFER: "모따기", HOLE: "구멍", PATTERN: "패턴", MIRROR: "대칭", SHELL: "쉘" };
export const isMach = (f: Feature): f is Feature & { feature_type: MachKind; params: MachParams } => f.feature_type in MACH_LABEL;

export const OP_SYMBOL: Record<BooleanOp, string> = { FUSE: "+", CUT: "−", COMMON: "∩" };
const cap = (s: string) => s[0] + s.slice(1).toLowerCase();
export const featureName = (f: Feature) => `${cap(f.feature_type === "BOOLEAN" ? (f.params as BooleanParams).op : f.feature_type)}${String(f.seq).padStart(3, "0")}`;

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
  const mine = own?.feature_type === "BOOLEAN" ? [own.params.target_feature_id, own.params.tool_feature_id] : own && isMach(own) ? [own.params.target_feature_id] : [];
  return features.filter((f) => f.status === "OK" && f !== own && (f.visible || mine.includes(f.feature_id)) && (!own || f.seq < own.seq));
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
  openFace: "TOP" | "BOTTOM";
};

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
      return positive ? { target_feature_id, radius: size, edges: d.edges } : "반지름은 0보다 커야 합니다.";
    case "CHAMFER":
      return positive ? { target_feature_id, distance: size, edges: d.edges } : "거리는 0보다 커야 합니다.";
    case "HOLE": {
      if (!positive) return "직경은 0보다 커야 합니다.";
      const parts = d.positions.split(/[;\n]/).map((t) => t.trim()).filter(Boolean);
      const positions = parts.map((t) => nums(t, 2));
      if (!positions.length || positions.some((q) => !q)) return '구멍 위치를 "x,y; x,y" 형식으로 입력하세요.';
      if (positions.length > 500) return "구멍은 500개까지입니다.";
      const depth = d.depth.trim() === "" ? null : fin(d.depth);
      if (depth !== null && !(depth > 0)) return "깊이는 0보다 커야 합니다 (비우면 관통).";
      return { target_feature_id, positions: positions as [number, number][], diameter: size, depth };
    }
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
    case "SHELL":
      return positive ? { target_feature_id, thickness: size, open_face: d.openFace } : "두께는 0보다 커야 합니다.";
  }
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
  };
}

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
};

/** feature_id of the dependent BOOLEAN whose regeneration failed (whole PATCH rolled back). */
export function failedDependent(details: unknown): number | null {
  const id = (details as { feature_id?: unknown } | null | undefined)?.feature_id;
  return typeof id === "number" ? id : null;
}
