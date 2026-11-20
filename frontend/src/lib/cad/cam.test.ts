import { describe, expect, it } from "vitest";
import { buildCamToolpathOverlay, type CamSegment } from "./cam";

describe("CAM 3D toolpath simulation overlay (C2)", () => {
  const sampleSegments: CamSegment[] = [
    { kind: "RAPID", start: [0, 0, 20], end: [10, 10, 20] },
    { kind: "PLUNGE", start: [10, 10, 20], end: [10, 10, -2], feed: 200 },
    { kind: "CUT", start: [10, 10, -2], end: [50, 10, -2], feed: 600 },
    { kind: "RAPID", start: [50, 10, -2], end: [50, 10, 20] },
  ];

  it("renders cut lines, dashed rapid lines, and tool marker at 100% progress and disposes cleanly", () => {
    const handle = buildCamToolpathOverlay(sampleSegments, 100);
    // 1 cut/plunge LineSegments + 1 rapid LineSegments + 1 tool marker Mesh = 3 children
    expect(handle.group.children.length).toBe(3);
    handle.dispose();
    expect(handle.group.children.length).toBe(0);
  });

  it("clips segments proportionally to progressPct and handles 0% cleanly", () => {
    const zero = buildCamToolpathOverlay(sampleSegments, 0);
    expect(zero.group.children.length).toBe(0);
    zero.dispose();

    const half = buildCamToolpathOverlay(sampleSegments, 50);
    expect(half.group.children.length).toBe(3);
    half.dispose();
  });
});
