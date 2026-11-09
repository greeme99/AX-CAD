// 2D underlay U3/U4: a PDF/DWFx page placed in the drawing. The placement is one transform,
// model = R(rotation) * scale * page + insert, page in mm from its lower-left corner; every
// edit (move, rotate, scale, align by two points, clip) changes this record only — the file and
// its page are never touched. Units: mm, degrees, WCS.
import type { Pt } from "./view";

export type UClip = { kind: "rect" | "poly"; points: Pt[] }; // page mm, unscaled and unrotated
export type UFlags = { on: boolean; monochrome: boolean; adjust: boolean; inside: boolean };
export type UnderlayGeom = {
  format: "pdf" | "dwf"; // dwf = a DWFx file
  sha256: string;
  name: string;
  page: number; // 0-based
  insert: Pt;
  scale: number; // model mm per page mm
  rotation: number;
  flags: UFlags;
  contrast: number; // 0..100
  fade: number; // 0..80
  clip: UClip | null;
};
/** What the server reports about an underlay entity (see core/dxf/underlay.py), plus the file it links to. */
export type UnderlayRec = UnderlayGeom & {
  linked: boolean;
  uniform: boolean;
  file_id?: string | null;
  size?: [number, number] | null; // the page's width and height in mm
  page_count?: number;
};

export const SCALE_LIMITS = { min: 1e-6, max: 1e6 };
export const rad = (deg: number) => (deg * Math.PI) / 180;

export function toModel(g: UnderlayGeom, p: Pt): Pt {
  const [c, s] = [Math.cos(rad(g.rotation)), Math.sin(rad(g.rotation))];
  const [x, y] = [p[0] * g.scale, p[1] * g.scale];
  return [x * c - y * s + g.insert[0], x * s + y * c + g.insert[1]];
}

export function toPage(g: UnderlayGeom, q: Pt): Pt {
  const [c, s] = [Math.cos(rad(-g.rotation)), Math.sin(rad(-g.rotation))];
  const [x, y] = [q[0] - g.insert[0], q[1] - g.insert[1]];
  return [(x * c - y * s) / g.scale, (x * s + y * c) / g.scale];
}

/** The clip polygon in page mm (a rectangle is its four corners), or null when not clipped. */
export function clipPolygon(g: UnderlayGeom): Pt[] | null {
  const c = g.clip;
  if (!c) return null;
  if (c.kind === "poly") return c.points;
  const [a, b] = c.points;
  const [x0, x1] = [Math.min(a[0], b[0]), Math.max(a[0], b[0])];
  const [y0, y1] = [Math.min(a[1], b[1]), Math.max(a[1], b[1])];
  return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
}

/** Even-odd point in polygon. */
export function inPolygon(poly: Pt[], p: Pt): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [a, b] = [poly[i], poly[j]];
    if (a[1] > p[1] !== b[1] > p[1] && p[0] < ((b[0] - a[0]) * (p[1] - a[1])) / (b[1] - a[1]) + a[0]) inside = !inside;
  }
  return inside;
}

/** Whether a page point is shown: inside the page, and inside (or outside) the clip. */
export function visibleAt(g: UnderlayGeom, size: [number, number] | null | undefined, p: Pt): boolean {
  if (size && (p[0] < 0 || p[1] < 0 || p[0] > size[0] || p[1] > size[1])) return false;
  const poly = clipPolygon(g);
  if (!poly) return true;
  return inPolygon(poly, p) === g.flags.inside;
}

/** The selectable frame in model mm: the page border, and the clip outline when clipped. */
export function outline(g: UnderlayGeom, size: [number, number]): Pt[][] {
  const [w, h] = size;
  const frame: Pt[] = [[0, 0], [w, 0], [w, h], [0, h], [0, 0]];
  const out = [frame.map((p) => toModel(g, p))];
  const poly = clipPolygon(g);
  if (poly) out.push([...poly, poly[0]].map((p) => toModel(g, p)));
  return out;
}

export function bounds(g: UnderlayGeom, size: [number, number]): { min: Pt; max: Pt } {
  const pts = outline(g, size)[0];
  const xs = pts.map((p) => p[0]);
  const ys = pts.map((p) => p[1]);
  return { min: [Math.min(...xs), Math.min(...ys)], max: [Math.max(...xs), Math.max(...ys)] };
}

// --- edits: each returns a new record ---------------------------------------------------------

export const moved = (g: UnderlayGeom, d: Pt): UnderlayGeom => ({ ...g, insert: [g.insert[0] + d[0], g.insert[1] + d[1]] });

export function rotatedAbout(g: UnderlayGeom, c: Pt, deg: number): UnderlayGeom {
  const [co, si] = [Math.cos(rad(deg)), Math.sin(rad(deg))];
  const [x, y] = [g.insert[0] - c[0], g.insert[1] - c[1]];
  return { ...g, insert: [c[0] + x * co - y * si, c[1] + x * si + y * co], rotation: normAngle(g.rotation + deg) };
}

export function scaledAbout(g: UnderlayGeom, c: Pt, k: number): UnderlayGeom | string {
  const scale = g.scale * k;
  if (!(k > 0) || !Number.isFinite(scale) || scale < SCALE_LIMITS.min || scale > SCALE_LIMITS.max) return `축척은 ${SCALE_LIMITS.min}~${SCALE_LIMITS.max} 사이여야 합니다`;
  return { ...g, insert: [c[0] + (g.insert[0] - c[0]) * k, c[1] + (g.insert[1] - c[1]) * k], scale };
}

export function normAngle(deg: number): number {
  const a = ((deg % 360) + 360) % 360;
  return Math.round((a > 180 ? a - 360 : a) * 1e9) / 1e9 + 0; // + 0: never -0
}

/** Two-point alignment (AutoCAD ALIGN / FreeCAD's image scaling): the points a1, a2 as they are
 * now in the drawing (picked on the page) must land on b1, b2. `rotate` false keeps the angle and
 * only scales (a known length: pick its two ends on the page, then give the real length). */
export function aligned(g: UnderlayGeom, a1: Pt, a2: Pt, b1: Pt, b2: Pt, rotate = true): UnderlayGeom | string {
  const [da, db] = [Math.hypot(a2[0] - a1[0], a2[1] - a1[1]), Math.hypot(b2[0] - b1[0], b2[1] - b1[1])];
  if (!(da > 1e-9)) return "원본의 두 점이 같은 위치입니다";
  if (!(db > 1e-9)) return "대상의 두 점이 같은 위치입니다";
  const scaled = scaledAbout(g, a1, db / da);
  if (typeof scaled === "string") return scaled;
  const turn = rotate ? normAngle((Math.atan2(b2[1] - b1[1], b2[0] - b1[0]) - Math.atan2(a2[1] - a1[1], a2[0] - a1[0])) * (180 / Math.PI)) : 0;
  return moved(rotatedAbout(scaled, a1, turn), [b1[0] - a1[0], b1[1] - a1[1]]);
}

/** Scale that makes the picked page length `measured` (model mm now) equal `real` mm. */
export const scaleFactorFor = (measured: number, real: number): number | string => (measured > 1e-9 && real > 0 ? real / measured : "길이는 0보다 커야 합니다");

export const defaultGeom = (file: { format: "pdf" | "dwf"; sha256: string; name: string }, page: number, insert: Pt): UnderlayGeom => ({
  format: file.format,
  sha256: file.sha256,
  name: file.name,
  page,
  insert,
  scale: 1,
  rotation: 0,
  flags: { on: true, monochrome: false, adjust: false, inside: true },
  contrast: 50,
  fade: 0,
  clip: null,
});

/** The edit request item for the server (see backend UnderlayGeom): only its own fields. */
export const toWire = (g: UnderlayGeom): UnderlayGeom => ({
  format: g.format,
  sha256: g.sha256,
  name: g.name,
  page: g.page,
  insert: [g.insert[0], g.insert[1]],
  scale: g.scale,
  rotation: g.rotation,
  flags: { ...g.flags },
  contrast: g.contrast,
  fade: g.fade,
  clip: g.clip ? { kind: g.clip.kind, points: g.clip.points.map((p): Pt => [p[0], p[1]]) } : null,
});

export const sameGeom = (a: UnderlayGeom, b: UnderlayGeom) => JSON.stringify(toWire(a)) === JSON.stringify(toWire(b));
