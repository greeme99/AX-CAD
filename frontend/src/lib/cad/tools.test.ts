import { describe, expect, it } from "vitest";
import { parseInput, start, step, type Sel, type ToolEvent, type ToolState } from "./tools";

const pt = (x: number, y: number): ToolEvent => ({ kind: "point", p: [x, y] });
const run = (s: ToolState, evs: ToolEvent[], sel: Sel[] = []) => {
  const created: unknown[] = [];
  let out = { state: s, prompt: "", results: [] as ReturnType<typeof step>["results"] };
  for (const e of evs) {
    out = step(out.state, e, sel);
    for (const r of out.results) created.push(...(r.created ?? []));
  }
  return { ...out, created };
};

describe("tools reducer", () => {
  it("LINE commits per click, Esc ends but keeps committed segments", () => {
    const r = run(start("LINE", 0).state, [pt(0, 0), pt(10, 0), pt(10, 5), { kind: "esc" }]);
    expect(r.created).toHaveLength(2);
    expect(r.state.tool).toBe("SELECT");
  });
  it("LINE rejects zero length", () => {
    const r = run(start("LINE", 0).state, [pt(1, 1), pt(1, 1)]);
    expect(r.created).toHaveLength(0);
    expect(r.state.pts).toHaveLength(1);
    expect(r.prompt).toContain("길이 0");
  });
  it("CIRCLE rejects radius <= 0, accepts typed radius", () => {
    const s = run(start("CIRCLE", 0).state, [pt(0, 0), { kind: "number", n: -3 }]);
    expect(s.created).toHaveLength(0);
    const ok = run(s.state, [{ kind: "number", n: 5 }]);
    expect(ok.created).toEqual([{ geom: { type: "CIRCLE", center: [0, 0], radius: 5 } }]);
  });
  it("ARC rejects collinear points", () => {
    const r = run(start("ARC", 0).state, [pt(0, 0), pt(1, 1), pt(2, 2)]);
    expect(r.created).toHaveLength(0);
    expect(r.state.pts).toHaveLength(2);
  });
  it("PLINE closes with C and commits open on Enter", () => {
    const c = run(start("PLINE", 0).state, [pt(0, 0), pt(4, 0), pt(4, 3), { kind: "close" }]);
    expect(c.created).toMatchObject([{ geom: { type: "LWPOLYLINE", closed: true } }]);
    const o = run(start("PLINE", 0).state, [pt(0, 0), pt(4, 0), { kind: "enter" }]);
    expect(o.created).toMatchObject([{ geom: { closed: false } }]);
  });
  it("TEXT rejects empty text, commits with default height", () => {
    const s = run(start("TEXT", 0).state, [pt(1, 2), { kind: "text", s: "  " }]);
    expect(s.created).toHaveLength(0);
    const ok = run(s.state, [{ kind: "text", s: "AB" }]);
    expect(ok.created).toEqual([{ geom: { type: "TEXT", insert: [1, 2], height: 2.5, value: "AB", rotation: 0 } }]);
  });
  it("MOVE translates the selection; COPY creates; empty selection refused", () => {
    const sel: Sel[] = [{ handle: "A1", layer: "CUT", geom: { type: "LINE", start: [0, 0], end: [1, 0] } }];
    const m = run(start("MOVE", 1).state, [pt(0, 0), pt(5, 2)], sel);
    expect(m.results[0].updated).toEqual([{ handle: "A1", geom: { type: "LINE", start: [5, 2], end: [6, 2] } }]);
    const c = run(start("COPY", 1).state, [pt(0, 0), pt(5, 2)], sel);
    expect(c.created).toMatchObject([{ layer: "CUT" }]);
    expect(start("MOVE", 0).state.tool).toBe("SELECT");
  });
});

describe("dimension tools", () => {
  it("DIMLINEAR picks horizontal vs vertical measurement from the location", () => {
    const h = run(start("DIMLINEAR", 0).state, [pt(0, 0), pt(10, 0), pt(5, 4)]);
    expect(h.created).toEqual([{ geom: { type: "DIM_LINEAR", p1: [0, 0], p2: [10, 0], base: [5, 4], angle: 0 } }]);
    const v = run(start("DIMLINEAR", 0).state, [pt(0, 0), pt(0, 10), pt(6, 5)]);
    expect(v.created).toMatchObject([{ geom: { angle: 90 } }]);
  });
  it("DIMALIGNED distance is signed (left positive) and rejects 0", () => {
    const l = run(start("DIMALIGNED", 0).state, [pt(0, 0), pt(10, 0), pt(5, 3)]);
    expect(l.created).toMatchObject([{ geom: { type: "DIM_ALIGNED", distance: 3 } }]);
    const r = run(start("DIMALIGNED", 0).state, [pt(0, 0), pt(10, 0), pt(5, -2)]);
    expect(r.created).toMatchObject([{ geom: { distance: -2 } }]);
    const z = run(start("DIMALIGNED", 0).state, [pt(0, 0), pt(10, 0), pt(5, 0)]);
    expect(z.created).toHaveLength(0);
    expect(z.state.pts).toHaveLength(2);
  });
  it("rejects p1 == p2 and coincident angular points", () => {
    const d = run(start("DIMLINEAR", 0).state, [pt(1, 1), pt(1, 1)]);
    expect(d.state.pts).toHaveLength(1);
    const a = run(start("DIMANGULAR", 0).state, [pt(0, 0), pt(5, 0), pt(10, 0)]);
    expect(a.state.pts).toHaveLength(2);
    const ok = run(start("DIMANGULAR", 0).state, [pt(0, 0), pt(5, 0), pt(0, 5), pt(3, 3)]);
    expect(ok.created).toMatchObject([{ geom: { type: "DIM_ANGULAR", center: [0, 0], base: [3, 3] } }]);
  });
  it("DIMRADIUS needs a circle/arc under the pick", () => {
    const c: Sel[] = [{ handle: "C", layer: "0", geom: { type: "CIRCLE", center: [0, 0], radius: 5 } }];
    const ok = run(start("DIMRADIUS", 0).state, [pt(0, 5)], c);
    expect(ok.created).toEqual([{ geom: { type: "DIM_RADIUS", center: [0, 0], radius: 5, angle: 90 } }]);
    expect(run(start("DIMRADIUS", 0).state, [pt(0, 5)]).created).toHaveLength(0);
    const ln: Sel[] = [{ handle: "L", layer: "0", geom: { type: "LINE", start: [0, 0], end: [1, 0] } }];
    expect(run(start("DIMRADIUS", 0).state, [pt(0, 5)], ln).created).toHaveLength(0);
  });
  it("aliases", () => {
    const s: ToolState = { tool: "SELECT", pts: [] };
    expect(parseInput("dli", s)).toEqual({ tool: "DIMLINEAR" });
    expect(parseInput("DRA", s)).toEqual({ tool: "DIMRADIUS" });
  });
});

describe("parseInput", () => {
  const s: ToolState = { tool: "LINE", pts: [[10, 10]] };
  it("parses absolute, relative, number, alias", () => {
    expect(parseInput("1.5,-2", s)).toEqual({ ev: { kind: "point", p: [1.5, -2] } });
    expect(parseInput("@5,5", s)).toEqual({ ev: { kind: "point", p: [15, 15] } });
    expect(parseInput("12", s)).toEqual({ ev: { kind: "number", n: 12 } });
    expect(parseInput("pl", s)).toEqual({ tool: "PLINE" });
    expect(parseInput("@1,1", { tool: "LINE", pts: [] })).toHaveProperty("error");
  });
  it("C means close inside PLINE, text inside TEXT", () => {
    expect(parseInput("c", { tool: "PLINE", pts: [[0, 0]] })).toEqual({ ev: { kind: "close" } });
    expect(parseInput("l", { tool: "TEXT", pts: [[0, 0]] })).toEqual({ ev: { kind: "text", s: "l" } });
  });
});
