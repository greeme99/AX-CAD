// Track 8 P1-4 measuring (FreeCAD Std_MeasureDistance / Draft area): lengths and areas in mm.
import { toPaths, type Geom } from "./geom";
import type { Pt } from "./view";

export const pathLength = (paths: Pt[][]) =>
  paths.reduce((sum, p) => sum + p.slice(1).reduce((s, q, i) => s + Math.hypot(q[0] - p[i][0], q[1] - p[i][1]), 0), 0);

/** Shoelace area of a closed outline (the first point need not repeat at the end). */
export function polyArea(pts: Pt[]): number {
  let a = 0;
  for (let i = 0; i < pts.length; i++) {
    const [p, q] = [pts[i], pts[(i + 1) % pts.length]];
    a += p[0] * q[1] - q[0] * p[1];
  }
  return Math.abs(a) / 2;
}

/** Exact length and area of a polyline with bulges: the straight outline's shoelace area plus,
 * for each arc segment, the circular segment between chord and arc (signed like the bulge). */
function bulgeMeasure(v: [number, number, number][], closed: boolean): { length: number; area?: number } {
  let length = 0;
  let twice = 0; // 2 x signed area
  const n = closed ? v.length : v.length - 1;
  for (let i = 0; i < n; i++) {
    const [x0, y0, b] = v[i];
    const [x1, y1] = v[(i + 1) % v.length];
    const c = Math.hypot(x1 - x0, y1 - y0);
    twice += x0 * y1 - x1 * y0;
    if (Math.abs(b) < 1e-12 || c < 1e-12) {
      length += c;
      continue;
    }
    const th = 4 * Math.atan(b); // signed sweep
    const r = c / (2 * Math.sin(Math.abs(th) / 2));
    length += r * Math.abs(th);
    twice += Math.sign(b) * r * r * (Math.abs(th) - Math.sin(Math.abs(th)));
  }
  return closed ? { length, area: Math.abs(twice) / 2 } : { length };
}

/** Length and, for closed shapes, enclosed area. Exact for lines, circles, arcs and polylines;
 * ellipse arcs and splines use their flattened outline (within 0.05 mm). */
export function measureGeom(g: Geom): { length: number; area?: number } | null {
  switch (g.type) {
    case "LINE":
      return { length: Math.hypot(g.end[0] - g.start[0], g.end[1] - g.start[1]) };
    case "CIRCLE":
      return { length: 2 * Math.PI * g.radius, area: Math.PI * g.radius * g.radius };
    case "ARC": {
      const sweep = ((((g.end_angle - g.start_angle) % 360) + 360) % 360 || 360) * (Math.PI / 180);
      return { length: g.radius * sweep };
    }
    case "ELLIPSE": {
      const [path] = toPaths(g);
      const full = Math.abs(g.end_param - g.start_param) >= 2 * Math.PI - 1e-9;
      const a = Math.hypot(...g.major);
      return full ? { length: pathLength([path]), area: Math.PI * a * a * g.ratio } : { length: pathLength([path]) };
    }
    case "LWPOLYLINE":
    case "HATCH":
      return bulgeMeasure(g.type === "HATCH" ? g.boundary : g.points, g.type === "HATCH" || g.closed);
    case "SPLINE":
    case "LEADER":
      return { length: pathLength(toPaths(g)) };
    default:
      return null; // text, points, dimensions, block references
  }
}

export const fmt = (v: number) => (Math.round(v * 1000) / 1000).toLocaleString("ko-KR", { maximumFractionDigits: 3 });
