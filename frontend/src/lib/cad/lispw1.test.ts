// W1 · W3 (plan §22.4): the rest of the AutoLISP core, files and settings.
import { describe, expect, it } from "vitest";
import { parseDcl } from "./dcl";
import { fireReactors, LispError, newSession, runLisp, type DialogRun, type Host } from "./lisp";
import { DrawingModel, type Ent } from "./objmodel";

const ents: Ent[] = [
  { handle: "A1", layer: "0", geom: { type: "LINE", start: [0, 0], end: [10, 0] } },
  { handle: "A2", layer: "0", geom: { type: "CIRCLE", center: [5, 5], radius: 2 } },
  { handle: "A3", layer: "0", geom: { type: "LWPOLYLINE", points: [[0, 0, 0], [10, 0, 0], [10, 10, 0]], closed: false } },
];
const host = (extra: Host = {}): Host => ({ model: new DrawingModel(ents, [{ name: "0" }, { name: "LOCK", locked: true }], "0"), ...extra });
const out = async (src: string, h: Host = host(), session = newSession()) => (await runLisp(src, h, undefined, session)).log;

/** An in-memory PC LISP library with the server's rule (programs write data files only). */
function library(seed: Record<string, string> = {}) {
  const store = new Map(Object.entries(seed));
  const files: NonNullable<Host["files"]> = {
    read: async (n) => store.get(n) ?? null,
    write: async (n, t) => {
      if (!/\.(txt|csv|dat|log|dcl)$/i.test(n)) throw new Error("데이터 파일만");
      store.set(n, t);
    },
    list: async () => [...store.keys()].map((name) => ({ name, modified: "2026-11-10T09:08:07.006Z" })),
    remove: async (n) => store.delete(n),
  };
  return { store, files };
}

describe("core", () => {
  it("boole, cvunit, vl-list*, vl-sort-i, string helpers, snvalid", async () => {
    const log = await out(`
      (princ (boole 1 12 5)) (princ (boole 6 12 5)) (princ (boole 7 12 5))
      (princ (cvunit 1 "inch" "mm")) (princ (cvunit 180 "degree" "radian")) (princ (cvunit 1 "kg" "m"))
      (princ (vl-list* 1 2 '(3 4))) (princ (vl-list* 1 2))
      (princ (vl-sort-i '(3 1 2) '<))
      (princ (vl-string-elt "ABC" 1)) (princ (vl-string-mismatch "VL-FUN" "VL-VAR"))
      (princ (vl-member-if-not 'numberp '(1 2 "a" 3)))
      (princ (snvalid "Layer 1")) (princ (snvalid "a<b"))`);
    expect(log).toEqual(["4", "9", "13", "25.4", "3.14159265", "nil", "(1 2 3 4)", "(1 . 2)", "(1 2 0)", "66", "3", '("a" 3)', "T", "nil"]);
  });
  it("defun-q lists can be read and changed; trace logs calls", async () => {
    const log = await out(`
      (defun-q sq (x) (* x x))
      (princ (defun-q-list-ref 'sq))
      (defun-q-list-set 'sq '((x) (+ x x)))
      (trace sq)
      (princ (sq 4))
      (untrace sq)
      (sq 1)`);
    expect(log).toEqual(["((X) (* X X))", "Entering (SQ 4)", "Result:  8", "8"]);
  });
  it("atoms-family, error modes, blackboard across runs", async () => {
    const session = newSession();
    await out(`(vl-bb-set 'shared 42) (*push-error-using-command*) (*pop-error-mode*)`, host(), session);
    const log = await out(`(princ (vl-bb-ref 'shared)) (princ (atoms-family 1 '("CAR" "NO-SUCH-FN")))`, host(), session);
    expect(log).toEqual(["42", '("CAR" nil)']);
  });
  it("safearrays keep bounds and types", async () => {
    const log = await out(`
      (setq a (vlax-make-safearray vlax-vbDouble '(0 . 2)))
      (vlax-safearray-put-element a 1 7.5)
      (princ (vlax-safearray-get-element a 1))
      (princ (list (vlax-safearray-get-dim a) (vlax-safearray-get-l-bound a 1) (vlax-safearray-get-u-bound a 1)))
      (setq m (vlax-make-safearray vlax-vbDouble '(1 . 2) '(1 . 2)))
      (vlax-safearray-put-element m 2 1 3.0)
      (princ (vlax-safearray-get-element m 2 1))
      (princ (vlax-variant-change-type "12" vlax-vbInteger))`);
    // vlax-vbDouble is not a defined symbol here: nil -> a list of zeros (type nil -> 0 cells)
    expect(log[0]).toBe("7.5");
    expect(log[1]).toBe("(1 0 2)");
    expect(log[2]).toBe("3");
    expect(log[3]).toBe("12");
  });
  it("textbox, xdsize, ssnamex, vports, getcname", async () => {
    const log = await out(`
      (princ (textbox '((1 . "AB") (40 . 2.0))))
      (princ (xdsize '((-3 ("APP" (1000 . "abc") (1070 . 1))))))
      (princ (ssnamex (ssget "X") 0))
      (princ (vports))
      (princ (getcname "line"))`);
    expect(log[0]).toBe("((0 -0.4 0) (3 2 0))");
    expect(log[1]).toBe("16"); // the application name counts too
    expect(log[2]).toBe("((0 <객체 A1> 0))");
    expect(log[3]).toBe("((1 (0 0) (1 1)))");
    expect(log[4]).toBe("_LINE");
  });
});

describe("curves and osnap", () => {
  it("vlax-curve-* distances, params, closest point and derivatives", async () => {
    const log = await out(`
      (setq l (vlax-ename->vla-object (handent "A1")) p (vlax-ename->vla-object (handent "A3")))
      (princ (vlax-curve-getPointAtDist p 15))
      (princ (vlax-curve-getDistAtPoint p '(10 5)))
      (princ (vlax-curve-getParamAtDist p 15))
      (princ (vlax-curve-getClosestPointTo l '(4 3)))
      (princ (vlax-curve-getParamAtPoint l '(4 3)))
      (princ (vlax-curve-getFirstDeriv l 2))
      (princ (vlax-curve-isPeriodic (vlax-ename->vla-object (handent "A2"))))`);
    expect(log).toEqual(["(10 5 0)", "15", "1.5", "(4 0 0)", "nil", "(1 0 0)", "T"]);
  });
  it("osnap finds end, mid, center and quadrant points", async () => {
    const log = await out(`
      (princ (osnap '(9 1) "_end"))
      (princ (osnap '(5.2 0.3) "_mid"))
      (princ (osnap '(5 4) "_cen"))
      (princ (osnap '(7.1 5) "_qua"))`);
    expect(log).toEqual(["(10 0 0)", "(5 0 0)", "(5 5 0)", "(7 5 0)"]);
  });
});

describe("files and settings (W3)", () => {
  it("vl-directory-files, copy, rename, delete, size, systime, mktemp", async () => {
    const lib = library({ "a.txt": "hello", "b.csv": "1,2", "tool.lsp": "(princ)" });
    const log = await out(`
      (princ (vl-directory-files "" "*.txt"))
      (princ (vl-file-copy "a.txt" "c.txt"))
      (princ (vl-file-copy "a.txt" "c.txt"))
      (princ (vl-file-rename "c.txt" "d.txt"))
      (princ (vl-file-size "d.txt"))
      (princ (vl-file-delete "d.txt"))
      (princ (vl-file-size "d.txt"))
      (princ (car (vl-file-systime "a.txt")))
      (princ (vl-filename-mktemp "tmp.txt"))
      (princ (vl-file-directory-p "."))`, host({ files: lib.files }));
    expect(log.slice(0, 10)).toEqual(['("a.txt")', "5", "nil", "T", "5", "T", "nil", "2026", '"tmp001.txt"'.replaceAll('"', ""), "T"]);
    expect(lib.store.has("c.txt")).toBe(false);
    expect(lib.store.has("d.txt")).toBe(false);
  });
  it("a program cannot write a LISP program through copy", async () => {
    const lib = library({ "tool.lsp": "(princ)" });
    await expect(runLisp(`(vl-file-copy "tool.lsp" "evil.lsp")`, host({ files: lib.files }))).rejects.toThrow(LispError);
    expect(lib.store.has("evil.lsp")).toBe(false);
  });
  it("getenv / setenv / getcfg / setcfg go to the host's settings, else the session", async () => {
    const kept = new Map<string, string>();
    const settings = { get: (k: string) => kept.get(k) ?? null, set: (k: string, v: string) => void kept.set(k, v) };
    const log = await out(`(setenv "MyVar" "1") (princ (getenv "MYVAR")) (setcfg "AppData/x/y" "z") (princ (getcfg "AppData/x/y"))`, host({ settings }));
    expect(log).toEqual(["1", "z"]);
    expect([...kept.keys()]).toEqual(["env:MYVAR", "cfg:APPDATA/X/Y"]);
    await expect(runLisp(`(setcfg "General/x" "1")`, host())).rejects.toThrow(/AppData/);
  });
  it("getfiled asks for a library name; an existing file is required unless flag 1", async () => {
    const lib = library({ "data.txt": "x" });
    const answers = ["data", "new"];
    const h = host({ files: lib.files, input: async () => answers.shift() ?? null });
    const log = await out(`(princ (getfiled "Open" "" "txt" 0)) (princ (getfiled "Save" "" "txt" 0))`, h);
    expect(log).toEqual(["data.txt", "nil"]);
  });
});

describe("reactors, colours, graphics, dialogs, Express Tools", () => {
  it("every reactor type registers; reactions, owners and persistence", async () => {
    const session = newSession();
    const log = await out(
      `(defun cb (r args) (princ (vlr-current-reaction-name)))
       (setq r (vlr-acdb-reactor nil '((:vlr-objectAppended . cb))))
       (princ (vlr-type r))
       (vlr-reaction-set r :vlr-objectErased 'cb)
       (princ (length (vlr-reactions r)))
       (vlr-pers r)
       (princ (length (vlr-pers-list)))
       (princ (length (vlr-types)))`,
      host(),
      session,
    );
    expect(log).toEqual([":VLR-ACDB-REACTOR", "2", "1", "18"]);
    const fired = await fireReactors(session, { kind: "other", type: "acdb", name: ":VLR-OBJECTAPPENDED", args: [] });
    expect(fired?.log).toEqual([":VLR-OBJECTAPPENDED"]);
  });
  it("colour dialogs read a number or R,G,B; grdraw draws through the host", async () => {
    const answers = ["3", "255,0,0"];
    const drawn: unknown[] = [];
    const h = host({ input: async () => answers.shift() ?? null, draw: (v) => void drawn.push(...v) });
    const log = await out(`(princ (acad_colordlg 1)) (princ (acad_truecolordlg '(62 . 1))) (grdraw '(0 0) '(1 1) 1) (grclear)`, h);
    expect(log).toEqual(["3", "((62 . 7) (420 . 16711680))"]);
    expect(drawn).toEqual([{ from: [0, 0], to: [1, 1], color: 1 }]);
  });
  it("image tiles reach the screen's dialog state", async () => {
    const lib = library({ "d.dcl": `t : dialog { : image { key = "img"; width = 10; height = 4; } ok_only; }` });
    let seen: DialogRun | null = null;
    const h = host({
      files: lib.files,
      dialog: async (run) => {
        seen = run;
        return 1;
      },
    });
    const log = await out(
      `(setq id (load_dialog "d.dcl")) (new_dialog "t" id)
       (princ (list (dimx_tile "img") (dimy_tile "img")))
       (start_image "img") (fill_image 0 0 80 64 1) (vector_image 0 0 80 64 2) (end_image)
       (start_dialog)`,
      h,
    );
    expect(log).toEqual(["(80 64)"]);
    expect(seen!.state().images).toEqual({ img: [{ op: "fill", x: 0, y: 0, w: 80, h: 64, color: 1 }, { op: "vector", x1: 0, y1: 0, x2: 80, y2: 64, color: 2 }] });
    expect(parseDcl).toBeTypeOf("function");
  });
  it("Express Tools strings, extents and messages", async () => {
    const answers = ["y"];
    const log = await out(
      `(princ (acet-str-find "b" "aBc" T)) (princ (acet-str-replace "a" "x" "banana"))
       (princ (acet-str-format "%1 of %2" 3 "parts")) (princ (acet-str-equal "Ab" "aB"))
       (princ (acet-ent-geomextents (handent "A2")))
       (princ (acet-ui-message "Go?" "T" 4))
       (princ (acet-ms-to-ps 10 0.5))`,
      host({ input: async () => answers.shift() ?? null }),
    );
    expect(log).toEqual(["2", "bxnxnx", "3 of parts", "T", "((3 3 0) (7 7 0))", "Go?", "6", "5"]);
  });
});

describe("left out on purpose (plan §22.5)", () => {
  it("sandboxed functions fail with a clear message", async () => {
    for (const f of ["(startapp \"notepad\")", "(acet-sys-spawn 1 \"cmd\")", "(vl-registry-read \"HKEY\" \"x\")", "(showhtmlmodalwindow \"http://x\")", "(arxload \"x\")"])
      await expect(runLisp(f, host())).rejects.toThrow(/보안상 AX-LISP에서 쓸 수 없는 함수/);
  });
});
