import { describe, expect, it } from "vitest";
import { diffEdits, type EditEntity } from "./edits";

const line = (x: number): EditEntity["geom"] => ({ type: "LINE", start: [0, 0], end: [x, 0] });
const base: EditEntity[] = [
  { handle: "1", layer: "CUT", geom: line(1) },
  { handle: "2", layer: "CUT", geom: line(2) },
  { handle: "3", layer: "CUT", geom: line(3) },
  { handle: "4", layer: "CUT" }, // read-only
];

describe("diffEdits", () => {
  it("is empty when nothing changed", () => {
    expect(diffEdits(base, base)).toEqual({ created: [], modified: [], deleted: [] });
  });
  it("detects created / modified (geom, layer) / deleted", () => {
    const cur: EditEntity[] = [
      { handle: "1", layer: "CUT", geom: line(9) },
      { handle: "2", layer: "DIM", geom: line(2) },
      base[3],
      { handle: "new-1", layer: "CUT", geom: line(5) },
    ];
    expect(diffEdits(base, cur)).toEqual({
      created: [{ layer: "CUT", geom: line(5) }],
      modified: [
        { handle: "1", layer: "CUT", geom: line(9) },
        { handle: "2", layer: "DIM", geom: line(2) },
      ],
      deleted: ["3"],
    });
  });
  it("never reports read-only entities", () => {
    const d = diffEdits(base, base.slice(0, 3));
    expect(d.deleted).toEqual([]);
    expect(diffEdits(base, [...base.slice(0, 3), { handle: "4", layer: "X" }]).modified).toEqual([]);
  });
});
