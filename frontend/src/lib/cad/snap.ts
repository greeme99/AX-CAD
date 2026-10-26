import { closest, intersect, perpFoot, samples, toPrims, type Prim } from "./curves";
import { at, ellipsePoint, type Geom } from "./geom";
import type { Pt } from "./view";

// END/MID/CEN/QUA are fixed points of an entity; INT (intersection), PERP (perpendicular from the
// last pick), ORTHO (horizontal/vertical from the last pick) and NEA (nearest on a curve) are found
// per query from the curves near the cursor (FreeCAD Draft snapper set)
export type SnapKind = "END" | "MID" | "CEN" | "QUA" | "INT" | "PERP" | "ORTHO" | "NEA" | "GRID";
type PointKind = "END" | "MID" | "CEN" | "QUA";
export type SnapPt = { x: number; y: number; kind: PointKind; handle: string };
export type SnapHit = { x: number; y: number; kind: SnapKind; handle?: string };
export type SnapEntity = { handle: string; layer: string; geom?: Geom; paths: Pt[][] };
/** Grid buckets keyed by integer cell. Pick cellSize once per drawing (extents diagonal / 200): ~1-4 cells per 12 px query at fit zoom.
 * Curves live in coarser buckets (`pcell`) by sampled points along them. */
export type SnapIndex = { cell: number; cells: Map<string, SnapPt[]>; prims: Prim[]; pcell: number; pcells: Map<string, number[]> };

// nearest wins; at equal distance the lower number. NEA only when nothing else is in reach
const PRIORITY: Record<SnapKind, number> = { END: 0, INT: 1, CEN: 2, QUA: 3, MID: 4, PERP: 5, ORTHO: 6, GRID: 7, NEA: 8 };
const PRIM_CELLS = 4; // curve bucket = 4 point cells
const MAX_NEAR = 64; // curves examined per query (pairs for INT grow with the square)
const MAX_SCAN = 2000; // bucket entries looked at per query, so one crowded cell cannot stall the mouse
// ponytail: query radius capped at 16 cells, so at extreme zoom-out the effective snap radius shrinks; scale cell size with zoom if users notice
const MAX_CELLS = 16;
const key = (ix: number, iy: number) => `${ix},${iy}`;
const mid = (a: Pt, b: Pt): Pt => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];

const QUADS = [0, 90, 180, 270];

function geomPoints(g: Geom): [Pt, SnapPt["kind"]][] {
  switch (g.type) {
    case "LINE":
      return [[g.start, "END"], [g.end, "END"], [mid(g.start, g.end), "MID"]];
    case "CIRCLE":
      return [[g.center, "CEN"], ...QUADS.map((a): [Pt, "QUA"] => [at(g.center, g.radius, a), "QUA"])];
    case "ARC": {
      const sweep = (((g.end_angle - g.start_angle) % 360) + 360) % 360 || 360;
      const quads = QUADS.filter((a) => (((a - g.start_angle) % 360) + 360) % 360 <= sweep);
      return [
        [at(g.center, g.radius, g.start_angle), "END"],
        [at(g.center, g.radius, g.end_angle), "END"],
        [at(g.center, g.radius, g.start_angle + sweep / 2), "MID"],
        [g.center, "CEN"],
        ...quads.map((a): [Pt, "QUA"] => [at(g.center, g.radius, a), "QUA"]),
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
    case "HATCH":
      return geomPoints({ type: "LWPOLYLINE", points: g.boundary, closed: true });
    case "INSERT":
      return [[g.insert, "END"]];
    case "POINT":
      return [[g.point, "END"]];
    case "ELLIPSE":
      return [[g.center, "CEN"], ...[0, Math.PI / 2, Math.PI, (3 * Math.PI) / 2].map((t): [Pt, "QUA"] => [ellipsePoint(g, t), "QUA"])];
    case "SPLINE":
      return g.fit.map((p): [Pt, "END"] => [p, "END"]);
    case "LEADER":
    case "MULTILEADER":
      return g.vertices.map((p): [Pt, "END"] => [p, "END"]);
    case "DIM_LINEAR":
      return [[g.p1, "END"], [g.p2, "END"], [g.base, "END"]];
    case "DIM_ALIGNED":
      return [[g.p1, "END"], [g.p2, "END"]];
    case "DIM_ANGULAR":
      return [[g.center, "END"], [g.p1, "END"], [g.p2, "END"], [g.base, "END"]];
    case "DIM_RADIUS":
    case "DIM_DIAMETER":
      return [[g.center, "END"]];
  }
}

/** Hidden layers are filtered at build time (callers rebuild when visibility changes). */
export function buildSnapIndex(entities: SnapEntity[], cellSize: number, hidden?: Set<string>): SnapIndex {
  const pcell = cellSize * PRIM_CELLS;
  const index: SnapIndex = { cell: cellSize, cells: new Map(), prims: [], pcell, pcells: new Map() };
  const addPrim = (pr: Prim) => {
    const id = index.prims.push(pr) - 1;
    const seen = new Set<string>();
    for (const q of samples(pr, pcell / 2)) {
      const k = key(Math.floor(q[0] / pcell), Math.floor(q[1] / pcell));
      if (seen.has(k)) continue;
      seen.add(k);
      const b = index.pcells.get(k);
      if (b) b.push(id);
      else index.pcells.set(k, [id]);
    }
  };
  const add = (p: Pt, kind: SnapPt["kind"], handle: string) => {
    const k = key(Math.floor(p[0] / cellSize), Math.floor(p[1] / cellSize));
    const b = index.cells.get(k);
    if (b) b.push({ x: p[0], y: p[1], kind, handle });
    else index.cells.set(k, [{ x: p[0], y: p[1], kind, handle }]);
  };
  for (const e of entities) {
    if (hidden?.has(e.layer)) continue;
    for (const pr of toPrims(e.handle, e.geom, e.paths)) addPrim(pr);
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

const CURVE_KINDS: SnapKind[] = ["INT", "PERP", "NEA"];

/** Curves passing within r of p, nearest first (at most MAX_NEAR). */
function nearPrims(index: SnapIndex, p: Pt, r: number): Prim[] {
  const { pcell, pcells, prims } = index;
  const ids = new Set<number>();
  const [x0, x1] = [Math.floor((p[0] - r) / pcell), Math.floor((p[0] + r) / pcell)];
  const [y0, y1] = [Math.floor((p[1] - r) / pcell), Math.floor((p[1] + r) / pcell)];
  scan: for (let ix = x0; ix <= x1; ix++)
    for (let iy = y0; iy <= y1; iy++)
      for (const id of pcells.get(key(ix, iy)) ?? []) {
        ids.add(id);
        if (ids.size >= MAX_SCAN) break scan;
      }
  const near: [number, Prim][] = [];
  for (const id of ids) {
    const q = closest(prims[id], p);
    const d = Math.hypot(q[0] - p[0], q[1] - p[1]);
    if (d <= r) near.push([d, prims[id]]);
  }
  return near.sort((a, b) => a[0] - b[0]).slice(0, MAX_NEAR).map(([, pr]) => pr);
}

/** Best candidate within radiusWorld: nearest wins, ties by PRIORITY. `from` = the command's last
 * pick, needed for PERP and ORTHO. NEA only applies when no other snap (except GRID) is in reach. */
export function querySnap(index: SnapIndex, p: Pt, radiusWorld: number, enabled: Set<SnapKind>, gridStep: number, from?: Pt): SnapHit | null {
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
  if (from && enabled.has("ORTHO")) {
    consider({ x: from[0], y: p[1], kind: "ORTHO" });
    consider({ x: p[0], y: from[1], kind: "ORTHO" });
  }
  let nearest: SnapHit | null = null;
  if (CURVE_KINDS.some((k) => enabled.has(k))) {
    const near = nearPrims(index, p, r);
    if (enabled.has("INT"))
      for (let i = 0; i < near.length; i++)
        for (let j = i + 1; j < near.length; j++)
          if (near[i].h !== near[j].h) for (const x of intersect(near[i], near[j])) consider({ x: x[0], y: x[1], kind: "INT", handle: near[i].h });
    if (from && enabled.has("PERP"))
      for (const pr of near) {
        const f = perpFoot(pr, from);
        if (f) consider({ x: f[0], y: f[1], kind: "PERP", handle: pr.h });
      }
    if (enabled.has("NEA") && near.length) {
      const q = closest(near[0], p);
      nearest = { x: q[0], y: q[1], kind: "NEA", handle: near[0].h };
    }
  }
  if (nearest && !best) return nearest; // on the curve beats a grid point
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
