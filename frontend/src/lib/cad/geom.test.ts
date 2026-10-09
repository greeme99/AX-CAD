import { describe, expect, it } from "vitest";
import { arcFrom3Points, hitTest, toPaths, translate } from "./geom";

describe("toPaths", () => {
  it("LINE keeps exact endpoints", () => {
    expect(toPaths({ type: "LINE", start: [1, 2], end: [3, 4] })).toEqual([[[1, 2], [3, 4]]]);
  });
  it("CIRCLE points lie on the radius and close", () => {
    const [p] = toPaths({ type: "CIRCLE", center: [10, 5], radius: 20 });
    expect(p.length).toBeGreaterThan(8);
    for (const [x, y] of p) expect(Math.abs(Math.hypot(x - 10, y - 5) - 20)).toBeLessThan(1e-9);
    expect(p[p.length - 1]).toEqual(p[0]);
  });
  it("ARC runs CCW from start to end, wrapping 360", () => {
    const [p] = toPaths({ type: "ARC", center: [0, 0], radius: 10, start_angle: 270, end_angle: 90 });
    expect(p[0][0]).toBeCloseTo(0);
    expect(p[0][1]).toBeCloseTo(-10);
    const mid = p[Math.floor(p.length / 2)];
    expect(Math.hypot(mid[0] - 10, mid[1])).toBeLessThan(0.5); // passes near (10,0)
  });
  it("bulge=1 gives a half circle through the expected midpoint", () => {
    const [p] = toPaths({ type: "LWPOLYLINE", points: [[0, 0, 1], [2, 0, 0]], closed: false });
    expect(p[0]).toEqual([0, 0]);
    expect(p[p.length - 1]).toEqual([2, 0]);
    for (const [x, y] of p) expect(Math.abs(Math.hypot(x - 1, y) - 1)).toBeLessThan(1e-9);
    expect(Math.min(...p.map(([, y]) => y))).toBeLessThan(-0.94); // bulges below the chord (right of travel)
  });
  it("closed polyline returns to its first vertex; TEXT has no paths", () => {
    const [p] = toPaths({ type: "LWPOLYLINE", points: [[0, 0, 0], [4, 0, 0], [4, 3, 0]], closed: true });
    expect(p).toEqual([[0, 0], [4, 0], [4, 3], [0, 0]]);
    expect(toPaths({ type: "TEXT", insert: [0, 0], height: 2.5, value: "x", rotation: 0 })).toEqual([]);
  });
});

describe("arcFrom3Points", () => {
  it("CCW points give start=p1, end=p3", () => {
    const a = arcFrom3Points([10, 0], [0, 10], [-10, 0]);
    expect(a).toMatchObject({ type: "ARC", radius: expect.closeTo(10, 9) });
    if (a?.type !== "ARC") throw new Error();
    expect(a.center[0]).toBeCloseTo(0);
    expect(a.center[1]).toBeCloseTo(0);
    expect(a.start_angle).toBeCloseTo(0);
    expect(a.end_angle).toBeCloseTo(180);
  });
  it("CW points swap start/end", () => {
    const a = arcFrom3Points([-10, 0], [0, 10], [10, 0]);
    if (a?.type !== "ARC") throw new Error();
    expect(a.start_angle).toBeCloseTo(0);
    expect(a.end_angle).toBeCloseTo(180);
  });
  it("collinear -> null", () => {
    expect(arcFrom3Points([0, 0], [1, 1], [2, 2])).toBeNull();
  });
});

describe("misc", () => {
  it("hitTest uses point-to-segment distance", () => {
    const paths: [number, number][][] = [[[0, 0], [10, 0]]];
    expect(hitTest(paths, [5, 3], 3.1)).toBe(true);
    expect(hitTest(paths, [13, 0], 2)).toBe(false);
  });
  it("translate moves every vertex", () => {
    expect(translate({ type: "LINE", start: [0, 0], end: [1, 1] }, 2, 3)).toEqual({ type: "LINE", start: [2, 3], end: [3, 4] });
  });
});
