import { describe, expect, it } from "vitest";
import { toPrims } from "./curves";
import { entityPaths, hitPaths } from "./geom";
import { eventText, journalText, MAX_JOURNAL_LINES, newJournal, note, num, onPaths, pickedAt, record, toolText } from "./journal";
import { runScript } from "./axs";
import { parseInput, PICK_TOOLS, start, step, type Ctx, type Result, type Sel, type Tool, type ToolEvent, type ToolState } from "./tools";
import type { Pt } from "./view";

/** The 2D screen in miniature: tools, clicks and keys recorded as the page does (A4). */
function screen(start0: Sel[] = []) {
  let ents = [...start0];
  let next = 1;
  let ts: ToolState = { tool: "SELECT", pts: [] };
  const j = newJournal();
  const ctx = (): Ctx => ({ reach: 1000, bounds: ents.flatMap((e) => toPrims(e.handle, e.geom, entityPaths({ geom: e.geom, paths: [] }))), editable: ents });
  const apply = (rs: Result[]) => {
    for (const r of rs) {
      if (r.deleted) ents = ents.filter((e) => !r.deleted!.includes(e.handle));
      if (r.updated) ents = ents.map((e) => ({ ...e, geom: r.updated!.find((u) => u.handle === e.handle)?.geom ?? e.geom }));
      if (r.created) ents = [...ents, ...r.created.map((c) => ({ handle: `new-${next++}`, layer: c.layer ?? "0", geom: c.geom }))];
    }
  };
  const tool = (t: Tool) => {
    const out = start(t, 0);
    ts = out.state;
    record(j, toolText(t));
  };
  const ev = (e: ToolEvent) => {
    // a pick tool's click names the entity under it (5 "pixels" here) and is written onto it
    const picks = PICK_TOOLS.includes(ts.tool);
    let under: Sel | undefined;
    if (picks && e.kind === "point") under = ents.find((x) => hitTestNear(x, e.p));
    const out = step(ts, e, under ? [under] : [], ctx());
    if (out.state === ts && !out.results.length) return out.prompt; // rejected: not recorded
    const written = under && e.kind === "point" ? { ...e, p: onPaths(hitPaths({ geom: under.geom, paths: [] }), e.p) } : e;
    const line = eventText(written);
    if (line !== null) record(j, line);
    ts = out.state;
    apply(out.results);
    return out.prompt;
  };
  const type = (text: string) => {
    const p = parseInput(text, ts);
    if ("error" in p) return p.error;
    return "tool" in p ? tool(p.tool) : ev(p.ev);
  };
  return { j, tool, ev, type, ents: () => ents, ctx };
}
const hitTestNear = (e: Sel, p: Pt) => {
  const paths = hitPaths({ geom: e.geom, paths: [] });
  const q = onPaths(paths, p);
  return Math.hypot(q[0] - p[0], q[1] - p[1]) <= 0.5;
};
const geoms = (es: Sel[]) => es.map((e) => e.geom);

describe("A4 command journal", () => {
  it("numbers and points as the command line reads them", () => {
    expect([num(1 / 3), num(-0.0000000001), num(100), num(2.5e-7), num(-12.75)]).toEqual(["0.333333333", "0", "100", "0.00000025", "-12.75"]);
    expect(eventText({ kind: "point", p: [10, -2.5] })).toBe("10,-2.5");
    expect([eventText({ kind: "enter" }), eventText({ kind: "esc" }), eventText({ kind: "close" }), eventText({ kind: "option", s: "C" })]).toEqual(["", "^C", "C", "C"]);
    expect([eventText({ kind: "text", s: "품번 A-01" }), eventText({ kind: "text", s: ";주석" }), eventText({ kind: "text", s: "^C" })]).toEqual(["품번 A-01", null, null]);
    expect([toolText("LINE"), toolText("DIMLINEAR"), toolText("RECT")]).toEqual(["LINE", "DLI", "RECT"]);
  });

  it("^C cancels a command on the command line and in scripts", () => {
    expect(parseInput("^C", { tool: "LINE", pts: [[0, 0]] })).toEqual({ ev: { kind: "esc" } });
    const r = runScript("C\n0,0\n^C\nL\n0,0\n10,0\n\n");
    expect(r.error).toBeUndefined();
    expect(r.results.flatMap((x) => x.created ?? []).map((c) => c.geom.type)).toEqual(["LINE"]);
  });

  it("records drawing, clicks, keys and text, and replays to the same drawing", () => {
    const s = screen();
    s.type("REC");
    s.ev({ kind: "point", p: [0, 0] });
    s.type("@100,50");
    s.tool("CIRCLE");
    s.ev({ kind: "point", p: [20.123456789, 25] });
    s.type("7.5");
    s.tool("LINE");
    s.ev({ kind: "point", p: [0, 0] });
    s.ev({ kind: "esc" }); // cancelled: nothing drawn
    s.type("T");
    s.ev({ kind: "point", p: [5, 60] });
    s.ev({ kind: "enter" }); // default height
    s.type("브래킷 A-01");
    expect(s.type("C")).toBeUndefined(); // CIRCLE again from the command line
    expect(s.type("abc")).toContain("알 수 없는"); // rejected input is not recorded
    s.ev({ kind: "esc" });
    const text = journalText(s.j, "테스트 도면");
    expect(text).toContain("; AX-CAD 명령 기록");
    expect(text).not.toContain("abc");
    const r = runScript(text);
    expect(r.error).toBeUndefined();
    expect(r.results.flatMap((x) => x.created ?? []).map((c) => c.geom)).toEqual(geoms(s.ents()));
  });

  it("a pick names the same entity on replay, also one the journal drew itself", () => {
    const s = screen();
    s.tool("LINE");
    s.ev({ kind: "point", p: [0, 0] });
    s.ev({ kind: "point", p: [100, 0] });
    s.ev({ kind: "enter" });
    s.tool("LINE");
    s.ev({ kind: "point", p: [50, -20] });
    s.ev({ kind: "point", p: [50, 20] });
    s.ev({ kind: "enter" });
    s.tool("TRIM");
    expect(s.ev({ kind: "point", p: [80, 0.3] })).not.toContain("클릭하세요"); // 0.3 off the line: still that line
    s.ev({ kind: "esc" });
    const after = geoms(s.ents());
    expect(after).toContainEqual({ type: "LINE", start: [0, 0], end: [50, 0] });
    const text = journalText(s.j, "trim");
    expect(text).toContain("\n80,0\n"); // written onto the line it picked
    // the page passes the handles it will give new entities: new-1, new-2, ... in order
    const r = runScript(text, [], { reach: 1000, bounds: [], editable: [], nextHandle: 1, layer: "0" });
    expect(r.error).toBeUndefined();
    const live = new Map<string, Sel["geom"]>();
    let n = 1;
    for (const x of r.results) {
      x.deleted?.forEach((h) => live.delete(h));
      x.updated?.forEach((u) => live.set(u.handle, u.geom));
      x.created?.forEach((c) => live.set(`new-${n++}`, c.geom));
    }
    expect([...live.values()]).toEqual(after);
  });

  it("a pick replays against entities already in the drawing, and fails clearly when they are gone", () => {
    const base: Sel[] = [
      { handle: "A1", layer: "0", geom: { type: "LINE", start: [0, 0], end: [100, 0] } },
      { handle: "A2", layer: "0", geom: { type: "LINE", start: [50, -20], end: [50, 20] } },
    ];
    const s = screen(base);
    s.tool("TRIM");
    s.ev({ kind: "point", p: [20, -0.2] });
    s.ev({ kind: "esc" });
    const text = journalText(s.j, "trim existing");
    const ctx = s.ctx;
    const again = runScript(text, [], { ...ctx(), editable: base, bounds: base.flatMap((e) => toPrims(e.handle, e.geom, entityPaths({ geom: e.geom, paths: [] }))) });
    expect(again.error).toBeUndefined();
    expect(again.results.flatMap((x) => [...(x.updated ?? []), ...(x.created ?? [])])).not.toHaveLength(0);
    const gone = runScript(text, [], { reach: 1000, bounds: [], editable: [] });
    expect(gone.line).toBe(5); // 3 header lines, TRIM, then the pick
    expect(gone.error?.message).toContain("클릭하세요");
  });

  it("picks only land on an entity's outline", () => {
    const e: Sel = { handle: "A1", layer: "0", geom: { type: "CIRCLE", center: [0, 0], radius: 10 } };
    const on = onPaths(hitPaths({ geom: e.geom, paths: [] }), [12, 1]);
    expect(pickedAt([e], [Number(num(on[0])), Number(num(on[1]))])?.handle).toBe("A1");
    expect(pickedAt([e], [12, 1])).toBeUndefined();
  });

  it("notes, header and the size limit", () => {
    const j = newJournal();
    note(j, "속성 변경\n(색 1)");
    const text = journalText(j, "x");
    expect(text).toContain("; ※ 속성 변경 (색 1)");
    expect(text).toContain("※ 표시 1건");
    for (let i = 0; i < MAX_JOURNAL_LINES - 1; i++) record(j, "");
    expect(record(j, "L")).toBe(false);
  });
});
