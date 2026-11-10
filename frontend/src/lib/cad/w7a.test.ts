// W7a (plan §22.8): entity properties (lineweight, true colour, transparency, linetype scale,
// visibility) and MTEXT through the shared object model, LISP groups, VBA and the edit diff.
import { describe, expect, it } from "vitest";
import { diffEdits, type EditEntity } from "./edits";
import { entityText, mtextPlain, type Geom } from "./geom";
import { DrawingModel, fromGroups, getProp, invoke, putProp, toGroups, type Ent } from "./objmodel";
import { runVba } from "./vba";

const MT: Geom = { type: "MTEXT", insert: [10, 20], height: 3.5, width: 40, value: "첫 줄\\P둘째 줄", rotation: 30, attachment: 7 };
const line: Ent = { handle: "A1", layer: "0", geom: { type: "LINE", start: [0, 0], end: [10, 0] }, lwt: 50, tr: 40 };

describe("W7a groups", () => {
  it("writes and reads the new properties and MTEXT", () => {
    const e: Ent = { handle: "B1", layer: "0", geom: MT, lwt: 25, tc: 0x336699, tr: 40, lts: 2, inv: true };
    const g = toGroups(e);
    expect(g).toEqual(expect.arrayContaining([[370, 25], [420, 0x336699], [48, 2], [60, 1], [41, 40], [71, 7], [1, "첫 줄\\P둘째 줄"]]));
    const back = fromGroups(g);
    expect(back.geom).toMatchObject({ type: "MTEXT", width: 40, attachment: 7, value: "첫 줄\\P둘째 줄" });
    expect((back.geom as { rotation: number }).rotation).toBeCloseTo(30);
    expect(back.props).toEqual({ lwt: 25, tc: 0x336699, tr: 40, lts: 2, inv: true });
  });
  it("refuses values the server would refuse", () => {
    expect(() => fromGroups([[0, "LINE"], [10, [0, 0]], [11, [1, 1]], [370, 7]])).toThrow(/370/);
    expect(() => fromGroups([[0, "LINE"], [10, [0, 0]], [11, [1, 1]], [420, -1]])).toThrow(/420/);
    expect(() => fromGroups([[0, "MTEXT"], [10, [0, 0]], [40, 2.5], [1, "x"], [71, 12]])).toThrow(/부착점/);
  });
});

describe("W7a ActiveX properties", () => {
  const model = () => new DrawingModel([line], [{ name: "0" }], "0");
  it("reads and sets Lineweight, EntityTransparency, LinetypeScale and Visible", () => {
    const m = model();
    const o = { t: "ent" as const, handle: "A1" };
    expect(getProp(m, o, "Lineweight")).toBe(50);
    expect(getProp(m, o, "EntityTransparency")).toBe("40");
    expect(getProp(m, o, "LinetypeScale")).toBe(1);
    putProp(m, o, "Lineweight", 35);
    putProp(m, o, "EntityTransparency", "ByLayer");
    putProp(m, o, "LinetypeScale", 0.5);
    putProp(m, o, "Visible", false);
    expect(m.get("A1")).toMatchObject({ lwt: 35, lts: 0.5, inv: true });
    expect(m.get("A1")).not.toHaveProperty("tr"); // back to ByLayer
    expect(m.changes().updated[0]).not.toHaveProperty("tr");
    expect(() => putProp(m, o, "Lineweight", 7)).toThrow(/Lineweight/);
    expect(() => putProp(m, o, "EntityTransparency", 95)).toThrow(/EntityTransparency/);
  });
  it("adds MTEXT and edits its properties", () => {
    const m = model();
    const made = invoke(m, { t: "space" }, "AddMText", [[1, 2, 0], 30, "A\\PB"]) as { t: "ent"; handle: string };
    expect(getProp(m, made, "ObjectName")).toBe("AcDbMText");
    putProp(m, made, "Width", 50);
    putProp(m, made, "AttachmentPoint", 5);
    expect(m.get(made.handle)!.geom).toMatchObject({ type: "MTEXT", width: 50, attachment: 5, value: "A\\PB", insert: [1, 2] });
  });
  it("runs from VBA", async () => {
    const m = model();
    const src = `Sub Main()
    Dim t As Object
    Set t = ThisDrawing.ModelSpace.AddMText(Pt(0, 0), 20, "Hello")
    t.Lineweight = 50
    t.Height = 5
End Sub
Function Pt(x As Double, y As Double) As Variant
    Dim p(0 To 2) As Double
    p(0) = x: p(1) = y: p(2) = 0
    Pt = p
End Function`;
    const out = await runVba(src, { model: m });
    expect(out.changes!.created).toHaveLength(1);
    expect(out.changes!.created[0]).toMatchObject({ lwt: 50, geom: { type: "MTEXT", height: 5, value: "Hello" } });
  });
});

describe("W7a edit diff", () => {
  it("sends properties that changed, and defaults when cleared", () => {
    const base: EditEntity[] = [{ handle: "A1", layer: "0", geom: line.geom, lwt: 50, tr: 40 }];
    const cur: EditEntity[] = [{ handle: "A1", layer: "0", geom: line.geom, lwt: 25 }, { handle: "new-1", layer: "0", geom: MT, inv: true, tc: 0xff0000 }];
    const body = diffEdits(base, cur);
    expect(body.modified).toEqual([{ handle: "A1", layer: "0", geom: line.geom, lineweight: 25, transparency: 0 }]);
    expect(body.created).toEqual([{ layer: "0", geom: MT, true_color: 0xff0000, visible: false }]);
  });
});

describe("W7a text preview", () => {
  it("shows MTEXT as one plain line until the server draws it", () => {
    expect(mtextPlain("{\\H2;큰} 글\\P둘째")).toBe("큰 글 둘째");
    expect(entityText({ geom: MT } as never)?.value).toBe("첫 줄 둘째 줄");
  });
});
