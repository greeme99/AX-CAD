import { toPrims } from "./curves";
import { entityPaths, type Geom } from "./geom";
import { pickedAt } from "./journal";
import { parseInput, runNow, start, step, NOW_TOOLS, PICK_TOOLS, type Ctx, type Result, type Sel, type ToolState } from "./tools";

export type RunOut = { results: Result[]; state: ToolState; prompt: string; error?: { index: number; input: string; message: string } };

/** Command prompt inputs run in one go through the same reducer as typing (Copilot commands, .axs
 * scripts). State is threaded through the calls, so the page applies all results at once as one
 * undo step; the first rejected input stops the run and nothing is applied. */
export function runInputs(inputs: string[], sel: Sel[] = [], ctx?: Ctx): RunOut {
  let s: ToolState = { tool: "SELECT", pts: [] };
  let prompt = "";
  const results: Result[] = [];
  // A4: the editable entities as the run changes them, for picks (see below). New entities join
  // only when the caller says which handles the page will give them (ctx.nextHandle)
  // and the trim/extend boundaries likewise
  let live = ctx?.editable;
  let bounds = ctx?.bounds;
  let made = ctx?.nextHandle;
  let now = ctx;
  const prims = (h: string, g: Geom) => toPrims(h, g, entityPaths({ geom: g, paths: [] }));
  const track = (rs: Result[]) => {
    if (!live || !bounds || !ctx) return;
    for (const r of rs) {
      if (r.deleted) {
        const gone = new Set(r.deleted);
        live = live.filter((e) => !gone.has(e.handle));
        bounds = bounds.filter((b) => !gone.has(b.h));
      }
      if (r.updated) {
        const m = new Map(r.updated.map((u) => [u.handle, u.geom]));
        live = live.map((e) => (m.has(e.handle) ? { ...e, geom: m.get(e.handle)! } : e));
        bounds = [...bounds.filter((b) => !m.has(b.h)), ...r.updated.flatMap((u) => prims(u.handle, u.geom))];
      }
      if (r.created && made !== undefined) {
        for (const c of r.created) {
          const h = `new-${made++}`;
          live = [...live, { handle: h, layer: c.layer ?? ctx.layer ?? "0", geom: c.geom }];
          bounds = [...bounds, ...prims(h, c.geom)];
        }
      }
    }
    now = { ...ctx, editable: live, bounds };
  };
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
      track(out.results);
      continue;
    }
    if (s.tool === "SELECT") {
      if (p.ev.kind === "enter" || p.ev.kind === "esc") continue;
      return fail("명령 없이 값을 입력했습니다. 먼저 명령(예: L, C, REC)을 입력하세요");
    }
    // A4: a pick (TRIM, FILLET, OFFSET's object...) lying on an editable entity names that entity,
    // as the click did when it was recorded; otherwise the selection is used as before
    const picks = PICK_TOOLS.includes(s.tool) || (s.tool === "OFFSET" && s.n !== undefined && !s.target);
    const on = picks && p.ev.kind === "point" && live ? pickedAt(live, p.ev.p) : undefined;
    const out = step(s, p.ev, on ? [on] : sel, now);
    if (out.state === s && !out.results.length) return fail(out.prompt);
    [s, prompt] = [out.state, out.prompt];
    results.push(...out.results);
    track(out.results);
  }
  return { results, state: s, prompt };
}
