// Track 9 Q2: assembly client helpers (plan.md §15.3). Placement = translation t (mm) and XYZ
// Euler angles r (deg), R = Rz·Ry·Rx — the same convention as core/geometry/assembly.py.
import type { BBox, Sig } from "./mesh";

type V3 = [number, number, number];
export type Placement = { t: V3; r: V3 };
export type Component = {
  component_id: number;
  name: string;
  source_document_id: number;
  source_doc_no: string | null;
  source_feature_id: number;
  body_feature_id: number | null;
  status: "OK" | "MISSING";
  bbox: BBox | null;
  placement: Placement;
  fixed: boolean;
  seq: number;
};
export type MateType = "COINCIDENT" | "DISTANCE" | "PARALLEL" | "ANGLE" | "CONCENTRIC";
export type Mate = {
  mate_id: number;
  mate_type: MateType;
  a: { component_id: number; face: Sig };
  b: { component_id: number; face: Sig };
  value: number | null;
  flip: boolean;
  status: "NEW" | "OK" | "FAILED";
  residual: number | null;
};
export type AssemblyState = { document_id: number; components: Component[]; mates: Mate[]; dof?: number | null };
export type Candidate = { document_id: number; doc_no: string; title: string; feature_id: number; name: string; bbox: BBox; volume_mm3: number };
export type Clash = { a: number; b: number; volume_mm3: number; center: V3 };
export type Interference = { clashes: Clash[]; near: { a: number; b: number; distance_mm: number }[]; pairs_checked: number; components: number; clearance: number; truncated?: boolean };

export const MATE_LABEL: Record<MateType, string> = {
  COINCIDENT: "일치 (면 맞닿음 / 축 일치)",
  DISTANCE: "거리 (평면 간격 mm)",
  PARALLEL: "평행",
  ANGLE: "각도 (deg)",
  CONCENTRIC: "동심 (원통 축)",
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

/** Which mates a pair of picked faces allows (the server checks again). */
export function matesFor(a: Sig | null, b: Sig | null): MateType[] {
  if (!a || !b) return [];
  if (a.kind === "PLANE" && b.kind === "PLANE") return ["COINCIDENT", "DISTANCE", "PARALLEL", "ANGLE"];
  if (a.kind === "CYLINDER" && b.kind === "CYLINDER") return ["CONCENTRIC", "COINCIDENT", "PARALLEL"];
  return [];
}
