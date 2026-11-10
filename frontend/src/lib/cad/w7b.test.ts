// W7b (plan §22.8): symbol tables (linetypes, text/dimension styles, regapps, layouts) and the rest
// of the layer record through the shared object model, AX-LISP and VBA.
import { describe, expect, it } from "vitest";
import { runLisp, type Host } from "./lisp";
import { DrawingModel, getProp, invoke, putProp, withTableOps, type Tables } from "./objmodel";
import { runVba } from "./vba";

const tables: Tables = {
  linetypes: [{ name: "Continuous", description: "Solid line", pattern: [] }, { name: "CENTER", description: "Center", pattern: [31.75, -6.35, 6.35, -6.35] }],
  textstyles: [{ name: "Standard", font: "txt.shx", height: 0, width: 1, oblique: 0 }],
  dimstyles: [{ name: "Standard", dim: { dimtxt: 2.5, dimasz: 2.5, dimdec: 2 } }],
  regapps: ["ACAD"],
  layouts: ["Layout1"],
};
const model = () => new DrawingModel([], [{ name: "0", color: 7 }, { name: "CUT", color: 1, lwt: 25 }], "0", tables);
const host = (): Host & { model: DrawingModel } => ({ model: model() });

describe("W7b object model", () => {
  it("adds, changes, renames and deletes records and reports the operations", () => {
    const m = model();
    m.tableAdd("textstyle", "KS", { font: "arial.ttf", height: 3.5 });
    m.tableSet("textstyle", "KS", { width: 0.8 }); // folded into the create
    m.tableSet("linetype", "CENTER", { description: "중심선" });
    expect(() => m.tableRename("dimstyle", "Standard", "ISO")).toThrow(/바꿀 수 없습니다/);
    expect(() => m.tableRemove("textstyle", "Standard")).toThrow(/지울 수 없습니다/);
    expect(() => m.tableAdd("linetype", "CENTER")).toThrow(/이미 있습니다/);
    m.tableAdd("regapp", "AXCAD");
    m.layerSet("CUT", { lineweight: 50, plot: false });
    const ch = m.changes();
    expect(ch.tables).toEqual([
      { kind: "textstyle", op: "create", name: "KS", font: "arial.ttf", height: 3.5, width: 0.8 },
      { kind: "linetype", op: "modify", name: "CENTER", description: "중심선" },
      { kind: "regapp", op: "create", name: "AXCAD" },
    ]);
    expect(ch.layerOps).toEqual([{ name: "CUT", lineweight: 50, plot: false }]);
    // the next run starts from the tables as they will be
    const next = withTableOps(tables, ch.tables);
    expect(next.textstyles.map((r) => r.name)).toEqual(["Standard", "KS"]);
    expect(next.regapps).toEqual(["ACAD", "AXCAD"]);
  });
});

describe("W7b AX-LISP", () => {
  it("tblsearch / tblnext over LTYPE, STYLE, DIMSTYLE, APPID and LAYER", async () => {
    const h = host();
    const out = await runLisp(
      `(list (cdr (assoc 3 (tblsearch "LTYPE" "center")))
             (cdr (assoc 2 (tblnext "STYLE" T)))
             (cdr (assoc 140 (tblsearch "DIMSTYLE" "Standard")))
             (cdr (assoc 370 (tblsearch "LAYER" "CUT")))
             (regapp "AXCAD") (regapp "AXCAD")
             (layoutlist))`,
      h,
    );
    expect(out.value).toEqual(["Center", "Standard", 2.5, 25, "AXCAD", [], ["Layout1"]]); // nil is the empty list
    expect(h.model.changes().tables).toEqual([{ kind: "regapp", op: "create", name: "AXCAD" }]);
  });
});

describe("W7b ActiveX / VBA", () => {
  it("collections, records and layer properties", () => {
    const m = model();
    const doc = { t: "doc" as const };
    const lts = getProp(m, doc, "Linetypes") as never;
    expect(getProp(m, lts, "Count")).toBe(2);
    const ks = invoke(m, getProp(m, doc, "TextStyles") as never, "Add", ["KS"]) as never;
    putProp(m, ks, "FontFile", "C:\\Windows\\Fonts\\malgun.ttf"); // the path is dropped
    putProp(m, ks, "Height", 5);
    expect(getProp(m, ks, "FontFile")).toBe("malgun.ttf");
    const cut = { t: "layer" as const, name: "CUT" };
    putProp(m, cut, "Lineweight", 35);
    putProp(m, cut, "Freeze", true);
    putProp(m, cut, "Description", "절단");
    expect(getProp(m, cut, "Lineweight")).toBe(35);
    expect(() => putProp(m, { t: "layer", name: "0" }, "Freeze", true)).toThrow(/현재 레이어/);
    expect(() => putProp(m, cut, "Linetype", "NOPE")).toThrow(/없는 선종류/);
    expect(m.changes().layerOps).toEqual([{ name: "CUT", lineweight: 35, frozen: true, description: "절단" }]);
    expect(getProp(m, getProp(m, doc, "Layouts") as never, "Count")).toBe(2); // Model + Layout1
  });
  it("runs from VBA", async () => {
    const m = model();
    const src = `Sub Main()
    Dim lt As Object
    Set lt = ThisDrawing.Linetypes.Item("CENTER")
    lt.Description = "새 설명"
    ThisDrawing.RegisteredApplications.Add "MYAPP"
    ThisDrawing.Layers.Item("CUT").LayerOn = False
End Sub`;
    const out = await runVba(src, { model: m });
    expect(out.changes!.tables).toEqual([
      { kind: "linetype", op: "modify", name: "CENTER", description: "새 설명" },
      { kind: "regapp", op: "create", name: "MYAPP" },
    ]);
    expect(out.changes!.layerOps).toEqual([{ name: "CUT", off: true }]);
  });
});
