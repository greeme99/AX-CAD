import { describe, expect, it } from "vitest";
import { LispError, read, runLisp, show, type Host } from "./lisp";
import { runInputs } from "./script";
import type { Sel } from "./tools";

const val = async (src: string, host?: Host) => show((await runLisp(src, host)).value);

describe("AX-LISP reader", () => {
  it("reads lists, strings, quotes, dotted pairs and comments", () => {
    const f = read(`; comment\n(setq a '(1 2.5 "x\\"y")) ;| block\n comment |; (b . 3)`);
    expect(f.map(show)).toEqual(['(SETQ A (QUOTE (1 2.5 "x\\"y")))', "(B . 3)"]);
  });
  it("names the line of a syntax error", () => {
    expect(() => read("(setq a 1)\n(command \"LINE\"")).toThrow(LispError);
    try {
      read('(setq a 1)\n\n(princ "x"');
    } catch (e) {
      expect((e as LispError).line).toBe(3);
    }
    expect(() => read(")")).toThrow("짝이 없는");
  });
});

describe("AX-LISP evaluation", () => {
  it("does arithmetic, lists, strings and geometry like AutoLISP", async () => {
    expect(await val("(+ 1 2 (* 3 4))")).toBe("15");
    expect(await val("(- 5)")).toBe("-5");
    expect(await val("(cadr '(1 2 3))")).toBe("2");
    expect(await val("(cdr (assoc 8 (list (cons 0 \"LINE\") (cons 8 \"OUTLINE\"))))")).toBe('"OUTLINE"');
    expect(await val('(strcat "R" (rtos 2.5 2 1))')).toBe('"R2.5"');
    expect(await val("(polar '(0 0) (/ pi 2) 10)")).toBe("(0.0 10.0)"); // W9: REALs print as reals
    expect(await val("(distance '(0 0) '(3 4))")).toBe("5.0"); // W9: a REAL
    expect(await val("(inters '(0 0) '(10 10) '(0 10) '(10 0))")).toBe("(5.0 5.0)");
    expect(await val("(mapcar '(lambda (x) (* x x)) '(1 2 3))")).toBe("(1 4 9)");
    expect(await val("(setq n 0) (repeat 4 (setq n (1+ n))) n")).toBe("4");
    expect(await val("(setq s 0) (foreach x '(1 2 3) (setq s (+ s x))) s")).toBe("6");
    expect(await val("(cond ((> 1 2) \"a\") (T \"b\"))")).toBe('"b"');
    expect(await val("undefined-var")).toBe("nil");
  });

  it("defines functions with locals and c: commands", async () => {
    const src = "(defun sq (x / tmp) (setq tmp (* x x)) tmp) (defun c:hello () (princ \"hi\") (sq 7))";
    const r = await runLisp(src, {}, "HELLO");
    expect(r.commands).toEqual(["HELLO"]);
    expect(show(r.value)).toBe("49");
    expect(r.log).toEqual(["hi"]);
    await expect(runLisp("(defun car (x) x)")).rejects.toThrow("다시 정의할 수 없습니다");
    // calling a command does not redo the file's top-level drawing
    const twice = await runLisp('(defun c:dot () (command "C" \'(0 0) 1)) (c:dot)', {}, "DOT");
    expect(twice.segments.flatMap((x) => x.inputs)).toEqual(["C", "0,0", "1"]);
  });

  it("stops runaway programs", async () => {
    await expect(runLisp("(while T (setq a 1))")).rejects.toThrow("실행 단계");
    await expect(runLisp("(defun f (n) (f n)) (f 1)")).rejects.toThrow("너무 깊습니다");
    await expect(runLisp("(setq l nil) (repeat 30000 (setq l (cons 1 l)))")).rejects.toThrow("목록은");
    await expect(runLisp("(/ 1 0)")).rejects.toThrow("0으로 나눌 수 없습니다"); // INT / INT 0
    await expect(runLisp("(/ 1.0 0)")).rejects.toThrow("숫자가 아닙니다");
    await expect(runLisp("(setq a nil) (repeat 200000 (setq a (list a))) (princ a)")).rejects.toThrow(LispError);
    await expect(runLisp('(getpoint "pick")')).rejects.toThrow("쓸 수 없습니다");
  });
});

describe("command -> command prompt inputs", () => {
  it("turns points and numbers into prompt text and draws through the reducer", async () => {
    const src = `(setq p (list 10 20))
(command "_.RECTANG" p (list (+ (car p) 100) (+ (cadr p) 50)))
(command "CIRCLE" (polar p 0 50) 5)
(command "LINE" '(0 0) '(10 0) "")`;
    const r = await runLisp(src.replace("RECTANG", "REC"));
    expect(r.segments).toHaveLength(1);
    expect(r.segments[0].inputs).toEqual(["REC", "10,20", "110,70", "CIRCLE", "60,20", "5", "LINE", "0,0", "10,0", ""]);
    const out = runInputs(r.segments[0].inputs);
    expect(out.error).toBeUndefined();
    const types = out.results.flatMap((x) => x.created ?? []).map((c) => c.geom.type);
    expect(types).toEqual(["LWPOLYLINE", "CIRCLE", "LINE"]);
  });

  it("keeps a selection and the current layer with the command that uses them", async () => {
    const line: Sel = { handle: "1A", layer: "0", geom: { type: "LINE", start: [0, 0], end: [10, 0] } };
    const host: Host = { ssget: async () => [line], layers: ["0", "CENTER"] };
    const r = await runLisp(`(command "C" '(0 0) 5)
(setq ss (ssget))
(command "MOVE" ss "" '(0 0) '(5 5))
(setvar "CLAYER" "center")
(command "L" '(0 0) '(1 1) "")`, host);
    expect(r.segments.map((s) => [s.inputs.join(" "), s.sel.length, s.layer])).toEqual([
      ["C 0,0 5", 0, undefined],
      ["MOVE 0,0 5,5", 1, undefined],
      ["L 0,0 1,1 ", 0, "CENTER"],
    ]);
    await expect(runLisp('(setvar "CLAYER" "NOPE")', host)).rejects.toThrow("없는 레이어");
  });

  it("reads entities with entget and asks the user with get*", async () => {
    const circle: Sel = { handle: "2B", layer: "HOLES", geom: { type: "CIRCLE", center: [5, 5], radius: 3 } };
    const asked: string[] = [];
    const host: Host = {
      entsel: async () => ({ sel: circle, at: [5, 8] }),
      input: async (kind, prompt) => {
        asked.push(`${kind}:${prompt}`);
        return kind === "point" ? [1, 2] : 7;
      },
    };
    const r = await runLisp(`(setq e (car (entsel)))
(setq d (entget e))
(setq r (cdr (assoc 40 d)) c (cdr (assoc 10 d)))
(setq p (getpoint "중심: ") k (getreal "배율: "))
(command "C" p (* r k))
(list (cdr (assoc 8 d)) c)`, host);
    expect(show(r.value)).toBe('("HOLES" (5.0 5.0 0.0))'); // entget coordinates are REALs
    expect(r.segments[0].inputs).toEqual(["C", "1,2", "21"]);
    expect(asked).toEqual(["point:중심: ", "real:배율: "]);
  });
});
