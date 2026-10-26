import type { Pt } from "./view";

// All values in mm, angles in degrees CCW (backend contract).
export type Geom =
  | { type: "LINE"; start: Pt; end: Pt }
  | { type: "CIRCLE"; center: Pt; radius: number }
  | { type: "ARC"; center: Pt; radius: number; start_angle: number; end_angle: number }
  | { type: "LWPOLYLINE"; points: [number, number, number][]; closed: boolean }
  | { type: "TEXT"; insert: Pt; height: number; value: string; rotation: number }
  // local-only until saved; the server renders the real dimension (backend contract)
  | { type: "DIM_LINEAR"; p1: Pt; p2: Pt; base: Pt; angle: number }
  | { type: "DIM_ALIGNED"; p1: Pt; p2: Pt; distance: number }
  | { type: "DIM_ANGULAR"; center: Pt; p1: Pt; p2: Pt; base: Pt }
  | { type: "DIM_RADIUS"; center: Pt; radius: number; angle: number }
  | { type: "DIM_DIAMETER"; center: Pt; radius: number; angle: number }
  // closed boundary [x, y, bulge]; pattern = SOLID or a library name, scale/angle of the pattern
  | { type: "HATCH"; boundary: [number, number, number][]; pattern: string; scale: number; angle: number }
  // block reference: block content (block coordinates) scaled, turned and moved to insert
  | { type: "INSERT"; name: string; insert: Pt; xscale: number; yscale: number; rotation: number }
  | { type: "POINT"; point: Pt }
  // major = major axis vector; ratio = minor/major (0..1]; params in radians CCW from the major axis
  | { type: "ELLIPSE"; center: Pt; major: Pt; ratio: number; start_param: number; end_param: number }
  | { type: "SPLINE"; fit: Pt[] } // cubic curve through fit points (the server builds the exact CAD curve)
  | { type: "LEADER"; vertices: Pt[] } // arrow at vertices[0]
  // leader and its note as one object: text beside the last vertex, on the side the last leg points to
  | { type: "MULTILEADER"; vertices: Pt[]; value: string; height: number };

/** Types the server draws in full (text, arrows, pattern lines): the local geom only previews them. */
export const serverDrawn = (type: string) => type.startsWith("DIM_") || ["HATCH", "INSERT", "SPLINE", "LEADER", "MULTILEADER"].includes(type);

/** Points of an ellipse (or elliptical arc) at parameter t. */
export function ellipsePoint(g: Extract<Geom, { type: "ELLIPSE" }>, t: number): Pt {
  const [mx, my] = g.major;
  const [nx, ny] = [-my * g.ratio, mx * g.ratio]; // minor axis: major turned 90 deg CCW
  return [g.center[0] + mx * Math.cos(t) + nx * Math.sin(t), g.center[1] + my * Math.cos(t) + ny * Math.sin(t)];
}

/** Centripetal Catmull-Rom through the fit points: a close local preview of the CAD spline. */
function splinePts(fit: Pt[]): Pt[] {
  if (fit.length < 3) return fit;
  const P = [fit[0], ...fit, fit[fit.length - 1]];
  const out: Pt[] = [fit[0]];
  for (let i = 1; i + 2 < P.length; i++) {
    const [p0, p1, p2, p3] = [P[i - 1], P[i], P[i + 1], P[i + 2]];
    const knot = (a: Pt, b: Pt) => Math.sqrt(Math.hypot(b[0] - a[0], b[1] - a[1])) || 1e-9;
    const [t1, t2, t3] = [knot(p0, p1), knot(p1, p2), knot(p2, p3)].map((d, j, ds) => ds.slice(0, j + 1).reduce((x, y) => x + y));
    const lerp = (a: Pt, b: Pt, ta: number, tb: number, t: number): Pt => {
      const w = tb - ta || 1e-9;
      return [((tb - t) * a[0] + (t - ta) * b[0]) / w, ((tb - t) * a[1] + (t - ta) * b[1]) / w];
    };
    for (let k = 1; k <= 16; k++) {
      const t = t1 + ((t2 - t1) * k) / 16;
      const [a1, a2, a3] = [lerp(p0, p1, 0, t1, t), lerp(p1, p2, t1, t2, t), lerp(p2, p3, t2, t3, t)];
      const [b1, b2] = [lerp(a1, a2, 0, t2, t), lerp(a2, a3, t1, t3, t)];
      out.push(lerp(b1, b2, t1, t2, t));
    }
  }
  return out;
}

/** Block content paths (block coordinates, base at 0,0) placed by an INSERT geom. */
export function placeBlock(paths: Pt[][], g: Extract<Geom, { type: "INSERT" }>): Pt[][] {
  const [c, s] = [Math.cos(g.rotation * RAD), Math.sin(g.rotation * RAD)];
  return paths.map((line) =>
    line.map(([x, y]): Pt => {
      const [u, v] = [x * g.xscale, y * g.yscale];
      return [g.insert[0] + u * c - v * s, g.insert[1] + u * s + v * c];
    }),
  );
}

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

export const at = (c: Pt, r: number, deg: number): Pt => [c[0] + r * Math.cos(deg * RAD), c[1] + r * Math.sin(deg * RAD)];

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
    case "HATCH": // ponytail: the boundary only, the server draws the pattern / fill on save
      return toPaths({ type: "LWPOLYLINE", points: g.boundary, closed: true });
    case "POINT": {
      const [x, y] = g.point;
      return [[[x - 1, y], [x + 1, y]], [[x, y - 1], [x, y + 1]]];
    }
    case "ELLIPSE": {
      const full = Math.PI * 2;
      const sweep = ((((g.end_param - g.start_param) % full) + full) % full) || full;
      const n = Math.max(16, Math.ceil((sweep / full) * 128));
      return [Array.from({ length: n + 1 }, (_, i) => ellipsePoint(g, g.start_param + (sweep * i) / n))];
    }
    case "SPLINE":
      return [splinePts(g.fit)];
    case "LEADER":
    case "MULTILEADER":
      return [g.vertices];
    case "INSERT": {
      // a marker at the insertion point; the page previews the block content when it knows it
      const [x, y] = g.insert;
      return [[[x - 2, y], [x + 2, y]], [[x, y - 2], [x, y + 2]]];
    }
    // ponytail: dimension previews have no measured-value TEXT, the server renders the real one on save
    case "DIM_LINEAR": {
      const a = g.angle * RAD;
      const n: Pt = [-Math.sin(a), Math.cos(a)];
      const foot = (p: Pt): Pt => {
        const t = (g.base[0] - p[0]) * n[0] + (g.base[1] - p[1]) * n[1];
        return [p[0] + n[0] * t, p[1] + n[1] * t];
      };
      const [f1, f2] = [foot(g.p1), foot(g.p2)];
      return [[g.p1, f1], [g.p2, f2], [f1, f2]];
    }
    case "DIM_ALIGNED": {
      const d = Math.hypot(g.p2[0] - g.p1[0], g.p2[1] - g.p1[1]) || 1;
      const o: Pt = [(-(g.p2[1] - g.p1[1]) / d) * g.distance, ((g.p2[0] - g.p1[0]) / d) * g.distance];
      const [f1, f2]: Pt[] = [[g.p1[0] + o[0], g.p1[1] + o[1]], [g.p2[0] + o[0], g.p2[1] + o[1]]];
      return [[g.p1, f1], [g.p2, f2], [f1, f2]];
    }
    case "DIM_ANGULAR": {
      const r = Math.hypot(g.base[0] - g.center[0], g.base[1] - g.center[1]);
      const ang = (p: Pt) => Math.atan2(p[1] - g.center[1], p[0] - g.center[0]);
      const [a1, a2] = [ang(g.p1), ang(g.p2)];
      const sweep = (((a2 - a1) % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
      const ray = (a: number): Pt[] => [g.center, [g.center[0] + r * Math.cos(a), g.center[1] + r * Math.sin(a)]];
      return [ray(a1), ray(a2), arcPts(g.center[0], g.center[1], r, a1, sweep)];
    }
    case "DIM_RADIUS":
      return [[g.center, at(g.center, g.radius, g.angle)]];
    case "DIM_DIAMETER":
      return [[at(g.center, g.radius, g.angle + 180), at(g.center, g.radius, g.angle)]];
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
    case "HATCH":
      return { ...g, boundary: g.boundary.map(([x, y, b]) => [x + dx, y + dy, b]) };
    case "INSERT":
      return { ...g, insert: t(g.insert) };
    case "POINT":
      return { ...g, point: t(g.point) };
    case "ELLIPSE":
      return { ...g, center: t(g.center) };
    case "SPLINE":
      return { ...g, fit: g.fit.map(t) };
    case "LEADER":
    case "MULTILEADER":
      return { ...g, vertices: g.vertices.map(t) };
    case "TEXT":
      return { ...g, insert: t(g.insert) };
    case "DIM_LINEAR":
      return { ...g, p1: t(g.p1), p2: t(g.p2), base: t(g.base) };
    case "DIM_ALIGNED":
      return { ...g, p1: t(g.p1), p2: t(g.p2) };
    case "DIM_ANGULAR":
      return { ...g, center: t(g.center), p1: t(g.p1), p2: t(g.p2), base: t(g.base) };
    case "DIM_RADIUS":
    case "DIM_DIAMETER":
      return { ...g, center: t(g.center) };
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
    case "MULTILEADER":
      return g.height <= 0 ? "높이는 0보다 커야 합니다" : g.value.trim() === "" ? "문자열이 비어 있습니다" : null;
    case "ELLIPSE":
      return g.ratio > 0 && g.ratio <= 1 ? null : "단축/장축 비는 0보다 크고 1 이하입니다";
    case "INSERT":
      return g.xscale !== 0 && g.yscale !== 0 ? null : "축척은 0이 아니어야 합니다";
    case "HATCH":
      return g.scale >= 0.01 && g.scale <= 1000 ? null : "축척은 0.01~1000입니다";
    default:
      return null;
  }
}

// ---- entity helpers (memoized per entity object so pan/zoom never re-flattens) ----
export type HasGeom = { geom?: Geom; paths: Pt[][]; text?: { insert: Pt; height: number; value: string; rotation: number } };
const cache = new WeakMap<object, Pt[][]>();

export function entityPaths(e: HasGeom): Pt[][] {
  // a dimension or hatch read from the drawing keeps its server-drawn paths until it is edited
  if (!e.geom || (serverDrawn(e.geom.type) && e.paths.length)) return e.paths;
  let p = cache.get(e);
  if (!p) cache.set(e, (p = toPaths(e.geom)));
  return p;
}

export function entityText(e: HasGeom) {
  const g = e.geom;
  if (g?.type === "MULTILEADER" && !e.text) return leaderNote(g); // local preview until the server draws it
  return g?.type === "TEXT" ? { insert: g.insert, height: g.height, value: g.value, rotation: g.rotation } : e.text;
}

/** Note of a multileader as TEXT (baseline left), placed like the server places its MTEXT. */
export function leaderNote(g: Extract<Geom, { type: "MULTILEADER" }>) {
  const [a, b] = g.vertices.slice(-2);
  const h = g.height;
  const x = b[0] >= a[0] ? b[0] + h / 2 : b[0] - h / 2 - 0.6 * h * g.value.length;
  return { insert: [x, b[1] - h / 2] as Pt, height: h, value: g.value, rotation: 0 };
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
