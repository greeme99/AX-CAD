// Backlog B-4: drawing clean-up suggestions (like AutoCAD OVERKILL, but never automatic).
// Finds broken endpoints (ends a hair apart) and overlapping / duplicated straight lines on the
// visible, unlocked, editable entities. Nothing changes until the user picks suggestions and
// applies them; the result is an ordinary edit (undo, then "저장" makes a new revision).
import type { Geom } from "./geom";
import type { Result } from "./tools";
import type { Pt } from "./view";

/** An editable entity; style = what must match before two lines merge (colour, linetype...). */
export type CleanEnt = { handle: string; layer: string; geom: Geom; style?: string };
type End = "start" | "end";
type Move = { handle: string; end: End; to: Pt };
export type Suggestion = {
  id: string;
  kind: "GAP" | "OVERLAP" | "DUPLICATE";
  handles: string[];
  summary: string;
  at: Pt; // where to look
  moves?: Move[]; // GAP: endpoints to bring together
  keep?: { handle: string; start: Pt; end: Pt }; // OVERLAP / DUPLICATE: the line that stays...
  drop?: string[]; // ...and the ones it replaces
};

export const TOL_DEFAULT = 0.1; // mm
export const TOL_MAX = 10;
const MAX_LINES = 50_000; // larger drawings: the first ones only (the dialog says so)
const MAX_SUGGESTIONS = 500;
const MAX_CLUSTER = 8;
const MAX_CELL = 300; // lines through one grid cell compared pair-wise
const SAMPLE_BUDGET = 400_000; // grid steps along all lines together (overlap search) // more ends than this near one point is a node, not a slip
const ZERO = 1e-9;

const d2 = (a: Pt, b: Pt) => Math.hypot(a[0] - b[0], a[1] - b[1]);
const fmt = (v: number) => (Math.round(v * 1000) / 1000).toString();

type EndPt = { handle: string; end: End; p: Pt; movable: boolean; ent: number };

function ends(e: CleanEnt): Omit<EndPt, "ent">[] {
  const g = e.geom;
  if (g.type === "LINE") return [{ handle: e.handle, end: "start", p: g.start, movable: true }, { handle: e.handle, end: "end", p: g.end, movable: true }];
  if (g.type === "LWPOLYLINE" && !g.closed && g.points.length >= 2) {
    const a = g.points[0], b = g.points[g.points.length - 1];
    return [{ handle: e.handle, end: "start", p: [a[0], a[1]], movable: true }, { handle: e.handle, end: "end", p: [b[0], b[1]], movable: true }];
  }
  if (g.type === "ARC") {
    // anchors only: moving an arc's end would change its radius or centre
    const r = (deg: number): Pt => [g.center[0] + g.radius * Math.cos((deg * Math.PI) / 180), g.center[1] + g.radius * Math.sin((deg * Math.PI) / 180)];
    return [{ handle: e.handle, end: "start", p: r(g.start_angle), movable: false }, { handle: e.handle, end: "end", p: r(g.end_angle), movable: false }];
  }
  return [];
}

/** Ends of different entities closer than tol but not touching: one GAP suggestion per cluster. */
function gaps(list: CleanEnt[], tol: number): Suggestion[] {
  const pts: EndPt[] = list.flatMap((e, ent) => ends(e).map((x) => ({ ...x, ent })));
  const cell = (v: number) => Math.floor(v / tol);
  const grid = new Map<string, number[]>();
  pts.forEach((x, i) => {
    const k = `${cell(x.p[0])},${cell(x.p[1])}`;
    (grid.get(k) ?? grid.set(k, []).get(k)!).push(i);
  });
  const parent = pts.map((_, i) => i);
  const root = (i: number): number => (parent[i] === i ? i : (parent[i] = root(parent[i])));
  pts.forEach((x, i) => {
    const cx = cell(x.p[0]), cy = cell(x.p[1]);
    for (let dx = -1; dx <= 1; dx++)
      for (let dy = -1; dy <= 1; dy++)
        for (const j of grid.get(`${cx + dx},${cy + dy}`) ?? []) if (j > i && d2(x.p, pts[j].p) <= tol) parent[root(j)] = root(i);
  });
  const groups = new Map<number, EndPt[]>();
  pts.forEach((x, i) => (groups.get(root(i)) ?? groups.set(root(i), []).get(root(i))!).push(x));
  const out: Suggestion[] = [];
  for (const g of groups.values()) {
    if (g.length < 2 || g.length > MAX_CLUSTER || new Set(g.map((x) => x.ent)).size < 2) continue;
    const spread = Math.max(...g.map((x) => d2(x.p, g[0].p)));
    if (spread <= ZERO) continue; // already joined
    // the point everyone goes to: an arc end if there is one (it cannot move), else the place most ends already share
    const anchors = g.filter((x) => !x.movable);
    if (anchors.some((a) => d2(a.p, anchors[0].p) > ZERO)) continue; // two arcs a gap apart: nothing to move
    let to = anchors[0]?.p;
    if (!to) {
      const votes = new Map<string, { p: Pt; n: number }>();
      for (const x of g) {
        const k = `${x.p[0].toFixed(9)},${x.p[1].toFixed(9)}`;
        const v = votes.get(k) ?? votes.set(k, { p: x.p, n: 0 }).get(k)!;
        v.n++;
      }
      to = [...votes.values()].sort((a, b) => b.n - a.n)[0].p;
    }
    const moves = g.filter((x) => x.movable && d2(x.p, to) > ZERO).map((x) => ({ handle: x.handle, end: x.end, to }));
    // a line whose two ends would meet would vanish: leave that cluster alone
    if (moves.some((m) => collapses(list.find((e) => e.handle === m.handle)!.geom, m.end, to!))) continue;
    const handles = [...new Set(g.map((x) => x.handle))];
    out.push({ id: `gap:${handles.join(",")}:${to.join(",")}`, kind: "GAP", handles, at: to, moves, summary: `${handles.length}개 객체의 끝점 간격 최대 ${fmt(spread)} mm → 한 점으로` });
  }
  return out;
}

function collapses(g: Geom, end: End, to: Pt): boolean {
  if (g.type !== "LINE") return false;
  return d2(end === "start" ? g.end : g.start, to) <= ZERO;
}

/** Straight lines (same layer and style) lying on one another: one OVERLAP suggestion per chain. */
function overlaps(list: CleanEnt[], tol: number): { items: Suggestion[]; crowded: boolean } {
  type L = { e: CleanEnt; a: Pt; b: Pt; len: number };
  const lines: L[] = [];
  for (const e of list) {
    if (e.geom.type !== "LINE") continue;
    const { start: a, end: b } = e.geom;
    const len = d2(a, b);
    if (len > tol) lines.push({ e, a, b, len });
  }
  // candidates pass through a shared grid cell (same layer and style): overlapping lines always do.
  // The cell grows with the drawing so the walk stays near SAMPLE_BUDGET steps in all.
  const total = lines.reduce((s, l) => s + l.len, 0);
  const size = Math.max(tol * 2, (2 * total) / SAMPLE_BUDGET);
  const cells = new Map<string, number[]>();
  lines.forEach((l, i) => {
    const seen = new Set<string>();
    const n = Math.ceil(l.len / (size / 2));
    for (let k = 0; k <= n; k++) {
      const x = l.a[0] + ((l.b[0] - l.a[0]) * k) / n, y = l.a[1] + ((l.b[1] - l.a[1]) * k) / n;
      const key = `${l.e.layer}\u0000${l.e.style ?? ""}\u0000${Math.floor(x / size)},${Math.floor(y / size)}`;
      if (seen.has(key)) continue;
      seen.add(key);
      (cells.get(key) ?? cells.set(key, []).get(key)!).push(i);
    }
  });
  const offLine = (p: Pt, l: L) => Math.abs((l.b[0] - l.a[0]) * (p[1] - l.a[1]) - (l.b[1] - l.a[1]) * (p[0] - l.a[0])) / l.len;
  const along = (p: Pt, l: L) => ((p[0] - l.a[0]) * (l.b[0] - l.a[0]) + (p[1] - l.a[1]) * (l.b[1] - l.a[1])) / l.len;
  const parent = lines.map((_, i) => i);
  const root = (i: number): number => (parent[i] === i ? i : (parent[i] = root(parent[i])));
  const tried = new Set<string>();
  let crowded = false;
  for (const ids of cells.values()) {
    if (ids.length > MAX_CELL) {
      crowded = true; // hundreds of lines through one spot (a hatch-like fan): not looked at
      continue;
    }
    for (let x = 0; x < ids.length; x++)
      for (let y = x + 1; y < ids.length; y++) {
        const [i, j] = [ids[x], ids[y]];
        const pair = `${i},${j}`;
        if (tried.has(pair)) continue;
        tried.add(pair);
        const l = lines[i], m = lines[j];
        if (offLine(m.a, l) > tol || offLine(m.b, l) > tol) continue;
        const [s0, t0] = [along(m.a, l), along(m.b, l)].sort((p, q) => p - q);
        if (Math.min(t0, l.len) - Math.max(s0, 0) > tol) parent[root(j)] = root(i); // they share more than a touch
      }
  }
  const groups = new Map<number, L[]>();
  lines.forEach((l, i) => (groups.get(root(i)) ?? groups.set(root(i), []).get(root(i))!).push(l));
  const out: Suggestion[] = [];
  for (const g of groups.values()) {
    if (g.length < 2) continue;
    const k = g.reduce((x, y) => (y.len > x.len ? y : x)); // the longest line stays (its handle, XDATA...)
    if (g.some((m) => offLine(m.a, k) > tol || offLine(m.b, k) > tol)) continue; // drifts along a chain: not one line
    const ts = g.flatMap((m) => [along(m.a, k), along(m.b, k)]);
    const t0 = Math.min(...ts), t1 = Math.max(...ts);
    const u: Pt = [(k.b[0] - k.a[0]) / k.len, (k.b[1] - k.a[1]) / k.len];
    const at = (t: number): Pt => [k.a[0] + u[0] * t, k.a[1] + u[1] * t];
    const same = g.every((m) => (d2(m.a, k.a) <= tol && d2(m.b, k.b) <= tol) || (d2(m.a, k.b) <= tol && d2(m.b, k.a) <= tol));
    const handles = g.map((m) => m.e.handle);
    out.push({
      id: `ovl:${handles.join(",")}`,
      kind: same ? "DUPLICATE" : "OVERLAP",
      handles,
      at: at((t0 + t1) / 2),
      keep: { handle: k.e.handle, start: at(t0), end: at(t1) },
      drop: handles.filter((h) => h !== k.e.handle),
      summary: same ? `같은 선 ${g.length}개 → 1개` : `겹친 선 ${g.length}개 → 1개로 병합 (길이 ${fmt(t1 - t0)} mm)`,
    });
  }
  return { items: out, crowded };
}

/** Suggestions for the given entities; `capped` = the drawing had more lines than were looked at. */
export function suggestCleanup(ents: CleanEnt[], tol = TOL_DEFAULT): { items: Suggestion[]; capped: boolean } {
  if (!(tol > 0 && tol <= TOL_MAX)) throw new Error(`허용 간격은 0보다 크고 ${TOL_MAX} mm 이하여야 합니다`);
  const list = ents.filter((e) => e.geom.type === "LINE" || e.geom.type === "ARC" || e.geom.type === "LWPOLYLINE");
  const capped = list.length > MAX_LINES;
  const look = capped ? list.slice(0, MAX_LINES) : list;
  const ovl = overlaps(look, tol);
  const items = [...ovl.items, ...gaps(look, tol)];
  return { items: items.slice(0, MAX_SUGGESTIONS), capped: capped || ovl.crowded || items.length > MAX_SUGGESTIONS };
}

/** The picked suggestions as one edit on the current entities. Overlaps go first; a gap on a line
 * an overlap already merged or removed is skipped (run the check again to see what is left). */
export function applyCleanup(ents: CleanEnt[], picks: Suggestion[]): { result: Result; applied: number; skipped: number } {
  const geoms = new Map(ents.map((e) => [e.handle, e.geom]));
  const changed = new Map<string, Geom>();
  const merged = new Set<string>(); // kept or removed by an overlap
  const dropped: string[] = [];
  let applied = 0, skipped = 0;
  const order = [...picks].sort((a, b) => Number(a.kind === "GAP") - Number(b.kind === "GAP"));
  for (const s of order) {
    if (s.handles.some((h) => merged.has(h) || !geoms.has(h))) {
      skipped++;
      continue;
    }
    if (s.keep) {
      changed.set(s.keep.handle, { type: "LINE", start: s.keep.start, end: s.keep.end });
      for (const h of s.handles) merged.add(h);
      dropped.push(...(s.drop ?? []));
    } else {
      for (const m of s.moves ?? []) {
        const g = changed.get(m.handle) ?? geoms.get(m.handle)!;
        changed.set(m.handle, moveEnd(g, m.end, m.to));
      }
    }
    applied++;
  }
  const result: Result = { updated: [...changed].map(([handle, geom]) => ({ handle, geom })), deleted: dropped };
  return { result, applied, skipped };
}

function moveEnd(g: Geom, end: End, to: Pt): Geom {
  if (g.type === "LINE") return end === "start" ? { ...g, start: to } : { ...g, end: to };
  if (g.type === "LWPOLYLINE") {
    const pts = g.points.map((p) => [...p] as [number, number, number]);
    const i = end === "start" ? 0 : pts.length - 1;
    pts[i] = [to[0], to[1], pts[i][2]];
    return { ...g, points: pts };
  }
  return g;
}
