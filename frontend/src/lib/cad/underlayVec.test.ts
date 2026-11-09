import { describe, expect, it } from "vitest";
import { buildSnapIndex, querySnap, type SnapKind } from "./snap";
import { defaultGeom } from "./underlay";
import { IMPORT_LIMITS, importObjects, joinChains, snapEntities, type ImportOptions, type PageVec } from "./underlayVec";
import type { Pt } from "./view";

const file = { format: "pdf" as const, sha256: "a".repeat(64), name: "p.pdf" };
const g0 = defaultGeom(file, 0, [1000, 2000]);
const vec = (over: Partial<PageVec> = {}): PageVec => ({ size: [210, 297], layers: ["Walls", "Doors"], paths: [], arcs: [], texts: [], raster_only: false, truncated: false, ...over });
const opts = (o: Partial<ImportOptions> = {}): ImportOptions => ({ region: null, crossing: true, hidden: new Set(), tol: 0.01, text: true, fills: true, ...o });
const ALL = new Set<SnapKind>(["END", "MID", "CEN", "QUA", "INT", "NEA"]);

describe("snapping to an underlay page", () => {
  const v = vec({
    paths: [{ p: [0, 0, 100, 0, 100, 50] }, { p: [0, 80, 100, 80], l: 1 }, { p: [10, 10, 20, 20, 30, 10], k: "c" }, { p: [0, 0, 10, 0, 10, 10], z: 1 }],
    arcs: [{ c: [50, 50], r: 10 }, { c: [150, 150], r: 20, a: [0, 90] }],
  });
  const hit = (ents: ReturnType<typeof snapEntities>, p: Pt, kinds = ALL) => querySnap(buildSnapIndex(ents, 5), p, 3, kinds, 0);
  it("polylines END/MID, circles CEN/QUA, arcs, curves keep only their ends; the transform is applied", () => {
    const e = snapEntities(v, g0, "U1");
    expect(hit(e, [1100.5, 2000.4])?.kind).toBe("END"); // (100,0) on the page
    expect(hit(e, [1050, 2000.2])?.kind).toBe("MID"); // the middle of the first segment
    expect(hit(e, [1050.5, 2050.3])).toMatchObject({ kind: "CEN", x: 1050, y: 2050 });
    expect(hit(e, [1060, 2050])?.kind).toBe("QUA"); // 10 mm right of the circle's centre
    expect(hit(e, [1010, 2010], new Set(["END"]))?.kind).toBe("END"); // the curve's first end
    expect(hit(e, [1020, 2020], new Set(["END"]))).toBeNull(); // its middle vertex is not a snap point
    const turned = snapEntities(v, { ...g0, rotation: 90, scale: 2 }, "U1");
    expect(hit(turned, [1000, 2000 + 200], new Set(["END"]))?.kind).toBe("END"); // page (100,0) -> up 200 mm
  });
  it("hidden layers, clipping and the page edge leave candidates out", () => {
    expect(snapEntities(v, g0, "U", new Set([1])).some((x) => x.handle === "U#p1")).toBe(false);
    const clipped = snapEntities(v, { ...g0, clip: { kind: "rect", points: [[-1, -1], [60, 60]] } }, "U");
    expect(clipped.some((x) => x.handle === "U#a1")).toBe(false); // the arc at (150,150) is clipped away
    expect(clipped.some((x) => x.handle === "U#a0")).toBe(true);
    const out = vec({ paths: [{ p: [500, 500, 600, 600] }] }); // off the page
    expect(snapEntities(out, g0, "U")).toEqual([]);
  });
  it("100 000 segments: an index in about a second, a query in well under 50 ms", () => {
    const paths = Array.from({ length: 100_000 }, (_, i) => ({ p: [(i % 300) * 0.7, Math.floor(i / 300) * 0.8, (i % 300) * 0.7 + 0.5, Math.floor(i / 300) * 0.8 + 0.3] }));
    const big = vec({ size: [300, 300], paths });
    let t = performance.now();
    const ents = snapEntities(big, { ...g0, insert: [0, 0] }, "B");
    const idx = buildSnapIndex(ents, 2);
    const build = performance.now() - t;
    console.log(`underlay snap: ${ents.length} entities, index ${Math.round(build)} ms`);
    t = performance.now();
    for (let i = 0; i < 20; i++) querySnap(idx, [100 + i, 100], 2, ALL, 0);
    const per = (performance.now() - t) / 20;
    expect(build).toBeLessThan(15_000);
    expect(per).toBeLessThan(50);
  });
});

describe("importing a page as objects", () => {
  it("lines, a closed polygon, joined chains, circles, arcs and text in model coordinates", () => {
    const v = vec({
      paths: [
        { p: [0, 0, 10, 0] }, // three lines that meet become one polyline
        { p: [10, 0, 10, 10] },
        { p: [10, 10, 0, 10] },
        { p: [50, 50, 60, 50, 60, 60], z: 1 },
        { p: [100, 100, 110, 100] }, // a lone line
        { p: [0, 20, 5, 25], f: 1, z: 1 }, // a filled shape: an outline
      ],
      arcs: [{ c: [30, 30], r: 5 }, { c: [40, 40], r: 5, a: [0, 90] }],
      texts: [{ v: "A-1", p: [5, 5], h: 3, r: 10 }],
    });
    const r = importObjects(v, { ...g0, scale: 2, rotation: 90 }, opts());
    expect(r.error).toBeUndefined();
    expect(r.counts).toMatchObject({ lines: 1, polylines: 2, circles: 1, arcs: 1, texts: 1, fills: 1, joined: 2 });
    const chain = r.items.find((i) => i.geom.type === "LWPOLYLINE" && !i.geom.closed && i.geom.points.length === 4);
    expect(chain).toBeTruthy();
    const circle = r.items.find((i) => i.geom.type === "CIRCLE")!.geom;
    expect(circle).toMatchObject({ type: "CIRCLE", radius: 10 });
    if (circle.type === "CIRCLE") expect([circle.center[0], circle.center[1]]).toEqual([1000 - 60, 2000 + 60].map((x) => expect.closeTo(x, 9)));
    const arc = r.items.find((i) => i.geom.type === "ARC")!.geom;
    if (arc.type === "ARC") expect([arc.start_angle, arc.end_angle]).toEqual([90, 180]); // + the 90 degree rotation
    const text = r.items.find((i) => i.geom.type === "TEXT")!.geom;
    if (text.type === "TEXT") expect([text.value, text.height, text.rotation]).toEqual(["A-1", 6, 100]);
  });
  it("a chain that returns to its start closes; branches stay apart", () => {
    const sq = joinChains([[[0, 0], [10, 0]], [[10, 0], [10, 10]], [[10, 10], [0, 10]], [[0, 10], [0, 0]]], 0.01);
    expect(sq.closed).toEqual([true]);
    expect(sq.paths[0]).toHaveLength(4);
    const t = joinChains([[[0, 0], [10, 0]], [[10, 0], [20, 0]], [[10, 0], [10, 10]]], 0.01); // a T junction
    expect(t.paths).toHaveLength(2);
    expect(t.joined).toBe(1);
    const rev = joinChains([[[0, 0], [10, 0]], [[20, 0], [10, 0]], [[20, 0], [30, 5]]], 0.01); // ends given the other way round
    expect(rev.paths).toHaveLength(1);
    expect(rev.paths[0]).toEqual([[0, 0], [10, 0], [20, 0], [30, 5]]);
    const head = joinChains([[[10, 0], [20, 0]], [[0, 0], [10, 0]]], 0.01); // the second one joins at the head
    expect(head.paths[0]).toEqual([[0, 0], [10, 0], [20, 0]]);
    expect(joinChains([[[0, 0], [10, 0]], [[10.5, 0], [20, 0]]], 0.01).paths).toHaveLength(2); // a gap over tol
  });
  it("region (window / crossing), layers, clip and options", () => {
    const v = vec({
      paths: [{ p: [10, 10, 20, 10] }, { p: [10, 100, 20, 100], l: 1 }, { p: [0, 20, 200, 20] }],
      texts: [{ v: "x", p: [15, 15], h: 2 }],
    });
    const region = { min: [1000, 2000] as Pt, max: [1030, 2030] as Pt };
    const win = importObjects(v, g0, opts({ region, crossing: false }));
    expect(win.counts.lines).toBe(1);
    expect(win.counts.texts).toBe(1);
    const cross = importObjects(v, g0, opts({ region, crossing: true }));
    expect(cross.counts.lines).toBe(2); // the long line passes through the window
    expect(importObjects(v, g0, opts({ hidden: new Set([1]) })).counts.lines).toBe(2);
    expect(importObjects(v, g0, opts({ text: false })).counts.texts).toBe(0);
    expect(importObjects(v, { ...g0, clip: { kind: "rect", points: [[0, 0], [30, 30]] } }, opts()).counts.lines).toBe(2); // the long line still touches the clip
    expect(importObjects(vec({ raster_only: true }), g0, opts()).error).toContain("스캔");
  });
  it("too many objects or vertices ask for a smaller region", () => {
    const many = vec({ paths: Array.from({ length: IMPORT_LIMITS.objects + 1 }, (_, i) => ({ p: [i % 200, Math.floor(i / 200) * 5, (i % 200) + 0.5, Math.floor(i / 200) * 5 + 1] })) });
    expect(importObjects(many, { ...g0, insert: [0, 0] }, opts({ tol: 1e-9 })).error).toContain("영역을 줄이세요");
  });
});
