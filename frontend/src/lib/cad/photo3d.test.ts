import { describe, expect, it } from "vitest";
import {
  defaultViewLabel,
  mmPerPixel,
  pixelDistance,
  uniformTurntableAngles,
  validatePhoto3dInputs,
} from "./photo3d";

describe("photo3d helpers", () => {
  it("computes pixel distance and mm/px scale accurately", () => {
    expect(pixelDistance([10, 20], [210, 20])).toBeCloseTo(200, 6);
    expect(mmPerPixel(100, [10, 20], [210, 20])).toBeCloseTo(0.5, 6);
    expect(mmPerPixel(-10, [0, 0], [100, 0])).toBeNull();
    expect(mmPerPixel(100, [5, 5], [5.5, 5.5])).toBeNull();
  });

  it("assigns default ortho view labels and uniform turntable angles", () => {
    expect(defaultViewLabel("ORTHO_MULTI", 0)).toBe("FRONT");
    expect(defaultViewLabel("ORTHO_MULTI", 1)).toBe("SIDE");
    expect(defaultViewLabel("ORTHO_MULTI", 2)).toBe("TOP");
    expect(uniformTurntableAngles(4)).toEqual([0, 90, 180, 270]);
  });

  it("validates photo count and reference dimension", () => {
    expect(validatePhoto3dInputs({ fileCount: 0, mode: "ORTHO_MULTI", referenceMm: 100 })).toBeTruthy();
    expect(validatePhoto3dInputs({ fileCount: 2, mode: "TURNTABLE", referenceMm: 100 })).toBeTruthy();
    expect(validatePhoto3dInputs({ fileCount: 6, mode: "TURNTABLE", referenceMm: 120 })).toBeNull();
  });
});
