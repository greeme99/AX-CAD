// Track 9 Q4: AX-CAD command scripts (.axs) — one command prompt input per line, like AutoCAD .scr
// (a blank line is Enter, a line starting with ";" is a comment). Run through runInputs.
import { runInputs, type RunOut } from "./script";
import type { Ctx, Sel } from "./tools";

export const MAX_SCRIPT_LINES = 10_000;
export const MAX_SCRIPT_BYTES = 1_000_000;

export type Script = { inputs: string[]; lines: number[] }; // lines[i]: 1-based line of inputs[i]

export function parseScript(text: string): Script | string {
  if (text.length > MAX_SCRIPT_BYTES) return "스크립트가 너무 큽니다 (1 MB 이하)";
  const rows = text.replace(/^﻿/, "").split(/\r?\n/);
  if (rows.length > 0 && rows[rows.length - 1] === "") rows.pop(); // the file's last newline
  if (rows.length > MAX_SCRIPT_LINES) return `스크립트는 ${MAX_SCRIPT_LINES.toLocaleString()}줄까지입니다`;
  const inputs: string[] = [];
  const lines: number[] = [];
  rows.forEach((row, i) => {
    const t = row.trim();
    if (t.startsWith(";")) return;
    inputs.push(t);
    lines.push(i + 1);
  });
  return inputs.length ? { inputs, lines } : "실행할 줄이 없습니다";
}

/** Parse and run; an error names the script line. */
export function runScript(text: string, sel: Sel[] = [], ctx?: Ctx): RunOut & { line?: number } {
  const s = parseScript(text);
  if (typeof s === "string") return { results: [], state: { tool: "SELECT", pts: [] }, prompt: s, error: { index: -1, input: "", message: s } };
  const out = runInputs(s.inputs, sel, ctx);
  return out.error ? { ...out, line: s.lines[out.error.index] } : out;
}
