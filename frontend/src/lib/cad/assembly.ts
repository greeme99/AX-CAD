// Track 9 Q2 + Assembly A1~A4: assembly client helpers (plan.md §15.3, §23.10).
// Placement = translation t (mm) and XYZ Euler angles r (deg), R = Rz·Ry·Rx — same as core/geometry/assembly.py.
import type { BBox, Sig } from "./mesh";

export type V3 = [number, number, number];
export type Placement = { t: V3; r: V3 };
export type Component = {
  component_id: number;
  name: string;
  source_document_id: number;
  source_doc_no: string | null;
  source_feature_id: number;
  body_feature_id: number | null;
  status: "OK" | "MISSING" | "BROKEN";
  bbox: BBox | null;
  placement: Placement;
  fixed: boolean;
  seq: number;
};

export type MateType =
  | "COINCIDENT"
  | "DISTANCE"
  | "PARALLEL"
  | "ANGLE"
  | "CONCENTRIC"
  | "PERPENDICULAR"
  | "TANGENT"
  | "LOCK"
  | "WIDTH"
  | "SYMMETRIC"
  | "DISTANCE_LIMIT"
  | "ANGLE_LIMIT";

export type Mate = {
  mate_id: number;
  mate_type: MateType;
  a: { component_id: number; face: Sig };
  b: { component_id: number; face: Sig };
  value: number | null;
  min_value?: number | null;
  max_value?: number | null;
  flip: boolean;
  suppressed?: boolean;
  status: "NEW" | "OK" | "FAILED" | "BROKEN";
  residual: number | null;
};

export type ExplodeStep = {
  component_ids: number[];
  direction: V3;
  distance_mm: number;
};

export type ExplodeView = {
  explode_id: number;
  name: string;
  steps: ExplodeStep[];
};

export type AssemblyMassSummary = {
  total_mass_kg: number;
  total_volume_mm3: number;
  center_of_gravity_mm: V3;
  inertia_kg_mm2: number[][];
  part_count: number;
};

export type AssemblyQuoteSummary = {
  rule_code: string;
  component_count: number;
  mate_count: number;
  fastener_count: number;
  standard_minutes: string;
  labor_cost_krw: string;
};

export type AssemblyState = {
  document_id: number;
  components: Component[];
  mates: Mate[];
  dof?: number | null;
  dof_by_component?: Record<string, number>;
  conflicting_mate_ids?: number[];
  explodes?: ExplodeView[];
};

export type Candidate = {
  document_id: number;
  doc_no: string;
  title: string;
  feature_id: number;
  name: string;
  bbox: BBox;
  volume_mm3: number;
};

export type Clash = { a: number; b: number; volume_mm3: number; center: V3 };
export type ContactPair = { a: number; b: number; distance_mm: number; kind?: "CONTACT" | "CLEARANCE" };
export type Interference = {
  clashes: Clash[];
  contacts?: ContactPair[];
  near: ContactPair[];
  pairs_checked: number;
  components: number;
  clearance: number;
  truncated?: boolean;
};

export const MATE_LABEL: Record<MateType, string> = {
  COINCIDENT: "일치 (면 맞닿음 / 축·점 일치)",
  DISTANCE: "거리 (평면·점 간격 mm)",
  PARALLEL: "평행",
  ANGLE: "각도 (deg)",
  CONCENTRIC: "동심 (원통·원뿔·구 축)",
  PERPENDICULAR: "직교 (90°)",
  TANGENT: "접선 (평면-원통/구, 원통-원통, 구-구)",
  LOCK: "잠금 (상대 6자유도 고정)",
  WIDTH: "너비 중앙 정렬",
  SYMMETRIC: "대칭 정렬",
  DISTANCE_LIMIT: "거리 범위 제한 (min~max mm)",
  ANGLE_LIMIT: "각도 범위 제한 (min~max deg)",
};

/** Row-major 3x3 rotation of XYZ Euler angles (deg), R = Rz·Ry·Rx. */
export function rotation(r: V3): number[][] {
  const [x, y, z] = r.map((a) => (a * Math.PI) / 180);
  const [cx, sx, cy, sy, cz, sz] = [Math.cos(x), Math.sin(x), Math.cos(y), Math.sin(y), Math.cos(z), Math.sin(z)];
  return [
    [cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx],
    [sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx],
    [-sy, cy * sx, cy * cx],
  ];
}

/** Column-major 4x4 (Three.js Matrix4.fromArray) of a placement. */
export function placementMatrix(p: Placement): number[] {
  const m = rotation(p.r);
  return [m[0][0], m[1][0], m[2][0], 0, m[0][1], m[1][1], m[2][1], 0, m[0][2], m[1][2], m[2][2], 0, p.t[0], p.t[1], p.t[2], 1];
}

/** Axis-aligned box of a box after a column-major transform (its 8 corners). */
export function transformBBox(b: BBox, m: number[]): BBox {
  const min: V3 = [Infinity, Infinity, Infinity];
  const max: V3 = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < 8; i++) {
    const p = [i & 1 ? b.max[0] : b.min[0], i & 2 ? b.max[1] : b.min[1], i & 4 ? b.max[2] : b.min[2]];
    for (let k = 0; k < 3; k++) {
      const v = m[k] * p[0] + m[4 + k] * p[1] + m[8 + k] * p[2] + m[12 + k];
      min[k] = Math.min(min[k], v);
      max[k] = Math.max(max[k], v);
    }
  }
  return { min, max };
}

/** Apply exploded-view steps scaled by factor in [0, 1] without mutating solver placement. */
export function applyExplodeSteps(
  p: Placement,
  componentId: number,
  steps: ExplodeStep[],
  factor: number,
): Placement {
  const f = Math.max(0, Math.min(1, factor));
  if (f <= 0 || !steps.length) return p;
  let [tx, ty, tz] = p.t;
  for (const st of steps) {
    if (!st.component_ids.includes(componentId)) continue;
    const [dx, dy, dz] = st.direction;
    const norm = Math.hypot(dx, dy, dz);
    if (norm <= 1e-12) continue;
    const d = st.distance_mm * f;
    tx += (dx / norm) * d;
    ty += (dy / norm) * d;
    tz += (dz / norm) * d;
  }
  return { t: [tx, ty, tz], r: p.r };
}

/** Which mates a pair of picked references allows (Assembly A1 12 mate types). */
export function matesFor(a: Sig | null, b: Sig | null): MateType[] {
  if (!a || !b) return [];
  const kinds = new Set([a.kind, b.kind]);
  if (a.kind === "PLANE" && b.kind === "PLANE") {
    return [
      "COINCIDENT",
      "DISTANCE",
      "PARALLEL",
      "PERPENDICULAR",
      "ANGLE",
      "LOCK",
      "WIDTH",
      "SYMMETRIC",
      "DISTANCE_LIMIT",
      "ANGLE_LIMIT",
    ];
  }
  const axial = new Set(["CYLINDER", "CONE", "LINE"]);
  if (axial.has(a.kind) && axial.has(b.kind)) {
    return ["CONCENTRIC", "COINCIDENT", "PARALLEL", "PERPENDICULAR", "ANGLE", "TANGENT", "LOCK"];
  }
  if (kinds.has("PLANE") && (kinds.has("CYLINDER") || kinds.has("SPHERE") || kinds.has("CONE"))) {
    return ["TANGENT", "PERPENDICULAR", "PARALLEL", "DISTANCE", "LOCK"];
  }
  if (a.kind === "SPHERE" && b.kind === "SPHERE") {
    return ["CONCENTRIC", "TANGENT", "DISTANCE", "LOCK"];
  }
  return ["COINCIDENT", "DISTANCE", "LOCK"];
}
