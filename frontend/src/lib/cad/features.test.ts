import { describe, expect, it } from "vitest";
import { axisLine, booleanCandidates, failedDependent, featureName, revolveParams, type Feature } from "./features";

const ok = { px: "0", py: "0", dx: "0", dy: "1", angle: "360" };

describe("revolveParams", () => {
  it("parses a valid axis", () => {
    expect(revolveParams(ok)).toEqual({ axis_point: [0, 0], axis_dir: [0, 1], angle_deg: 360 });
  });
  it.each([
    [{ dx: "0", dy: "0" }, "(0, 0)"],
    [{ px: "" }, "숫자"],
    [{ py: "abc" }, "숫자"],
    [{ angle: "0" }, "각도"],
    [{ angle: "361" }, "각도"],
  ])("rejects %o", (patch, msg) => {
    expect(revolveParams({ ...ok, ...patch })).toContain(msg);
  });
});

describe("axisLine", () => {
  const ext = { min: [0, 0] as [number, number], max: [40, 30] as [number, number] };
  it("passes through the point along the direction, beyond the extents", () => {
    const [a, b] = axisLine({ ...ok, px: "10" }, ext);
    expect(a[0]).toBeCloseTo(10);
    expect(b[0]).toBeCloseTo(10);
    expect(a[1]).toBeLessThan(0);
    expect(b[1]).toBeGreaterThan(30);
  });
  it("is empty for a zero or invalid direction", () => {
    expect(axisLine({ ...ok, dy: "0" }, ext)).toEqual([]);
    expect(axisLine({ ...ok, px: "x" }, ext)).toEqual([]);
  });
});

const base = { status: "OK" as const, error_code: null, metrics: null, created_at: "", updated_at: "", inputs: [] as number[] };
const ext = (id: number, seq: number, visible = true): Feature => ({ ...base, feature_id: id, seq, visible, feature_type: "EXTRUDE", params: { source_revision_id: "a".repeat(32), handles: ["1"], distance: 1, direction: "+Z" } });
const cut: Feature = { ...base, feature_id: 3, seq: 3, visible: true, inputs: [1, 2], feature_type: "BOOLEAN", params: { op: "CUT", target_feature_id: 1, tool_feature_id: 2 } };

describe("booleanCandidates", () => {
  const all = [ext(1, 1, false), ext(2, 2, false), cut, ext(4, 4), { ...ext(5, 5), status: "ERROR" as const }];
  it("new BOOLEAN: visible, built features only", () => {
    expect(booleanCandidates(all).map((f) => f.feature_id)).toEqual([3, 4]);
  });
  it("editing a BOOLEAN: its own inputs stay, later features and itself excluded", () => {
    expect(booleanCandidates(all, cut).map((f) => f.feature_id)).toEqual([1, 2]);
  });
});

describe("names and errors", () => {
  it("names by type/op and seq", () => {
    expect(featureName(ext(1, 1))).toBe("Extrude001");
    expect(featureName(cut)).toBe("Cut003");
  });
  it("reads the failed dependent id", () => {
    expect(failedDependent({ feature_id: 7 })).toBe(7);
    expect(failedDependent(undefined)).toBeNull();
    expect(failedDependent({ feature_id: "7" })).toBeNull();
  });
});
