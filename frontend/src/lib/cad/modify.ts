// Track 8 P1-2: further 2D modify operations, following the behaviour of FreeCAD Draft/Sketcher
// (trimex, join/split, upgrade/downgrade, stretch, fillet/chamfer; LGPL-2.1, studied only, the
// math here is our own). Pure functions on Geom (mm, degrees CCW). Every result is one of the
// entity types the server writer already round-trips (LINE, ARC, CIRCLE, LWPOLYLINE).
import { intersect, type Prim } from "./curves";
import type { Geom } from "./geom";
import type { Pt } from "./view";

type Line = Extract<Geom, { type: "LINE" }>;
type Poly = Extract<Geom, { type: "LWPOLYLINE" }>;
/** A path piece a→b; bulge = tan(sweep/4), + = counter-clockwise (DXF LWPOLYLINE convention). */
export type Seg = { a: Pt; b: Pt; bulge: number };

const TAU = 2 * Math.PI;
const RAD = Math.PI / 180;
const DEG = 180 / Math.PI;
const EPS = 1e-9;
const JOIN_TOL = 1e-4; // mm: end points closer than this count as touching
const sub = (a: Pt, b: Pt): Pt => [a[0] - b[0], a[1] - b[1]];
const add = (a: Pt, b: Pt): Pt => [a[0] + b[0], a[1] + b[1]];
const mul = (a: Pt, k: number): Pt => [a[0] * k, a[1] * k];
const dot = (a: Pt, b: Pt) => a[0] * b[0] + a[1] * b[1];
const cross = (a: Pt, b: Pt) => a[0] * b[1] - a[1] * b[0];
const len = (a: Pt) => Math.hypot(a[0], a[1]);
const unit = (a: Pt): Pt => mul(a, 1 / len(a));
const norm360 = (d: number) => ((d % 360) + 360) % 360;
const near = (a: Pt, b: Pt, tol = JOIN_TOL) => len(sub(a, b)) <= tol;
const straight = (s: Seg) => Math.abs(s.bulge) < 1e-12;

// --- segments ----------------------------------------------------------------------------------

/** Circle of a curved segment: centre, radius, start angle and signed sweep (radians). */
export function bulgeCircle(s: Seg): { c: Pt; r: number; a0: number; sweep: number } {
  const sweep = 4 * Math.atan(s.bulge);
  const d = len(sub(s.b, s.a));
  const off = ((d / 2) * (1 - s.bulge * s.bulge)) / (2 * s.bulge);
  const c = add(mul(add(s.a, s.b), 0.5), mul([-(s.b[1] - s.a[1]) / d, (s.b[0] - s.a[0]) / d], off));
  return { c, r: len(sub(s.a, c)), a0: Math.atan2(s.a[1] - c[1], s.a[0] - c[0]), sweep };
}

export function segPoint(s: Seg, u: number): Pt {
  if (u <= 0) return s.a;
  if (u >= 1) return s.b;
  if (straight(s)) return add(s.a, mul(sub(s.b, s.a), u));
  const k = bulgeCircle(s);
  const a = k.a0 + k.sweep * u;
  return [k.c[0] + k.r * Math.cos(a), k.c[1] + k.r * Math.sin(a)];
}

function subSeg(s: Seg, u0: number, u1: number): Seg {
  return { a: segPoint(s, u0), b: segPoint(s, u1), bulge: straight(s) ? 0 : Math.tan((4 * Math.atan(s.bulge) * (u1 - u0)) / 4) };
}

/** Fraction along s of its point nearest p, and the distance to it. */
function segParam(s: Seg, p: Pt): { u: number; d: number } {
  let u: number;
  if (straight(s)) {
    const v = sub(s.b, s.a);
    const L = dot(v, v);
    u = L < EPS ? 0 : Math.min(1, Math.max(0, dot(sub(p, s.a), v) / L));
  } else {
    const k = bulgeCircle(s);
    const full = Math.abs(k.sweep);
    const t = ((((Math.atan2(p[1] - k.c[1], p[0] - k.c[0]) - k.a0) * Math.sign(k.sweep)) % TAU) + TAU) % TAU;
    u = t <= full ? t / full : t - full < TAU - t ? 1 : 0; // outside the arc: nearest end
  }
  return { u, d: len(sub(p, segPoint(s, u))) };
}

function segPrim(s: Seg): Prim {
  if (straight(s)) return { kind: "SEG", a: s.a, b: s.b, h: "" };
  const k = bulgeCircle(s);
  return { kind: "ARC", c: k.c, r: k.r, a0: k.sweep > 0 ? k.a0 : k.a0 + k.sweep, sweep: Math.abs(k.sweep), h: "" };
}

const reverseSeg = (s: Seg): Seg => ({ a: s.b, b: s.a, bulge: -s.bulge });

/** LINE / ARC / open LWPOLYLINE as a chain of segments (null for closed shapes, text, dims). */
export function toSegs(g: Geom): Seg[] | null {
  if (g.type === "LINE") return [{ a: g.start, b: g.end, bulge: 0 }];
  if (g.type === "ARC") {
    const sweep = (norm360(g.end_angle - g.start_angle) || 360) * RAD;
    const at = (deg: number): Pt => [g.center[0] + g.radius * Math.cos(deg * RAD), g.center[1] + g.radius * Math.sin(deg * RAD)];
    return [{ a: at(g.start_angle), b: at(g.end_angle), bulge: Math.tan(sweep / 4) }];
  }
  if (g.type === "LWPOLYLINE") return polySegs(g);
  return null;
}

/** Segments of a polyline (closed ones include the closing segment). */
export function polySegs(g: Poly): Seg[] {
  const v = g.points;
  const n = g.closed ? v.length : v.length - 1;
  const out: Seg[] = [];
  for (let i = 0; i < n; i++) {
    const [p, q] = [v[i], v[(i + 1) % v.length]];
    out.push({ a: [p[0], p[1]], b: [q[0], q[1]], bulge: p[2] });
  }
  return out;
}

/** A segment chain back to geometry: one straight segment = LINE, one curved = ARC, else
 * LWPOLYLINE (`poly`: always a polyline, so pieces of a polyline stay polylines). */
export function fromSegs(segs: Seg[], closed = false, poly = false): Geom {
  if (!closed && !poly && segs.length === 1) {
    const s = segs[0];
    if (straight(s)) return { type: "LINE", start: s.a, end: s.b };
    const k = bulgeCircle(s);
    const [a, b] = [k.a0 * DEG, (k.a0 + k.sweep) * DEG];
    return { type: "ARC", center: k.c, radius: k.r, start_angle: norm360(k.sweep > 0 ? a : b), end_angle: norm360(k.sweep > 0 ? b : a) };
  }
  const points: [number, number, number][] = segs.map((s) => [s.a[0], s.a[1], s.bulge]);
  if (!closed) points.push([segs[segs.length - 1].b[0], segs[segs.length - 1].b[1], 0]);
  return { type: "LWPOLYLINE", points, closed };
}

// --- explode / join -----------------------------------------------------------------------------

/** Polyline -> its LINE and ARC pieces (FreeCAD Draft downgrade). */
export function explodeGeom(g: Geom): Geom[] | string {
  if (g.type !== "LWPOLYLINE") return "폴리라인만 분해할 수 있습니다";
  const segs = polySegs(g).filter((s) => !near(s.a, s.b, EPS));
  if (!segs.length) return "분해할 구간이 없습니다";
  return segs.map((s) => fromSegs([s]));
}

/** Lines, arcs and open polylines touching end to end -> one polyline (FreeCAD Draft join/upgrade). */
export function joinGeoms(gs: Geom[]): Geom | string {
  if (gs.length < 2) return "결합할 객체를 2개 이상 선택하세요";
  const pieces: Seg[][] = [];
  for (const g of gs) {
    if (g.type === "LWPOLYLINE" && g.closed) return "닫힌 폴리라인은 결합할 수 없습니다";
    const s = toSegs(g);
    if (!s) return "선·호·열린 폴리라인만 결합할 수 있습니다";
    pieces.push(s);
  }
  let chain = pieces.shift()!;
  while (pieces.length) {
    const [head, tail] = [chain[0].a, chain[chain.length - 1].b];
    const i = pieces.findIndex((p) => [p[0].a, p[p.length - 1].b].some((e) => near(e, head) || near(e, tail)));
    if (i < 0) return "끝점이 맞닿지 않는 객체가 있습니다";
    const p = pieces.splice(i, 1)[0];
    const rev = () => [...p].reverse().map(reverseSeg);
    if (near(p[0].a, tail)) chain = [...chain, ...p];
    else if (near(p[p.length - 1].b, tail)) chain = [...chain, ...rev()];
    else if (near(p[p.length - 1].b, head)) chain = [...p, ...chain];
    else chain = [...rev(), ...chain];
  }
  for (let i = 1; i < chain.length; i++) chain[i] = { ...chain[i], a: chain[i - 1].b }; // close tiny gaps
  const closed = chain.length > 2 && near(chain[chain.length - 1].b, chain[0].a);
  return fromSegs(chain, closed);
}

// --- polyline-aware trim / break / extend ------------------------------------------------------

/** Positions along a chain where the cutters cross it: segment index + fraction. */
function chainCuts(segs: Seg[], cutters: Prim[]): number[] {
  const out: number[] = [];
  segs.forEach((s, i) => {
    const me = segPrim(s);
    for (const c of cutters) for (const x of intersect(me, c)) out.push(i + segParam(s, x).u);
  });
  return out.sort((a, b) => a - b);
}

/** Position along the chain nearest p. */
function chainParam(segs: Seg[], p: Pt): number {
  let best = { s: 0, d: Infinity };
  segs.forEach((s, i) => {
    const r = segParam(s, p);
    if (r.d < best.d) best = { s: i + r.u, d: r.d };
  });
  return best.s;
}

/** The chain between positions s0 < s1 (both in [0, segs.length]). */
function chainPart(segs: Seg[], s0: number, s1: number): Seg[] {
  const out: Seg[] = [];
  for (let i = Math.floor(s0); i < Math.min(segs.length, Math.ceil(s1)); i++) {
    const u0 = Math.max(0, s0 - i);
    const u1 = Math.min(1, s1 - i);
    if (u1 - u0 > 1e-12) out.push(subSeg(segs[i], u0, u1));
  }
  return out;
}

/** Remove the piece of a polyline around `pick` between its nearest cut points. */
export function trimPolyline(g: Poly, pick: Pt, cutters: Prim[]): Geom[] | string {
  const segs = polySegs(g);
  const m = segs.length;
  const xs = chainCuts(segs, cutters).filter((x, i, a) => i === 0 || x - a[i - 1] > 1e-9);
  const p = chainParam(segs, pick);
  if (g.closed) {
    const inner = xs.map((x) => x % m);
    if (inner.length < 2) return "닫힌 폴리라인은 경계와 두 번 이상 만나야 자를 수 있습니다";
    const hi = inner.find((x) => x > p) ?? inner[0] + m;
    const lo = [...inner].reverse().find((x) => x < p) ?? inner[inner.length - 1] - m;
    // what remains runs from hi forward, wrapping, back to lo
    const rest = [...chainPart([...segs, ...segs], hi, lo + m)];
    return rest.length ? [fromSegs(rest, false, true)] : "남는 부분이 없습니다";
  }
  const inner = xs.filter((x) => x > 1e-9 && x < m - 1e-9);
  const lo = [...inner].reverse().find((x) => x < p) ?? 0;
  const hi = inner.find((x) => x > p) ?? m;
  if (lo === 0 && hi === m) return "선택한 부분에 자를 경계가 없습니다";
  const out: Geom[] = [];
  if (lo > 0) out.push(fromSegs(chainPart(segs, 0, lo), false, true));
  if (hi < m) out.push(fromSegs(chainPart(segs, hi, m), false, true));
  return out;
}

/** Break between two picked points (same point = split in two). LINE, ARC, CIRCLE, LWPOLYLINE. */
export function breakAt(g: Geom, p1: Pt, p2: Pt): Geom[] | string {
  if (g.type === "CIRCLE") {
    if (near(p1, p2)) return "원은 서로 다른 두 점에서 끊어야 합니다";
    const ang = (p: Pt) => norm360(Math.atan2(p[1] - g.center[1], p[0] - g.center[0]) * DEG);
    // like AutoCAD: the part from the first point counter-clockwise to the second is removed
    return [{ type: "ARC", center: g.center, radius: g.radius, start_angle: ang(p2), end_angle: ang(p1) }];
  }
  const segs = g.type === "LWPOLYLINE" ? polySegs(g) : toSegs(g);
  if (!segs) return "선·호·원·폴리라인만 끊을 수 있습니다";
  const poly = g.type === "LWPOLYLINE";
  const m = segs.length;
  let [a, b] = [chainParam(segs, p1), chainParam(segs, p2)];
  if (g.type === "LWPOLYLINE" && g.closed) {
    if (Math.abs(a - b) < 1e-9) return "닫힌 폴리라인은 서로 다른 두 점에서 끊어야 합니다";
    const rest = chainPart([...segs, ...segs], b, a < b ? a + m : a); // keep b -> a
    return [fromSegs(rest, false, true)];
  }
  if (a > b) [a, b] = [b, a];
  const out: Geom[] = [];
  if (a > 1e-9) out.push(fromSegs(chainPart(segs, 0, a), false, poly));
  if (b < m - 1e-9) out.push(fromSegs(chainPart(segs, b, m), false, poly));
  if (Math.abs(a - b) < 1e-9 && out.length < 2) return "끝점에서는 나눌 수 없습니다";
  return out.length ? out : "남는 부분이 없습니다";
}

/** Extend the polyline end nearest `pick` (its end segment, straight or curved) to the first boundary. */
export function extendPolyline(g: Poly, pick: Pt, bounds: Prim[], reach: number): Geom | string {
  if (g.closed) return "닫힌 폴리라인은 연장할 수 없습니다";
  const segs = polySegs(g);
  const atEnd = len(sub(pick, segs[segs.length - 1].b)) <= len(sub(pick, segs[0].a));
  const s = atEnd ? segs[segs.length - 1] : reverseSeg(segs[0]); // s.b is the end being extended
  const ext = extendSeg(s, bounds, reach);
  if (typeof ext === "string") return ext;
  if (atEnd) segs[segs.length - 1] = ext;
  else segs[0] = reverseSeg(ext);
  return fromSegs(segs, false, true);
}

/** Extend s past its end b to the nearest boundary crossing. */
function extendSeg(s: Seg, bounds: Prim[], reach: number): Seg | string {
  if (straight(s)) {
    const u = unit(sub(s.b, s.a));
    const ray: Prim = { kind: "SEG", a: s.b, b: add(s.b, mul(u, reach)), h: "" };
    let best = Infinity;
    for (const c of bounds) for (const x of intersect(ray, c)) {
      const d = dot(sub(x, s.b), u);
      if (d > 1e-7 && d < best) best = d;
    }
    return best === Infinity ? "연장 방향에 경계가 없습니다" : { ...s, b: add(s.b, mul(u, best)) };
  }
  const k = bulgeCircle(s);
  const dir = Math.sign(k.sweep);
  const full: Prim = { kind: "ARC", c: k.c, r: k.r, a0: 0, sweep: TAU, h: "" };
  const endAng = k.a0 + k.sweep;
  let best = Infinity;
  for (const c of bounds)
    for (const x of intersect(full, c)) {
      const d = ((((Math.atan2(x[1] - k.c[1], x[0] - k.c[0]) - endAng) * dir) % TAU) + TAU) % TAU;
      if (d > 1e-7 && d < TAU - Math.abs(k.sweep) - 1e-7 && d < best) best = d;
    }
  if (best === Infinity) return "연장 방향에 경계가 없습니다";
  const sweep = k.sweep + dir * best;
  const b: Pt = [k.c[0] + k.r * Math.cos(k.a0 + sweep), k.c[1] + k.r * Math.sin(k.a0 + sweep)];
  return { a: s.a, b, bulge: Math.tan(sweep / 4) };
}

// --- stretch -----------------------------------------------------------------------------------

const inBox = (p: Pt, lo: Pt, hi: Pt) => p[0] >= lo[0] - EPS && p[0] <= hi[0] + EPS && p[1] >= lo[1] - EPS && p[1] <= hi[1] + EPS;

/** FreeCAD Draft stretch: points inside the crossing window move by (dx, dy). Lines and polyline
 * vertices move individually (bulges kept); circles, arcs and text move whole when their centre
 * or insertion point is inside. Null = nothing of g is inside. */
export function stretchGeom(g: Geom, lo: Pt, hi: Pt, dx: number, dy: number): Geom | null {
  const mv = (p: Pt): Pt => (inBox(p, lo, hi) ? [p[0] + dx, p[1] + dy] : p);
  switch (g.type) {
    case "LINE": {
      if (!inBox(g.start, lo, hi) && !inBox(g.end, lo, hi)) return null;
      const r = { ...g, start: mv(g.start), end: mv(g.end) };
      return near(r.start, r.end, EPS) ? null : r;
    }
    case "LWPOLYLINE":
      return g.points.some(([x, y]) => inBox([x, y], lo, hi))
        ? { ...g, points: g.points.map(([x, y, b]) => [...mv([x, y]), b] as [number, number, number]) }
        : null;
    case "HATCH":
      return g.boundary.some(([x, y]) => inBox([x, y], lo, hi))
        ? { ...g, boundary: g.boundary.map(([x, y, b]) => [...mv([x, y]), b] as [number, number, number]) }
        : null;
    case "CIRCLE":
    case "ARC":
      return inBox(g.center, lo, hi) ? { ...g, center: mv(g.center) } : null;
    case "TEXT":
      return inBox(g.insert, lo, hi) ? { ...g, insert: mv(g.insert) } : null;
    default:
      return null;
  }
}

// --- chamfer / fillet with arcs ----------------------------------------------------------------

function lineMeet(a: Line, b: Line): Pt | null {
  const r = sub(a.end, a.start);
  const s = sub(b.end, b.start);
  const den = cross(r, s);
  if (Math.abs(den) < 1e-12 * len(r) * len(s)) return null;
  return add(a.start, mul(r, cross(sub(b.start, a.start), s) / den));
}

/** Bevel the corner of two lines with distances d1 (first line) and d2 (second), measured from
 * their intersection; each line keeps the side that was picked. */
export function chamferLines(l1: Line, pick1: Pt, l2: Line, pick2: Pt, d1: number, d2: number): { l1: Line; l2: Line; edge: Geom | null } | string {
  if (!(d1 >= 0 && d2 >= 0)) return "거리는 0 이상이어야 합니다";
  const P = lineMeet(l1, l2);
  if (!P) return "두 선이 평행합니다";
  const side = (l: Line, pick: Pt) => {
    const d = unit(sub(l.end, l.start));
    const u = dot(sub(pick, P), d) >= 0 ? d : mul(d, -1);
    const far = dot(sub(l.start, P), u) >= dot(sub(l.end, P), u) ? l.start : l.end;
    return { u, far, room: dot(sub(far, P), u) };
  };
  const [s1, s2] = [side(l1, pick1), side(l2, pick2)];
  if (s1.room <= EPS || s2.room <= EPS) return "선택한 쪽에 남는 선이 없습니다. 모서리 바깥쪽을 클릭하세요";
  if (d1 > s1.room + EPS || d2 > s2.room + EPS) return "모따기 거리가 선보다 깁니다";
  const [t1, t2] = [add(P, mul(s1.u, d1)), add(P, mul(s2.u, d2))];
  const keep = (l: Line, far: Pt, at: Pt): Line => (len(sub(l.start, far)) <= len(sub(l.end, far)) ? { ...l, start: far, end: at } : { ...l, start: at, end: far });
  const edge: Geom | null = near(t1, t2, EPS) ? null : { type: "LINE", start: t1, end: t2 };
  return { l1: keep(l1, s1.far, t1), l2: keep(l2, s2.far, t2), edge };
}

type Curve = { line: Line } | { arc: Extract<Geom, { type: "ARC" }> };

/** Fillet a line or arc with another line or arc (Sketcher fillet: the round's centre is where the
 * two curves' offsets by r meet; the candidate nearest the picks wins). Each curve is cut back to
 * its tangent point on the picked side. */
export function filletCurves(g1: Geom, pick1: Pt, g2: Geom, pick2: Pt, r: number): { g1: Geom; g2: Geom; arc: Geom } | string {
  if (!(r > 0)) return "반지름은 0보다 커야 합니다";
  const c1 = asCurve(g1);
  const c2 = asCurve(g2);
  if (!c1 || !c2) return "선 또는 호를 클릭하세요";
  const offsets = (c: Curve): Prim[] => {
    if ("line" in c) {
      const d = sub(c.line.end, c.line.start);
      const n = mul(unit([-d[1], d[0]]), r);
      const big = mul(unit(d), 1e6); // infinite line: a fillet may need the line extended
      return [1, -1].map((k) => ({ kind: "SEG", a: add(sub(c.line.start, big), mul(n, k)), b: add(add(c.line.end, big), mul(n, k)), h: "" }) as Prim);
    }
    return [c.arc.radius + r, c.arc.radius - r].filter((x) => x > EPS).map((rr) => ({ kind: "ARC", c: c.arc.center, r: rr, a0: 0, sweep: TAU, h: "" }) as Prim);
  };
  const foot = (c: Curve, p: Pt): Pt => {
    if ("line" in c) {
      const d = unit(sub(c.line.end, c.line.start));
      return add(c.line.start, mul(d, dot(sub(p, c.line.start), d)));
    }
    return add(c.arc.center, mul(unit(sub(p, c.arc.center)), c.arc.radius));
  };
  let best: { c: Pt; t1: Pt; t2: Pt; score: number } | null = null;
  for (const o1 of offsets(c1))
    for (const o2 of offsets(c2))
      for (const c of intersect(o1, o2)) {
        const [t1, t2] = [foot(c1, c), foot(c2, c)];
        const score = len(sub(t1, pick1)) + len(sub(t2, pick2));
        if (!best || score < best.score) best = { c, t1, t2, score };
      }
  if (!best) return "이 반지름으로는 두 객체를 둥글게 이을 수 없습니다";
  const cut1 = cutTo(c1, best.t1, pick1);
  const cut2 = cutTo(c2, best.t2, pick2);
  if (typeof cut1 === "string") return cut1;
  if (typeof cut2 === "string") return cut2;
  const [a1, a2] = [Math.atan2(best.t1[1] - best.c[1], best.t1[0] - best.c[0]) * DEG, Math.atan2(best.t2[1] - best.c[1], best.t2[0] - best.c[0]) * DEG];
  const ccw = norm360(a2 - a1) <= 180; // the round is the short way between the tangent points
  const arc: Geom = { type: "ARC", center: best.c, radius: r, start_angle: norm360(ccw ? a1 : a2), end_angle: norm360(ccw ? a2 : a1) };
  return { g1: cut1, g2: cut2, arc };
}

function asCurve(g: Geom): Curve | null {
  return g.type === "LINE" ? { line: g } : g.type === "ARC" ? { arc: g } : null;
}

/** Shorten (or lengthen) a curve so it ends at t, keeping the part on the picked side of t. */
function cutTo(c: Curve, t: Pt, pick: Pt): Geom | string {
  if ("line" in c) {
    const l = c.line;
    const d = unit(sub(l.end, l.start));
    const side = dot(sub(pick, t), d) >= 0 ? 1 : -1;
    const far = dot(sub(l.end, t), d) * side >= dot(sub(l.start, t), d) * side ? l.end : l.start;
    if (dot(sub(far, t), d) * side <= EPS) return "선택한 쪽에 남는 선이 없습니다";
    return len(sub(l.start, far)) < len(sub(l.end, far)) ? { ...l, start: far, end: t } : { ...l, start: t, end: far };
  }
  const a = c.arc;
  const ang = (p: Pt) => norm360(Math.atan2(p[1] - a.center[1], p[0] - a.center[0]) * DEG);
  const [ta, pa] = [ang(t), ang(pick)];
  // keep the part from t towards the pick: replace the arc end that lies beyond t (seen from the pick)
  const inArc = (x: number) => norm360(x - a.start_angle) <= (norm360(a.end_angle - a.start_angle) || 360) + 1e-9;
  const startSide = norm360(pa - ta) <= 180; // pick is CCW of t: t becomes the start
  const out = startSide ? { ...a, start_angle: ta } : { ...a, end_angle: ta };
  if (!inArc(pa) && !inArc(ta)) return "호의 선택한 쪽이 반지름 접점과 이어지지 않습니다";
  return norm360(out.end_angle - out.start_angle) < 1e-9 ? "남는 호가 없습니다" : out;
}


// --- offset with curved segments -----------------------------------------------------------------

/** Offset a polyline that has curved segments (Draft offsetWire): straight segments shift
 * sideways, arcs change radius about the same centre, neighbours re-join where their offsets meet
 * (nearest the plain offset of the shared vertex). `sgn` +1 = left of the drawing direction. */
export function offsetPolylineCurved(g: Poly, d: number, sgn: number): Geom | string {
  const segs = polySegs(g).filter((s) => !near(s.a, s.b, EPS));
  const leftNormal = (s: Seg, atEnd: boolean): Pt => {
    if (straight(s)) {
      const v = unit(sub(s.b, s.a));
      return [-v[1], v[0]];
    }
    const k = bulgeCircle(s);
    const p = atEnd ? s.b : s.a;
    const out = unit(sub(p, k.c));
    return k.sweep > 0 ? mul(out, -1) : out; // CCW arc: its left side faces the centre
  };
  const moved: { seg: Seg; prim: Prim }[] = [];
  for (const s of segs) {
    if (straight(s)) {
      const o = mul(leftNormal(s, false), sgn * d);
      const seg = { a: add(s.a, o), b: add(s.b, o), bulge: 0 };
      const u = mul(unit(sub(seg.b, seg.a)), 1e6);
      moved.push({ seg, prim: { kind: "SEG", a: sub(seg.a, u), b: add(seg.b, u), h: "" } });
    } else {
      const k = bulgeCircle(s);
      const r = k.r - (k.sweep > 0 ? 1 : -1) * sgn * d;
      if (r <= EPS) return "간격이 호의 반지름보다 큽니다";
      const at = (p: Pt) => add(k.c, mul(unit(sub(p, k.c)), r));
      moved.push({ seg: { a: at(s.a), b: at(s.b), bulge: s.bulge }, prim: { kind: "ARC", c: k.c, r, a0: 0, sweep: TAU, h: "" } });
    }
  }
  const m = moved.length;
  const joints = g.closed ? m : m - 1;
  for (let i = 0; i < joints; i++) {
    const [p, q] = [moved[i], moved[(i + 1) % m]];
    const want = add(segs[i].b, mul(leftNormal(segs[i], true), sgn * d));
    const xs = intersect(p.prim, q.prim);
    const v = xs.length ? xs.reduce((a, b) => (len(sub(a, want)) <= len(sub(b, want)) ? a : b)) : want;
    p.seg = { ...p.seg, b: v };
    q.seg = { ...q.seg, a: v };
  }
  const out = moved.map(({ seg }, i) => {
    if (straight(seg)) return seg;
    // same centre, new end points: keep the original sweep direction, recompute its size
    const k = bulgeCircle(segs[i]);
    const ang = (p: Pt) => Math.atan2(p[1] - k.c[1], p[0] - k.c[0]);
    const dir = Math.sign(k.sweep);
    const sweep = dir * (((((ang(seg.b) - ang(seg.a)) * dir) % TAU) + TAU) % TAU || TAU);
    return { ...seg, bulge: Math.tan(sweep / 4) };
  });
  return fromSegs(out, g.closed, true);
}
