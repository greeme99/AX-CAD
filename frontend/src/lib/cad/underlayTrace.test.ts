import { describe, expect, it } from "vitest";
import type { TraceResponse } from "../underlayApi";
import { defaultGeom } from "./underlay";
import { buildTracedEntities, scaleForReferenceWidth } from "./underlayTrace";

const sampleTrace: TraceResponse = {
  source: "raster",
  page_size_mm: [200, 150],
  scale_detected: null,
  has_dimensions: false,
  needs_reference_dimension: true,
  traced_bbox_mm: [20, 25, 120, 85],
  lines: [],
  polylines: [
    {
      points: [
        [20, 25],
        [120, 25],
        [120, 85],
        [20, 85],
      ],
      closed: true,
    },
  ],
  circles: [{ center: [70, 55], radius: 12 }],
  arcs: [],
  stats: { line_count: 0, polyline_count: 1, circle_count: 1, arc_count: 0 },
};

describe("underlayTrace", () => {
  it("computes exact scale factor from a user-provided reference width", () => {
    // Traced bbox width is 120 - 20 = 100 mm; user specifies real width = 250 mm -> scale 2.5
    const s = scaleForReferenceWidth(sampleTrace.traced_bbox_mm, 250);
    expect(s).toBeCloseTo(2.5, 6);
    expect(typeof scaleForReferenceWidth(sampleTrace.traced_bbox_mm, -5)).toBe("string");
    expect(typeof scaleForReferenceWidth(null, 200)).toBe("string");
  });

  it("builds model-space CAD entities and automatic overall + diameter dimensions", () => {
    const g = { ...defaultGeom({ format: "pdf", sha256: "a".repeat(64), name: "scan.pdf" }, 0, [10, 5]), scale: 2 };
    const built = buildTracedEntities(sampleTrace, g, {
      traceLayer: "PDF-TRACE",
      dimLayer: "PDF-DIM",
      autoDimension: true,
    });
    expect(built.geomCount).toBe(2); // 1 closed polyline + 1 circle
    expect(built.dimCount).toBeGreaterThanOrEqual(3); // overall W, overall H, circle diameter

    const dims = built.items.filter((i) => i.layer === "PDF-DIM");
    expect(dims.some((d) => d.geom.type === "DIM_LINEAR" && d.geom.angle === 0)).toBe(true);
    expect(dims.some((d) => d.geom.type === "DIM_LINEAR" && d.geom.angle === 90)).toBe(true);
    expect(dims.some((d) => d.geom.type === "DIM_DIAMETER" && Math.abs(d.geom.radius - 24) < 1e-6)).toBe(true);
  });

  it("omits dimensions when autoDimension is false", () => {
    const g = defaultGeom({ format: "pdf", sha256: "b".repeat(64), name: "scan.pdf" }, 0, [0, 0]);
    const built = buildTracedEntities(sampleTrace, g, {
      traceLayer: "PDF-TRACE",
      dimLayer: "PDF-DIM",
      autoDimension: false,
    });
    expect(built.geomCount).toBe(2);
    expect(built.dimCount).toBe(0);
  });
});
