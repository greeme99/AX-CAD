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
type Measure = { volume_mm3: number; surface_area_mm2: number; bbox: BBox & { size: [number, number, number] } };
export type Part = Measure & { part_key: string; name: string; instance_count: number };
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
} & ({ feature_type: "EXTRUDE"; params: ExtrudeParams } | { feature_type: "REVOLVE"; params: RevolveParams } | { feature_type: "BOOLEAN"; params: BooleanParams } | { feature_type: "IMPORT"; params: ImportParams });

export const OP_SYMBOL: Record<BooleanOp, string> = { FUSE: "+", CUT: "−", COMMON: "∩" };
const cap = (s: string) => s[0] + s.slice(1).toLowerCase();
export const featureName = (f: Feature) => `${cap(f.feature_type === "BOOLEAN" ? f.params.op : f.feature_type)}${String(f.seq).padStart(3, "0")}`;

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
  const mine = own?.feature_type === "BOOLEAN" ? [own.params.target_feature_id, own.params.tool_feature_id] : [];
  return features.filter((f) => f.status === "OK" && f !== own && (f.visible || mine.includes(f.feature_id)) && (!own || f.seq < own.seq));
}

/** feature_id of the dependent BOOLEAN whose regeneration failed (whole PATCH rolled back). */
export function failedDependent(details: unknown): number | null {
  const id = (details as { feature_id?: unknown } | null | undefined)?.feature_id;
  return typeof id === "number" ? id : null;
}
