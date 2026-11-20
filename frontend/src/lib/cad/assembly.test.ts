import { describe, expect, it } from "vitest";
import { applyExplodeSteps, MATE_LABEL, matesFor, placementMatrix, rotation, transformBBox } from "./assembly";

const apply = (m: number[], p: number[]) => [0, 1, 2].map((k) => m[k] * p[0] + m[4 + k] * p[1] + m[8 + k] * p[2] + m[12 + k]);
const close = (a: number[], b: number[]) => a.forEach((v, i) => expect(v).toBeCloseTo(b[i], 12));

describe("assembly placement & A1~A4 helpers (same convention as core/geometry/assembly.py)", () => {
  it("Rz(90) turns x into y; X first then Z", () => {
    close(apply(placementMatrix({ t: [0, 0, 0], r: [0, 0, 90] }), [1, 0, 0]), [0, 1, 0]);
    close(apply(placementMatrix({ t: [0, 0, 0], r: [90, 0, 90] }), [0, 1, 0]), [0, 0, 1]);
  });
  it("translation after rotation", () => {
    close(apply(placementMatrix({ t: [10, 20, 30], r: [0, 0, 90] }), [1, 0, 0]), [10, 21, 30]);
  });
  it("rotation is orthonormal", () => {
    const m = rotation([30, -20, 75]);
    for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) expect(m[i][0] * m[j][0] + m[i][1] * m[j][1] + m[i][2] * m[j][2]).toBeCloseTo(i === j ? 1 : 0, 12);
  });
  it("box of a placed box", () => {
    const b = transformBBox({ min: [0, 0, 0], max: [40, 20, 10] }, placementMatrix({ t: [100, 0, 0], r: [0, 0, 90] }));
    close(b.min, [80, 0, 0]);
    close(b.max, [100, 40, 10]);
  });
  it("supports all 12 A1 mate types across plane, cylinder, cone, and sphere refs", () => {
    const plane = { kind: "PLANE", mid: [0, 0, 0] as [number, number, number] };
    const cyl = { kind: "CYLINDER", mid: [0, 0, 0] as [number, number, number] };
    const sphere = { kind: "SPHERE", mid: [0, 0, 0] as [number, number, number] };
    expect(Object.keys(MATE_LABEL).length).toBe(12);
    expect(matesFor(plane, plane)).toContain("DISTANCE");
    expect(matesFor(plane, plane)).toContain("DISTANCE_LIMIT");
    expect(matesFor(plane, plane)).toContain("ANGLE_LIMIT");
    expect(matesFor(cyl, cyl)[0]).toBe("CONCENTRIC");
    expect(matesFor(plane, cyl)).toContain("TANGENT");
    expect(matesFor(sphere, sphere)).toContain("TANGENT");
    expect(matesFor(null, plane)).toEqual([]);
  });
  it("applies exploded-view steps scaled by factor in [0, 1] without mutating rotation", () => {
    const base = { t: [10, 20, 30] as [number, number, number], r: [0, 45, 90] as [number, number, number] };
    const steps = [{ component_ids: [7], direction: [0, 0, 2] as [number, number, number], distance_mm: 50 }];
    expect(applyExplodeSteps(base, 7, steps, 0)).toEqual(base);
    const half = applyExplodeSteps(base, 7, steps, 0.5);
    close(half.t, [10, 20, 55]);
    expect(half.r).toEqual(base.r);
  });
});
