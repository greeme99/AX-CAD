// Pure mesh/camera helpers for the 3D viewport. No Three.js here (cad-geometry rule: rendering vs geometry).
export type Vec3 = [number, number, number];
export type BBox = { min: Vec3; max: Vec3 };
export type MeshFace = { face_index: number; positions: number[]; normals: number[]; indices: number[] };
export type MeshJson = { faces: MeshFace[]; triangle_count: number; bbox: BBox };
export type Buffers = { positions: Float32Array; normals: Float32Array; indices: Uint32Array };

/** One merged buffer set for a feature. Malformed faces (bad lengths, index out of range) are skipped and counted. */
export function meshBuffers(faces: MeshFace[]): Buffers & { skipped: number } {
  const ok = faces.filter((f) => {
    const n = f.positions.length;
    return n % 3 === 0 && f.normals.length === n && f.indices.length % 3 === 0 && f.indices.every((i) => Number.isInteger(i) && i >= 0 && i < n / 3);
  });
  const positions = new Float32Array(ok.reduce((s, f) => s + f.positions.length, 0));
  const normals = new Float32Array(positions.length);
  const indices = new Uint32Array(ok.reduce((s, f) => s + f.indices.length, 0));
  let v = 0; // float offset
  let t = 0; // index offset
  for (const f of ok) {
    positions.set(f.positions, v);
    normals.set(f.normals, v);
    for (const i of f.indices) indices[t++] = i + v / 3;
    v += f.positions.length;
  }
  return { positions, normals, indices, skipped: faces.length - ok.length };
}

export function unionBBox(boxes: BBox[]): BBox | null {
  if (!boxes.length) return null;
  const min: Vec3 = [Infinity, Infinity, Infinity];
  const max: Vec3 = [-Infinity, -Infinity, -Infinity];
  for (const b of boxes)
    for (let k = 0; k < 3; k++) {
      min[k] = Math.min(min[k], b.min[k]);
      max[k] = Math.max(max[k], b.max[k]);
    }
  return { min, max };
}

/** Distance from the bbox center so its bounding sphere fits the narrower of the vertical/horizontal FOV. */
export function fitDistance(b: BBox, fovDeg: number, aspect = 1): number {
  const r = Math.max(Math.hypot(b.max[0] - b.min[0], b.max[1] - b.min[1], b.max[2] - b.min[2]) / 2, 1e-6);
  const half = (fovDeg * Math.PI) / 360;
  const halfMin = Math.min(half, Math.atan(Math.tan(half) * aspect));
  return r / Math.sin(halfMin);
}

const unit = (v: Vec3): Vec3 => {
  const l = Math.hypot(...v);
  return [v[0] / l, v[1] / l, v[2] / l];
};
// Directions from the target toward the camera, Z up. Front camera sits at -Y looking +Y.
// top is tilted by a hair so the camera never looks exactly along its up vector (lookAt degenerates).
export const VIEW_DIRS = {
  front: unit([0, -1, 0]),
  top: unit([0, -1e-3, 1]),
  right: unit([1, 0, 0]),
  iso: unit([1, -1, 1]),
} as const;
export type ViewName = keyof typeof VIEW_DIRS;
