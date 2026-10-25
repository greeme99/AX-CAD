import { describe, expect, it } from "vitest";
import { toPrims } from "./curves";
import { parseInput, runNow, start, step, type Sel, type ToolEvent, type ToolState } from "./tools";

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
  it("TEXT asks the height first (Enter = default), rejects empty text", () => {
    const s = run(start("TEXT", 0).state, [pt(1, 2), { kind: "enter" }, { kind: "text", s: "  " }]);
    expect(s.created).toHaveLength(0);
    const ok = run(s.state, [{ kind: "text", s: "AB" }]);
    expect(ok.created).toEqual([{ geom: { type: "TEXT", insert: [1, 2], height: 2.5, value: "AB", rotation: 0 } }]);
    const big = run(start("TEXT", 0).state, [pt(0, 0), { kind: "number", n: 5 }, { kind: "text", s: "H" }]);
    expect(big.created).toMatchObject([{ geom: { height: 5 } }]);
    expect(big.state.h).toBe(5); // the next text offers 5 as its default
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
  it("HATCH fills a picked closed polyline or circle with the chosen pattern and scale", () => {
    const c: Sel[] = [{ handle: "C", layer: "WALL", geom: { type: "CIRCLE", center: [0, 0], radius: 5 } }];
    const s0 = start("HATCH", 0).state;
    const r = run(s0, [{ kind: "option", s: "SOLID" }, { kind: "number", n: 2 }, pt(5, 0)], c);
    expect(r.created).toEqual([{ geom: { type: "HATCH", boundary: [[-5, 0, 1], [5, 0, 1]], pattern: "SOLID", scale: 2, angle: 0 }, layer: "WALL" }]);
    expect(r.state).toMatchObject({ tool: "HATCH", pat: "SOLID", n: 2 }); // repeatable with the same settings
    const open: Sel[] = [{ handle: "P", layer: "0", geom: { type: "LWPOLYLINE", points: [[0, 0, 0], [5, 0, 0]], closed: false } }];
    expect(run(s0, [pt(1, 0)], open).prompt).toContain("닫힌 폴리라인");
    expect(parseInput("ar-conc", s0)).toEqual({ ev: { kind: "option", s: "AR-CONC" } });
    expect(parseInput("H", start("SELECT", 0).state)).toEqual({ tool: "HATCH" });
  });
  it("DIMDIAMETER dimensions a circle across its centre", () => {
    const c: Sel[] = [{ handle: "C", layer: "0", geom: { type: "CIRCLE", center: [0, 0], radius: 5 } }];
    expect(run(start("DIMDIAMETER", 0).state, [pt(5, 0)], c).created).toEqual([{ geom: { type: "DIM_DIAMETER", center: [0, 0], radius: 5, angle: 0 } }]);
  });
  it("DIMBASELINE steps outwards from the first origin, DIMCONTINUE chains on one line", () => {
    const base = run(start("DIMBASELINE", 0).state, [pt(0, 0), pt(10, 0), pt(5, 8), pt(25, 0), pt(40, 0), { kind: "enter" }]);
    expect(base.created).toEqual([
      { geom: { type: "DIM_LINEAR", p1: [0, 0], p2: [10, 0], base: [5, 8], angle: 0 } },
      { geom: { type: "DIM_LINEAR", p1: [0, 0], p2: [25, 0], base: [5, 15], angle: 0 } },
      { geom: { type: "DIM_LINEAR", p1: [0, 0], p2: [40, 0], base: [5, 22], angle: 0 } },
    ]);
    expect(base.state.tool).toBe("SELECT");
    const below = run(start("DIMBASELINE", 0).state, [pt(0, 0), pt(10, 0), pt(5, -8), pt(25, 0)]);
    expect(below.created[1]).toMatchObject({ geom: { base: [5, -15] } });
    const cont = run(start("DIMCONTINUE", 0).state, [pt(0, 0), pt(0, 10), pt(-6, 5), pt(0, 25), pt(0, 25)]);
    expect(cont.created).toEqual([
      { geom: { type: "DIM_LINEAR", p1: [0, 0], p2: [0, 10], base: [-6, 5], angle: 90 } },
      { geom: { type: "DIM_LINEAR", p1: [0, 10], p2: [0, 25], base: [-6, 5], angle: 90 } },
    ]);
    expect(cont.prompt).toContain("같은 위치"); // a zero-length step is refused
  });
  it("aliases", () => {
    const s: ToolState = { tool: "SELECT", pts: [] };
    expect(parseInput("dli", s)).toEqual({ tool: "DIMLINEAR" });
    expect(parseInput("DRA", s)).toEqual({ tool: "DIMRADIUS" });
    expect(parseInput("dba", s)).toEqual({ tool: "DIMBASELINE" });
    expect(parseInput("DCO", s)).toEqual({ tool: "DIMCONTINUE" });
    expect(parseInput("DDI", s)).toEqual({ tool: "DIMDIAMETER" });
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
    expect(parseInput("l", { tool: "TEXT", pts: [[0, 0]], n: 2.5 })).toEqual({ ev: { kind: "text", s: "l" } });
    expect(parseInput("3.5", { tool: "TEXT", pts: [[0, 0]] })).toEqual({ ev: { kind: "number", n: 3.5 } }); // height step
    expect(parseInput("c", { tool: "ARC", pts: [] })).toEqual({ ev: { kind: "option", s: "C" } });
  });
});

describe("FreeCAD Draft modify tools", () => {
  const line = (h: string, a: [number, number], b: [number, number]): Sel => ({ handle: h, layer: "CUT", geom: { type: "LINE", start: a, end: b } });
  const n = (v: number): ToolEvent => ({ kind: "number", n: v });

  it("RECT and POLYGON create closed polylines; POLYGON takes the side count first", () => {
    expect(run(start("RECT", 0).state, [pt(0, 0), pt(10, 5)]).created).toMatchObject([{ geom: { type: "LWPOLYLINE", closed: true } }]);
    const r = run(start("POLYGON", 0).state, [n(5), pt(0, 0), pt(10, 0)]);
    expect((r.created[0] as { geom: { points: unknown[] } }).geom.points).toHaveLength(5);
    expect(run(start("POLYGON", 0).state, [n(2)]).prompt).toContain("3~64");
  });
  it("selection tools refuse to start without a selection", () => {
    for (const t of ["ROTATE", "SCALE", "MIRROR", "ARRAY", "ARRAYPOLAR"] as const) expect(start(t, 0).state.tool).toBe("SELECT");
  });
  it("ROTATE by typed angle updates in place; SCALE rejects 0", () => {
    const sel = [line("L1", [1, 0], [2, 0])];
    const out = step(step(start("ROTATE", 1).state, pt(0, 0), sel).state, n(90), sel);
    const g = out.results[0].updated![0].geom as { start: number[] };
    expect(g.start[0]).toBeCloseTo(0, 9);
    expect(g.start[1]).toBeCloseTo(1, 9);
    expect(step(step(start("SCALE", 1).state, pt(0, 0), sel).state, n(0), sel).prompt).toContain("배율");
  });
  it("MIRROR keeps the original and adds a reflected copy on the source layer", () => {
    const r = run(start("MIRROR", 1).state, [pt(0, 0), pt(0, 1)], [line("L1", [1, 0], [2, 0])]);
    expect(r.created).toMatchObject([{ layer: "CUT", geom: { start: [-1, 0], end: [-2, 0] } }]);
  });
  it("ARRAY: count, base, spacing -> n-1 copies; ARRAYPOLAR around a centre", () => {
    const sel = [line("L1", [0, 0], [1, 0])];
    expect(run(start("ARRAY", 1).state, [n(4), pt(0, 0), pt(5, 0)], sel).created).toHaveLength(3);
    expect(run(start("ARRAYPOLAR", 1).state, [n(6), pt(0, 0)], sel).created).toHaveLength(5);
    expect(run(start("ARRAY", 1).state, [pt(0, 0)], sel).prompt).toContain("개수");
  });
  it("OFFSET: distance, entity, side; repeats with the same distance", () => {
    const l = line("L1", [0, 0], [10, 0]);
    let out = step(start("OFFSET", 0).state, n(2), []);
    out = step(out.state, pt(5, 0), [l]);
    out = step(out.state, pt(5, 4), []);
    expect(out.results[0].created).toMatchObject([{ layer: "CUT", geom: { start: [0, 2], end: [10, 2] } }]);
    expect(out.state).toMatchObject({ tool: "OFFSET", n: 2 });
  });
  it("FILLET: radius, first line, second line -> both lines updated + arc", () => {
    const [a, b] = [line("A", [0, 0], [10, 0]), line("B", [10, 0], [10, 10])];
    let out = step(start("FILLET", 0).state, n(2), []);
    out = step(out.state, pt(2, 0), [a]);
    out = step(out.state, pt(10, 8), [b]);
    expect(out.results[0].updated!.map((u) => u.handle)).toEqual(["A", "B"]);
    expect(out.results[1].created).toMatchObject([{ geom: { type: "ARC", radius: 2 } }]);
    expect(step(step(start("FILLET", 0).state, pt(2, 0), [a]).state, pt(3, 0), [a]).prompt).toContain("다른 객체");
  });
  it("TRIM: a line piece is updated + created; a circle is replaced by an arc (delete + create)", () => {
    const cut = [line("X1", [3, -1], [3, 1]), line("X2", [7, -1], [7, 1])];
    const ctx = { bounds: cut.flatMap((c) => toPrims(c.handle, c.geom, [])), reach: 1000 };
    const r = step(start("TRIM", 0).state, pt(5, 0), [line("T", [0, 0], [10, 0])], ctx);
    expect(r.results[0].updated![0].handle).toBe("T");
    expect(r.results[0].created).toHaveLength(1);
    expect(r.state.tool).toBe("TRIM"); // stays until Esc
    const c: Sel = { handle: "C", layer: "0", geom: { type: "CIRCLE", center: [5, 0], radius: 3 } };
    const rc = step(start("TRIM", 0).state, pt(5, 3), [c], { bounds: toPrims("H", { type: "LINE", start: [0, 0], end: [10, 0] }, []), reach: 1000 });
    expect(rc.results[0]).toMatchObject({ deleted: ["C"], created: [{ geom: { type: "ARC" } }] });
  });
  it("EXTEND uses the other entities as boundaries, never the picked one itself", () => {
    const t = line("T", [0, 0], [5, 0]);
    const bounds = [...toPrims("T", t.geom, []), ...toPrims("B", { type: "LINE", start: [8, -1], end: [8, 1] }, [])];
    const r = step(start("EXTEND", 0).state, pt(4.5, 0), [t], { bounds, reach: 1000 });
    expect(r.results[0].updated![0].geom).toMatchObject({ end: [8, 0] });
  });
  it("aliases", () => {
    const s0 = start("SELECT", 0).state;
    for (const [k, t] of [["REC", "RECT"], ["POL", "POLYGON"], ["RO", "ROTATE"], ["SC", "SCALE"], ["MI", "MIRROR"], ["AR", "ARRAY"], ["ARP", "ARRAYPOLAR"], ["O", "OFFSET"], ["F", "FILLET"], ["TR", "TRIM"], ["EX", "EXTEND"]])
      expect(parseInput(k, s0)).toEqual({ tool: t });
  });
});

describe("Track 8 P1-2 tools", () => {
  const line = (h: string, a: [number, number], b: [number, number]): Sel => ({ handle: h, layer: "CUT", geom: { type: "LINE", start: a, end: b } });
  const go = (s: ToolState, ev: ToolEvent, sel: Sel[] = [], editable: Sel[] = []) => step(s, ev, sel, { bounds: [], reach: 100, editable });

  it("polar coordinates: d<a from the origin, @d<a from the last point", () => {
    const p = parseInput("10<90", { tool: "LINE", pts: [] }) as { ev: { p: number[] } };
    expect(p.ev.p[0]).toBeCloseTo(0);
    expect(p.ev.p[1]).toBeCloseTo(10);
    const q = parseInput("@5<180", { tool: "LINE", pts: [[10, 0]] }) as { ev: { p: number[] } };
    expect(q.ev.p[0]).toBeCloseTo(5);
    expect(parseInput("@5<0", { tool: "LINE", pts: [] })).toEqual({ error: "상대 좌표의 기준점이 없습니다" });
  });

  it("ARC with [C]: centre, start, end counter-clockwise", () => {
    let s = go(start("ARC", 0).state, { kind: "option", s: "C" }).state;
    s = go(s, pt(0, 0)).state;
    s = go(s, pt(5, 0)).state;
    const out = go(s, pt(0, 5));
    expect(out.results[0].created).toEqual([{ geom: { type: "ARC", center: [0, 0], radius: 5, start_angle: 0, end_angle: 90 } }]);
  });

  it("CHAMFER with two distances", () => {
    const [a, b] = [line("A", [0, 0], [10, 0]), line("B", [0, 0], [0, 10])];
    let s = go(start("CHAMFER", 0).state, { kind: "number", n: 2 }).state;
    s = go(s, { kind: "number", n: 3 }).state; // second distance
    s = go(s, pt(8, 0), [a]).state;
    const out = go(s, pt(0, 8), [b]);
    expect(out.results[1].created).toEqual([{ geom: { type: "LINE", start: [2, 0], end: [0, 3] }, layer: "CUT" }]);
  });

  it("BREAK between two points, or split with Enter", () => {
    const a = line("A", [0, 0], [10, 0]);
    let s = go(start("BREAK", 0).state, pt(3, 0), [a]).state;
    const two = go(s, pt(6, 0));
    expect(two.results[0]).toEqual({ updated: [{ handle: "A", geom: { type: "LINE", start: [0, 0], end: [3, 0] } }], created: [{ geom: { type: "LINE", start: [6, 0], end: [10, 0] }, layer: "CUT" }] });
    s = go(start("BREAK", 0).state, pt(4, 0), [a]).state;
    expect(go(s, { kind: "enter" }).results[0].created).toHaveLength(1);
  });

  it("STRETCH moves the ends inside the crossing window", () => {
    const ents = [line("A", [0, 0], [10, 0]), line("B", [0, 5], [10, 5])];
    let s = start("STRETCH", 0).state;
    for (const p of [pt(8, -1), pt(12, 1), pt(10, 0)]) s = go(s, p, [], ents).state;
    const out = go(s, pt(15, 0), [], ents);
    expect(out.results[0].updated).toEqual([{ handle: "A", geom: { type: "LINE", start: [0, 0], end: [15, 0] } }]); // B is outside
  });

  it("EXPLODE and JOIN run on the selection at once", () => {
    const pl: Sel = { handle: "P", layer: "L", geom: { type: "LWPOLYLINE", points: [[0, 0, 0], [10, 0, 0], [10, 10, 0]], closed: false } };
    const ex = runNow("EXPLODE", [pl]);
    expect(ex.results[0].deleted).toEqual(["P"]);
    expect(ex.results[0].created).toHaveLength(2);
    const j = runNow("JOIN", [line("A", [0, 0], [10, 0]), line("B", [10, 0], [10, 10])]);
    expect(j.results[0].created![0].geom).toMatchObject({ type: "LWPOLYLINE", closed: false });
    expect(runNow("JOIN", [line("A", [0, 0], [1, 0])]).prompt).toContain("2개 이상");
  });

  it("TRIM works on polylines", () => {
    const pl: Sel = { handle: "P", layer: "L", geom: { type: "LWPOLYLINE", points: [[0, 0, 0], [10, 0, 0]], closed: false } };
    const cutters = [...toPrims("c1", { type: "LINE", start: [3, -1], end: [3, 1] }, []), ...toPrims("c2", { type: "LINE", start: [6, -1], end: [6, 1] }, [])];
    const out = step(start("TRIM", 0).state, pt(4, 0), [pl], { bounds: cutters, reach: 100 });
    expect(out.results[0].updated![0].geom).toMatchObject({ type: "LWPOLYLINE" });
    expect(out.results[0].created).toHaveLength(1);
  });
});

describe("blocks", () => {
  const sq: Sel[] = [
    { handle: "1", layer: "WALL", geom: { type: "LINE", start: [0, 0], end: [10, 0] } },
    { handle: "2", layer: "WALL", geom: { type: "CIRCLE", center: [5, 5], radius: 2 } },
  ];
  it("BLOCK turns the selection into a block and one reference at its base point", () => {
    const s0 = start("BLOCK", 2).state;
    expect(step(s0, { kind: "text", s: "OLD" }, sq, { bounds: [], reach: 0, blocks: ["old"] }).prompt).toContain("같은 이름");
    const named = step(s0, { kind: "text", s: "PLATE" }, sq).state;
    const r = step(named, pt(5, 5), sq);
    expect(r.results).toEqual([
      {
        block: { name: "PLATE", base: [5, 5], handles: ["1", "2"] },
        deleted: ["1", "2"],
        created: [{ geom: { type: "INSERT", name: "PLATE", insert: [5, 5], xscale: 1, yscale: 1, rotation: 0 }, layer: "WALL" }],
      },
    ]);
    expect(start("BLOCK", 0).prompt).toContain("먼저 객체를 선택");
    const withRef: Sel[] = [...sq, { handle: "3", layer: "0", geom: { type: "INSERT", name: "X", insert: [0, 0], xscale: 1, yscale: 1, rotation: 0 } }];
    expect(step(named, pt(5, 5), withRef).prompt).toContain("블록 참조");
  });
  it("INSERT places a known block, repeatedly, at the typed scale", () => {
    const ctx = { bounds: [], reach: 0, blocks: ["PLATE"] };
    let out = step(start("INSERT", 0).state, { kind: "text", s: "NOPE" }, [], ctx);
    expect(out.prompt).toContain("도면에 없는 블록");
    out = step(out.state, { kind: "text", s: "PLATE" }, [], ctx);
    out = step(out.state, { kind: "number", n: 2 }, [], ctx);
    out = step(out.state, pt(30, 0), [], ctx);
    expect(out.results[0].created).toEqual([{ geom: { type: "INSERT", name: "PLATE", insert: [30, 0], xscale: 2, yscale: 2, rotation: 0 } }]);
    expect(out.state).toMatchObject({ tool: "INSERT", pat: "PLATE" });
    expect(parseInput("PLATE", start("INSERT", 0).state)).toEqual({ ev: { kind: "text", s: "PLATE" } });
  });
});
