import { describe, expect, it } from "vitest";
import { runLisp, type Host } from "./lisp";
import { runInputs } from "./script";
import type { Sel } from "./tools";
import examples from "./text2lisp.fixture.json";

// Track 11 L3 (TC-185): the LISP the Text to Draw rules write (tests/test_text2lisp.py makes the
// fixture) runs in the screen's interpreter and command reducer and makes the expected shapes.
const square: Sel = { handle: "S1", layer: "0", geom: { type: "LWPOLYLINE", points: [[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]], closed: true } };
const host: Host = { ssget: async () => [square], layers: ["0", "OUTLINE", "CENTER", "TEXT", "DIM"] };

describe("Text to Draw examples", () => {
  it.each(examples as { text: string; lisp: string; expect: Record<string, number> | "edit" }[])("$text", async ({ lisp, expect: want }) => {
    const out = await runLisp(lisp, host);
    const made: Record<string, number> = {};
    let changed = 0;
    for (const seg of out.segments) {
      const r = runInputs(seg.inputs, seg.sel);
      expect(r.error, JSON.stringify(r.error)).toBeUndefined();
      for (const res of r.results) {
        for (const c of res.created ?? []) made[c.geom.type] = (made[c.geom.type] ?? 0) + 1;
        changed += (res.updated?.length ?? 0) + (res.created?.length ?? 0);
      }
    }
    if (want === "edit") expect(changed).toBeGreaterThan(0);
    else expect(made).toEqual(want);
  });
});
