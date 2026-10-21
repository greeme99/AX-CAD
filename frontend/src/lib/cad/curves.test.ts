import { describe, expect, it } from "vitest";
import { closest, intersect, perpFoot, toPrims, type Prim } from "./curves";

const seg = (a: [number, number], b: [number, number]): Prim => ({ kind: "SEG", a, b, h: "s" });
const circle = (c: [number, number], r: number): Prim => toPrims("c", { type: "CIRCLE", center: c, radius: r }, [])[0];
const arc = (c: [number, number], r: number, s: number, e: number): Prim => toPrims("a", { type: "ARC", center: c, radius: r, start_angle: s, end_angle: e }, [])[0];
const near = (p: number[], q: number[]) => {
  expect(p[0]).toBeCloseTo(q[0], 9);
  expect(p[1]).toBeCloseTo(q[1], 9);
};

describe("curves", () => {
  it("segment x segment: crossing, touching end, parallel, apart", () => {
    near(intersect(seg([0, 0], [10, 10]), seg([0, 10], [10, 0]))[0], [5, 5]);
    near(intersect(seg([0, 0], [10, 0]), seg([10, 0], [10, 5]))[0], [10, 0]);
    expect(intersect(seg([0, 0], [10, 0]), seg([0, 1], [10, 1]))).toEqual([]);
    expect(intersect(seg([0, 0], [1, 0]), seg([5, -1], [5, 1]))).toEqual([]);
  });
  it("segment x circle and arc range", () => {
    const pts = intersect(seg([-10, 0], [10, 0]), circle([0, 0], 5));
    expect(pts).toHaveLength(2);
    expect(pts.map((p) => Math.round(p[0])).sort((a, b) => a - b)).toEqual([-5, 5]);
    // upper half arc only meets the vertical line at (0, 5)
    const top = intersect(seg([0, -10], [0, 10]), arc([0, 0], 5, 0, 180));
    expect(top).toHaveLength(1);
    near(top[0], [0, 5]);
    expect(intersect(seg([0, 0], [1, 0]), circle([0, 0], 5))).toEqual([]); // inside, segment too short
  });
  it("circle x circle: two points, tangent, concentric", () => {
    const pts = intersect(circle([0, 0], 5), circle([8, 0], 5));
    expect(pts).toHaveLength(2);
    near(pts[0], [4, 3]);
    near(intersect(circle([0, 0], 5), circle([10, 0], 5))[0], [5, 0]);
    expect(intersect(circle([0, 0], 5), circle([0, 0], 3))).toEqual([]);
  });
  it("closest and perpendicular foot", () => {
    near(closest(seg([0, 0], [10, 0]), [3, 4]), [3, 0]);
    near(closest(seg([0, 0], [10, 0]), [-3, 4]), [0, 0]);
    near(perpFoot(seg([0, 0], [10, 0]), [3, 4])!, [3, 0]);
    expect(perpFoot(seg([0, 0], [10, 0]), [12, 4])).toBeNull();
    near(perpFoot(circle([0, 0], 5), [10, 0])!, [5, 0]);
    expect(perpFoot(arc([0, 0], 5, 90, 180), [10, 0])).toBeNull();
    near(closest(arc([0, 0], 5, 90, 180), [10, 1]), [0, 5]); // off the arc: nearer end
  });
  it("polyline bulges become CCW arcs; read-only paths become segments", () => {
    // half circle from (0,0) to (10,0), bulge 1 = 180 deg CCW; -1 = clockwise
    const [ccw] = toPrims("p", { type: "LWPOLYLINE", points: [[0, 0, 1], [10, 0, 0]], closed: false }, []);
    const [cw] = toPrims("p", { type: "LWPOLYLINE", points: [[0, 0, -1], [10, 0, 0]], closed: false }, []);
    expect(ccw.kind === "ARC" && cw.kind === "ARC" && ccw.sweep > 0 && cw.sweep > 0).toBe(true);
    near(intersect(seg([5, -10], [5, 10]), ccw)[0], [5, -5]);
    near(intersect(seg([5, -10], [5, 10]), cw)[0], [5, 5]);
    expect(toPrims("r", undefined, [[[0, 0], [1, 0], [1, 1]]])).toHaveLength(2);
  });
});
