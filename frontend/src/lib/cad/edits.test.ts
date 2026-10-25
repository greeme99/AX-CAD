import { describe, expect, it } from "vitest";
import { dashFor, diffEdits, sameEntities, type EditEntity } from "./edits";

const line = (x: number): EditEntity["geom"] => ({ type: "LINE", start: [0, 0], end: [x, 0] });
const base: EditEntity[] = [
  { handle: "1", layer: "CUT", geom: line(1) },
  { handle: "2", layer: "CUT", geom: line(2) },
  { handle: "3", layer: "CUT", geom: line(3) },
  { handle: "4", layer: "CUT" }, // read-only
];

describe("diffEdits", () => {
  it("sends only the colour/linetype that changed", () => {
    const b: EditEntity[] = [{ handle: "1", layer: "CUT", geom: line(1), aci: 256, linetype: "BYLAYER" }];
    const cur: EditEntity[] = [{ ...b[0], aci: 1 }, { handle: "new-1", layer: "CUT", geom: line(2), linetype: "CENTER" }];
    expect(diffEdits(b, cur)).toEqual({
      created: [{ layer: "CUT", geom: line(2), linetype: "CENTER" }],
      modified: [{ handle: "1", layer: "CUT", geom: line(1), color: 1 }],
      deleted: [],
    });
    expect(sameEntities(b, [{ ...b[0], linetype: "DASHED" }])).toBe(false);
  });
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
  it("local DIM_* geoms pass through unchanged as created", () => {
    const geom: EditEntity["geom"] = { type: "DIM_RADIUS", center: [0, 0], radius: 5, angle: 30 };
    expect(diffEdits(base, [...base, { handle: "new-1", layer: "DIM", geom }]).created).toEqual([{ layer: "DIM", geom }]);
  });
  it("sameEntities ignores render-only fields and copies", () => {
    const a = [{ handle: "1", layer: "0", geom: { type: "LINE", start: [0, 0], end: [1, 0] } as never }];
    const moved0 = [{ ...a[0], geom: { type: "LINE", start: [0, 0], end: [1, 0] } as never, paths: [] }];
    expect(sameEntities(a, moved0)).toBe(true);
    expect(sameEntities(a, [{ ...a[0], layer: "CUT" }])).toBe(false);
    expect(sameEntities(a, [])).toBe(false);
  });
});

describe("dashFor (linetype previews before saving)", () => {
  const cat = { Continuous: null, DASHED: [2.54, 0.508], CENTER: [6.35, 1.27, 1.27, 1.27] };
  const layers = [{ name: "AXIS", linetype: "CENTER" }, { name: "0", linetype: "Continuous" }];
  it("uses the entity's own linetype, else its layer's, scaled by the entity", () => {
    expect(dashFor(cat, layers, "dashed", "0")).toEqual([2.54, 0.508]); // case-insensitive
    expect(dashFor(cat, layers, "BYLAYER", "AXIS", 0.5)).toEqual([3.175, 0.635, 0.635, 0.635]);
    expect(dashFor(cat, layers, undefined, "0")).toBeUndefined(); // continuous
    expect(dashFor(cat, layers, "NO-SUCH", "0")).toBeUndefined();
    expect(dashFor(undefined, layers, "DASHED", "0")).toBeUndefined(); // payload before the catalog
  });
});
