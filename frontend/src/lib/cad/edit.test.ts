import { describe, expect, it } from "vitest";
import { toPrims } from "./curves";
import { extendAt, filletLines, mirrorGeom, offsetGeom, polygonGeom, rectGeom, rotateGeom, scaleGeom, trimAt } from "./edit";
import type { Geom } from "./geom";
import type { Pt } from "./view";

type Line = Extract<Geom, { type: "LINE" }>;
const L = (a: Pt, b: Pt): Line => ({ type: "LINE", start: a, end: b });
const near = (p: Pt, q: Pt) => {
  expect(p[0]).toBeCloseTo(q[0], 9);
  expect(p[1]).toBeCloseTo(q[1], 9);
};
const prims = (...gs: Geom[]) => gs.flatMap((g, i) => toPrims(`b${i}`, g, []));

describe("transforms", () => {
  it("rotate: points, arc angles, text rotation", () => {
    const r = rotateGeom(L([1, 0], [2, 0]), [0, 0], 90) as Line;
    near(r.start, [0, 1]);
    near(r.end, [0, 2]);
    const a = rotateGeom({ type: "ARC", center: [1, 0], radius: 1, start_angle: 0, end_angle: 90 }, [0, 0], 90);
    expect(a).toMatchObject({ start_angle: 90, end_angle: 180 });
    expect(rotateGeom({ type: "TEXT", insert: [0, 0], height: 2.5, value: "A", rotation: 350 }, [0, 0], 20)).toMatchObject({ rotation: 10 });
  });
  it("scale: points and sizes about the base point", () => {
    const c = scaleGeom({ type: "CIRCLE", center: [2, 2], radius: 1 }, [1, 1], 2);
    expect(c).toMatchObject({ center: [3, 3], radius: 2 });
    expect(scaleGeom({ type: "TEXT", insert: [1, 1], height: 2.5, value: "A", rotation: 0 }, [1, 1], 2)).toMatchObject({ height: 5 });
  });
  it("mirror: arcs stay CCW, bulges flip, text stays readable", () => {
    // across the Y axis: arc 0..90 (first quadrant) becomes 90..180
    const a = mirrorGeom({ type: "ARC", center: [0, 0], radius: 1, start_angle: 0, end_angle: 90 }, [0, 0], [0, 1]);
    expect(a).toMatchObject({ start_angle: 90, end_angle: 180 });
    const p = mirrorGeom({ type: "LWPOLYLINE", points: [[1, 0, 0.5], [2, 0, 0]], closed: false }, [0, 0], [0, 1]);
    expect(p).toMatchObject({ points: [[-1, 0, -0.5], [-2, 0, -0]] });
    expect(mirrorGeom({ type: "TEXT", insert: [1, 0], height: 1, value: "A", rotation: 30 }, [0, 0], [0, 1])).toMatchObject({ insert: [-1, 0], rotation: 30 });
  });
  it("rectangle and polygon", () => {
    expect(rectGeom([0, 0], [10, 5])).toMatchObject({ closed: true, points: [[0, 0, 0], [10, 0, 0], [10, 5, 0], [0, 5, 0]] });
    expect(rectGeom([0, 0], [10, 0])).toBeNull();
    const hex = polygonGeom([0, 0], [10, 0], 6);
    expect(hex?.type === "LWPOLYLINE" && hex.points.length).toBe(6);
    if (hex?.type === "LWPOLYLINE") near([hex.points[1][0], hex.points[1][1]], [5, 5 * Math.sqrt(3)]);
  });
});

describe("offset", () => {
  it("line to the picked side, circle in and out", () => {
    expect(offsetGeom(L([0, 0], [10, 0]), 2, [5, 3])).toMatchObject({ start: [0, 2], end: [10, 2] });
    expect(offsetGeom(L([0, 0], [10, 0]), 2, [5, -3])).toMatchObject({ start: [0, -2], end: [10, -2] });
    expect(offsetGeom({ type: "CIRCLE", center: [0, 0], radius: 5 }, 2, [0, 1])).toMatchObject({ radius: 3 });
    expect(offsetGeom({ type: "CIRCLE", center: [0, 0], radius: 5 }, 6, [0, 1])).toMatch(/반지름/);
  });
  it("closed rectangle grows outward with mitred corners", () => {
    const sq: Geom = { type: "LWPOLYLINE", points: [[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]], closed: true };
    const o = offsetGeom(sq, 1, [5, -3]);
    expect(o).toMatchObject({ closed: true });
    if (typeof o !== "string" && o.type === "LWPOLYLINE") {
      near([o.points[0][0], o.points[0][1]], [-1, -1]);
      near([o.points[2][0], o.points[2][1]], [11, 11]);
    }
    const arcPoly: Geom = { type: "LWPOLYLINE", points: [[0, 0, 0.5], [10, 0, 0]], closed: false };
    expect(offsetGeom(arcPoly, 1, [0, 1])).toMatch(/곡선/);
  });
});

describe("fillet", () => {
  it("radius 0 makes a sharp corner from two lines that fall short", () => {
    const r = filletLines(L([0, 0], [8, 0]), [2, 0], L([10, 2], [10, 10]), [10, 8], 0);
    expect(typeof r).not.toBe("string");
    if (typeof r !== "string") {
      near(r.l1.end, [10, 0]);
      near(r.l2.start, [10, 0]);
      expect(r.arc).toBeNull();
    }
  });
  it("radius r: tangent points and a 90 degree arc", () => {
    const r = filletLines(L([0, 0], [10, 0]), [2, 0], L([10, 0], [10, 10]), [10, 8], 2);
    if (typeof r === "string") throw new Error(r);
    near(r.l1.end, [8, 0]);
    near(r.l2.start, [10, 2]);
    expect(r.arc).toMatchObject({ center: [8, 2], radius: 2 });
    if (r.arc?.type === "ARC") expect((((r.arc.end_angle - r.arc.start_angle) % 360) + 360) % 360).toBeCloseTo(90, 9);
  });
  it("rejects parallel lines and a radius that does not fit", () => {
    expect(filletLines(L([0, 0], [10, 0]), [1, 0], L([0, 1], [10, 1]), [1, 1], 1)).toMatch(/평행/);
    expect(filletLines(L([0, 0], [10, 0]), [2, 0], L([10, 0], [10, 10]), [10, 8], 20)).toMatch(/너무 커서/);
  });
});

describe("trim and extend", () => {
  it("trims the middle of a line between two cutters into two pieces", () => {
    const cutters = prims(L([3, -1], [3, 1]), L([7, -1], [7, 1]));
    const out = trimAt(L([0, 0], [10, 0]), [5, 0], cutters);
    expect(out).toEqual([L([0, 0], [3, 0]), L([7, 0], [10, 0])]);
    expect(trimAt(L([0, 0], [10, 0]), [9, 0], cutters)).toEqual([L([0, 0], [7, 0])]);
    expect(trimAt(L([0, 0], [10, 0]), [5, 0], [])).toMatch(/경계/);
  });
  it("trims a circle into the arc that remains", () => {
    const out = trimAt({ type: "CIRCLE", center: [0, 0], radius: 5 }, [5, 0.5], prims(L([-10, 0], [10, 0])));
    expect(out).toHaveLength(1);
    // cutting line through the centre meets at 0 and 180; the picked upper half (near 0+) goes
    expect((out as Geom[])[0]).toMatchObject({ type: "ARC", start_angle: 180, end_angle: 0 });
  });
  it("extends a line end to the next boundary and an arc end along its circle", () => {
    expect(extendAt(L([0, 0], [5, 0]), [4.5, 0], prims(L([8, -1], [8, 1])), 1000)).toEqual(L([0, 0], [8, 0]));
    expect(extendAt(L([0, 0], [5, 0]), [0.5, 0], prims(L([8, -1], [8, 1])), 1000)).toMatch(/경계/);
    const arc: Geom = { type: "ARC", center: [0, 0], radius: 5, start_angle: 0, end_angle: 45 };
    const ext = extendAt(arc, [3.5, 3.6], prims(L([0, 0], [0, 10])), 1000);
    expect(ext).toMatchObject({ start_angle: 0 });
    if (typeof ext !== "string" && ext.type === "ARC") expect(ext.end_angle).toBeCloseTo(90, 9);
  });
});
