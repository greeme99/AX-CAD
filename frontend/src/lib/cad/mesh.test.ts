import { describe, expect, it } from "vitest";
import { booleanCandidates, featureName, featureSummary, fitDistance, meshBuffers, unionBBox, VIEW_DIRS, type Feature, type MeshFace } from "./mesh";

const tri = (idx = [0, 1, 2]): MeshFace => ({ face_index: 0, positions: [0, 0, 0, 1, 0, 0, 0, 1, 0], normals: [0, 0, 1, 0, 0, 1, 0, 0, 1], indices: idx });

describe("meshBuffers", () => {
  it("merges faces with index offsets", () => {
    const b = meshBuffers([tri(), tri()]);
    expect(b.positions.length).toBe(18);
    expect(b.normals.length).toBe(18);
    expect([...b.indices]).toEqual([0, 1, 2, 3, 4, 5]);
    expect(b.skipped).toBe(0);
  });
  it("skips faces with out-of-range or malformed data", () => {
    const bad = { ...tri(), normals: [0, 0, 1] };
    const b = meshBuffers([tri([0, 1, 3]), tri([0, 1, -1]), tri([0, 1]), bad, tri()]);
    expect(b.skipped).toBe(4);
    expect(b.indices.length).toBe(3);
  });
});

describe("unionBBox", () => {
  it("covers all boxes, null when empty", () => {
    expect(unionBBox([])).toBeNull();
    expect(unionBBox([{ min: [0, 0, 0], max: [1, 1, 1] }, { min: [-2, 0, 0], max: [0, 5, 1] }])).toEqual({ min: [-2, 0, 0], max: [1, 5, 1] });
  });
});

describe("fitDistance", () => {
  it("grows with bbox size and shrinks with fov", () => {
    const d = (s: number, fov = 45) => fitDistance({ min: [0, 0, 0], max: [s, s, s] }, fov);
    expect(d(20)).toBeGreaterThan(d(10));
    expect(d(10, 30)).toBeGreaterThan(d(10, 60));
  });
  it("is larger for a narrow viewport", () => {
    const b = { min: [0, 0, 0], max: [10, 10, 10] } as const;
    expect(fitDistance({ min: [...b.min], max: [...b.max] }, 45, 0.5)).toBeGreaterThan(fitDistance({ min: [...b.min], max: [...b.max] }, 45, 2));
  });
});

describe("VIEW_DIRS", () => {
  it("are unit length with the documented orientation", () => {
    for (const v of Object.values(VIEW_DIRS)) expect(Math.hypot(...v)).toBeCloseTo(1, 12);
    expect(VIEW_DIRS.front).toEqual([0, -1, 0]);
    expect(VIEW_DIRS.top[2]).toBeGreaterThan(0.999);
    expect(VIEW_DIRS.right).toEqual([1, 0, 0]);
  });
});

describe("FeatureTree helpers (S6)", () => {
  const base = { status: "OK" as const, error_code: null, metrics: null, visible: true, inputs: [] as number[], created_at: "", updated_at: "" };
  const ext: Feature = { ...base, feature_id: 10, seq: 1, feature_type: "EXTRUDE", params: { source_revision_id: "a".repeat(32), handles: ["1A"], distance: 15, direction: "+Z" } };
  const rev: Feature = { ...base, feature_id: 11, seq: 2, feature_type: "REVOLVE", params: { source_revision_id: "a".repeat(32), handles: ["1B"], axis_point: [0, 0], axis_dir: [0, 1], angle_deg: 180 } };
  const cut: Feature = { ...base, feature_id: 12, seq: 3, feature_type: "BOOLEAN", inputs: [10, 11], params: { op: "CUT", target_feature_id: 10, tool_feature_id: 11 } };

  it("formats feature names and summaries for EXTRUDE, REVOLVE, and BOOLEAN", () => {
    expect(featureName(ext)).toBe("Extrude001");
    expect(featureSummary(ext)).toBe("+Z15");
    expect(featureName(rev)).toBe("Revolve002");
    expect(featureSummary(rev)).toBe("180°");
    expect(featureName(cut)).toBe("Cut003");
    expect(featureSummary(cut)).toBe("#10,11");
  });

  it("filters unconsumed bodies and restores own inputs when editing a BOOLEAN", () => {
    const all = [{ ...ext, visible: false }, { ...rev, visible: false }, cut];
    expect(booleanCandidates(all).map((f) => f.feature_id)).toEqual([12]);
    expect(booleanCandidates(all, 3, 12).map((f) => f.feature_id)).toEqual([10, 11]);
  });
});
