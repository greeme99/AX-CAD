import { describe, expect, it } from "vitest";
import { DrawingModel, fromGroups, getProp, invoke, matches, ModelError, putProp, toGroups, type Ent, type Obj } from "./objmodel";

const line: Ent = { handle: "A1", layer: "0", geom: { type: "LINE", start: [0, 0], end: [10, 0] } };
const circ: Ent = { handle: "A2", layer: "CUT", geom: { type: "CIRCLE", center: [5, 5], radius: 2 }, aci: 1 };
const locked: Ent = { handle: "A3", layer: "LOCK", geom: { type: "POINT", point: [1, 1] } };
const model = () => new DrawingModel([line, circ, locked], [{ name: "0" }, { name: "CUT" }, { name: "LOCK", locked: true }], "0");
const space: Obj = { t: "space" };

describe("DrawingModel", () => {
  it("creates, updates and deletes against the starting drawing", () => {
    const m = model();
    const made = m.add({ type: "LINE", start: [0, 0], end: [1, 1] });
    expect(made.handle).toBe("~1");
    m.set("A1", { aci: 3 });
    m.remove("A2");
    const gone = m.add({ type: "POINT", point: [2, 2] });
    m.remove(gone.handle); // made and erased in the same run: nothing to apply
    const ch = m.changes();
    expect(ch.created.map((e) => e.handle)).toEqual(["~1"]);
    expect(ch.updated).toEqual([{ ...line, aci: 3 }]);
    expect(ch.deleted).toEqual(["A2"]);
  });
  it("refuses locked or missing layers, bad numbers and unknown handles", () => {
    const m = model();
    expect(() => m.set("A3", { aci: 1 })).toThrow(/잠긴 레이어/);
    expect(() => m.add({ type: "POINT", point: [0, 0] }, "NOPE")).toThrow(/없는 레이어/);
    expect(() => m.add({ type: "CIRCLE", center: [0, 0], radius: 0 })).toThrow(/반지름/);
    expect(() => m.add({ type: "LINE", start: [Number.NaN, 0], end: [1, 1] })).toThrow(ModelError);
    expect(() => m.add({ type: "LINE", start: [1e12, 0], end: [1, 1] })).toThrow(/범위/);
    expect(() => m.remove("ZZ")).toThrow(/없는 객체/);
    expect(m.changes()).toEqual({ created: [], updated: [], deleted: [], layers: [], tables: [], layerOps: [] });
  });
});

describe("DXF groups", () => {
  it("round-trips entities through entget-style groups", () => {
    for (const g of [
      { type: "LINE", start: [1, 2], end: [3, 4] },
      { type: "CIRCLE", center: [1, 1], radius: 3 },
      { type: "ARC", center: [0, 0], radius: 5, start_angle: 30, end_angle: 120 },
      { type: "LWPOLYLINE", points: [[0, 0, 0], [10, 0, 0.5], [10, 5, 0]], closed: true },
      { type: "TEXT", insert: [1, 1], height: 2.5, value: "A-1", rotation: 90 },
      { type: "POINT", point: [7, 8] },
    ] as Ent["geom"][]) {
      const e: Ent = { handle: "X", layer: "0", geom: g };
      const back = fromGroups(toGroups(e));
      expect(back.geom.type).toBe(g.type);
      // angles go through radians: equal within rounding, everything else exactly
      for (const [k, v] of Object.entries(g)) {
        const got = (back.geom as Record<string, unknown>)[k];
        if (typeof v === "number") expect(got as number).toBeCloseTo(v, 9);
        else expect(got).toEqual(v);
      }
    }
  });
  it("entmod keeps what the list leaves out and refuses a type change", () => {
    const r = fromGroups([[10, [2, 2]]], circ);
    expect(r.geom).toEqual({ type: "CIRCLE", center: [2, 2], radius: 2 });
    expect(() => fromGroups([[0, "LINE"]], circ)).toThrow(/바꿀 수 없습니다/);
    expect(() => fromGroups([[0, "HATCH"]])).toThrow(/만들거나 고칠 수 없는/);
    expect(() => fromGroups([[0, "LINE"], [10, [0, 0]]])).toThrow(/\(11 \. 점\)/);
    expect(() => fromGroups([[0, "LINE"], [10, [0, 0]], [11, [1, 1]], [62, 300]])).toThrow(/0~256/);
  });
  it("filters like ssget X", () => {
    expect(matches(line, [[0, "LINE,ARC"]])).toBe(true);
    expect(matches(circ, [[0, "LINE"]])).toBe(false);
    expect(matches(circ, [[8, "C*"], [62, 1]])).toBe(true);
    expect(matches(circ, [[8, "c?t"]])).toBe(true);
  });
});

describe("ActiveX-style object model", () => {
  it("adds through ModelSpace and reads properties", () => {
    const m = model();
    const l = invoke(m, space, "AddLine", [[0, 0, 0], [3, 4, 0]]) as Obj;
    expect(getProp(m, l, "Length")).toBe(5);
    expect(getProp(m, l, "objectname")).toBe("AcDbLine");
    expect(getProp(m, l, "StartPoint")).toEqual([0, 0, 0]);
    const c = invoke(m, space, "AddCircle", [[1, 1], 2]) as Obj;
    expect(getProp(m, c, "Area")).toBeCloseTo(Math.PI * 4);
    const p = invoke(m, space, "AddLightWeightPolyline", [[0, 0, 10, 0, 10, 10]]) as Obj;
    putProp(m, p, "Closed", true);
    expect(getProp(m, p, "Area")).toBeCloseTo(50);
    expect(getProp(m, { t: "space" }, "Count")).toBe(6);
  });
  it("changes properties and runs methods", () => {
    const m = model();
    const l: Obj = { t: "ent", handle: "A1" };
    putProp(m, l, "EndPoint", [0, 10, 0]);
    putProp(m, l, "Color", 5);
    invoke(m, l, "Move", [[0, 0], [5, 5]]);
    expect(getProp(m, l, "StartPoint")).toEqual([5, 5, 0]);
    invoke(m, l, "Rotate", [[5, 5], Math.PI / 2]);
    expect((getProp(m, l, "EndPoint") as number[]).map((v) => Math.round(v))).toEqual([-5, 5, 0]);
    const copy = invoke(m, l, "Mirror", [[0, 0], [0, 1]]) as Obj;
    expect(getProp(m, copy, "Color")).toBe(5);
    invoke(m, { t: "ent", handle: "A2" }, "Delete", []);
    expect(m.changes().deleted).toEqual(["A2"]);
    expect(() => getProp(m, l, "Radius")).toThrow(/Radius 속성이 없습니다/);
    expect(() => invoke(m, l, "Explode", [])).toThrow(/Explode 메서드가 없습니다/);
  });
  it("layers: add, item and the active layer", () => {
    const m = model();
    const lay = invoke(m, { t: "layers" }, "Add", ["NEW-1"]) as Obj;
    putProp(m, { t: "doc" }, "ActiveLayer", lay);
    const t = invoke(m, space, "AddText", ["A", [0, 0], 2.5]) as Obj;
    expect(getProp(m, t, "Layer")).toBe("NEW-1");
    expect(m.changes().layers).toEqual(["NEW-1"]);
    expect(() => invoke(m, { t: "layers" }, "Add", ["bad*name"])).toThrow(/쓸 수 없습니다/);
    expect(getProp(m, invoke(m, { t: "layers" }, "Item", [0]) as Obj, "Name")).toBe("0");
  });
});
