import { parseInput, runNow, start, step, NOW_TOOLS, type Ctx, type Result, type Sel, type ToolState } from "./tools";

export type RunOut = { results: Result[]; state: ToolState; prompt: string; error?: { index: number; input: string; message: string } };

/** Command prompt inputs run in one go through the same reducer as typing (Copilot commands, .axs
 * scripts). State is threaded through the calls, so the page applies all results at once as one
 * undo step; the first rejected input stops the run and nothing is applied. */
export function runInputs(inputs: string[], sel: Sel[] = [], ctx?: Ctx): RunOut {
  let s: ToolState = { tool: "SELECT", pts: [] };
  let prompt = "";
  const results: Result[] = [];
  for (let i = 0; i < inputs.length; i++) {
    const raw = inputs[i];
    const fail = (message: string): RunOut => ({ results: [], state: { tool: "SELECT", pts: [] }, prompt: message, error: { index: i, input: raw, message } });
    const p = parseInput(raw, s);
    if ("error" in p) return fail(p.error);
    if ("tool" in p) {
      const out = NOW_TOOLS.includes(p.tool) && sel.length ? runNow(p.tool, sel) : start(p.tool, sel.length);
      if (out.state.tool === "SELECT" && p.tool !== "SELECT" && !out.results.length) return fail(out.prompt);
      [s, prompt] = [out.state, out.prompt];
      results.push(...out.results);
      continue;
    }
    if (s.tool === "SELECT") {
      if (p.ev.kind === "enter" || p.ev.kind === "esc") continue;
      return fail("명령 없이 값을 입력했습니다. 먼저 명령(예: L, C, REC)을 입력하세요");
    }
    const out = step(s, p.ev, sel, ctx);
    if (out.state === s && !out.results.length) return fail(out.prompt);
    [s, prompt] = [out.state, out.prompt];
    results.push(...out.results);
  }
  return { results, state: s, prompt };
}
