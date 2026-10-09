import { at, type Geom } from "./geom";
import type { Pt } from "./view";

export type SnapKind = "END" | "MID" | "CEN" | "GRID";
export type SnapPt = { x: number; y: number; kind: Exclude<SnapKind, "GRID">; handle: string };
export type SnapHit = { x: number; y: number; kind: SnapKind; handle?: string };
export type SnapEntity = { handle: string; layer: string; geom?: Geom; paths: Pt[][] };
/** Grid buckets keyed by integer cell. Pick cellSize once per drawing (extents diagonal / 200): ~1-4 cells per 12 px query at fit zoom. */
export type SnapIndex = { cell: number; cells: Map<string, SnapPt[]> };

const PRIORITY: Record<SnapKind, number> = { END: 0, CEN: 1, MID: 2, GRID: 3 };
// ponytail: query radius capped at 16 cells, so at extreme zoom-out the effective snap radius shrinks; scale cell size with zoom if users notice
const MAX_CELLS = 16;
const key = (ix: number, iy: number) => `${ix},${iy}`;
const mid = (a: Pt, b: Pt): Pt => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];

function geomPoints(g: Geom): [Pt, SnapPt["kind"]][] {
  switch (g.type) {
    case "LINE":
      return [[g.start, "END"], [g.end, "END"], [mid(g.start, g.end), "MID"]];
    case "CIRCLE":
      return [[g.center, "CEN"]];
    case "ARC": {
      const sweep = (((g.end_angle - g.start_angle) % 360) + 360) % 360 || 360;
      return [
        [at(g.center, g.radius, g.start_angle), "END"],
        [at(g.center, g.radius, g.end_angle), "END"],
        [at(g.center, g.radius, g.start_angle + sweep / 2), "MID"],
        [g.center, "CEN"],
      ];
    }
    case "LWPOLYLINE": {
      const v = g.points;
      const out: [Pt, SnapPt["kind"]][] = v.map((p) => [[p[0], p[1]], "END"]);
      const segs = g.closed ? v.length : v.length - 1;
      // ponytail: bulged segments get no MID, add the arc midpoint if arc-segment midpoints are needed
      for (let i = 0; i < segs; i++)
        if (Math.abs(v[i][2]) < 1e-12) out.push([mid([v[i][0], v[i][1]], [v[(i + 1) % v.length][0], v[(i + 1) % v.length][1]]), "MID"]);
      return out;
    }
    case "TEXT":
      return [[g.insert, "END"]];
    case "DIM_LINEAR":
      return [[g.p1, "END"], [g.p2, "END"], [g.base, "END"]];
    case "DIM_ALIGNED":
      return [[g.p1, "END"], [g.p2, "END"]];
    case "DIM_ANGULAR":
      return [[g.center, "END"], [g.p1, "END"], [g.p2, "END"], [g.base, "END"]];
    case "DIM_RADIUS":
      return [[g.center, "END"]];
  }
}

/** Hidden layers are filtered at build time (callers rebuild when visibility changes). */
export function buildSnapIndex(entities: SnapEntity[], cellSize: number, hidden?: Set<string>): SnapIndex {
  const index: SnapIndex = { cell: cellSize, cells: new Map() };
  const add = (p: Pt, kind: SnapPt["kind"], handle: string) => {
    const k = key(Math.floor(p[0] / cellSize), Math.floor(p[1] / cellSize));
    const b = index.cells.get(k);
    if (b) b.push({ x: p[0], y: p[1], kind, handle });
    else index.cells.set(k, [{ x: p[0], y: p[1], kind, handle }]);
  };
  for (const e of entities) {
    if (hidden?.has(e.layer)) continue;
    if (e.geom) for (const [p, kind] of geomPoints(e.geom)) add(p, kind, e.handle);
    else
      for (const path of e.paths) {
        // ponytail: read-only entities snap at path first/last only, add vertices if users need them
        if (path.length) add(path[0], "END", e.handle);
        if (path.length > 1) add(path[path.length - 1], "END", e.handle);
      }
  }
  return index;
}

/** Best candidate within radiusWorld: nearest wins, ties by END > CEN > MID > GRID. */
export function querySnap(index: SnapIndex, p: Pt, radiusWorld: number, enabled: Set<SnapKind>, gridStep: number): SnapHit | null {
  const { cell, cells } = index;
  const r = Math.min(radiusWorld, MAX_CELLS * cell);
  let best: SnapHit | null = null;
  let bd = Infinity;
  const consider = (h: SnapHit) => {
    const d = Math.hypot(h.x - p[0], h.y - p[1]);
    if (d > r) return;
    if (d < bd - 1e-12 || (Math.abs(d - bd) <= 1e-12 && best && PRIORITY[h.kind] < PRIORITY[best.kind])) [best, bd] = [h, d];
  };
  const [x0, x1] = [Math.floor((p[0] - r) / cell), Math.floor((p[0] + r) / cell)];
  const [y0, y1] = [Math.floor((p[1] - r) / cell), Math.floor((p[1] + r) / cell)];
  for (let ix = x0; ix <= x1; ix++)
    for (let iy = y0; iy <= y1; iy++)
      for (const s of cells.get(key(ix, iy)) ?? []) if (enabled.has(s.kind)) consider(s);
  if (enabled.has("GRID") && gridStep > 0)
    consider({ x: Math.round(p[0] / gridStep) * gridStep, y: Math.round(p[1] / gridStep) * gridStep, kind: "GRID" });
  return best;
}

/** Grid step that keeps on-screen spacing >= 8 px (jumps x10). */
export function effectiveGridStep(step: number, scale: number): number {
  let s = step;
  if (!(s > 0) || !(scale > 0)) return step;
  while (s * scale < 8) s *= 10;
  return s;
}
