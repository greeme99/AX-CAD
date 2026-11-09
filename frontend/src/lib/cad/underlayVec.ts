// 2D underlay U4/U5: the page's vector cache (see core/underlay) in model coordinates — for
// snapping (UOSNAP) and for "import as objects". A scanned page has no vectors: it is shown but
// cannot be snapped to or imported. The page file is never changed by either.
import type { Geom } from "./geom";
import type { SnapEntity } from "./snap";
import { clipPolygon, inPolygon, toModel, toPage, visibleAt, type UnderlayGeom, rad } from "./underlay";
import type { Pt } from "./view";

export type VecPath = { p: number[]; z?: 1; f?: 1; k?: "c"; l?: number; c?: string };
export type VecArc = { c: [number, number]; r: number; a?: [number, number]; l?: number; co?: string };
export type VecText = { v: string; p: [number, number]; h: number; r?: number; l?: number };
export type PageVec = { size: [number, number]; layers: string[]; paths: VecPath[]; arcs: VecArc[]; texts: VecText[]; raster_only: boolean; truncated: boolean };

const pairs = (p: number[]): Pt[] => {
  const out: Pt[] = [];
  for (let i = 0; i + 1 < p.length; i += 2) out.push([p[i], p[i + 1]]);
  return out;
};
const shown = (g: UnderlayGeom, vec: PageVec, hidden: Set<number>, l?: number) => (l === undefined || l < 0 || !hidden.has(l)) && vec.size.length === 2;

/** Whether any of these page points is inside the page and the clip (a path partly in view is kept whole). */
const anyVisible = (g: UnderlayGeom, size: [number, number], pts: Pt[]) => pts.some((p) => visibleAt(g, size, p));

// --- snapping ----------------------------------------------------------------------------------

/** Snap candidates of one underlay page: polylines (END at every vertex, MID per segment), circles
 * and arcs (CEN, QUA, END, MID) and flattened curves (their two ends). Layers switched off and
 * everything clipped away are left out. Every entity carries `handle`. */
export function snapEntities(vec: PageVec, g: UnderlayGeom, handle: string, hidden: Set<number> = new Set()): SnapEntity[] {
  const out: SnapEntity[] = [];
  const m = (p: Pt) => toModel(g, p);
  vec.paths.forEach((pa, i) => {
    if (!shown(g, vec, hidden, pa.l)) return;
    const pts = pairs(pa.p);
    if (pts.length < 2 || !anyVisible(g, vec.size, pts)) return;
    const mp = pts.map(m);
    const id = `${handle}#p${i}`;
    if (pa.k === "c") return void out.push({ handle: id, layer: "", paths: [mp] }); // a flattened curve: ends + curve
    const closed = pa.z === 1;
    const geom: Geom = mp.length === 2 && !closed ? { type: "LINE", start: mp[0], end: mp[1] } : { type: "LWPOLYLINE", points: mp.map((q): [number, number, number] => [q[0], q[1], 0]), closed };
    out.push({ handle: id, layer: "", geom, paths: [closed ? [...mp, mp[0]] : mp] });
  });
  vec.arcs.forEach((a, i) => {
    if (!shown(g, vec, hidden, a.l)) return;
    const c = m(a.c);
    const r = a.r * g.scale;
    const probe: Pt[] = a.a ? [[a.c[0] + a.r * Math.cos(rad(a.a[0])), a.c[1] + a.r * Math.sin(rad(a.a[0]))], a.c] : [[a.c[0] + a.r, a.c[1]], a.c];
    if (!anyVisible(g, vec.size, probe)) return;
    const geom: Geom = a.a ? { type: "ARC", center: c, radius: r, start_angle: (a.a[0] + g.rotation + 360) % 360, end_angle: (a.a[1] + g.rotation + 360) % 360 } : { type: "CIRCLE", center: c, radius: r };
    out.push({ handle: `${handle}#a${i}`, layer: "", geom, paths: [] });
  });
  return out;
}

// --- import as objects -----------------------------------------------------------------------------

export type ImportOptions = {
  region: { min: Pt; max: Pt } | null; // model mm; null = the whole visible page
  crossing: boolean; // an object touching the region counts (false: wholly inside)
  hidden: Set<number>; // PDF layers switched off
  tol: number; // mm: ends closer than this are joined into one polyline
  text: boolean;
  fills: boolean; // filled shapes as closed outlines (no hatch)
};
export const IMPORT_LIMITS = { objects: 10_000, vertices: 100_000 };
export type Imported = { geom: Geom; layer?: string }[];
export type ImportResult = { items: Imported; counts: { lines: number; polylines: number; circles: number; arcs: number; texts: number; fills: number; joined: number }; skipped: number; error?: string };

const inRegion = (r: ImportOptions["region"], pts: Pt[], crossing: boolean) => {
  if (!r) return true;
  const inside = (p: Pt) => p[0] >= r.min[0] && p[0] <= r.max[0] && p[1] >= r.min[1] && p[1] <= r.max[1];
  return crossing ? pts.some(inside) || segmentsCross(r, pts) : pts.every(inside);
};
/** A circle's box against the region: touching it (crossing) or wholly inside it. */
function boxInRegion(r: ImportOptions["region"], lo: Pt, hi: Pt, crossing: boolean): boolean {
  if (!r) return true;
  if (crossing) return hi[0] >= r.min[0] && lo[0] <= r.max[0] && hi[1] >= r.min[1] && lo[1] <= r.max[1];
  return lo[0] >= r.min[0] && hi[0] <= r.max[0] && lo[1] >= r.min[1] && hi[1] <= r.max[1];
}
function segmentsCross(r: NonNullable<ImportOptions["region"]>, pts: Pt[]): boolean {
  // a segment may pass through the window with both ends outside
  const [x0, y0, x1, y1] = [r.min[0], r.min[1], r.max[0], r.max[1]];
  for (let i = 0; i + 1 < pts.length; i++) {
    const [a, b] = [pts[i], pts[i + 1]];
    if (Math.max(a[0], b[0]) < x0 || Math.min(a[0], b[0]) > x1 || Math.max(a[1], b[1]) < y0 || Math.min(a[1], b[1]) > y1) continue;
    const edges: [Pt, Pt][] = [[[x0, y0], [x1, y0]], [[x1, y0], [x1, y1]], [[x1, y1], [x0, y1]], [[x0, y1], [x0, y0]]];
    if (edges.some(([c, d]) => cross(a, b, c, d))) return true;
  }
  return false;
}
const cross = (a: Pt, b: Pt, c: Pt, d: Pt) => {
  const o = (p: Pt, q: Pt, s: Pt) => Math.sign((q[0] - p[0]) * (s[1] - p[1]) - (q[1] - p[1]) * (s[0] - p[0]));
  return o(a, b, c) !== o(a, b, d) && o(c, d, a) !== o(c, d, b);
};

/** Open polylines whose ends meet (within tol) become one polyline; a chain that comes back to its
 * start closes. Each end joins at most one other path (a branch stays separate). */
export function joinChains(paths: Pt[][], tol: number): { paths: Pt[][]; closed: boolean[]; joined: number } {
  const cell = Math.max(tol, 1e-9) * 2;
  const cellOf = (p: Pt): [number, number] => [Math.round(p[0] / cell), Math.round(p[1] / cell)];
  const ends = new Map<string, { i: number; head: boolean }[]>();
  const put = (pt: Pt, i: number, head: boolean) => {
    const k = cellOf(pt).join(",");
    const b = ends.get(k);
    if (b) b.push({ i, head });
    else ends.set(k, [{ i, head }]);
  };
  paths.forEach((p, i) => {
    put(p[0], i, true);
    put(p[p.length - 1], i, false);
  });
  const near = (a: Pt, b: Pt) => Math.hypot(a[0] - b[0], a[1] - b[1]) <= tol;
  const used = new Array<boolean>(paths.length).fill(false);
  let joined = 0;
  /** An unused path with an end at pt (the nearest cells are looked at: an end may sit on a cell border). */
  const partner = (pt: Pt): { i: number; head: boolean } | null => {
    const [cx, cy] = cellOf(pt);
    for (let dx = -1; dx <= 1; dx++)
      for (let dy = -1; dy <= 1; dy++)
        for (const e of ends.get(`${cx + dx},${cy + dy}`) ?? []) {
          if (used[e.i]) continue;
          const q = paths[e.i];
          if (near(pt, e.head ? q[0] : q[q.length - 1])) return e;
        }
    return null;
  };
  const extend = (chain: Pt[], atTail: boolean): boolean => {
    const e = partner(atTail ? chain[chain.length - 1] : chain[0]);
    if (!e) return false;
    used[e.i] = true;
    joined++;
    const q = paths[e.i];
    // the path turned so that its touching end meets the chain
    const oriented = atTail === e.head ? q : q.slice().reverse();
    if (atTail) for (let k = 1; k < oriented.length; k++) chain.push(oriented[k]);
    else chain.unshift(...oriented.slice(0, -1)); // a few points: the spread is safe at this size
    return true;
  };
  const out: Pt[][] = [];
  const closed: boolean[] = [];
  for (let s = 0; s < paths.length; s++) {
    if (used[s]) continue;
    used[s] = true;
    const chain = paths[s].slice();
    while (extend(chain, true));
    while (extend(chain, false));
    const isClosed = chain.length > 3 && near(chain[0], chain[chain.length - 1]);
    out.push(isClosed ? chain.slice(0, -1) : chain);
    closed.push(isClosed);
  }
  return { paths: out, closed, joined };
}

/** The page's vectors as drawing objects, ready to be created (the user previews the counts first). */
export function importObjects(vec: PageVec, g: UnderlayGeom, o: ImportOptions): ImportResult {
  const counts = { lines: 0, polylines: 0, circles: 0, arcs: 0, texts: 0, fills: 0, joined: 0 };
  const res: ImportResult = { items: [], counts, skipped: 0 };
  if (vec.raster_only) return { ...res, error: "스캔(래스터) 페이지는 객체로 바꿀 수 없습니다" };
  const m = (p: Pt) => toModel(g, p);
  const loose: Pt[][] = []; // open, unfilled polylines: candidates for joining
  const done: Imported = [];
  let vertices = 0;
  for (const pa of vec.paths) {
    if (pa.l !== undefined && pa.l >= 0 && o.hidden.has(pa.l)) continue;
    const pts = pairs(pa.p);
    if (pts.length < 2 || !anyVisible(g, vec.size, pts)) continue;
    const mp = pts.map(m);
    if (!inRegion(o.region, mp, o.crossing)) {
      res.skipped++;
      continue;
    }
    if (pa.f) {
      if (!o.fills) continue;
      counts.fills++;
      done.push({ geom: { type: "LWPOLYLINE", points: mp.map((q): [number, number, number] => [q[0], q[1], 0]), closed: true } });
      vertices += mp.length;
    } else if (pa.z) {
      counts.polylines++;
      done.push({ geom: { type: "LWPOLYLINE", points: mp.map((q): [number, number, number] => [q[0], q[1], 0]), closed: true } });
      vertices += mp.length;
    } else loose.push(mp);
  }
  const j = joinChains(loose, o.tol);
  counts.joined = j.joined;
  j.paths.forEach((p, i) => {
    if (p.length === 2 && !j.closed[i]) {
      counts.lines++;
      done.push({ geom: { type: "LINE", start: p[0], end: p[1] } });
    } else {
      counts.polylines++;
      done.push({ geom: { type: "LWPOLYLINE", points: p.map((q): [number, number, number] => [q[0], q[1], 0]), closed: j.closed[i] } });
    }
    vertices += p.length;
  });
  for (const a of vec.arcs) {
    if (a.l !== undefined && a.l >= 0 && o.hidden.has(a.l)) continue;
    const c = m(a.c);
    const r = a.r * g.scale;
    if (!anyVisible(g, vec.size, [a.c]) || !boxInRegion(o.region, [c[0] - r, c[1] - r], [c[0] + r, c[1] + r], o.crossing)) {
      res.skipped++;
      continue;
    }
    if (a.a) {
      counts.arcs++;
      done.push({ geom: { type: "ARC", center: c, radius: r, start_angle: (a.a[0] + g.rotation + 360) % 360, end_angle: (a.a[1] + g.rotation + 360) % 360 } });
    } else {
      counts.circles++;
      done.push({ geom: { type: "CIRCLE", center: c, radius: r } });
    }
  }
  if (o.text)
    for (const t of vec.texts) {
      if (t.l !== undefined && t.l >= 0 && o.hidden.has(t.l)) continue;
      const at = m(t.p);
      if (!visibleAt(g, vec.size, t.p) || !inRegion(o.region, [at], o.crossing)) continue;
      counts.texts++;
      done.push({ geom: { type: "TEXT", insert: at, height: t.h * g.scale, value: t.v, rotation: ((t.r ?? 0) + g.rotation + 360) % 360 } });
    }
  if (done.length > IMPORT_LIMITS.objects) return { ...res, error: `객체 ${done.length.toLocaleString()}개: 한 번에 ${IMPORT_LIMITS.objects.toLocaleString()}개까지입니다. 영역을 줄이세요` };
  if (vertices > IMPORT_LIMITS.vertices) return { ...res, error: `꼭짓점 ${vertices.toLocaleString()}개: ${IMPORT_LIMITS.vertices.toLocaleString()}개까지입니다. 영역을 줄이세요` };
  res.items = done;
  return res;
}

export { clipPolygon, inPolygon, toPage };
