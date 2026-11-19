import { describe, expect, it } from "vitest";
import {
  SHEET_CUT_DEFAULT,
  SHEET_DEFAULT,
  sheetCutParams,
  sheetFlangeParams,
  sheetFormParams,
  sheetSharpParams,
  type SheetInfo,
} from "./features";

const EDGES = [
  { index: 0, start: [0, 0] as [number, number], end: [100, 0] as [number, number], length: 100 },
  { index: 1, start: [100, 0] as [number, number], end: [100, 50] as [number, number], length: 50 },
];

const INFO = {
  flanges: [{ feature_id: 7, edge: 0, length: 30, angle: 90, up: true, inset_start: 0, inset_end: 0 }],
} as unknown as SheetInfo;

describe("sheet metal frontend boundary guards (review TASK-008)", () => {
  it("rejects negative and non-numeric values on form & sharp params", () => {
    expect(sheetFormParams({ target: "3", sketch: "4", type: "EMBOSS", depth: "-1", up: true })).toMatch(/0.1~100/);
    expect(sheetFormParams({ target: "3", sketch: "4", type: "EMBOSS", depth: "abc", up: true })).toMatch(/0.1~100/);
    expect(sheetSharpParams({ target: "2", radius: "-2", k: "" })).toMatch(/굽힘 R/);
    expect(sheetSharpParams({ target: "2", radius: "2", k: "1.5" })).toMatch(/K/);
  });

  it("rejects empty target on cut params", () => {
    expect(sheetCutParams({ ...SHEET_CUT_DEFAULT, target: "", sketch: "8" })).toMatch(/판금/);
  });

  // Verified P1/P2 defects from docs/reviews/sheet-ui-dwg-review.md — convert it.fails -> it when fixed
  it.fails("UI-05 (P1): blank edge string must be rejected instead of coercing to edge #0", () => {
    const res = sheetFlangeParams({ ...SHEET_DEFAULT, target: "5", edge: "" }, EDGES);
    expect(typeof res).toBe("string");
  });

  it.fails("UI-05 (P1): blank insetStart must be rejected instead of coercing to 0", () => {
    const res = sheetFlangeParams({ ...SHEET_DEFAULT, target: "5", edge: "1", insetStart: "" }, EDGES);
    expect(typeof res).toBe("string");
  });

  it.fails("UI-11 (P2): SIDE flange must block stale closeStart instead of sending close_start", () => {
    const res = sheetFlangeParams(
      { ...SHEET_DEFAULT, target: "5", side: "7:0:LEFT", closeStart: "4" },
      EDGES,
      INFO,
    );
    expect(typeof res).toBe("string");
  });

  it.fails("UI-12 (P2): CONTOUR flange with valid profile should not fail when unused angle is blank", () => {
    const res = sheetFlangeParams(
      { ...SHEET_DEFAULT, target: "5", edge: "1", style: "CONTOUR", angle: "", profile: "0,0\n0,20\n15,20" },
      EDGES,
    );
    expect(typeof res).toBe("object");
  });
});
