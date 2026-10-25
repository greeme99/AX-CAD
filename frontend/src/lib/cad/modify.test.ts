import { describe, expect, it } from "vitest";
import type { Prim } from "./curves";
import { offsetGeom } from "./edit";
import type { Geom } from "./geom";
import { breakAt, chamferLines, explodeGeom, extendPolyline, filletCurves, joinGeoms, polySegs, segPoint, stretchGeom, trimPolyline } from "./modify";

type Poly = Extract<Geom, { type: "LWPOLYLINE" }>;
const line = (x0: number, y0: number, x1: number, y1: number): Extract<Geom, { type: "LINE" }> => ({ type: "LINE", start: [x0, y0], end: [x1, y1] });
const seg = (x0: number, y0: number, x1: number, y1: number): Prim => ({ kind: "SEG", a: [x0, y0], b: [x1, y1], h: "x" });
const close = (a: number[], b: number[], tol = 1e-6) => a.every((v, i) => Math.abs(v - b[i]) < tol);
// a 20 x 10 slot: two straight sides and two half-circle ends (bulge 1 = 180 deg CCW)
const slot: Poly = { type: "LWPOLYLINE", closed: true, points: [[0, 0, 0], [20, 0, 1], [20, 10, 0], [0, 10, 1]] };

describe("explode and join", () => {
  it("explodes a polyline into lines and arcs, and joins them back", () => {
    const parts = explodeGeom(slot) as Geom[];
    expect(parts.map((g) => g.type)).toEqual(["LINE", "ARC", "LINE", "ARC"]);
    const arc = parts[1] as Extract<Geom, { type: "ARC" }>;
    expect(arc.radius).toBeCloseTo(5);
    expect(close(arc.center, [20, 5])).toBe(true);
    const back = joinGeoms([parts[2], parts[0], parts[3], parts[1]]) as Poly; // any order
    expect(back.type).toBe("LWPOLYLINE");
    expect(back.closed).toBe(true);
    expect(back.points.filter((p) => Math.abs(Math.abs(p[2]) - 1) < 1e-9)).toHaveLength(2);
  });

  it("refuses pieces that do not touch", () => {
    expect(joinGeoms([line(0, 0, 10, 0), line(20, 0, 30, 0)])).toBe("끝점이 맞닿지 않는 객체가 있습니다");
    expect(explodeGeom(line(0, 0, 1, 0))).toBe("폴리라인만 분해할 수 있습니다");
  });
});

describe("polyline trim, break and extend", () => {
  const zig: Poly = { type: "LWPOLYLINE", closed: false, points: [[0, 0, 0], [10, 0, 0], [10, 10, 0]] };

  it("trims the picked span between two cutters", () => {
    const out = trimPolyline(zig, [10, 5], [seg(0, 2, 20, 2), seg(0, 8, 20, 8)]) as Geom[];
    expect(out).toHaveLength(2);
    const [a, b] = out as [Poly, Geom];
    expect(close(a.points.at(-1)!.slice(0, 2), [10, 2])).toBe(true);
    expect(close((b.type === "LINE" ? b.start : (b as Poly).points[0]).slice(0, 2), [10, 8])).toBe(true);
  });

  it("opens a closed polyline when trimming it", () => {
    const sq: Poly = { type: "LWPOLYLINE", closed: true, points: [[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]] };
    const [rest] = trimPolyline(sq, [5, 0], [seg(2, -1, 2, 1), seg(8, -1, 8, 1)]) as Poly[];
    expect(rest.closed).toBe(false);
    expect(close(rest.points[0].slice(0, 2), [8, 0])).toBe(true);
    expect(close(rest.points.at(-1)!.slice(0, 2), [2, 0])).toBe(true);
  });

  it("breaks lines, circles and polylines", () => {
    expect(breakAt(line(0, 0, 10, 0), [3, 0], [6, 0])).toEqual([line(0, 0, 3, 0), line(6, 0, 10, 0)]);
    expect(breakAt(line(0, 0, 10, 0), [4, 0], [4, 0])).toEqual([line(0, 0, 4, 0), line(4, 0, 10, 0)]); // split
    const [arc] = breakAt({ type: "CIRCLE", center: [0, 0], radius: 5 }, [5, 0], [0, 5]) as Extract<Geom, { type: "ARC" }>[];
    expect([arc.start_angle, arc.end_angle]).toEqual([90, 0]); // the CCW part from 1st to 2nd point is gone
    const parts = breakAt(zig, [10, 4], [10, 4]) as Geom[];
    expect(parts).toHaveLength(2);
  });

  it("extends the end segment, straight or curved", () => {
    const ext = extendPolyline(zig, [10, 9], [seg(-5, 15, 25, 15)], 100) as Poly;
    expect(ext.points.at(-1)!.slice(0, 2)).toEqual([10, 15]);
    // a quarter arc (bulge tan(22.5deg)) from (0,0) to (5,5) about (0,5), extended to the y = 10 line
    const arcPl: Poly = { type: "LWPOLYLINE", closed: false, points: [[-5, 0, 0], [0, 0, Math.tan(Math.PI / 8)], [5, 5, 0]] };
    const e2 = extendPolyline(arcPl, [5, 5], [seg(-20, 10, 20, 10)], 100) as Poly;
    expect(close(e2.points.at(-1)!.slice(0, 2), [0, 10])).toBe(true);
    expect(e2.points[1][2]).toBeCloseTo(1); // now a half circle
  });
});

describe("stretch, chamfer, fillet, curved offset", () => {
  it("stretches only what is inside the window", () => {
    expect(stretchGeom(line(0, 0, 10, 0), [8, -1], [12, 1], 5, 0)).toEqual(line(0, 0, 15, 0));
    expect(stretchGeom(line(0, 0, 10, 0), [20, 20], [30, 30], 5, 0)).toBeNull();
    const moved = stretchGeom(slot, [15, -1], [30, 11], 10, 0) as Poly;
    expect(moved.points.map((p) => p[0])).toEqual([0, 30, 30, 0]); // the right end moved, bulges kept
    expect(moved.points[1][2]).toBe(1);
  });

  it("chamfers a corner with two distances", () => {
    const r = chamferLines(line(0, 0, 10, 0), [8, 0], line(0, 0, 0, 10), [0, 8], 2, 3);
    if (typeof r === "string") throw new Error(r);
    expect(r.l1).toEqual(line(2, 0, 10, 0)); // drawing direction kept
    expect(r.l2).toEqual(line(0, 3, 0, 10));
    expect(r.edge).toEqual(line(2, 0, 0, 3));
  });

  it("fillets a line with an arc (tangent at both ends)", () => {
    const arc: Geom = { type: "ARC", center: [0, 10], radius: 5, start_angle: 180, end_angle: 0 }; // lower half
    expect(filletCurves(line(-20, 0, 20, 0), [15, 0], arc, [4, 7], 2)).toBe("이 반지름으로는 두 객체를 둥글게 이을 수 없습니다"); // gap 5 > 2r
    const r = filletCurves(line(-20, 0, 20, 0), [15, 0], arc, [4, 7], 3);
    if (typeof r === "string") throw new Error(r);
    const f = r.arc as Extract<Geom, { type: "ARC" }>;
    expect(f.center[1]).toBeCloseTo(3); // r above the line
    expect(Math.hypot(f.center[0], f.center[1] - 10)).toBeCloseTo(8); // and 5 + r from the arc centre
    const l = r.g1 as Extract<Geom, { type: "LINE" }>;
    expect(Math.min(l.start[0], l.end[0])).toBeCloseTo(f.center[0]); // the picked (right) part now starts at the tangent point
  });

  it("offsets a slot outwards and inwards", () => {
    const out = offsetGeom(slot, 2, [10, -5]) as Poly;
    const segs = polySegs(out);
    expect(close(segPoint(segs[1], 0.5), [27, 5])).toBe(true); // right end: radius 5 + 2
    const inner = offsetGeom(slot, 2, [10, 5]) as Poly;
    expect(close(segPoint(polySegs(inner)[1], 0.5), [23, 5])).toBe(true);
    expect(offsetGeom(slot, 6, [10, 5])).toBe("간격이 호의 반지름보다 큽니다");
  });
});
