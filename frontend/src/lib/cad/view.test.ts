import { describe, expect, it } from "vitest";
import { fitExtents, screenToWorld, worldToScreen, zoomAt, type View } from "./view";

const v0: View = { scale: 2.5, offsetX: 40, offsetY: 300 };

describe("view", () => {
  it("zoomAt keeps the world point under the cursor fixed", () => {
    const [sx, sy] = [313.7, 128.2];
    const before = screenToWorld(v0, sx, sy);
    let v = v0;
    for (let i = 0; i < 10; i++) v = zoomAt(v, sx, sy, i % 2 ? 1 / 1.2 : 1.2);
    const after = screenToWorld(v, sx, sy);
    expect(Math.abs(after[0] - before[0])).toBeLessThan(1e-9);
    expect(Math.abs(after[1] - before[1])).toBeLessThan(1e-9);
  });

  it("screenToWorld(worldToScreen(p)) round-trips", () => {
    const p: [number, number] = [-12.5, 77.25];
    const q = screenToWorld(v0, ...worldToScreen(v0, ...p));
    expect(q[0]).toBeCloseTo(p[0], 9);
    expect(q[1]).toBeCloseTo(p[1], 9);
  });

  it("fitExtents centers the extents", () => {
    const e = { min: [10, 20] as [number, number], max: [110, 70] as [number, number] };
    const v = fitExtents(e, 800, 600, 20);
    const [cx, cy] = worldToScreen(v, 60, 45);
    expect(cx).toBeCloseTo(400, 9);
    expect(cy).toBeCloseTo(300, 9);
    const [x0] = worldToScreen(v, 10, 0);
    const [x1] = worldToScreen(v, 110, 0);
    expect(x0).toBeGreaterThanOrEqual(20 - 1e-9);
    expect(x1).toBeLessThanOrEqual(780 + 1e-9);
  });
});
