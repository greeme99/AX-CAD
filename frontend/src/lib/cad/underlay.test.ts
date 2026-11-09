import { describe, expect, it } from "vitest";
import { aligned, bounds, clipPolygon, defaultGeom, inPolygon, moved, normAngle, outline, rotatedAbout, sameGeom, scaledAbout, toModel, toPage, toWire, visibleAt } from "./underlay";

const file = { format: "pdf" as const, sha256: "a".repeat(64), name: "plan.pdf" };
const g0 = defaultGeom(file, 0, [100, 50]);
const close = (a: number[], b: number[], d = 1e-9) => a.forEach((v, i) => expect(v).toBeCloseTo(b[i], -Math.log10(d)));

describe("underlay placement", () => {
  it("page <-> model with scale, rotation and insert", () => {
    const g = { ...g0, scale: 2, rotation: 90 };
    close(toModel(g, [10, 0]), [100, 70]); // 10 mm along the page's x -> 20 mm up, from (100,50)
    close(toModel(g, [0, 5]), [90, 50]);
    for (const p of [[0, 0], [13.5, -7], [210, 297]] as [number, number][]) close(toPage(g, toModel(g, p)), p, 1e-9);
  });
  it("outline is the page frame plus the clip", () => {
    const g = { ...g0, rotation: 0, clip: { kind: "rect" as const, points: [[10, 10], [60, 40]] as [number, number][] } };
    const [frame, clip] = outline(g, [210, 297]);
    expect(frame).toHaveLength(5);
    expect(frame[2]).toEqual([310, 347]);
    expect(clip[0]).toEqual([110, 60]);
    const b = bounds({ ...g0, rotation: 90 }, [200, 100]);
    close(b.min, [0, 50]);
    close(b.max, [100, 250]);
  });
  it("clipping: inside, outside (inverted) and the page edge", () => {
    const rect = { kind: "rect" as const, points: [[10, 10], [60, 40]] as [number, number][] };
    expect(clipPolygon({ ...g0, clip: rect })).toEqual([[10, 10], [60, 10], [60, 40], [10, 40]]);
    const keep = { ...g0, clip: rect };
    const hide = { ...g0, clip: rect, flags: { ...g0.flags, inside: false } };
    expect(visibleAt(keep, [210, 297], [30, 20])).toBe(true);
    expect(visibleAt(keep, [210, 297], [80, 20])).toBe(false);
    expect(visibleAt(hide, [210, 297], [30, 20])).toBe(false);
    expect(visibleAt(hide, [210, 297], [80, 20])).toBe(true);
    expect(visibleAt(g0, [210, 297], [-1, 5])).toBe(false); // outside the page
    const l: [number, number][] = [[0, 0], [10, 0], [10, 10], [5, 3], [0, 10]]; // a concave polygon
    expect([inPolygon(l, [2, 5]), inPolygon(l, [5, 8]), inPolygon(l, [5, 2])]).toEqual([true, false, true]);
  });
});

describe("underlay edits", () => {
  it("move, rotate and scale about a point keep that point where it is", () => {
    const c: [number, number] = [150, 120];
    const spot = toPage(g0, c); // a page point under c
    const r = rotatedAbout(g0, c, 30);
    close(toModel(r, spot), c);
    expect(r.rotation).toBeCloseTo(30, 9);
    const s = scaledAbout(g0, c, 2);
    expect(typeof s).toBe("object");
    if (typeof s !== "string") {
      close(toModel(s, spot), c);
      expect(s.scale).toBe(2);
    }
    expect(moved(g0, [5, -5]).insert).toEqual([105, 45]);
    expect(scaledAbout(g0, c, 0)).toContain("축척");
    expect(scaledAbout({ ...g0, scale: 1e6 }, c, 10)).toContain("축척");
    expect([normAngle(190), normAngle(-190), normAngle(360), normAngle(-0.0000000001)]).toEqual([-170, 170, 0, 0]);
  });
  it("two-point alignment lands the picked points on the targets", () => {
    const a1: [number, number] = [120, 70];
    const a2: [number, number] = [220, 70]; // 100 mm now
    const b1: [number, number] = [0, 0];
    const b2: [number, number] = [0, 5000]; // should be 5 m, pointing up
    const spot1 = toPage(g0, a1);
    const spot2 = toPage(g0, a2);
    const r = aligned(g0, a1, a2, b1, b2);
    expect(typeof r).toBe("object");
    if (typeof r === "string") return;
    close(toModel(r, spot1), b1, 1e-6);
    close(toModel(r, spot2), b2, 1e-6);
    expect(r.scale).toBeCloseTo(50, 9);
    expect(r.rotation).toBeCloseTo(90, 9);
    const only = aligned(g0, a1, a2, [0, 0], [0, 5000], false); // a known length, no turning
    if (typeof only !== "string") {
      expect(only.rotation).toBe(0);
      expect(only.scale).toBeCloseTo(50, 9);
      close(toModel(only, spot1), [0, 0], 1e-6);
    }
    expect(aligned(g0, a1, a1, b1, b2)).toContain("원본");
    expect(aligned(g0, a1, a2, b1, b1)).toContain("대상");
  });
  it("wire form drops everything but the record's own fields", () => {
    const x = { ...g0, linked: true, size: [1, 2], extra: 1 } as never;
    expect(Object.keys(toWire(x)).sort()).toEqual(["clip", "contrast", "fade", "flags", "format", "insert", "name", "page", "rotation", "scale", "sha256"]);
    expect(sameGeom(g0, { ...g0, insert: [100, 50] })).toBe(true);
    expect(sameGeom(g0, moved(g0, [1, 0]))).toBe(false);
  });
});
