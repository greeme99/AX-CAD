import { describe, expect, it } from "vitest";
import { boxSelect } from "./select";

const ln = (handle: string, a: [number, number], b: [number, number], layer = "0") => ({ handle, layer, paths: [], geom: { type: "LINE" as const, start: a, end: b } });

describe("box selection", () => {
  const ents = [ln("in", [1, 1], [2, 2]), ln("cross", [1, 1], [20, 1]), ln("out", [30, 30], [40, 40]), ln("thru", [-5, 5], [15, 5]), ln("hid", [1, 1], [2, 2], "H")];
  const hidden = new Set(["H"]);

  it("window takes only what lies fully inside", () => {
    expect(boxSelect(ents, [0, 0], [10, 10], false, hidden)).toEqual(["in"]);
  });

  it("crossing also takes what it touches, even with no end point inside", () => {
    expect(boxSelect(ents, [10, 10], [0, 0], true, hidden).sort()).toEqual(["cross", "in", "thru"]);
  });

  it("a block's parts share one handle: the window needs all of them inside", () => {
    const block = [{ handle: "B", layer: "0", paths: [[[1, 1], [2, 2]] as [number, number][]] }, { handle: "B", layer: "0", paths: [[[1, 1], [50, 50]] as [number, number][]] }];
    expect(boxSelect(block, [0, 0], [10, 10], false, new Set())).toEqual([]);
    expect(boxSelect(block, [0, 0], [10, 10], true, new Set())).toEqual(["B"]);
  });
});
