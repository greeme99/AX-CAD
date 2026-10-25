// Box selection (AutoCAD/FreeCAD style): a window (dragged left to right) takes entities lying
// fully inside it, a crossing window (right to left) also takes those it touches.
import { hitPaths, type HasGeom } from "./geom";
import type { Pt } from "./view";

type Boxable = HasGeom & { handle: string; layer: string; point?: [number, number] };

const inside = (p: Pt, lo: Pt, hi: Pt) => p[0] >= lo[0] && p[0] <= hi[0] && p[1] >= lo[1] && p[1] <= hi[1];

/** Does segment a-b cross the box (Liang-Barsky clip)? */
function segHitsBox(a: Pt, b: Pt, lo: Pt, hi: Pt): boolean {
  let [t0, t1] = [0, 1];
  const d: Pt = [b[0] - a[0], b[1] - a[1]];
  const edges: [number, number][] = [[-d[0], a[0] - lo[0]], [d[0], hi[0] - a[0]], [-d[1], a[1] - lo[1]], [d[1], hi[1] - a[1]]];
  for (const [p, q] of edges) {
    if (Math.abs(p) < 1e-15) {
      if (q < 0) return false;
    } else {
      const t = q / p;
      if (p < 0) t0 = Math.max(t0, t);
      else t1 = Math.min(t1, t);
      if (t0 > t1) return false;
    }
  }
  return true;
}

/** Handles of the entities picked by a box from a to b. */
export function boxSelect(entities: Boxable[], a: Pt, b: Pt, crossing: boolean, hidden: Set<string>): string[] {
  const lo: Pt = [Math.min(a[0], b[0]), Math.min(a[1], b[1])];
  const hi: Pt = [Math.max(a[0], b[0]), Math.max(a[1], b[1])];
  const out = new Set<string>();
  for (const e of entities) {
    if (hidden.has(e.layer)) continue;
    const paths = hitPaths(e).filter((p) => p.length);
    const pts = paths.flat().concat(e.point ? [e.point] : e.text ? [e.text.insert] : []);
    if (!pts.length) continue;
    const ok = crossing
      ? pts.some((p) => inside(p, lo, hi)) || paths.some((p) => p.some((q, i) => i > 0 && segHitsBox(p[i - 1], q, lo, hi)))
      : pts.every((p) => inside(p, lo, hi));
    if (ok) out.add(e.handle);
  }
  // block parts share their INSERT's handle: a window must hold every part
  if (!crossing)
    for (const e of entities) if (out.has(e.handle) && !hidden.has(e.layer) && !hitPaths(e).flat().every((p) => inside(p, lo, hi))) out.delete(e.handle);
  return [...out];
}
