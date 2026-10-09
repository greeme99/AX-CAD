import type { Pt } from "./view";

// All values in mm, angles in degrees CCW (backend contract).
export type Geom =
  | { type: "LINE"; start: Pt; end: Pt }
  | { type: "CIRCLE"; center: Pt; radius: number }
  | { type: "ARC"; center: Pt; radius: number; start_angle: number; end_angle: number }
  | { type: "LWPOLYLINE"; points: [number, number, number][]; closed: boolean }
  | { type: "TEXT"; insert: Pt; height: number; value: string; rotation: number };

const TOL = 0.05; // max chord sagitta (mm) when flattening arcs
const RAD = Math.PI / 180;

// ponytail: fixed tolerance in world mm (not zoom-aware), re-flatten per zoom level if huge circles look faceted
function arcPts(cx: number, cy: number, r: number, a0: number, sweep: number): Pt[] {
  const step = r > TOL ? 2 * Math.acos(1 - TOL / r) : Math.PI / 2;
  const n = Math.min(4096, Math.max(1, Math.ceil(Math.abs(sweep) / step)));
  const out: Pt[] = [];
  for (let i = 0; i <= n; i++) {
    const a = a0 + (sweep * i) / n;
    out.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]);
  }
  return out;
}

function bulgeArc(a: Pt, b: Pt, bulge: number): Pt[] {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const d = Math.hypot(dx, dy);
  if (d < 1e-12) return [a, b];
  const off = ((d / 2) * (1 - bulge * bulge)) / (2 * bulge);
  const cx = (a[0] + b[0]) / 2 + (-dy / d) * off;
  const cy = (a[1] + b[1]) / 2 + (dx / d) * off;
  const pts = arcPts(cx, cy, Math.hypot(a[0] - cx, a[1] - cy), Math.atan2(a[1] - cy, a[0] - cx), 4 * Math.atan(bulge));
  pts[0] = a;
  pts[pts.length - 1] = b;
  return pts;
}

export function toPaths(g: Geom): Pt[][] {
  switch (g.type) {
    case "LINE":
      return [[g.start, g.end]];
    case "CIRCLE": {
      const p = arcPts(g.center[0], g.center[1], g.radius, 0, 2 * Math.PI);
      p[p.length - 1] = p[0];
      return [p];
    }
    case "ARC": {
      const sweep = ((((g.end_angle - g.start_angle) % 360) + 360) % 360 || 360) * RAD;
      return [arcPts(g.center[0], g.center[1], g.radius, g.start_angle * RAD, sweep)];
    }
    case "LWPOLYLINE": {
      const v = g.points;
      if (v.length < 2) return [];
      const out: Pt[] = [[v[0][0], v[0][1]]];
      const segs = g.closed ? v.length : v.length - 1;
      for (let i = 0; i < segs; i++) {
        const a: Pt = [v[i][0], v[i][1]];
        const b: Pt = [v[(i + 1) % v.length][0], v[(i + 1) % v.length][1]];
        if (Math.abs(v[i][2]) < 1e-12) out.push(b);
        else out.push(...bulgeArc(a, b, v[i][2]).slice(1));
      }
      return [out];
    }
    case "TEXT":
      return [];
  }
}

export function translate(g: Geom, dx: number, dy: number): Geom {
  const t = (p: Pt): Pt => [p[0] + dx, p[1] + dy];
  switch (g.type) {
    case "LINE":
      return { ...g, start: t(g.start), end: t(g.end) };
    case "CIRCLE":
    case "ARC":
      return { ...g, center: t(g.center) };
    case "LWPOLYLINE":
      return { ...g, points: g.points.map(([x, y, b]) => [x + dx, y + dy, b]) };
    case "TEXT":
      return { ...g, insert: t(g.insert) };
  }
}

const norm360 = (deg: number) => ((deg % 360) + 360) % 360;

/** ARC through 3 points (CCW start/end), null when collinear. */
export function arcFrom3Points(p1: Pt, p2: Pt, p3: Pt): Geom | null {
  const [ax, ay] = p1;
  const [bx, by] = p2;
  const [cx, cy] = p3;
  const cross = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
  if (Math.abs(cross) <= 1e-9 * Math.hypot(bx - ax, by - ay) * Math.hypot(cx - ax, cy - ay)) return null;
  const d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by));
  const a2 = ax * ax + ay * ay;
  const b2 = bx * bx + by * by;
  const c2 = cx * cx + cy * cy;
  const ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d;
  const uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d;
  const ang = (p: Pt) => norm360((Math.atan2(p[1] - uy, p[0] - ux) * 180) / Math.PI);
  const ccw = cross > 0;
  return {
    type: "ARC",
    center: [ux, uy],
    radius: Math.hypot(ax - ux, ay - uy),
    start_angle: ang(ccw ? p1 : p3),
    end_angle: ang(ccw ? p3 : p1),
  };
}

function distSeg(p: Pt, a: Pt, b: Pt): number {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const l2 = dx * dx + dy * dy;
  const t = l2 === 0 ? 0 : Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2));
  return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
}

export function distToPaths(paths: Pt[][], p: Pt): number {
  let m = Infinity;
  for (const path of paths) for (let i = 0; i + 1 < path.length; i++) m = Math.min(m, distSeg(p, path[i], path[i + 1]));
  return m;
}

export const hitTest = (paths: Pt[][], p: Pt, tol: number) => distToPaths(paths, p) <= tol;

/** Semantic checks shared by the inspector (reducer has its own prompts). */
export function validateGeom(g: Geom): string | null {
  switch (g.type) {
    case "LINE":
      return Math.hypot(g.end[0] - g.start[0], g.end[1] - g.start[1]) > 1e-9 ? null : "길이 0인 선은 허용되지 않습니다";
    case "CIRCLE":
    case "ARC":
      return g.radius > 0 ? null : "반지름은 0보다 커야 합니다";
    case "TEXT":
      return g.height <= 0 ? "높이는 0보다 커야 합니다" : g.value.trim() === "" ? "문자열이 비어 있습니다" : null;
    default:
      return null;
  }
}

// ---- entity helpers (memoized per entity object so pan/zoom never re-flattens) ----
type HasGeom = { geom?: Geom; paths: Pt[][]; text?: { insert: Pt; height: number; value: string; rotation: number } };
const cache = new WeakMap<object, Pt[][]>();

export function entityPaths(e: HasGeom): Pt[][] {
  if (!e.geom) return e.paths;
  let p = cache.get(e);
  if (!p) cache.set(e, (p = toPaths(e.geom)));
  return p;
}

export function entityText(e: HasGeom) {
  const g = e.geom;
  return g?.type === "TEXT" ? { insert: g.insert, height: g.height, value: g.value, rotation: g.rotation } : e.text;
}

/** Paths used for picking: TEXT is approximated by its (rotated) bounding box. */
export function hitPaths(e: HasGeom): Pt[][] {
  const t = e.geom?.type === "TEXT" ? e.geom : null;
  if (!t) return entityPaths(e);
  // ponytail: 0.6*height per char width estimate, measure real text metrics if picking feels off
  const w = 0.6 * t.height * t.value.length;
  const c = Math.cos(t.rotation * RAD);
  const s = Math.sin(t.rotation * RAD);
  const q = (x: number, y: number): Pt => [t.insert[0] + x * c - y * s, t.insert[1] + x * s + y * c];
  return [[q(0, 0), q(w, 0), q(w, t.height), q(0, t.height), q(0, 0)]];
}
