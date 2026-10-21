// Exact 2D curve primitives for snapping: nearest point, perpendicular foot, intersections.
// The snap set follows FreeCAD's Draft snapper (gui_snapper.py, LGPL-2.1); the math here is our own.
import type { Geom } from "./geom";
import type { Pt } from "./view";

/** SEG a→b, or ARC from a0 sweeping CCW by sweep (radians, full circle = 2π). */
export type Prim = { kind: "SEG"; a: Pt; b: Pt; h: string } | { kind: "ARC"; c: Pt; r: number; a0: number; sweep: number; h: string };

const TAU = 2 * Math.PI;
const RAD = Math.PI / 180;
const EPS = 1e-9;

function bulgePrim(a: Pt, b: Pt, bulge: number, h: string): Prim {
  const [dx, dy] = [b[0] - a[0], b[1] - a[1]];
  const d = Math.hypot(dx, dy);
  const off = ((d / 2) * (1 - bulge * bulge)) / (2 * bulge);
  const c: Pt = [(a[0] + b[0]) / 2 + (-dy / d) * off, (a[1] + b[1]) / 2 + (dx / d) * off];
  const sweep = 4 * Math.atan(bulge);
  const a0 = Math.atan2(a[1] - c[1], a[0] - c[0]);
  // store CCW: a clockwise bulge starts at b instead
  return { kind: "ARC", c, r: Math.hypot(a[0] - c[0], a[1] - c[1]), a0: sweep > 0 ? a0 : a0 + sweep, sweep: Math.abs(sweep), h };
}

/** Editable entities from their geometry, read-only ones from their render polylines. */
export function toPrims(h: string, geom: Geom | undefined, paths: Pt[][]): Prim[] {
  if (!geom) {
    const out: Prim[] = [];
    for (const p of paths) for (let i = 1; i < p.length; i++) out.push({ kind: "SEG", a: p[i - 1], b: p[i], h });
    return out;
  }
  switch (geom.type) {
    case "LINE":
      return [{ kind: "SEG", a: geom.start, b: geom.end, h }];
    case "CIRCLE":
      return [{ kind: "ARC", c: geom.center, r: geom.radius, a0: 0, sweep: TAU, h }];
    case "ARC": {
      const sweep = (((geom.end_angle - geom.start_angle) % 360) + 360) % 360 || 360;
      return [{ kind: "ARC", c: geom.center, r: geom.radius, a0: geom.start_angle * RAD, sweep: sweep * RAD, h }];
    }
    case "LWPOLYLINE": {
      const v = geom.points;
      const out: Prim[] = [];
      const n = geom.closed ? v.length : v.length - 1;
      for (let i = 0; i < n; i++) {
        const [p, q] = [v[i], v[(i + 1) % v.length]];
        const a: Pt = [p[0], p[1]];
        const b: Pt = [q[0], q[1]];
        if (Math.hypot(b[0] - a[0], b[1] - a[1]) < EPS) continue;
        out.push(Math.abs(p[2]) < 1e-12 ? { kind: "SEG", a, b, h } : bulgePrim(a, b, p[2], h));
      }
      return out;
    }
    default:
      return []; // text and dimensions: no curve to snap along
  }
}

export function onArc(pr: Extract<Prim, { kind: "ARC" }>, p: Pt): boolean {
  if (pr.sweep >= TAU - EPS) return true;
  const t = (((Math.atan2(p[1] - pr.c[1], p[0] - pr.c[0]) - pr.a0) % TAU) + TAU) % TAU;
  return t <= pr.sweep + 1e-7 || t >= TAU - 1e-7;
}

export function arcPoint(pr: Extract<Prim, { kind: "ARC" }>, a: number): Pt {
  return [pr.c[0] + pr.r * Math.cos(a), pr.c[1] + pr.r * Math.sin(a)];
}

/** Closest point of the primitive to p. */
export function closest(pr: Prim, p: Pt): Pt {
  if (pr.kind === "SEG") {
    const [dx, dy] = [pr.b[0] - pr.a[0], pr.b[1] - pr.a[1]];
    const L = dx * dx + dy * dy;
    const t = L < EPS ? 0 : Math.max(0, Math.min(1, ((p[0] - pr.a[0]) * dx + (p[1] - pr.a[1]) * dy) / L));
    return [pr.a[0] + t * dx, pr.a[1] + t * dy];
  }
  const a = Math.atan2(p[1] - pr.c[1], p[0] - pr.c[0]);
  const q = arcPoint(pr, a);
  if (onArc(pr, q)) return q;
  const [s, e] = [arcPoint(pr, pr.a0), arcPoint(pr, pr.a0 + pr.sweep)];
  return Math.hypot(s[0] - p[0], s[1] - p[1]) <= Math.hypot(e[0] - p[0], e[1] - p[1]) ? s : e;
}

/** Foot of the perpendicular from `from` onto the primitive, only if it lies on it. */
export function perpFoot(pr: Prim, from: Pt): Pt | null {
  if (pr.kind === "SEG") {
    const [dx, dy] = [pr.b[0] - pr.a[0], pr.b[1] - pr.a[1]];
    const L = dx * dx + dy * dy;
    if (L < EPS) return null;
    const t = ((from[0] - pr.a[0]) * dx + (from[1] - pr.a[1]) * dy) / L;
    return t < -1e-9 || t > 1 + 1e-9 ? null : [pr.a[0] + t * dx, pr.a[1] + t * dy];
  }
  if (Math.hypot(from[0] - pr.c[0], from[1] - pr.c[1]) < EPS) return null; // every point is perpendicular
  const q = arcPoint(pr, Math.atan2(from[1] - pr.c[1], from[0] - pr.c[0]));
  return onArc(pr, q) ? q : null;
}

function lineCircle(a: Pt, b: Pt, c: Pt, r: number): { p: Pt; t: number }[] {
  const [dx, dy] = [b[0] - a[0], b[1] - a[1]];
  const [fx, fy] = [a[0] - c[0], a[1] - c[1]];
  const A = dx * dx + dy * dy;
  const B = 2 * (fx * dx + fy * dy);
  const C = fx * fx + fy * fy - r * r;
  const disc = B * B - 4 * A * C;
  if (A < EPS || disc < -1e-9 * A * r * r) return [];
  const s = Math.sqrt(Math.max(0, disc));
  const ts = s < 1e-12 ? [-B / (2 * A)] : [(-B - s) / (2 * A), (-B + s) / (2 * A)];
  return ts.map((t) => ({ t, p: [a[0] + t * dx, a[1] + t * dy] as Pt }));
}

/** Intersection points of two primitives (overlapping collinear segments and concentric arcs: none). */
export function intersect(p: Prim, q: Prim): Pt[] {
  if (p.kind === "SEG" && q.kind === "SEG") {
    const [rx, ry] = [p.b[0] - p.a[0], p.b[1] - p.a[1]];
    const [sx, sy] = [q.b[0] - q.a[0], q.b[1] - q.a[1]];
    const den = rx * sy - ry * sx;
    if (Math.abs(den) < 1e-12 * Math.hypot(rx, ry) * Math.hypot(sx, sy)) return [];
    const [qx, qy] = [q.a[0] - p.a[0], q.a[1] - p.a[1]];
    const t = (qx * sy - qy * sx) / den;
    const u = (qx * ry - qy * rx) / den;
    const e = 1e-9;
    return t >= -e && t <= 1 + e && u >= -e && u <= 1 + e ? [[p.a[0] + t * rx, p.a[1] + t * ry]] : [];
  }
  if (p.kind === "ARC" && q.kind === "SEG") return intersect(q, p);
  if (p.kind === "SEG" && q.kind === "ARC")
    return lineCircle(p.a, p.b, q.c, q.r)
      .filter(({ t, p: x }) => t >= -1e-9 && t <= 1 + 1e-9 && onArc(q, x))
      .map(({ p: x }) => x);
  const [a, b] = [p as Extract<Prim, { kind: "ARC" }>, q as Extract<Prim, { kind: "ARC" }>];
  const [dx, dy] = [b.c[0] - a.c[0], b.c[1] - a.c[1]];
  const d = Math.hypot(dx, dy);
  if (d < EPS || d > a.r + b.r + 1e-9 || d < Math.abs(a.r - b.r) - 1e-9) return [];
  const l = (a.r * a.r - b.r * b.r + d * d) / (2 * d);
  const hh = Math.sqrt(Math.max(0, a.r * a.r - l * l));
  const [mx, my] = [a.c[0] + (l * dx) / d, a.c[1] + (l * dy) / d];
  const pts: Pt[] = hh < 1e-12 ? [[mx, my]] : [[mx - (hh * dy) / d, my + (hh * dx) / d], [mx + (hh * dy) / d, my - (hh * dx) / d]];
  return pts.filter((x) => onArc(a, x) && onArc(b, x));
}

/** Points along the primitive at most `step` apart (for bucketing it into grid cells). */
export function samples(pr: Prim, step: number, max = 4096): Pt[] {
  if (pr.kind === "SEG") {
    const n = Math.min(max, Math.max(1, Math.ceil(Math.hypot(pr.b[0] - pr.a[0], pr.b[1] - pr.a[1]) / step)));
    return Array.from({ length: n + 1 }, (_, i) => [pr.a[0] + ((pr.b[0] - pr.a[0]) * i) / n, pr.a[1] + ((pr.b[1] - pr.a[1]) * i) / n] as Pt);
  }
  const n = Math.min(max, Math.max(4, Math.ceil((pr.r * pr.sweep) / step)));
  return Array.from({ length: n + 1 }, (_, i) => arcPoint(pr, pr.a0 + (pr.sweep * i) / n));
}
