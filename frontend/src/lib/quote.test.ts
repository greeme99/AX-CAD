import { describe, expect, it } from "vitest";
import { approverCandidates, lineStatus, parseSelect, sourceLink, won, type QuoteLine, type Trace } from "./quote";

const line = (over: Partial<QuoteLine> = {}): QuoteLine => ({
  quote_line_id: 1,
  line_no: 1,
  cost_category: "LABOR",
  item_code: "L",
  item_name: "레이저",
  unit: "h",
  calculated_qty: "0.36",
  calculated_unit_price: "35000.00",
  calculated_amount: "12630.00",
  excluded: false,
  override_qty: null,
  override_unit_price: null,
  override_amount: null,
  override_reason: null,
  overridden_by: null,
  effective_amount: "12630.00",
  traces: [],
  ...over,
});
const trace = (over: Partial<Trace> = {}): Trace => ({ source_kind: "ENTITY", sources: ["1A", "2B"], source_count: 2, revision_id: "a".repeat(32), rule_code: "LASER", price_item_code: "L", unit_price: "35000", inputs: {}, formula_text: "x", ...over });

describe("lineStatus", () => {
  it("AUTO / MANUAL / ERR", () => {
    expect(lineStatus(line())).toBe("AUTO");
    expect(lineStatus(line({ override_amount: "1", effective_amount: "1" }))).toBe("MANUAL");
    expect(lineStatus(line({ excluded: true, calculated_amount: null, effective_amount: null }))).toBe("ERR");
  });
});

describe("won", () => {
  it("formats decimal strings", () => {
    expect(won("1234567.80")).toBe("1,234,568");
    expect(won("0.36", 6)).toBe("0.36");
    expect(won(null)).toBe("-");
  });
});

describe("sourceLink / parseSelect (trace highlight)", () => {
  it("2D entities open the viewer with the handles selected", () => {
    expect(sourceLink(trace(), 7)).toBe(`/viewer/${"a".repeat(32)}?doc=7&select=1A%2C2B`);
    expect(sourceLink(trace({ source_kind: "REVISION", sources: ["x"] }), 7)).toBe(`/viewer/${"a".repeat(32)}?doc=7`);
  });
  it("3D features open the model workbench", () => {
    expect(sourceLink(trace({ source_kind: "FEATURE", sources: ["12"], revision_id: null }), 7)).toBe("/model/7?select=12");
  });
  it("only well-formed ids survive the URL", () => {
    expect(parseSelect("1A,zz,<x>,2B", "handle")).toEqual(["1A", "2B"]);
    expect(parseSelect("12,0,-3,abc", "feature")).toEqual(["12"]);
    expect(parseSelect(null, "handle")).toEqual([]);
  });
});

describe("sourceLink rejects malformed ids", () => {
  it("returns null instead of building a path", () => {
    expect(sourceLink(trace({ revision_id: "../../admin" }), 7)).toBeNull();
    expect(sourceLink(trace(), Number.NaN)).toBeNull();
  });
});

describe("approverCandidates (FN-22: nobody approves own numbers)", () => {
  it("keeps reviewers who neither created, requested nor adjusted the quote", () => {
    const m = (user_id: number, ...roles: string[]) => ({ user_id, roles });
    const members = [m(1, "ESTIMATOR"), m(2, "REVIEWER"), m(3, "REVIEWER"), m(4, "REVIEWER", "ESTIMATOR"), m(5, "REVIEWER")];
    const q = { authors: [3, 4] }; // creator 3, adjuster 4 (from the audit log)
    expect(approverCandidates(members, q, 5).map((x) => x.user_id)).toEqual([2]);
  });
});
