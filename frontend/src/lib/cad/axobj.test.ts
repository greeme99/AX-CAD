// W4 (plan §22.4): the ActiveX member table — behaviour of the common members, and the snapshot
// tools/coverage/ref/axobj_members.json the coverage report reads (AXOBJ_WRITE=1 rewrites it).
import { describe, expect, it } from "vitest";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { memberTable } from "./axobj";
import { DrawingModel, getProp, invoke, putProp, NO_TABLES, type Ent, type Obj } from "./objmodel";
import { runLisp } from "./lisp";
import { runVba } from "./vba";

const ents: Ent[] = [
  { handle: "1A", layer: "0", geom: { type: "LINE", start: [0, 0], end: [10, 0] } },
  { handle: "1B", layer: "0", geom: { type: "LINE", start: [5, -5], end: [5, 5] } },
  { handle: "1C", layer: "0", geom: { type: "LWPOLYLINE", points: [[0, 0, 0], [10, 0, 0], [10, 10, 0]], closed: false } },
  { handle: "1D", layer: "0", geom: { type: "DIM_LINEAR", p1: [0, 0], p2: [50, 0], base: [0, 10], angle: 0, overrides: { dimasz: 3 } } },
  { handle: "1E", layer: "0", geom: { type: "CIRCLE", center: [0, 0], radius: 5 } },
];
const model = () => {
  const m = new DrawingModel(ents, [{ name: "0" }, { name: "L1", color: 3 }], "0", { ...NO_TABLES, regapps: ["ACAD", "MYAPP"] });
  m.ids = { model_space: "1F", nod: "C", tables: { layer: "2" }, records: { layer: { "0": "10", L1: "11" } }, blocks: {}, layouts: {}, groups: {} };
  return m;
};
const E = (h: string): Obj => ({ t: "ent", handle: h });

describe("common members", () => {
  it("object identity: Handle, ObjectID, OwnerID, ObjectName, Application", () => {
    const m = model();
    expect(getProp(m, E("1A"), "Handle")).toBe("1A");
    expect(getProp(m, E("1A"), "ObjectID")).toBe(26);
    expect(getProp(m, E("1A"), "OwnerID")).toBe(31);
    expect(getProp(m, E("1A"), "ObjectName")).toBe("AcDbLine");
    expect(getProp(m, { t: "layer", name: "L1" }, "Handle")).toBe("11");
    expect(getProp(m, E("1A"), "Application")).toEqual({ t: "app" });
  });
  it("GetBoundingBox and IntersectWith (with extension)", () => {
    const m = model();
    expect(invoke(m, E("1C"), "GetBoundingBox", [])).toEqual({ out: { 0: [0, 0, 0], 1: [10, 10, 0] }, ret: null });
    expect(invoke(m, E("1A"), "IntersectWith", [E("1B"), 0])).toEqual([5, 0, 0]);
    expect(invoke(m, E("1B"), "IntersectWith", [E("1E"), 0])).toEqual([5, 0, 0]);
  });
  it("arrays, TransformBy, Mirror3D, Rotate3D, Offset", () => {
    const m = model();
    expect((invoke(m, E("1A"), "ArrayRectangular", [2, 3, 1, 10, 20, 0]) as Obj[]).length).toBe(5);
    expect((invoke(m, E("1E"), "ArrayPolar", [4, 2 * Math.PI, [20, 0, 0]]) as Obj[]).length).toBe(3);
    invoke(m, E("1A"), "TransformBy", [[[0, -1, 0, 1], [1, 0, 0, 2], [0, 0, 1, 0], [0, 0, 0, 1]]]);
    const g = m.get("1A")!.geom as { start: number[]; end: number[] };
    expect(g.start.map((v) => Math.round(v * 1e9) / 1e9)).toEqual([1, 2]);
    expect(g.end.map((v) => Math.round(v * 1e9) / 1e9)).toEqual([1, 12]);
    expect(() => invoke(m, E("1A"), "TransformBy", [[[2, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]])).toThrow(/균일 축척/);
    const mir = invoke(m, E("1E"), "Mirror3D", [[10, 0, 0], [10, 1, 0], [10, 0, 1]]) as Obj;
    expect(m.get((mir as { handle: string }).handle)!.geom).toMatchObject({ center: [20, 0] });
    expect(() => invoke(m, E("1E"), "Rotate3D", [[0, 0, 0], [1, 0, 0], 1])).toThrow(/Z축/);
    const off = invoke(m, E("1C"), "Offset", [1]) as Obj[];
    expect(m.get((off[0] as { handle: string }).handle)!.geom).toMatchObject({ points: [[0, 1, 0], [9, 1, 0], [9, 10, 0]] });
  });
  it("SetXData / GetXData, limited members", () => {
    const m = model();
    invoke(m, E("1A"), "SetXData", [[1001, 1000, 1070], ["MYAPP", "tag", 7]]);
    expect(invoke(m, E("1A"), "GetXData", ["MYAPP"])).toEqual({ out: { 1: [1001, 1000, 1070], 2: ["MYAPP", "tag", 7] }, ret: null });
    expect(() => invoke(m, E("1A"), "SetXData", [[1001, 1000], ["ACAD", "x"]])).toThrow(/시스템/);
    expect(getProp(m, E("1A"), "Thickness")).toBe(0);
    expect(() => putProp(m, E("1A"), "Thickness", 5)).toThrow(/2D 도면/);
    putProp(m, E("1A"), "Thickness", 0); // the default is taken
  });
  it("dimension properties are its own dimension variables", () => {
    const m = model();
    expect(getProp(m, E("1D"), "ArrowheadSize")).toBe(3);
    putProp(m, E("1D"), "TextHeight", 5);
    putProp(m, E("1D"), "SuppressTrailingZeros", true);
    putProp(m, E("1D"), "TextPrefix", "Ø");
    putProp(m, E("1D"), "TextOverride", "<> TYP");
    expect(m.get("1D")!.geom).toMatchObject({ overrides: { dimasz: 3, dimtxt: 5, dimzin: 8, dimpost: "Ø<>" }, text: "<> TYP" });
    expect(getProp(m, E("1D"), "Measurement")).toBe(50);
    putProp(m, E("1D"), "Arrowhead1Type", 4);
    expect(getProp(m, E("1D"), "Arrowhead1Type")).toBe(4);
  });
});

describe("security review (W4)", () => {
  it("GetXData wildcards without regular expressions; array vertex budget; degenerate polylines", () => {
    const m = model();
    invoke(m, E("1A"), "SetXData", [[1001, 1000], ["MYAPP", "a"]]);
    expect((invoke(m, E("1A"), "GetXData", ["MY*P"]) as { out: Record<number, unknown[]> }).out[2]).toEqual(["MYAPP", "a"]);
    expect((invoke(m, E("1A"), "GetXData", ["M?APP"]) as { out: Record<number, unknown[]> }).out[2]).toEqual(["MYAPP", "a"]);
    expect((invoke(m, E("1A"), "GetXData", ["*(a+)+$*X"]) as { out: Record<number, unknown[]> }).out[2]).toEqual([]);
    const big = m.add({ type: "LWPOLYLINE", points: Array.from({ length: 1000 }, (_, i) => [i, i % 2, 0]), closed: false });
    expect(() => invoke(m, E(big.handle), "ArrayRectangular", [20, 20, 1, 1, 1, 0])).toThrow(/정점/);
    const dup = m.add({ type: "LWPOLYLINE", points: [[0, 0, 0.5], [0, 0, 0], [5, 0, 0]], closed: false });
    expect((invoke(m, E(dup.handle), "Explode", []) as Obj[]).length).toBe(1);
  });
});

describe("through LISP and VBA", () => {
  it("vla-GetBoundingBox fills the quoted symbols", async () => {
    const out = await runLisp(`(vla-GetBoundingBox (vlax-ename->vla-object (handent "1C")) 'mn 'mx) (princ mn) (princ mx)`, { model: model() });
    expect(out.log).toEqual(["(0 0 0)", "(10 10 0)"]);
  });
  it("VBA ByRef arguments", async () => {
    const out = await runVba(
      `Sub Main()
    Dim mn As Variant, mx As Variant
    ThisDrawing.HandleToObject("1C").GetBoundingBox mn, mx
    Debug.Print mx(0), mx(1)
End Sub`,
      { model: model() },
    );
    expect(out.log).toEqual(["10 10"]);
  });
});

describe("LISP property functions (W4)", () => {
  it("getpropertyvalue / setpropertyvalue / ispropertyreadonly / dump / method-applicable", async () => {
    const out = await runLisp(
      `(setq e (handent "1A"))
       (princ (getpropertyvalue e "EndPoint/X"))
       (setpropertyvalue e "EndPoint/Y" 4)
       (princ (getpropertyvalue e "EndPoint"))
       (princ (ispropertyreadonly e "ObjectName"))
       (princ (ispropertyreadonly e "Layer"))
       (princ (vlax-method-applicable-p (vlax-ename->vla-object e) "Offset"))
       (princ (vlax-method-applicable-p (vlax-ename->vla-object e) "AddLine"))
       (dumpallproperties e)`,
      { model: model() },
    );
    expect(out.log.slice(0, 6)).toEqual(["10", "(10 4 0)", "1", "0", "T", "nil"]);
    expect(out.log).toContain("Begin dumping object (class: AcDbLine)");
    expect(out.log.some((l) => /^Handle \(RO\) = 1A$/.test(l))).toBe(true);
  });
});

describe("coverage snapshot", () => {
  it("tools/coverage/ref/axobj_members.json matches the table", () => {
    const path = resolve(__dirname, "../../../../tools/coverage/ref/axobj_members.json");
    const now = `${JSON.stringify(memberTable(), null, 1)}\n`;
    if (process.env.AXOBJ_WRITE) writeFileSync(path, now);
    expect(readFileSync(path, "utf8")).toBe(now); // run with AXOBJ_WRITE=1 after changing axobj.ts
  });
});
