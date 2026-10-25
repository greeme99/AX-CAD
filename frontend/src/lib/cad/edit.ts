// 2D modify operations (FreeCAD Draft: rectangle, polygon, rotate, scale, mirror, arrays, offset,
// fillet, trim/extend). Pure functions on our Geom (mm, degrees CCW); results only use the entity
// types the server writer already round-trips, so saving needs no backend change.
import { closest, intersect, onArc, type Prim } from "./curves";
import { offsetPolylineCurved } from "./modify";
import type { Geom } from "./geom";
import type { Pt } from "./view";

type Line = Extract<Geom, { type: "LINE" }>;
const RAD = Math.PI / 180;
const DEG = 180 / Math.PI;
const EPS = 1e-9;
const norm360 = (d: number) => ((d % 360) + 360) % 360;
const sub = (a: Pt, b: Pt): Pt => [a[0] - b[0], a[1] - b[1]];
const add = (a: Pt, b: Pt): Pt => [a[0] + b[0], a[1] + b[1]];
const mul = (a: Pt, k: number): Pt => [a[0] * k, a[1] * k];
const dot = (a: Pt, b: Pt) => a[0] * b[0] + a[1] * b[1];
const cross = (a: Pt, b: Pt) => a[0] * b[1] - a[1] * b[0];
const len = (a: Pt) => Math.hypot(a[0], a[1]);
const unit = (a: Pt): Pt => mul(a, 1 / len(a));

/** Same shape with every point mapped; `ang` maps a direction angle, `flip` = mirror (orientation reverses). */
function mapGeom(g: Geom, f: (p: Pt) => Pt, ang: (deg: number) => number, k: number, flip: boolean): Geom {
  switch (g.type) {
    case "LINE":
      return { ...g, start: f(g.start), end: f(g.end) };
    case "CIRCLE":
      return { ...g, center: f(g.center), radius: g.radius * k };
    case "ARC": {
      const [s, e] = [norm360(ang(g.start_angle)), norm360(ang(g.end_angle))];
      return { ...g, center: f(g.center), radius: g.radius * k, start_angle: flip ? e : s, end_angle: flip ? s : e };
    }
    case "LWPOLYLINE":
      return { ...g, points: g.points.map(([x, y, b]) => [...f([x, y]), flip ? -b : b] as [number, number, number]) };
    case "TEXT": // stays readable when mirrored (AutoCAD MIRRTEXT=0)
      return { ...g, insert: f(g.insert), height: g.height * k, rotation: flip ? g.rotation : norm360(ang(g.rotation)) };
    case "DIM_LINEAR":
      return { ...g, p1: f(g.p1), p2: f(g.p2), base: f(g.base), angle: norm360(ang(g.angle)) % 180 };
    case "DIM_ALIGNED":
      return { ...g, p1: f(g.p1), p2: f(g.p2), distance: g.distance * k * (flip ? -1 : 1) };
    case "DIM_ANGULAR":
      return { ...g, center: f(g.center), p1: f(flip ? g.p2 : g.p1), p2: f(flip ? g.p1 : g.p2), base: f(g.base) };
    case "DIM_RADIUS":
      return { ...g, center: f(g.center), radius: g.radius * k, angle: norm360(ang(g.angle)) };
  }
}

export function rotateGeom(g: Geom, c: Pt, deg: number): Geom {
  const [co, si] = [Math.cos(deg * RAD), Math.sin(deg * RAD)];
  const f = (p: Pt): Pt => [c[0] + (p[0] - c[0]) * co - (p[1] - c[1]) * si, c[1] + (p[0] - c[0]) * si + (p[1] - c[1]) * co];
  return mapGeom(g, f, (a) => a + deg, 1, false);
}

export function scaleGeom(g: Geom, c: Pt, k: number): Geom {
  return mapGeom(g, (p) => [c[0] + (p[0] - c[0]) * k, c[1] + (p[1] - c[1]) * k], (a) => a, k, false);
}

/** Reflection across the line through a and b. */
export function mirrorGeom(g: Geom, a: Pt, b: Pt): Geom {
  const u = unit(sub(b, a));
  const phi = Math.atan2(u[1], u[0]) * DEG;
  const f = (p: Pt): Pt => {
    const v = sub(p, a);
    return add(a, sub(mul(u, 2 * dot(v, u)), v));
  };
  return mapGeom(g, f, (x) => 2 * phi - x, 1, true);
}

export function rectGeom(a: Pt, b: Pt): Geom | null {
  if (Math.abs(b[0] - a[0]) < EPS || Math.abs(b[1] - a[1]) < EPS) return null;
  const pts: [number, number, number][] = [[a[0], a[1], 0], [b[0], a[1], 0], [b[0], b[1], 0], [a[0], b[1], 0]];
  return { type: "LWPOLYLINE", points: pts, closed: true };
}

/** Regular polygon inscribed in the circle through `vertex`. */
export function polygonGeom(c: Pt, vertex: Pt, n: number): Geom | null {
  const r = len(sub(vertex, c));
  if (r < EPS || n < 3) return null;
  const a0 = Math.atan2(vertex[1] - c[1], vertex[0] - c[0]);
  const pts = Array.from({ length: n }, (_, i): [number, number, number] => {
    const a = a0 + (2 * Math.PI * i) / n;
    return [c[0] + r * Math.cos(a), c[1] + r * Math.sin(a), 0];
  });
  return { type: "LWPOLYLINE", points: pts, closed: true };
}

/** Parallel copy at distance d on the side of `side`. Straight polylines join at mitred corners. */
export function offsetGeom(g: Geom, d: number, side: Pt): Geom | string {
  if (!(d > 0)) return "간격은 0보다 커야 합니다";
  if (g.type === "LINE") {
    const dir = sub(g.end, g.start);
    const n = unit([-dir[1], dir[0]]);
    const o = mul(n, dot(sub(side, g.start), n) >= 0 ? d : -d);
    return { ...g, start: add(g.start, o), end: add(g.end, o) };
  }
  if (g.type === "CIRCLE" || g.type === "ARC") {
    const r = len(sub(side, g.center)) > g.radius ? g.radius + d : g.radius - d;
    return r > EPS ? { ...g, radius: r } : "안쪽 간격이 반지름보다 큽니다";
  }
  if (g.type !== "LWPOLYLINE") return "선·원·호·폴리라인만 간격띄우기할 수 있습니다";
  const curved = g.points.some((p, i) => Math.abs(p[2]) > 1e-12 && (g.closed || i < g.points.length - 1));
  const v: Pt[] = g.points.map(([x, y]) => [x, y]);
  const m = g.closed ? v.length : v.length - 1;
  const segs: [Pt, Pt][] = [];
  for (let i = 0; i < m; i++) segs.push([v[i], v[(i + 1) % v.length]]);
  // side: the nearest segment decides the sign for the whole polyline
  let best = 0;
  let bd = Infinity;
  segs.forEach(([a, b], i) => {
    const q = closest({ kind: "SEG", a, b, h: "" }, side);
    const dd = len(sub(q, side));
    if (dd < bd) [best, bd] = [i, dd];
  });
  const [sa, sb] = segs[best];
  const sgn = cross(sub(sb, sa), sub(side, sa)) >= 0 ? 1 : -1;
  if (curved) return offsetPolylineCurved(g, d, sgn); // ponytail: side picked by the nearest chord
  const moved = segs.map(([a, b]): [Pt, Pt] => {
    const dir = sub(b, a);
    if (len(dir) < EPS) return [a, b];
    const o = mul(unit([-dir[1], dir[0]]), sgn * d);
    return [add(a, o), add(b, o)];
  });
  const meet = (p: [Pt, Pt], q: [Pt, Pt]): Pt => {
    const r = sub(p[1], p[0]);
    const s = sub(q[1], q[0]);
    const den = cross(r, s);
    if (Math.abs(den) < 1e-12 * len(r) * len(s)) return p[1]; // collinear: keep the shared end
    return add(p[0], mul(r, cross(sub(q[0], p[0]), s) / den));
  };
  const out: Pt[] = [];
  for (let i = 0; i < v.length; i++) {
    if (!g.closed && i === 0) out.push(moved[0][0]);
    else if (!g.closed && i === v.length - 1) out.push(moved[m - 1][1]);
    else out.push(meet(moved[(i - 1 + m) % m], moved[i % m]));
  }
  return { ...g, points: out.map(([x, y]) => [x, y, 0]) };
}

/** Infinite-line intersection of two LINEs, null when parallel. */
function lineMeet(a: Line, b: Line): Pt | null {
  const r = sub(a.end, a.start);
  const s = sub(b.end, b.start);
  const den = cross(r, s);
  if (Math.abs(den) < 1e-12 * len(r) * len(s)) return null;
  return add(a.start, mul(r, cross(sub(b.start, a.start), s) / den));
}

/** Rebuild a line keeping its far end, ending at `at`; original direction preserved. */
function keep(l: Line, far: Pt, at: Pt): Line {
  return len(sub(l.start, far)) <= len(sub(l.end, far)) ? { ...l, start: far, end: at } : { ...l, start: at, end: far };
}

/** Fillet two lines with radius r (0 = sharp corner). Each line keeps the side that was picked. */
export function filletLines(l1: Line, pick1: Pt, l2: Line, pick2: Pt, r: number): { l1: Line; l2: Line; arc: Geom | null } | string {
  if (!(r >= 0)) return "반지름은 0 이상이어야 합니다";
  const P = lineMeet(l1, l2);
  if (!P) return "두 선이 평행합니다";
  const side = (l: Line, pick: Pt): { u: Pt; far: Pt } => {
    const d = unit(sub(l.end, l.start));
    const u = dot(sub(pick, P), d) >= 0 ? d : mul(d, -1);
    const far = dot(sub(l.start, P), u) >= dot(sub(l.end, P), u) ? l.start : l.end;
    return { u, far };
  };
  const [s1, s2] = [side(l1, pick1), side(l2, pick2)];
  const room = (s: { u: Pt; far: Pt }) => dot(sub(s.far, P), s.u);
  if (room(s1) <= EPS || room(s2) <= EPS) return "선택한 쪽에 남는 선이 없습니다. 모서리 바깥쪽을 클릭하세요";
  if (r === 0) return { l1: keep(l1, s1.far, P), l2: keep(l2, s2.far, P), arc: null };
  const alpha = Math.acos(Math.max(-1, Math.min(1, dot(s1.u, s2.u)))); // corner angle
  if (alpha < 1e-9 || Math.PI - alpha < 1e-9) return "두 선이 일직선입니다";
  const t = r / Math.tan(alpha / 2);
  if (t > room(s1) + EPS || t > room(s2) + EPS) return "반지름이 너무 커서 선 안에 들어가지 않습니다";
  const [t1, t2] = [add(P, mul(s1.u, t)), add(P, mul(s2.u, t))];
  const c = add(P, mul(unit(add(s1.u, s2.u)), r / Math.sin(alpha / 2)));
  const [a1, a2] = [Math.atan2(t1[1] - c[1], t1[0] - c[0]) * DEG, Math.atan2(t2[1] - c[1], t2[0] - c[0]) * DEG];
  const ccw = norm360(a2 - a1) <= 180; // the fillet is the short way round
  const arc: Geom = { type: "ARC", center: c, radius: r, start_angle: norm360(ccw ? a1 : a2), end_angle: norm360(ccw ? a2 : a1) };
  return { l1: keep(l1, s1.far, t1), l2: keep(l2, s2.far, t2), arc };
}

type Arcish = Extract<Prim, { kind: "ARC" }>;

/** Where the cutters cross g, as positions along g: line t in [0,1], circle/arc CCW degrees from its start. */
function cuts(g: Geom, cutters: Prim[]): number[] {
  const out: number[] = [];
  if (g.type === "LINE") {
    const me: Prim = { kind: "SEG", a: g.start, b: g.end, h: "" };
    const d = sub(g.end, g.start);
    for (const c of cutters) for (const x of intersect(me, c)) out.push(dot(sub(x, g.start), d) / dot(d, d));
  } else if (g.type === "CIRCLE" || g.type === "ARC") {
    const a0 = g.type === "ARC" ? g.start_angle : 0;
    const sweep = g.type === "ARC" ? norm360(g.end_angle - g.start_angle) || 360 : 360;
    const me: Arcish = { kind: "ARC", c: g.center, r: g.radius, a0: a0 * RAD, sweep: sweep * RAD, h: "" };
    for (const c of cutters)
      for (const x of intersect(me, c)) if (onArc(me, x)) out.push(norm360(Math.atan2(x[1] - g.center[1], x[0] - g.center[0]) * DEG - a0));
  }
  return out.sort((a, b) => a - b);
}

const arcGeom = (c: Pt, r: number, s: number, e: number): Geom => ({ type: "ARC", center: c, radius: r, start_angle: norm360(s), end_angle: norm360(e) });

/** Remove the piece of g around `pick` between the nearest cutting points (FreeCAD trimex). Returns what remains. */
export function trimAt(g: Geom, pick: Pt, cutters: Prim[]): Geom[] | string {
  if (g.type !== "LINE" && g.type !== "CIRCLE" && g.type !== "ARC") return "선·원·호만 자를 수 있습니다";
  const at = cuts(g, cutters);
  if (g.type === "CIRCLE") {
    const xs = [...new Set(at.map((a) => Math.round(a * 1e9) / 1e9))];
    if (xs.length < 2) return "원은 경계와 두 번 이상 만나야 자를 수 있습니다";
    const p = norm360(Math.atan2(pick[1] - g.center[1], pick[0] - g.center[0]) * DEG);
    const hi = xs.find((a) => a > p) ?? xs[0] + 360;
    const lo = [...xs].reverse().find((a) => a < p) ?? xs[xs.length - 1] - 360;
    return [arcGeom(g.center, g.radius, hi, lo)]; // what is left runs CCW from the upper cut to the lower one
  }
  const span = g.type === "LINE" ? 1 : norm360(g.end_angle - g.start_angle) || 360;
  const p =
    g.type === "LINE"
      ? dot(sub(pick, g.start), sub(g.end, g.start)) / dot(sub(g.end, g.start), sub(g.end, g.start))
      : norm360(Math.atan2(pick[1] - g.center[1], pick[0] - g.center[0]) * DEG - g.start_angle);
  const inner = at.filter((x) => x > EPS * span && x < span * (1 - EPS));
  const lo = [...inner].reverse().find((x) => x < p) ?? 0;
  const hi = inner.find((x) => x > p) ?? span;
  if (lo === 0 && hi === span) return "선택한 부분에 자를 경계가 없습니다";
  const out: Geom[] = [];
  if (g.type === "LINE") {
    const pt = (t: number): Pt => add(g.start, mul(sub(g.end, g.start), t));
    if (lo > 0) out.push({ ...g, start: g.start, end: pt(lo) });
    if (hi < span) out.push({ ...g, start: pt(hi), end: g.end });
  } else {
    if (lo > 0) out.push(arcGeom(g.center, g.radius, g.start_angle, g.start_angle + lo));
    if (hi < span) out.push(arcGeom(g.center, g.radius, g.start_angle + hi, g.end_angle));
  }
  return out;
}

/** Extend the end of a line or arc nearest to `pick` up to the first boundary it would reach. */
export function extendAt(g: Geom, pick: Pt, bounds: Prim[], reach: number): Geom | string {
  if (g.type === "LINE") {
    const atEnd = len(sub(pick, g.end)) <= len(sub(pick, g.start));
    const [from, other] = atEnd ? [g.end, g.start] : [g.start, g.end];
    const u = unit(sub(from, other));
    const ray: Prim = { kind: "SEG", a: from, b: add(from, mul(u, reach)), h: "" };
    let best = Infinity;
    for (const c of bounds) for (const x of intersect(ray, c)) {
      const d = dot(sub(x, from), u);
      if (d > 1e-7 && d < best) best = d;
    }
    if (best === Infinity) return "연장 방향에 경계가 없습니다";
    const to = add(from, mul(u, best));
    return atEnd ? { ...g, end: to } : { ...g, start: to };
  }
  if (g.type === "ARC") {
    const ang = (p: Pt) => norm360(Math.atan2(p[1] - g.center[1], p[0] - g.center[0]) * DEG);
    const sweep = norm360(g.end_angle - g.start_angle) || 360;
    const p = ang(pick);
    const gap = (a: number) => Math.min(norm360(p - a), norm360(a - p));
    const atEnd = gap(g.end_angle) <= gap(g.start_angle);
    const full: Arcish = { kind: "ARC", c: g.center, r: g.radius, a0: 0, sweep: 2 * Math.PI, h: "" };
    let best = Infinity;
    for (const c of bounds)
      for (const x of intersect(full, c)) {
        const d = atEnd ? norm360(ang(x) - g.end_angle) : norm360(g.start_angle - ang(x));
        if (d > 1e-7 && d < 360 - sweep - 1e-7 && d < best) best = d;
      }
    if (best === Infinity) return "연장 방향에 경계가 없습니다";
    return atEnd ? { ...g, end_angle: norm360(g.end_angle + best) } : { ...g, start_angle: norm360(g.start_angle - best) };
  }
  return "선과 호만 연장할 수 있습니다";
}
