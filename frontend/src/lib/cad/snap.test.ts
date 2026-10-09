import { describe, expect, it } from "vitest";
import type { Geom } from "./geom";
import { buildSnapIndex, effectiveGridStep, querySnap, type SnapEntity, type SnapKind } from "./snap";

const ent = (handle: string, geom: Geom, layer = "0"): SnapEntity => ({ handle, layer, geom, paths: [] });
const line = (h: string, a: [number, number], b: [number, number], layer = "0") => ent(h, { type: "LINE", start: a, end: b }, layer);
const ALL = new Set<SnapKind>(["END", "MID", "CEN"]);

describe("snap", () => {
  const idx = buildSnapIndex([line("L1", [0, 0], [10, 0])], 1);
  it("snaps to endpoint within radius, null outside", () => {
    expect(querySnap(idx, [0.3, 0.2], 1, ALL, 10)).toMatchObject({ x: 0, y: 0, kind: "END", handle: "L1" });
    expect(querySnap(idx, [5, 5], 1, ALL, 10)).toBeNull();
  });
  it("snaps to midpoint", () => {
    expect(querySnap(idx, [5.2, 0.1], 1, ALL, 10)).toMatchObject({ x: 5, y: 0, kind: "MID" });
  });
  it("END beats MID at equal distance", () => {
    const i = buildSnapIndex([line("A", [0, 0], [2, 0]), line("B", [1, 1], [1, 5])], 1); // A.mid (1,0), B.end (1,1)
    expect(querySnap(i, [1, 0.5], 1, ALL, 10)?.kind).toBe("END");
  });
  it("circle/arc centers and disabled kinds", () => {
    const i = buildSnapIndex([ent("C", { type: "CIRCLE", center: [3, 3], radius: 2 }), ent("R", { type: "ARC", center: [0, 0], radius: 1, start_angle: 0, end_angle: 90 })], 1);
    expect(querySnap(i, [3.1, 3], 1, ALL, 10)).toMatchObject({ kind: "CEN", x: 3, y: 3 });
    expect(querySnap(i, [3.1, 3], 1, new Set(["END"]), 10)).toBeNull();
    const m = querySnap(i, [0.71, 0.71], 0.2, ALL, 10);
    expect(m?.kind).toBe("MID");
  });
  it("grid snap rounds to the step, only when enabled", () => {
    expect(querySnap(idx, [23, 48], 5, new Set(["GRID"]), 10)).toMatchObject({ x: 20, y: 50, kind: "GRID" });
    expect(querySnap(idx, [23, 48], 5, ALL, 10)).toBeNull();
  });
  it("hidden layer excluded", () => {
    const i = buildSnapIndex([line("H", [0, 0], [10, 0], "HID")], 1, new Set(["HID"]));
    expect(querySnap(i, [0, 0], 1, ALL, 10)).toBeNull();
  });
  it("read-only paths snap to first/last point", () => {
    const i = buildSnapIndex([{ handle: "R", layer: "0", paths: [[[0, 0], [1, 1], [4, 4]]] }], 1);
    expect(querySnap(i, [4, 4.1], 1, ALL, 10)).toMatchObject({ kind: "END", x: 4, y: 4 });
    expect(querySnap(i, [1, 1], 0.5, ALL, 10)).toBeNull();
  });
  it("effectiveGridStep bumps x10 below 8 px", () => {
    expect(effectiveGridStep(10, 1)).toBe(10);
    expect(effectiveGridStep(10, 0.5)).toBe(100);
  });
  it("10,000 lines x 1,000 queries stays fast", () => {
    let seed = 1;
    const rnd = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 2 ** 32) * 1000;
    const ents = Array.from({ length: 10000 }, (_, i) => line(`L${i}`, [rnd(), rnd()], [rnd(), rnd()]));
    const big = buildSnapIndex(ents, Math.hypot(1000, 1000) / 200);
    const t0 = performance.now();
    for (let i = 0; i < 1000; i++) querySnap(big, [rnd(), rnd()], 5, ALL, 10);
    const total = performance.now() - t0;
    console.log(`snap query avg ${(total / 1000).toFixed(4)} ms`);
    expect(total).toBeLessThan(5000);
  });
});
