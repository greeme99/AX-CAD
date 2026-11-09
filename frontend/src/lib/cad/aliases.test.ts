import { afterEach, describe, expect, it } from "vitest";
import { applyPgp, parsePgp } from "./aliases";
import { newSession, runLisp } from "./lisp";
import { runStartup } from "./startup";
import { parseInput, USER_ALIAS } from "./tools";

afterEach(() => USER_ALIAS.clear());

describe("alias files (.pgp)", () => {
  it("reads ALIAS, *COMMAND lines, comments and skips what it must", () => {
    const r = parsePgp("; my aliases\nXL, *LINE\nZ,*circle ; trailing comment\nBAD, notepad.exe\n9X, *LINE\nQQ, *NOPE\nnoformat\n");
    expect([...r.aliases.entries()]).toEqual([["XL", "LINE"], ["Z", "CIRCLE"]]);
    expect(r.skipped).toHaveLength(4);
    expect(r.skipped.join("|")).toContain("외부 프로그램");
  });
  it("a user alias starts its command and wins over a built-in one; applying replaces the old set", () => {
    expect(parseInput("xl", { tool: "SELECT", pts: [] })).toHaveProperty("error"); // not an alias yet
    applyPgp("XL, *LINE\nL, *CIRCLE\n"); // L is a built-in alias of LINE: the user's file wins
    expect(parseInput("XL", { tool: "SELECT", pts: [] })).toEqual({ tool: "LINE" });
    expect(parseInput("l", { tool: "SELECT", pts: [] })).toEqual({ tool: "CIRCLE" });
    applyPgp("Q, *ARC\n");
    expect(parseInput("XL", { tool: "SELECT", pts: [] })).toHaveProperty("error");
    expect(parseInput("q", { tool: "SELECT", pts: [] })).toEqual({ tool: "ARC" });
  });
  it("limits: size and count", () => {
    expect(parsePgp("A, *LINE\n".repeat(10_000)).skipped[0]).toContain("너무 큽니다");
    const many = Array.from({ length: 1200 }, (_, i) => `A${i}, *LINE`).join("\n");
    expect(parsePgp(many).aliases.size).toBe(1000);
  });
});

describe("start-up files", () => {
  it("acad.pgp and acad.lsp / acaddoc.lsp / S::STARTUP run once, define only", async () => {
    const session = newSession();
    const items = [
      { name: "acad.pgp", source: "XL, *LINE" },
      { name: "acad.lsp", source: "(defun hello () 42)" },
      { name: "acaddoc.lsp", source: '(defun S::STARTUP () (setq started "yes")) (command "LINE" "0,0" "1,1" "")' },
      { name: "other.lsp", source: "(setq never 1)" },
    ];
    const notes = await runStartup(items, session, { layers: ["0"], layer: "0" });
    expect(notes.join("\n")).toContain("별칭 1개");
    expect(notes.join("\n")).toContain("도면 명령은 시작 파일에서 실행하지 않습니다");
    expect(USER_ALIAS.get("XL")).toBe("LINE");
    expect((await runLisp("(hello)", {}, undefined, session)).value).toBe(42); // the namespace stays for the routines
    expect((await runLisp("started", {}, undefined, session)).value).toBe("yes");
    expect((await runLisp("never", {}, undefined, session)).value).toEqual([]); // only the named files run
  });
  it("an error in a start-up file is a note, not a failure", async () => {
    const notes = await runStartup([{ name: "acad.lsp", source: "(car 5)" }], newSession(), {});
    expect(notes[0]).toContain("오류");
  });
  it("a start-up file cannot change the drawing or ask for input", async () => {
    const notes = await runStartup([{ name: "acaddoc.lsp", source: '(entmake (list (cons 0 "LINE") (cons 10 (list 0 0)) (cons 11 (list 5 5))))' }], newSession(), { layers: ["0"] });
    expect(notes[0]).toContain("오류");
    const n2 = await runStartup([{ name: "acaddoc.lsp", source: '(getpoint "p")' }], newSession(), { input: async () => [1, 1] });
    expect(n2[0]).toContain("오류");
  });
});
