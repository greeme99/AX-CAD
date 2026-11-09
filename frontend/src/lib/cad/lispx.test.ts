// 2D automation A1: entmake/entmod/entdel, ssget filters, vla-/vlax-, vl- utilities, files,
// DCL dialogs and reactors in AX-LISP (plan.md §20).
import { describe, expect, it } from "vitest";
import { parseDcl } from "./dcl";
import { fireReactors, LispError, newSession, runLisp, Sym, type DialogRun, type Host } from "./lisp";
import { DrawingModel, type Ent } from "./objmodel";

const ents: Ent[] = [
  { handle: "A1", layer: "0", geom: { type: "LINE", start: [0, 0], end: [10, 0] } },
  { handle: "A2", layer: "CUT", geom: { type: "CIRCLE", center: [5, 5], radius: 2 } },
  { handle: "A3", layer: "LOCK", geom: { type: "POINT", point: [1, 1] } },
];
const host = (extra: Host = {}): Host & { model: DrawingModel } => ({ model: new DrawingModel(ents, [{ name: "0" }, { name: "CUT" }, { name: "LOCK", locked: true }], "0"), ...extra });
const run = async (src: string, h = host()) => ({ out: await runLisp(src, h), h });

describe("entity lists", () => {
  it("entmake, entmod, entdel and entlast change the drawing copy", async () => {
    const { out } = await run(`
      (entmake '((0 . "CIRCLE") (8 . "CUT") (10 20.0 20.0 0.0) (40 . 3.0) (62 . 1)))
      (setq e (entlast) ed (entget e))
      (setq ed (subst (cons 40 6.0) (assoc 40 ed) ed))
      (entmod ed)
      (setq l (car (entsel)))
      (entdel (handent "A1"))
      (princ (cdr (assoc 40 (entget e))))`, host({ entsel: async () => null }));
    expect(out.log).toEqual(["6"]);
    expect(out.changes!.created).toEqual([{ handle: "~1", layer: "CUT", geom: { type: "CIRCLE", center: [20, 20], radius: 6 }, aci: 1 }]);
    expect(out.changes!.deleted).toEqual(["A1"]);
  });
  it("entmod of an existing entity, entnext walk, tblsearch", async () => {
    const { out } = await run(`
      (setq ed (entget (handent "A1")))
      (entmod (subst '(11 0.0 10.0 0.0) (assoc 11 ed) ed))
      (setq n 0 e (entnext))
      (while e (setq n (1+ n) e (entnext e)))
      (princ n)
      (princ (cdr (assoc 70 (tblsearch "LAYER" "lock"))))`);
    expect(out.log).toEqual(["3", "4"]);
    expect(out.changes!.updated[0].geom).toEqual({ type: "LINE", start: [0, 0], end: [0, 10] });
  });
  it("returns nil with a reason for what cannot be made; locked layers are protected", async () => {
    const { out } = await run(`
      (princ (entmake '((0 . "HATCH"))))
      (princ (entdel (handent "A3")))
      (princ (entmake '((0 . "LINE") (10 0 0) (11 1 1) (8 . "NOPE"))))`);
    expect(out.log[0]).toMatch(/만들거나 고칠 수 없는/);
    expect(out.log).toContain("nil");
    expect(out.log.some((l) => /잠긴 레이어/.test(l))).toBe(true);
    expect(out.log.some((l) => /없는 레이어/.test(l))).toBe(true);
    expect(out.changes).toEqual({ created: [], updated: [], deleted: [], layers: [] });
  });
  it("ssget X with a filter; ssadd/ssdel/ssmemb", async () => {
    const { out } = await run(`
      (setq s (ssget "X" '((0 . "LINE,CIRCLE"))))
      (princ (sslength s))
      (setq s (ssget "X" '((8 . "C*"))))
      (princ (cdr (assoc 0 (entget (ssname s 0)))))
      (setq t2 (ssadd))
      (ssadd (handent "A1") t2)
      (princ (sslength t2))
      (princ (if (ssmemb (handent "A1") t2) "in" "out"))`);
    expect(out.log).toEqual(["2", "CIRCLE", "1", "in"]);
  });
  it("command cannot take an object made in the same run", async () => {
    await expect(run(`(entmake '((0 . "POINT") (10 1 1))) (command "MOVE" (entlast) "" '(0 0) '(1 1))`)).rejects.toThrow(/이번 실행에서 만든 객체/);
  });
});

describe("vla- / vlax-", () => {
  it("draws and edits through ModelSpace like AutoCAD", async () => {
    const { out } = await run(`
      (vl-load-com)
      (setq ms (vla-get-ModelSpace (vla-get-ActiveDocument (vlax-get-acad-object))))
      (setq ln (vla-AddLine ms (vlax-3d-point 0 0 0) (vlax-3d-point 30 40 0)))
      (princ (vla-get-Length ln))
      (vla-put-Color ln 1)
      (vla-Move ln (vlax-3d-point 0 0 0) (vlax-3d-point 10 0 0))
      (princ (vlax-safearray->list (vlax-variant-value (vla-get-StartPoint ln))))
      (setq c (vlax-invoke ms 'AddCircle '(0 0 0) 5))
      (princ (rtos (vlax-get c 'Area) 2 2))
      (setq pl (vla-AddLightWeightPolyline ms (vlax-safearray-fill (vlax-make-safearray vlax-vbDouble '(0 . 7)) '(0 0 10 0 10 10 0 10))))
      (vla-put-Closed pl :vlax-true)
      (princ (vlax-curve-getArea pl))
      (princ (vlax-curve-getDistAtParam pl (vlax-curve-getEndParam pl)))
      (princ (vla-get-ObjectName (vlax-ename->vla-object (handent "A2"))))
      (setq n 0)
      (vlax-for o ms (setq n (1+ n)))
      (princ n)`);
    expect(out.log).toEqual(["50", "(10 0 0)", "78.54", "100", "40", "AcDbCircle", "6"]);
    expect(out.changes!.created.map((e) => e.geom.type)).toEqual(["LINE", "CIRCLE", "LWPOLYLINE"]);
    expect(out.changes!.created[0]).toMatchObject({ aci: 1, geom: { start: [10, 0], end: [40, 40] } });
  });
  it("layers, active layer, booleans and property checks", async () => {
    const { out } = await run(`
      (setq doc (vla-get-ActiveDocument (vlax-get-acad-object)))
      (setq lay (vla-Add (vla-get-Layers doc) "PART"))
      (vla-put-ActiveLayer doc lay)
      (setq t1 (vla-AddText (vla-get-ModelSpace doc) "A-1" '(0 0 0) 3.5))
      (princ (vla-get-Layer t1))
      (princ (vla-get-Closed (vla-AddLightWeightPolyline (vla-get-ModelSpace doc) '(0 0 1 1))))
      (princ (vlax-property-available-p t1 'Radius))
      (princ (vlax-property-available-p t1 'TextString))`);
    expect(out.log).toEqual(["PART", ":VLAX-FALSE", "nil", "T"]);
    expect(out.changes!.layers).toEqual(["PART"]);
  });
  it("explains a missing property, and vl-catch-all-apply catches it", async () => {
    await expect(run(`(vla-get-Radius (vlax-ename->vla-object (handent "A1")))`)).rejects.toThrow(/AcDbLine에 Radius 속성이 없습니다/i);
    const { out } = await run(`
      (setq r (vl-catch-all-apply 'vla-get-Radius (list (vlax-ename->vla-object (handent "A1")))))
      (if (vl-catch-all-error-p r) (princ (vl-catch-all-error-message r)))`);
    expect(out.log[0]).toMatch(/Radius 속성이 없습니다/i);
  });
  it("limit errors are not caught", async () => {
    await expect(run(`(defun f () (while t (+ 1 1))) (vl-catch-all-apply 'f nil)`)).rejects.toThrow(/실행 단계/);
  });
  it("vl- utilities", async () => {
    const { out } = await run(`
      (princ (vl-string-search "b" "abc"))
      (princ (vl-string-subst "X" "b" "abcb"))
      (princ (vl-string-trim " " "  a  "))
      (princ (vl-sort '(3 1 2) '<))
      (princ (vl-remove-if '(lambda (x) (> x 1)) '(1 2 3)))
      (princ (vl-position 2 '(1 2 3)))
      (princ (vl-filename-base "C:/a/b/plate.dxf"))
      (princ (type 1.5))`);
    expect(out.log).toEqual(["1", "aXcb", "a", "(1 2 3)", "(1)", "1", "plate", "REAL"]);
  });
});

describe("files (PC library only)", () => {
  const store = new Map<string, string>([["data.txt", "100\n200\n"]]);
  const files: Host["files"] = { read: async (n) => store.get(n) ?? null, write: async (n, t) => void store.set(n, t) };
  it("reads, writes and appends; paths are reduced to library names", async () => {
    const { out } = await run(
      `
      (setq f (open "C:\\\\work\\\\data.txt" "r") s 0)
      (while (setq ln (read-line f)) (setq s (+ s (atoi ln))))
      (close f)
      (setq w (open "sum.txt" "w"))
      (write-line (itoa s) w)
      (princ "합계" w)
      (close w)
      (setq a (open "sum.txt" "a")) (write-line "!" a) (close a)
      (princ (findfile "nope.txt"))
      (princ (open "nope.txt" "r"))`,
      host({ files }),
    );
    expect(store.get("sum.txt")).toBe("300\n합계!\n");
    expect(out.log).toEqual(["nil", "nil"]);
  });
  it("refuses odd names and reading a write file", async () => {
    await expect(run(`(open ".." "r")`, host({ files }))).rejects.toThrow(/라이브러리 파일 이름/);
    await expect(run(`(open "a<b>.txt" "r")`, host({ files }))).rejects.toThrow(/라이브러리 파일 이름/);
    await expect(run(`(read-line (open "x.txt" "w"))`, host({ files }))).rejects.toThrow(/쓰기용 파일은 읽을 수 없습니다/);
    await expect(run(`(write-line "a" (open "data.txt" "r"))`, host({ files }))).rejects.toThrow(/읽기용 파일에는 쓸 수 없습니다/);
    await expect(run(`(open "x.txt" "r")`)).rejects.toThrow(/파일\(open\)을 쓸 수 없습니다/);
  });
});

const DCL = `
size : dialog {
  label = "판 크기";
  : edit_box { key = "w"; label = "폭"; value = "100"; }
  : popup_list { key = "mat"; label = "재질"; }
  : toggle { key = "hole"; label = "구멍"; value = "1"; }
  ok_cancel;
}`;

describe("DCL dialogs", () => {
  it("parses tiles, predefined buttons and prototypes", () => {
    const d = parseDcl(`${DCL}\nmybtn : button { label = "B"; }\ntwo : dialog { : row { mybtn; : mybtn { key = "x"; } } ok_only; }`);
    const size = d.get("size")!;
    expect(size.label).toBe("판 크기");
    expect(size.children.map((t) => t.type)).toEqual(["edit_box", "popup_list", "toggle", "row"]);
    expect(size.children[3].children.map((t) => t.key)).toEqual(["accept", "cancel"]);
    expect(d.get("two")!.children[0].children[1]).toMatchObject({ type: "button", key: "x", label: "B" });
    expect(() => parseDcl("x : dialog { : grid { } }")).toThrow(/지원하지 않는 타일/);
    expect(() => parseDcl('@include "acad.dcl"')).toThrow(/@include/);
    expect(() => parseDcl("x : dialog { label = ; }")).toThrow();
  });
  it("runs a dialog: lists, actions, get_tile and done_dialog", async () => {
    const files: Host["files"] = { read: async (n) => (n === "size.dcl" ? DCL : null), write: async () => {} };
    let seen: DialogRun | null = null;
    const dialog = async (d: DialogRun) => {
      seen = d;
      expect(d.state().lists.mat).toEqual(["SS400", "SUS304"]);
      expect(d.state().values.w).toBe("100");
      expect((await d.act("w", "250")).done).toBeNull(); // the action sets a value, does not close
      expect(d.state().values.hole).toBe("0");
      return (await d.act("accept", "")).done ?? 0;
    };
    const { out } = await run(
      `
      (setq id (load_dialog "size"))
      (new_dialog "size" id)
      (start_list "mat") (mapcar 'add_list '("SS400" "SUS304")) (end_list)
      (action_tile "w" "(set_tile \\"hole\\" \\"0\\")")
      (action_tile "accept" "(setq w (atof (get_tile \\"w\\"))) (done_dialog 1)")
      (setq r (start_dialog))
      (unload_dialog id)
      (princ r) (princ w)`,
      host({ files, dialog }),
    );
    expect(seen).not.toBeNull();
    expect(out.log).toEqual(["1", "250"]);
  });
  it("a missing dialog file gives a negative id; no dialog without new_dialog", async () => {
    const files: Host["files"] = { read: async () => null, write: async () => {} };
    const { out } = await run(`(princ (load_dialog "none"))`, host({ files }));
    expect(out.log).toEqual(["-1"]);
    await expect(run(`(start_dialog)`, host({ files }))).rejects.toThrow(/new_dialog/);
  });
});

describe("reactors", () => {
  it("stay in the session and run on screen events", async () => {
    const session = newSession();
    const h = host();
    await runLisp(
      `
      (defun on-end (r cmds) (setq ended (car cmds)) (entmake '((0 . "POINT") (10 0 0))))
      (defun on-mod (o r p) (setq moved (vla-get-Handle o)))
      (setq cr (vlr-command-reactor "data" '((:vlr-commandEnded . on-end))))
      (vlr-object-reactor (list (vlax-ename->vla-object (handent "A2"))) nil '((:vlr-modified . on-mod)))`,
      h,
      undefined,
      session,
    );
    expect(session.reactors.length).toBe(2);
    const out = await fireReactors(session, { kind: "command", name: ":vlr-commandEnded", command: "LINE" }, host());
    expect(out!.changes!.created.map((e) => e.geom.type)).toEqual(["POINT"]);
    await fireReactors(session, { kind: "object", name: ":vlr-modified", handle: "A2" }, host());
    expect(await fireReactors(session, { kind: "object", name: ":vlr-modified", handle: "A1" }, host())).toBeNull();
    const after = await runLisp(`(princ ended) (princ moved) (vlr-remove cr) (princ (length (vlr-reactors)))`, host(), undefined, session);
    expect(after.log).toEqual(["LINE", "A2", "1"]);
    expect(await fireReactors(session, { kind: "command", name: ":vlr-commandEnded", command: "LINE" }, host())).toBeNull();
  });
  it("reactor callbacks have the same limits", async () => {
    const session = newSession();
    await runLisp(`(defun spin (r c) (while t 1)) (vlr-command-reactor nil '((:vlr-commandEnded . spin)))`, host(), undefined, session);
    await expect(fireReactors(session, { kind: "command", name: ":vlr-commandEnded", command: "X" }, host())).rejects.toThrow(LispError);
  });
});

describe("security review fixes (A1)", () => {
  it("a DCL definition chain cannot grow past the tile limit", () => {
    const chain = ["d0 : row { : text { label = \"a\"; } : text { label = \"b\"; } }"];
    for (let i = 1; i < 40; i++) chain.push(`d${i} : row { : d${i - 1} { } : d${i - 1} { } }`);
    expect(() => parseDcl(chain.join("\n"))).toThrow(/타일은 500개까지/);
  });
  it("wildcard filters do not backtrack catastrophically", async () => {
    const long = "a".repeat(60_000);
    const m = new DrawingModel([{ handle: "T", layer: "0", geom: { type: "TEXT", insert: [0, 0], height: 1, value: long, rotation: 0 } }], [{ name: "0" }], "0");
    const t0 = Date.now();
    const out = await runLisp(`(princ (if (ssget "X" '((1 . "*a*a*a*a*a*a*a*a*b"))) "hit" "none"))`, { model: m });
    expect(out.log).toEqual(["none"]);
    expect(Date.now() - t0).toBeLessThan(2000);
  });
  it("callbacks count steps and depth: recursion through vl-catch-all-apply stops", async () => {
    await expect(run(`(defun f (n) (vl-catch-all-apply 'f (list n))) (f 1)`)).rejects.toThrow(/너무 깊습니다/);
    const { out } = await run(`(setq l nil) (repeat 300 (setq l (cons (rem (* (length l) 7919) 301) l))) (setq s (vl-sort l '<)) (princ (list (car s) (last s) (length s)))`);
    expect(out.log).toEqual(["(0 300 300)"]);
  });
  it("tile keys are keys, not object members; no nested dialogs", async () => {
    const files: Host["files"] = { read: async () => DCL, write: async () => {} };
    const dialog = async (d: DialogRun) => (await d.act("accept", "")).done ?? 0;
    const { out } = await run(`(setq id (load_dialog "size")) (new_dialog "size" id) (princ (get_tile "constructor")) (start_dialog)`, host({ files, dialog }));
    expect(out.log).toEqual(["nil"]);
    const nested = async (d: DialogRun) => (await d.act("w", "1")).done ?? 0;
    await expect(run(`(setq id (load_dialog "size")) (new_dialog "size" id) (action_tile "w" "(new_dialog \\"size\\" id)") (start_dialog)`, host({ files, dialog: nested }))).rejects.toThrow(/다른 대화상자를 열 수 없습니다/);
  });
  it("layer names follow the server's rule; ELLIPSE ratio is checked", async () => {
    await expect(run(`(vla-Add (vla-get-Layers (vla-get-ActiveDocument (vlax-get-acad-object))) (strcat "A" (chr 9) "B"))`)).rejects.toThrow(/레이어 이름으로 쓸 수 없습니다/);
    const { out } = await run(`(princ (entmake '((0 . "ELLIPSE") (10 0 0) (11 10 0) (40 . 2.0))))`);
    expect(out.log[0]).toMatch(/비율은 0보다 크고 1 이하/);
  });
});

describe("AutoLISP core functions (A5)", () => {
  const run = async (src: string, host: Host = {}) => (await runLisp(src, host)).value;
  it("symbols and evaluation see the caller's variables", async () => {
    expect(await run("(set 'a 5) (+ a 1)")).toBe(6);
    expect(await run("(defun f (x / y) (set 'y (* x 2)) y) (f 4)")).toBe(8);
    expect(await run("(setq n 3) (boundp 'n)")).toEqual(Sym.of("T"));
    expect(await run("(boundp 'nope)")).toEqual([]);
    expect(await run("(setq q 9) (vl-symbol-value 'q)")).toBe(9);
    expect(await run("(eval '(+ 1 2))")).toBe(3);
    expect(await run("(eval (list '* 2 3))")).toBe(6);
    expect(await run("(mapcar (function (lambda (x) (* x x))) '(1 2 3))")).toEqual([1, 4, 9]);
    expect(await run("(vl-symbolp 'a)")).toEqual(Sym.of("T"));
    expect(await run("(vl-symbolp 1)")).toEqual([]);
  });
  it("bit and number helpers", async () => {
    expect(await run("(list (gcd 12 18) (logand 12 10) (logior 12 10) (lsh 1 4) (lsh 16 -2) (~ 5))")).toEqual([6, 8, 14, 16, 4, -6]);
    expect(await run('(acad_strlsort (list "b" "B" "a"))')).toEqual(["a", "B", "b"]);
    expect(await run("(vl-list-length '(1 2 3))")).toBe(3);
  });
  it("wcmatch and strings", async () => {
    expect(await run('(wcmatch "ABC1" "A*,[~A]#")')).toEqual(Sym.of("T"));
    expect(await run('(wcmatch "abc" "ABC")')).toEqual([]);
    expect(await run('(vl-string-translate "abc" "xyz" "a-b-c")')).toBe("x-y-z");
    await expect(run('(vl-string-translate "ab" "x" "a")')).rejects.toBeInstanceOf(LispError);
    expect(await run('(vl-filename-directory "C:\\\\a\\\\b.txt")')).toBe("C:/a");
    expect(await run('(vl-filename-directory "b.txt")')).toBe("");
    expect(await run("(trans '(1 2 3) 0 1)")).toEqual([1, 2, 3]);
    expect(await run('(read "(1 (2 3))")')).toEqual([1, [2, 3]]);
    expect(await run('(read "")')).toEqual([]);
  });
  it("distof and angtof read what rtos / angtos write", async () => {
    expect(await run('(distof "1.5")')).toBe(1.5);
    expect(await run('(distof "1-1/2" 5)')).toBe(1.5);
    expect(await run('(distof "3/4" 5)')).toBe(0.75);
    expect(await run('(distof "x")')).toEqual([]);
    expect(await run('(angtof "180")')).toBeCloseTo(Math.PI, 9);
    expect(await run('(angtof "200g")')).toBeCloseTo(Math.PI, 9);
    expect(await run('(angtof "90d30\'" 1)')).toBeCloseTo(((90.5) * Math.PI) / 180, 9);
  });
  it("initget + getkword take keywords, short or full; initget 1 refuses an empty answer", async () => {
    const answers = ["", "n", "maybe", "Yes"];
    const host: Host = { input: async () => (answers.length ? (answers.shift() as string) : null) };
    expect(await run('(initget 1 "Yes No") (getkword "계속? ")', host)).toBe("No"); // "" refused, then "n"
    const h2: Host = { input: async () => "maybe" };
    expect(await run('(initget "Yes No") (getkword "?")', h2)).toEqual([]); // five bad answers
    const h3: Host = { input: async () => "yes" };
    expect(await run('(initget "Yes No") (getkword "?")', h3)).toBe("Yes");
  });
  it("(command \"undo\" ...) is accepted: a routine is one undo step already", async () => {
    const o = await runLisp('(command "_.undo" "_begin") (command "LINE" "0,0" "5,0" "") (command "_.undo" "_end")');
    expect(o.segments.flatMap((s) => s.inputs)).toEqual(["LINE", "0,0", "5,0", ""]);
    expect(o.log.length).toBeGreaterThan(0);
    const o2 = await runLisp('(command "undo" "mark") (command "CIRCLE" "0,0" "3")');
    expect(o2.segments.flatMap((s) => s.inputs)).toEqual(["CIRCLE", "0,0", "3"]);
  });
  it("load runs a library file here; exit stops; *error* hears the message", async () => {
    const files = new Map([["util.lsp", "(defun twice (x) (* 2 x))"]]);
    const host: Host = { files: { read: async (n) => files.get(n) ?? null, write: async () => {} } };
    expect(await run('(load "util") (twice 21)', host)).toBe(42);
    expect(await run('(load "nope.lsp" "fallback")', host)).toBe("fallback");
    await expect(run('(load "nope")', host)).rejects.toBeInstanceOf(LispError);
    files.set("self.lsp", '(load "self")');
    await expect(run('(load "self")', host)).rejects.toThrow(/3단계/);
    await expect(run("(exit)")).rejects.toThrow(/exit abort/);
    const s = newSession();
    await expect(runLisp('(defun *error* (m) (setq seen m)) (car 5)', {}, undefined, s)).rejects.toBeInstanceOf(LispError);
    expect(JSON.stringify([...s.globals.entries()].map(([k, v]) => [k.name, v]).find(([k]) => k === "SEEN"))).toContain("car");
  });
  it("a missing argument is an error message, not a crash", async () => {
    for (const f of ["tblnext", "strlen", "atoi", "substr", "wcmatch", "distof", "vl-string-translate"]) await expect(runLisp(`(${f})`)).rejects.toBeInstanceOf(LispError);
  });
});
