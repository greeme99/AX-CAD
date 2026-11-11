import { describe, expect, it } from "vitest";
import { applyCleanup, suggestCleanup, type CleanEnt } from "./cleanup";
import type { Geom } from "./geom";
import type { Pt } from "./view";

const line = (handle: string, start: Pt, end: Pt, layer = "0", style?: string): CleanEnt => ({ handle, layer, style, geom: { type: "LINE", start, end } });

describe("drawing clean-up suggestions (B-4)", () => {
  it("joins ends a hair apart onto the point most ends share; touching ends are left alone", () => {
    const ents = [line("A", [0, 0], [10, 0]), line("B", [10.05, 0.02], [10, 10]), line("C", [10, 10], [0, 10])];
    const { items } = suggestCleanup(ents, 0.1);
    expect(items).toHaveLength(1); // B-C already meet at (10,10)
    const s = items[0];
    expect(s.kind).toBe("GAP");
    expect(s.summary).toMatch(/^2개 객체의 끝점 간격/);
    expect(s.handles.sort()).toEqual(["A", "B"]);
    const { result, applied } = applyCleanup(ents, items);
    expect(applied).toBe(1);
    expect(result.deleted).toEqual([]);
    expect(result.updated).toHaveLength(1);
    // a tie (one end each): the first one found stays, the other moves onto it
    expect(result.updated).toEqual([{ handle: "B", geom: { type: "LINE", start: [10, 0], end: [10, 10] } }]);
  });

  it("an arc end is the anchor: the line moves to it, never the arc", () => {
    const ents: CleanEnt[] = [
      { handle: "R", layer: "0", geom: { type: "ARC", center: [0, 0], radius: 10, start_angle: 0, end_angle: 90 } },
      line("L", [10.03, 0], [20, 0]),
    ];
    const { items } = suggestCleanup(ents, 0.1);
    expect(items.map((s) => s.kind)).toEqual(["GAP"]);
    const { result } = applyCleanup(ents, items);
    expect(result.updated).toEqual([{ handle: "L", geom: { type: "LINE", start: [10, 0], end: [20, 0] } }]);
  });

  it("an open polyline end moves too, keeping its bulge", () => {
    const ents: CleanEnt[] = [
      { handle: "P", layer: "0", geom: { type: "LWPOLYLINE", closed: false, points: [[0, 0, 0], [5, 0, 0.5], [9.98, 0, 0]] } },
      line("L", [10, 0], [10, 5]),
      line("M", [10, 0], [15, 5]), // two ends already meet at (10,0): that point wins
    ];
    const { result } = applyCleanup(ents, suggestCleanup(ents, 0.1).items);
    expect(result.updated).toEqual([{ handle: "P", geom: { type: "LWPOLYLINE", closed: false, points: [[0, 0, 0], [5, 0, 0.5], [10, 0, 0]] } }]);
  });

  it("merges overlapping collinear lines into the longest one and spots exact duplicates", () => {
    const ents = [line("A", [0, 0], [10, 0]), line("B", [6, 0.01], [15, 0]), line("C", [0, 5], [10, 5]), line("D", [10, 5], [0, 5])];
    const { items } = suggestCleanup(ents, 0.1);
    const ovl = items.find((s) => s.kind === "OVERLAP")!;
    const dup = items.find((s) => s.kind === "DUPLICATE")!;
    expect(ovl.handles.sort()).toEqual(["A", "B"]);
    expect(dup.handles.sort()).toEqual(["C", "D"]);
    const { result, applied } = applyCleanup(ents, [ovl, dup]);
    expect(applied).toBe(2);
    expect(result.deleted!.length).toBe(2);
    const kept = result.updated!.find((u) => u.handle === ovl.keep!.handle)!.geom as Extract<Geom, { type: "LINE" }>;
    const xs = [kept.start[0], kept.end[0]].sort((a, b) => a - b);
    expect(xs[0]).toBeCloseTo(0, 6);
    expect(xs[1]).toBeCloseTo(15, 1);
  });

  it("finds overlaps of long lines drawn in opposite directions (no angle buckets to fall between)", () => {
    const ents = [line("A", [0, 0], [1000, 0]), line("B", [1200, -0.05], [500, 0.05])];
    const { items } = suggestCleanup(ents, 0.1);
    expect(items.map((s) => s.kind)).toEqual(["OVERLAP"]);
    expect(items[0].keep!.handle).toBe("A");
  });

  it("never merges lines that only touch, differ in layer or style, or are parallel apart", () => {
    const ents = [
      line("A", [0, 0], [10, 0]),
      line("B", [10, 0], [20, 0]), // end to end: a joint, not an overlap
      line("C", [0, 0], [10, 0], "OTHER"),
      line("D", [0, 0], [10, 0], "0", "red"),
      line("E", [0, 1], [10, 1]),
    ];
    expect(suggestCleanup(ents, 0.1).items.filter((s) => s.kind !== "GAP")).toEqual([]);
  });

  it("a gap on a line an overlap merges is skipped, not applied twice", () => {
    const ents = [line("A", [0, 0], [10, 0]), line("B", [5, 0], [12, 0]), line("C", [12.05, 0], [12, 8])];
    const { items } = suggestCleanup(ents, 0.1);
    expect(items.map((s) => s.kind).sort()).toEqual(["GAP", "OVERLAP"]);
    const { applied, skipped } = applyCleanup(ents, items);
    expect([applied, skipped]).toEqual([1, 1]);
  });

  it("refuses a bad tolerance and leaves a would-be zero-length line alone", () => {
    expect(() => suggestCleanup([], 0)).toThrow();
    expect(() => suggestCleanup([], 11)).toThrow();
    // A's two ends sit 0.05 apart near B's end: joining them all would collapse A
    const ents = [line("A", [0, 0], [0.05, 0]), line("B", [0.02, 0], [5, 5])];
    expect(applyCleanup(ents, suggestCleanup(ents, 0.1).items).result.updated).toEqual([]);
  });

  it("stays fast on a large drawing (grid buckets, no pair-wise scan)", () => {
    const ents: CleanEnt[] = [];
    for (let i = 0; i < 20000; i++) ents.push(line(`h${i}`, [i * 3, 0], [i * 3 + 2, 0]));
    const t = performance.now();
    expect(suggestCleanup(ents, 0.1).items).toEqual([]);
    expect(performance.now() - t).toBeLessThan(3000);
  });
});
