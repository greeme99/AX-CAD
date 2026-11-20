import { describe, expect, it } from "vitest";
import { buildCaeResultOverlay, caeColorRamp, type CaeResultData } from "./cae";

describe("CAE 3D color map & deformation overlay (E3)", () => {
  const sampleResult: CaeResultData = {
    study_id: 1,
    node_count: 4,
    element_count: 1,
    max_disp_mm: 0.76,
    max_von_mises_mpa: 60.0,
    min_safety_factor: 4.1667,
    nodes: [
      [0, 0, 0],
      [200, 0, 0],
      [0, 20, 0],
      [0, 0, 10],
    ],
    elements: [[0, 1, 2, 3]],
    displacements_mm: [
      [0, 0, 0],
      [0, 0, -0.76],
      [0, 0, 0],
      [0, 0, 0],
    ],
    von_mises_mpa: [60.0, 0.5, 30.0, 45.0],
    safety_factor: [4.1667, 500.0, 8.3333, 5.5556],
  };

  it("maps 0..1 to blue..red and inverts for safety factor", () => {
    expect(caeColorRamp(0)).toEqual([0, 0, 1]);
    expect(caeColorRamp(1)).toEqual([1, 0, 0]);
    expect(caeColorRamp(0, true)).toEqual([1, 0, 0]);
    expect(caeColorRamp(1, true)).toEqual([0, 0, 1]);
  });

  it("builds vertex-colored point cloud + min/max markers and disposes all buffers", () => {
    const h = buildCaeResultOverlay(sampleResult, "von_mises", 10);
    // 1 Points + 1 max marker Mesh + 1 min marker Mesh = 3 children
    expect(h.group.children.length).toBe(3);
    expect(h.minVal).toBeCloseTo(0.5);
    expect(h.maxVal).toBeCloseTo(60.0);
    h.dispose();
    expect(h.group.children.length).toBe(0);
  });
});
