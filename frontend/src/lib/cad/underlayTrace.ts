// Pure-TS helpers for 2D Overlay PDF Auto-Trace & Automatic Dimensioning.
import type { TraceResponse } from "../underlayApi";
import { SCALE_LIMITS, toModel, type UnderlayGeom } from "./underlay";
import type { Imported } from "./underlayVec";
import type { Pt } from "./view";

export type BuildTraceOpts = {
  traceLayer: string;
  dimLayer: string;
  autoDimension: boolean;
  maxEdgeDims?: number;
};

export type BuiltTrace = {
  items: Imported;
  geomCount: number;
  dimCount: number;
};

/** Compute underlay scale factor so that the traced bounding box width equals referenceWidthMm. */
export function scaleForReferenceWidth(
  tracedBboxMm: [number, number, number, number] | null,
  referenceWidthMm: number,
): number | string {
  if (!Number.isFinite(referenceWidthMm) || referenceWidthMm <= 0) {
    return "기준 치수는 0보다 큰 숫자(mm)여야 합니다";
  }
  if (!tracedBboxMm) return "도형 외곽을 찾지 못했습니다. '길이로 축척'으로 두 점을 직접 지정하세요";
  const rawW = tracedBboxMm[2] - tracedBboxMm[0];
  if (rawW <= 1e-6) return "추출된 도형의 가로 폭이 너무 작습니다";
  const nextScale = referenceWidthMm / rawW;
  if (nextScale < SCALE_LIMITS.min || nextScale > SCALE_LIMITS.max) {
    return `축척(${nextScale.toFixed(4)})이 허용 범위(${SCALE_LIMITS.min}~${SCALE_LIMITS.max})를 벗어납니다`;
  }
  return nextScale;
}

/** Convert backend trace primitives (in page mm) to model-space CAD entities + automatic dimensions. */
export function buildTracedEntities(
  res: TraceResponse,
  g: UnderlayGeom,
  opts: BuildTraceOpts,
): BuiltTrace {
  const items: Imported = [];
  const m = (p: Pt): Pt => toModel(g, p);
  const segs: [Pt, Pt][] = [];
  const circlesModel: { c: Pt; r: number }[] = [];
  const arcsModel: { c: Pt; r: number; a0: number; a1: number }[] = [];

  for (const [x1, y1, x2, y2] of res.lines) {
    const a = m([x1, y1]);
    const b = m([x2, y2]);
    items.push({ geom: { type: "LINE", start: a, end: b }, layer: opts.traceLayer });
    segs.push([a, b]);
  }

  for (const pl of res.polylines) {
    if (pl.points.length < 2) continue;
    const mp = pl.points.map((p) => m([p[0], p[1]]));
    items.push({
      geom: {
        type: "LWPOLYLINE",
        points: mp.map((q): [number, number, number] => [q[0], q[1], 0]),
        closed: pl.closed,
      },
      layer: opts.traceLayer,
    });
    for (let i = 0; i + 1 < mp.length; i++) segs.push([mp[i], mp[i + 1]]);
    if (pl.closed && mp.length >= 3) segs.push([mp[mp.length - 1], mp[0]]);
  }

  for (const c of res.circles) {
    const mc = m([c.center[0], c.center[1]]);
    const mr = c.radius * g.scale;
    if (mr <= 1e-6) continue;
    items.push({ geom: { type: "CIRCLE", center: mc, radius: mr }, layer: opts.traceLayer });
    circlesModel.push({ c: mc, r: mr });
  }

  for (const a of res.arcs) {
    const mc = m([a.center[0], a.center[1]]);
    const mr = a.radius * g.scale;
    if (mr <= 1e-6) continue;
    const a0 = (a.start_angle + g.rotation + 360) % 360;
    const a1 = (a.end_angle + g.rotation + 360) % 360;
    items.push({
      geom: { type: "ARC", center: mc, radius: mr, start_angle: a0, end_angle: a1 },
      layer: opts.traceLayer,
    });
    arcsModel.push({ c: mc, r: mr, a0, a1 });
  }

  const geomCount = items.length;
  let dimCount = 0;

  if (opts.autoDimension && geomCount > 0) {
    // 1. Overall bounding box width & height linear dimensions
    const xs: number[] = [];
    const ys: number[] = [];
    for (const [p1, p2] of segs) {
      xs.push(p1[0], p2[0]);
      ys.push(p1[1], p2[1]);
    }
    for (const c of circlesModel) {
      xs.push(c.c[0] - c.r, c.c[0] + c.r);
      ys.push(c.c[1] - c.r, c.c[1] + c.r);
    }
    for (const a of arcsModel) {
      xs.push(a.c[0] - a.r, a.c[0] + a.r);
      ys.push(a.c[1] - a.r, a.c[1] + a.r);
    }

    if (xs.length >= 2) {
      const xmin = Math.min(...xs);
      const xmax = Math.max(...xs);
      const ymin = Math.min(...ys);
      const ymax = Math.max(...ys);
      const w = xmax - xmin;
      const h = ymax - ymin;
      const offset = Math.max(8, Math.min(40, Math.max(w, h) * 0.08));

      if (w > 1e-3) {
        items.push({
          geom: {
            type: "DIM_LINEAR",
            p1: [xmin, ymin],
            p2: [xmax, ymin],
            base: [0.5 * (xmin + xmax), ymin - offset],
            angle: 0,
          },
          layer: opts.dimLayer,
        });
        dimCount++;
      }
      if (h > 1e-3) {
        items.push({
          geom: {
            type: "DIM_LINEAR",
            p1: [xmax, ymin],
            p2: [xmax, ymax],
            base: [xmax + offset, 0.5 * (ymin + ymax)],
            angle: 90,
          },
          layer: opts.dimLayer,
        });
        dimCount++;
      }

      // 2. Major orthogonal sub-edges (deduplicated & capped)
      const maxEdge = opts.maxEdgeDims ?? 8;
      const seenKey = new Set<string>([`H:${w.toFixed(1)}`, `V:${h.toFixed(1)}`]);
      const cand = segs
        .map(([p1, p2]) => {
          const dx = Math.abs(p2[0] - p1[0]);
          const dy = Math.abs(p2[1] - p1[1]);
          const len = Math.hypot(dx, dy);
          const isH = dy <= Math.max(0.5, len * 0.02);
          const isV = dx <= Math.max(0.5, len * 0.02);
          return { p1, p2, len, isH, isV };
        })
        .filter((s) => (s.isH || s.isV) && s.len >= Math.max(2.0, Math.max(w, h) * 0.08))
        .sort((a, b) => b.len - a.len);

      let addedEdges = 0;
      for (const s of cand) {
        if (addedEdges >= maxEdge) break;
        const key = `${s.isH ? "H" : "V"}:${s.len.toFixed(1)}`;
        if (seenKey.has(key)) continue;
        seenKey.add(key);
        const mx = 0.5 * (s.p1[0] + s.p2[0]);
        const my = 0.5 * (s.p1[1] + s.p2[1]);
        const subOff = offset * 0.65;
        items.push({
          geom: {
            type: "DIM_LINEAR",
            p1: s.p1,
            p2: s.p2,
            base: s.isH ? [mx, my + subOff] : [mx - subOff, my],
            angle: s.isH ? 0 : 90,
          },
          layer: opts.dimLayer,
        });
        dimCount++;
        addedEdges++;
      }
    }

    // 3. Diameter dimensions for circles
    for (const c of circlesModel.slice(0, 12)) {
      items.push({
        geom: { type: "DIM_DIAMETER", center: c.c, radius: c.r, angle: 45 },
        layer: opts.dimLayer,
      });
      dimCount++;
    }

    // 4. Radius dimensions for arcs
    for (const a of arcsModel.slice(0, 8)) {
      const midAng = 0.5 * (a.a0 + a.a1);
      items.push({
        geom: { type: "DIM_RADIUS", center: a.c, radius: a.r, angle: midAng },
        layer: opts.dimLayer,
      });
      dimCount++;
    }
  }

  return { items, geomCount, dimCount };
}
