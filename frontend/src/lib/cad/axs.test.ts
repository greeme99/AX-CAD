import { describe, expect, it } from "vitest";
import { parseScript, runScript } from "./axs";

describe(".axs scripts", () => {
  it("one input per line, comments skipped, blank line = Enter", () => {
    const s = parseScript("; bracket outline\r\nREC\n0,0\n@100,50\nC\n20,25\n3\nL\n0,60\n100,60\n\n");
    expect(s).toEqual({ inputs: ["REC", "0,0", "@100,50", "C", "20,25", "3", "L", "0,60", "100,60", ""], lines: [2, 3, 4, 5, 6, 7, 8, 9, 10, 11] });
  });
  it("runs as one undo step and reports the failing line", () => {
    const ok = runScript("REC\n0,0\n@100,50\nC\n20,25\n3\n");
    expect(ok.error).toBeUndefined();
    expect(ok.results.flatMap((r) => r.created ?? [])).toHaveLength(2);
    const bad = runScript("; header\nC\n0,0\n-5\n");
    expect(bad.line).toBe(4);
    expect(bad.results).toEqual([]);
  });
  it("limits", () => {
    expect(parseScript("; only comments\n")).toContain("없습니다");
    expect(parseScript("L\n".repeat(10_001))).toContain("10,000");
    expect(parseScript("x".repeat(1_000_001))).toContain("1 MB");
    expect(runScript("").error?.index).toBe(-1);
  });
});
