// W2 (plan §22.4): XDATA, dictionaries / XRecords, LDATA, layer states, tblobjname, nentsel in AX-LISP.
import { describe, expect, it } from "vitest";
import { runLisp, type Host } from "./lisp";
import { DrawingModel, NO_TABLES, type Ent } from "./objmodel";
import { diffEdits } from "./edits";

const ents: Ent[] = [
  { handle: "1A", layer: "0", geom: { type: "LINE", start: [0, 0], end: [10, 0] } },
  { handle: "1B", layer: "0", geom: { type: "INSERT", name: "B", insert: [5, 5], xscale: 2, yscale: 2, rotation: 90 } },
];
const tables = { ...NO_TABLES, regapps: ["ACAD", "MYAPP"] };
/** The saved drawing as the server answers (GET …/entities, …/dictionaries). */
const server: NonNullable<Host["objects"]> = {
  entity: async (h) => (h === "1A" ? { type: "LINE", tags: [[0, "LINE"], [5, "1A"], [102, "{ACAD_XDICTIONARY"], [360, "2F"], [102, "}"], [1001, "MYAPP"], [1000, "saved"], [1010, 1], [1020, 2], [1030, 3], [1001, "OTHER"], [1070, 7]] } : null),
  dict: async (path, owner) => {
    const key = `${owner ?? ""}/${path.join("/")}`;
    const db: Record<string, Awaited<ReturnType<NonNullable<Host["objects"]>["dict"]>>> = {
      "/": { type: "DICTIONARY", handle: "C", entries: [{ name: "ACAD_GROUP", type: "DICTIONARY", handle: "D" }, { name: "MYDATA", type: "DICTIONARY", handle: "E" }] },
      "/MYDATA": { type: "DICTIONARY", handle: "E", entries: [{ name: "R1", type: "XRECORD", handle: "F" }] },
      "/MYDATA/R1": { type: "XRECORD", handle: "F", tags: [[1, "steel"], [40, 2.5]] },
      "/ACAD_GROUP": { type: "DICTIONARY", handle: "D", entries: [] },
      "1A/": { type: "DICTIONARY", handle: "2F", entries: [] },
    };
    return db[key] ?? null;
  },
};
const host = (extra: Host = {}): Host => ({ model: new DrawingModel(ents, [{ name: "0" }, { name: "A" }, { name: "B", color: 3 }], "0", tables), objects: server, ...extra });
const run = async (src: string, h: Host = host()) => runLisp(src, h);

describe("XDATA", () => {
  it("entmake / entmod set it; entget '(apps) shows saved and new data; xdroom counts it", async () => {
    const out = await run(`
      (entmake '((0 . "LINE") (10 0 0 0) (11 1 1 0) (-3 ("MYAPP" (1000 . "new") (1070 . 5)))))
      (princ (assoc -3 (entget (entlast) '("MYAPP"))))
      (setq e (handent "1A"))
      (princ (assoc -3 (entget e '("MYAPP" "OTHER"))))
      (entmod (append (entget e) '((-3 ("MYAPP" (1000 . "changed"))))))
      (princ (assoc -3 (entget e '("MY*"))))
      (princ (xdroom e))
      (princ (entmake '((0 . "LINE") (10 0 0 0) (11 1 1 0) (-3 ("NOTREG" (1000 . "x"))))))`);
    expect(out.log[0]).toBe('(-3 ("MYAPP" (1000 . "new") (1070 . 5)))');
    expect(out.log[1]).toBe('(-3 ("MYAPP" (1000 . "saved") (1010 1 2 3)) ("OTHER" (1070 . 7)))');
    expect(out.log[2]).toBe('(-3 ("MYAPP" (1000 . "changed")))');
    expect(Number(out.log[3])).toBeLessThan(16_383);
    expect(out.log.at(-1)).toBe("nil"); // an application not registered: entmake fails
    expect(out.changes!.created[0].xd).toEqual({ MYAPP: [[1000, "new"], [1070, 5]] });
    expect(out.changes!.updated[0].xd).toEqual({ MYAPP: [[1000, "changed"]] });
  });
  it("the edit request carries what changed", () => {
    const base = [{ handle: "1A", layer: "0", geom: ents[0].geom }];
    const body = diffEdits(base, [{ ...base[0], xd: { MYAPP: [[1000, "x"]] } }]);
    expect(body.modified[0]).toMatchObject({ handle: "1A", xdata: { MYAPP: [[1000, "x"]] } });
  });
});

describe("dictionaries", () => {
  it("namedobjdict, dictsearch, dictnext over the saved drawing", async () => {
    const out = await run(`
      (setq nod (namedobjdict))
      (setq d (cdr (assoc -1 (dictsearch nod "mydata"))))
      (princ (cdr (assoc 1 (dictsearch d "R1"))))
      (princ (cdr (assoc 5 (dictnext nod T))))
      (princ (dictsearch nod "NONE"))`);
    expect(out.log).toEqual(["steel", "D", "nil"]);
  });
  it("dictadd / dictremove / dictrename become edit API operations; reads see them", async () => {
    const out = await run(`
      (setq nod (namedobjdict))
      (setq mine (dictadd nod "AXDATA" (entmakex '((0 . "DICTIONARY") (100 . "AcDbDictionary")))))
      (dictadd mine "PART" (entmakex '((0 . "XRECORD") (100 . "AcDbXrecord") (1 . "plate") (70 . 3))))
      (princ (cdr (assoc 70 (dictsearch mine "PART"))))
      (setq d (cdr (assoc -1 (dictsearch nod "MYDATA"))))
      (princ (dictrename d "R1" "R2"))
      (princ (cdr (assoc 1 (dictsearch d "R2"))))
      (princ (dictsearch d "R1"))
      (dictremove d "R2")
      (princ (dictsearch d "R2"))
      (princ (dictadd nod "MYDATA" (entmakex '((0 . "XRECORD") (1 . "dup")))))`);
    expect(out.log).toEqual(["3", "R2", "steel", "nil", "nil", "nil"]);
    expect(out.changes!.dictionaries).toEqual([
      { path: ["AXDATA", "PART"], op: "set", xrecord: [[1, "plate"], [70, 3]] },
      { path: ["MYDATA", "R2"], op: "set", xrecord: [[1, "steel"], [40, 2.5]] },
      { path: ["MYDATA", "R1"], op: "delete" },
      { path: ["MYDATA", "R2"], op: "delete" },
    ]);
  });
  it("an entity's extension dictionary appears in entget (360) and takes entries", async () => {
    const out = await run(`
      (setq xd (cdr (assoc 360 (entget (handent "1A")))))
      (dictadd xd "NOTE" (entmakex '((0 . "XRECORD") (300 . "hi"))))
      (princ (cdr (assoc 300 (dictsearch xd "NOTE"))))`);
    expect(out.log).toEqual(["hi"]);
    expect(out.changes!.dictionaries).toEqual([{ path: ["NOTE"], op: "set", handle: "1A", xrecord: [[300, "hi"]] }]);
  });
  it("system dictionaries stay as they are", async () => {
    const out = await run(`(princ (dictremove (namedobjdict) "ACAD_GROUP"))`);
    expect(out.log[0]).toMatch(/시스템 사전/);
    expect(out.changes!.dictionaries).toBeUndefined();
  });
});

describe("LDATA, layer states, tables, nested selection", () => {
  it("vlax-ldata-put / get / list / delete keep LISP values in the drawing", async () => {
    const out = await run(`
      (vlax-ldata-put "AXSET" "size" '(10 20 "mm"))
      (princ (vlax-ldata-get "AXSET" "size"))
      (princ (vlax-ldata-get "AXSET" "none" 0))
      (vlax-ldata-put (vlax-ename->vla-object (handent "1A")) "tag" 'a1)
      (princ (vlax-ldata-list "AXSET"))
      (princ (vlax-ldata-test (vlax-get-acad-object)))
      (princ (vlax-ldata-delete "AXSET" "size"))
      (princ (vlax-ldata-get "AXSET" "size"))`);
    expect(out.log).toEqual(['(10 20 "mm")', "0", '(("size" 10 20 "mm"))', "nil", "T", "nil"]);
    expect(out.changes!.dictionaries!.map((d) => [d.handle ?? "", d.path.join("/"), d.op])).toEqual([["", "AXSET/size", "set"], ["1A", "AXCAD_LDATA/tag", "set"], ["", "AXSET/size", "delete"]]);
  });
  it("layerstate-save / restore / rename / compare / export / import", async () => {
    const store = new Map<string, string>();
    const files = { read: async (n: string) => store.get(n) ?? null, write: async (n: string, t: string) => void store.set(n, t) };
    const out = await run(
      `(layerstate-save "Plot" 255 nil)
       (princ (layerstate-getnames))
       (princ (layerstate-has "Plot"))
       (setq l (vla-item (vla-get-layers (vla-get-activedocument (vlax-get-acad-object))) "B"))
       (vla-put-color l 1)
       (princ (layerstate-compare "Plot" nil))
       (princ (layerstate-restore "Plot" nil 0))
       (princ (layerstate-compare "Plot" nil))
       (princ (layerstate-getlastrestored))
       (layerstate-export "Plot" "plot.las")
       (princ (layerstate-rename "Plot" "Print"))
       (princ (layerstate-getlayers "Print"))
       (princ (layerstate-removelayers "Print" '("A")))
       (princ (layerstate-getlayers "Print"))
       (princ (layerstate-delete "Print"))
       (princ (layerstate-import "plot.las"))
       (princ (layerstate-has "Plot"))`,
      host({ files }),
    );
    expect(out.log).toEqual(['("Plot")', "T", '("B")', "Plot", "nil", "Plot", "T", '("0" "A" "B")', "T", '("0" "B")', "T", "Plot", "T"]);
    expect(out.changes!.layerOps).toContainEqual(expect.objectContaining({ name: "B", color: 3 }));
    expect(store.get("plot.las")).toMatch(/^AXCAD-LAYERSTATE\tPlot/);
  });
  it("tblobjname names a table record entget can read", async () => {
    const out = await run(`(princ (cdr (assoc 62 (entget (tblobjname "LAYER" "b")))))`);
    expect(out.log).toEqual(["3"]);
  });
  it("nentsel on a block reference gives its transform and container", async () => {
    const out = await run(`(setq r (nentsel)) (princ (length r)) (princ (last (caddr r)))`, host({ entsel: async () => ({ sel: { handle: "1B", layer: "0", geom: ents[1].geom }, at: [5, 5] }) }));
    expect(out.log).toEqual(["4", "(5 5 0)"]);
  });
});

describe("W2 security review", () => {
  it("refuses AutoCAD's own XDATA, oversized UTF-8 XDATA and bad XRecord values", async () => {
    const big = "가".repeat(200); // 600 bytes each
    const out = await run(`
      (princ (entmake '((0 . "LINE") (10 0 0 0) (11 1 1 0) (-3 ("ACAD" (1000 . "x"))))))
      (setq xs nil) (repeat 30 (setq xs (cons (cons 1000 "${big}") xs)))
      (princ (entmake (list '(0 . "LINE") '(10 0 0 0) '(11 1 1 0) (list -3 (cons "MYAPP" xs)))))
      (princ (dictadd (namedobjdict) "K" (entmakex '((0 . "XRECORD") (70 . 99999)))))
      (princ (dictsearch (namedobjdict) ""))`);
    expect(out.log[0]).toMatch(/시스템 응용 프로그램/);
    expect(out.log.some((l) => /16 KB/.test(l))).toBe(true);
    expect(out.log.some((l) => /XRecord 70/.test(l))).toBe(true);
    expect(out.log.at(-1)).toBe("nil");
    expect(out.changes!.created).toEqual([]);
  });
  it("ldata refuses values that grow past their print; earlier unsaved changes are seen", async () => {
    await expect(run(`(setq x '(1)) (repeat 40 (setq x (list x x))) (vlax-ldata-put "D" "k" x)`)).rejects.toThrow(/숫자·문자열/);
    const m = new DrawingModel(ents, [{ name: "0" }], "0", tables);
    m.seedDicts([{ path: ["D", "k"], op: "set", xrecord: [[1, "42"]] }]);
    const out = await run(`(princ (vlax-ldata-get "D" "k"))`, { model: m, objects: server });
    expect(out.log).toEqual(["42"]);
    expect(out.changes!.dictionaries).toBeUndefined(); // not sent twice
  });
});
