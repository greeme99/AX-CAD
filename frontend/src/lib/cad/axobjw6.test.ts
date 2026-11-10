// W6 (plan §22.4): blocks, attributes, groups, dictionaries, selection sets through VBA and LISP.
import { describe, expect, it } from "vitest";
import { DrawingModel, getProp, invoke, itemsOf, NO_TABLES, putProp, type Ent, type Obj } from "./objmodel";
import { runLisp } from "./lisp";
import { runVba } from "./vba";

const ents: Ent[] = [
  { handle: "1A", layer: "0", geom: { type: "LINE", start: [0, 0], end: [10, 0] } },
  { handle: "1B", layer: "0", geom: { type: "CIRCLE", center: [50, 50], radius: 5 } },
  { handle: "1C", layer: "L1", geom: { type: "LINE", start: [100, 100], end: [110, 100] } },
  { handle: "1D", layer: "0", geom: { type: "INSERT", name: "TAG", insert: [0, 0], xscale: 1, yscale: 1, rotation: 0 }, att: { PART: "P-1" } },
];
const model = () => {
  const m = new DrawingModel(ents, [{ name: "0" }, { name: "L1" }], "0", { ...NO_TABLES, regapps: ["MYAPP"], groups: [{ name: "G1", handles: ["1A", "1B"] }] });
  m.blockInfos = [{ name: "TAG", attdefs: [{ tag: "PART", prompt: "Part", default: "" }] }];
  m.dicts = { MYDICT: { type: "DICTIONARY", handle: "A0", entries: { REC: { type: "XRECORD", handle: "A1", tags: [[1, "hello"], [40, 2.5]] } } } };
  return m;
};

describe("Blocks / Block / Attribute", () => {
  it("Blocks.Add, Block.AddLine / AddCircle / AddAttribute, saved as one block create", async () => {
    const m = model();
    const out = await runVba(
      `Sub Main()
    Dim b As Object, a As Object
    Set b = ThisDrawing.Blocks.Add(Array(10, 10, 0), "FLANGE")
    b.AddLine Array(0, 0, 0), Array(20, 0, 0)
    b.AddCircle Array(10, 10, 0), 5
    Set a = b.AddAttribute(2.5, 0, "Size?", Array(0, -5, 0), "size", "DN50")
    a.Layer = "L1"
    Debug.Print ThisDrawing.Blocks.Count, b.Count, b.Name, a.TagString, a.Mode
    Dim e As Object
    For Each e In b
        Debug.Print e.ObjectName
    Next
End Sub`,
      { model: m },
    );
    expect(out.log).toEqual(["2 2 FLANGE SIZE 0", "AcDbLine", "AcDbCircle"]);
    const blocks = m.changes().blocks!;
    expect(blocks).toHaveLength(1);
    expect(blocks[0]).toMatchObject({ op: "create", name: "FLANGE", base: [10, 10], entities: [{ geom: { type: "LINE" } }, { geom: { type: "CIRCLE" } }], attdefs: [{ tag: "SIZE", default: "DN50", layer: "L1", insert: [10, 5] }] });
    expect(m.changes().created).toHaveLength(0); // block contents are not model space entities
  });
  it("rename and delete of a drawing's block; existing contents are the server's", () => {
    const m = model();
    m.blockInfos.push({ name: "OLD" });
    putProp(m, { t: "block", name: "OLD" }, "Name", "NEW");
    expect(() => getProp(m, { t: "block", name: "NEW" }, "Count")).toThrow(/서버/);
    invoke(m, { t: "block", name: "NEW" }, "Delete", []);
    expect(() => invoke(m, { t: "block", name: "TAG" }, "Delete", [])).toThrow(/사용 중/);
    expect(m.changes().blocks).toEqual([{ op: "rename", name: "OLD", new_name: "NEW" }, { op: "delete", name: "NEW" }]);
  });
  it("AttributeReference.TextString writes the reference's value", () => {
    const m = model();
    const [a] = invoke(m, { t: "ent", handle: "1D" }, "GetAttributes", []) as Obj[];
    expect(getProp(m, a, "TagString")).toBe("PART");
    putProp(m, a, "TextString", "P-2");
    expect(m.changes().updated[0].att).toEqual({ PART: "P-2" });
    expect(() => getProp(m, a, "Height")).toThrow(/속성 정의/);
  });
});

describe("Groups", () => {
  it("Add + AppendItems is one create with its members; existing groups are modified", () => {
    const m = model();
    const g = invoke(m, { t: "groups" }, "Add", ["NEWG"]) as Obj;
    invoke(m, g, "AppendItems", [[{ t: "ent", handle: "1C" }]]);
    invoke(m, { t: "group", name: "G1" }, "RemoveItems", [[{ t: "ent", handle: "1B" }]]);
    putProp(m, { t: "group", name: "G1" }, "Layer", "L1");
    expect(m.get("1A")!.layer).toBe("L1");
    expect(getProp(m, { t: "groups" }, "Count")).toBe(2);
    expect(m.changes().groups).toEqual([
      { op: "create", name: "NEWG", selectable: true, handles: ["1C"] },
      { op: "modify", name: "G1", handles: ["1A"] },
    ]);
    expect(() => invoke(m, { t: "group", name: "G1" }, "RemoveItems", [[{ t: "ent", handle: "1A" }]])).toThrow(/Delete/);
  });
});

describe("Dictionaries / XRecord", () => {
  it("reads the drawing's, adds and sets XRecords", async () => {
    const m = model();
    const out = await runVba(
      `Sub Main()
    Dim d As Object, x As Object, t As Variant, v As Variant
    Set d = ThisDrawing.Dictionaries.Item("MYDICT")
    Set x = d.GetObject("REC")
    x.GetXRecordData t, v
    Debug.Print d.Count, x.Name, t(0), v(0), v(1)
    Set x = d.AddXRecord("NEW")
    x.SetXRecordData Array(1, 70), Array("a", 3)
    Debug.Print d.Count
End Sub`,
      { model: m },
    );
    expect(out.log).toEqual(["1 REC 1 hello 2.5", "2"]);
    expect(m.changes().dictionaries!.at(-1)).toEqual({ path: ["MYDICT", "NEW"], op: "set", xrecord: [[1, "a"], [70, 3]] });
  });
});

describe("SelectionSets", () => {
  it("Select all with a filter, window, polygon and Erase", () => {
    const m = model();
    const s = invoke(m, { t: "ssets" }, "Add", ["S1"]) as Obj;
    invoke(m, s, "Select", [5, null, null, [8], ["L1"]]);
    expect(getProp(m, s, "Count")).toBe(1);
    invoke(m, s, "Clear", []);
    invoke(m, s, "Select", [0, [-1, -1, 0], [60, 60, 0]]);
    expect(itemsOf(m, s)!.map((o) => (o as { handle: string }).handle)).toEqual(["1A", "1B"]);
    invoke(m, s, "Clear", []);
    invoke(m, s, "SelectByPolygon", [2, [5, -5, 0, 5, 5, 0]]); // a fence across the first line
    expect(getProp(m, s, "Count")).toBe(1);
    invoke(m, s, "Erase", []);
    expect(m.get("1A")).toBeUndefined();
    expect(() => invoke(m, { t: "ssets" }, "Add", ["s1"])).toThrow(/이미/);
  });
  it("vlax-for walks any collection", async () => {
    const out = await runLisp(`(vlax-for g (vla-get-Groups (vla-get-ActiveDocument (vlax-get-acad-object))) (princ (vla-get-Name g)))`, { model: model() });
    expect(out.log).toEqual(["G1"]);
  });
});

describe("VBA Utility (W5)", () => {
  it("angle and distance text, typed arrays, keywords, object id strings", async () => {
    const answers = ["x", "l"];
    const out = await runVba(
      `Sub Main()
    Dim u As Object, a As Variant
    Set u = ThisDrawing.Utility
    Debug.Print u.AngleToString(3.14159265358979 / 2, 0, 2), u.AngleToString(0.5, 1, 4), u.AngleToString(3.14159265358979, 3, 3)
    Debug.Print u.RealToString(1.5, 2, 3), u.RealToString(1.375, 5, 3), u.RealToString(30.5, 4, 1), u.RealToString(1234.5, 1, 2)
    Debug.Print u.DistanceToReal("2'-6""", 4), u.DistanceToReal("1 1/2", 5), u.DistanceToReal("12.5", 2)
    Debug.Print Round(u.AngleFromXAxis(Array(0, 0, 0), Array(0, 5, 0)), 6)
    u.CreateTypedArray a, vbDouble, 1, 2, 3
    Debug.Print UBound(a), a(2)
    u.InitializeUserInput 1, "Line Circle"
    Debug.Print u.GetKeyword("Line/Circle? "), u.GetInput()
    Debug.Print u.GetObjectIdString(ThisDrawing.HandleToObject("1A"), False)
End Sub`,
      { model: model(), input: async () => answers.shift() ?? null },
    );
    expect(out.log).toEqual([
      "90.00 28d38'52\" 3.142r",
      "1.500 1 3/8 2'-6 1/2\" 1.23E+03",
      "30 1.5 12.5",
      "1.570796",
      "2 3",
      "키워드가 아닙니다: Line / Circle",
      "Line Line",
      "26",
    ]);
  });
});

describe("VBA events (W5)", () => {
  it("ObjectAdded / ObjectModified / ObjectErased and WithEvents x_Modified fire during a run", async () => {
    const out = await runVba(
      `Dim WithEvents L As Object
Sub ThisDrawing_ObjectAdded(ByVal o As Object)
    Debug.Print "added " & o.ObjectName
End Sub
Sub ThisDrawing_ObjectErased(ByVal id As Long)
    Debug.Print "erased " & id
End Sub
Sub L_Modified(ByVal o As Object)
    Debug.Print "L moved"
    o.Layer = "L1"
End Sub
Sub Main()
    Set L = ThisDrawing.HandleToObject("1A")
    ThisDrawing.ModelSpace.AddCircle Array(0, 0, 0), 1
    L.Move Array(0, 0, 0), Array(1, 0, 0)
    ThisDrawing.HandleToObject("1B").Delete
End Sub`,
      { model: model() },
    );
    expect(out.log).toEqual(["added AcDbCircle", "L moved", "erased 27"]);
    expect(out.changes!.updated.find((e) => e.handle === "1A")!.layer).toBe("L1"); // a handler's change is kept, and wakes nothing
  });
  it("a loaded project answers document events", async () => {
    const src = `Sub ThisDrawing_EndCommand(ByVal name As String)
    Debug.Print "after " & name
    ThisDrawing.ModelSpace.AddPoint Array(1, 1, 0)
End Sub`;
    const { eventHandlers, fireVbaEvent } = await import("./vba");
    expect(eventHandlers(src)).toEqual(["EndCommand"]);
    expect(await fireVbaEvent(src, "BeginCommand", ["LINE"], { model: model() })).toBeNull();
    const out = await fireVbaEvent(src, "EndCommand", ["LINE"], { model: model() });
    expect(out!.log).toEqual(["after LINE"]);
    expect(out!.changes!.created).toHaveLength(1);
  });
});

describe("W6 security review", () => {
  it("a renamed block: references show the new name, the request keeps the drawing's (the server renames them)", () => {
    const m = model();
    putProp(m, { t: "block", name: "TAG" }, "Name", "TAG2");
    expect(getProp(m, { t: "ent", handle: "1D" }, "Name")).toBe("TAG2");
    invoke(m, { t: "space" }, "InsertBlock", [[5, 5, 0], "TAG2", 1, 1, 1, 0]);
    const ch = m.changes();
    expect(ch.updated).toEqual([]); // the rename alone is no entity change
    expect(ch.created[0].geom).toMatchObject({ type: "INSERT", name: "TAG" });
    expect(ch.blocks).toEqual([{ op: "rename", name: "TAG", new_name: "TAG2" }]);
    expect(() => putProp(m, { t: "block", name: "TAG2" }, "Name", "*X")).toThrow(/블록 이름/);
    expect(() => invoke(m, { t: "space" }, "InsertBlock", [[0, 0, 0], "NOPE", 1, 1, 1, 0])).toThrow(/없는 블록/);
  });
  it("a block made in this run cannot contain itself; group changes in a row are one modify", () => {
    const m = model();
    const a = invoke(m, { t: "blocks" }, "Add", [[0, 0, 0], "A"]) as Obj;
    const b = invoke(m, { t: "blocks" }, "Add", [[0, 0, 0], "B"]) as Obj;
    invoke(m, a, "InsertBlock", [[0, 0, 0], "B", 1, 1, 1, 0]);
    expect(() => invoke(m, b, "InsertBlock", [[0, 0, 0], "A", 1, 1, 1, 0])).toThrow(/자기 자신/);
    expect(() => invoke(m, a, "InsertBlock", [[0, 0, 0], "A", 1, 1, 1, 0])).toThrow(/자기 자신/);
    for (const h of ["1C", "1D"]) invoke(m, { t: "group", name: "G1" }, "AppendItems", [[{ t: "ent", handle: h }]]);
    expect(m.changes().groups).toEqual([{ op: "modify", name: "G1", handles: ["1A", "1B", "1C", "1D"] }]);
  });
});
