import { describe, expect, it } from "vitest";
import { buildInsertSpecs, geomsToPreviewSvg, pushRecentBlock } from "./blockPalette";
import type { Geom } from "./geom";

describe("blockPalette", () => {
  it("builds rotated, scaled, mirrored, and layer-overridden INSERT specs", () => {
    const specs = buildInsertSpecs("PB-DOOR-1234", [1000, 2000], {
      rotation: -90,
      xscale: 1.5,
      yscale: 2,
      flipX: true,
      flipY: false,
      layer: "A-DOOR",
    });
    expect(specs).toHaveLength(1);
    expect(specs[0].layer).toBe("A-DOOR");
    expect(specs[0].geom).toEqual({
      type: "INSERT",
      name: "PB-DOOR-1234",
      insert: [1000, 2000],
      xscale: -1.5,
      yscale: 2,
      rotation: 270,
    });
  });

  it("generates rectangular array INSERT specs (rows x cols) with dx/dy spacing", () => {
    const specs = buildInsertSpecs("PB-LED_PANEL-abcd", [500, 500], {
      cols: 3,
      rows: 2,
      dx: 1200,
      dy: 1800,
      layer: "E-LTNG",
    });
    expect(specs).toHaveLength(6);
    const coords = specs.map((s) => (s.geom.type === "INSERT" ? s.geom.insert : null));
    expect(coords).toEqual([
      [500, 500],
      [1700, 500],
      [2900, 500],
      [500, 2300],
      [1700, 2300],
      [2900, 2300],
    ]);
  });

  it("renders SVG preview paths with rotation/flip and origin crosshair", () => {
    const geoms: Geom[] = [
      { type: "LINE", start: [0, 0], end: [900, 0] },
      { type: "CIRCLE", center: [450, 450], radius: 200 },
    ];
    const svg = geomsToPreviewSvg(geoms, { rotation: 90, flipX: false, flipY: false });
    expect(svg.paths.length).toBeGreaterThanOrEqual(2);
    expect(svg.viewBox.split(" ")).toHaveLength(4);
    expect(svg.originCross[2]).toBeGreaterThan(0);
  });

  it("maintains unique recent block order up to max size", () => {
    let list: string[] = [];
    for (const n of ["A", "B", "C", "D", "E", "F", "G"]) {
      list = pushRecentBlock(list, n, 6);
    }
    expect(list).toEqual(["G", "F", "E", "D", "C", "B"]);
    expect(pushRecentBlock(list, "D", 6)).toEqual(["D", "G", "F", "E", "C", "B"]);
  });
});
