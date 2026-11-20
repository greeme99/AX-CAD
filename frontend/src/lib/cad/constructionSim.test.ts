import { describe, expect, it } from "vitest";
import { buildScurvePolyline, formatKrw, ganttBarRect } from "./constructionSim";

describe("constructionSim helpers", () => {
  it("formats KRW amounts cleanly", () => {
    expect(formatKrw(12500000)).toBe("12,500,000원");
  });

  it("computes normalized Gantt bar positions", () => {
    expect(ganttBarRect(0, 5, 10)).toEqual({ leftPct: 0, widthPct: 50 });
    expect(ganttBarRect(5, 10, 10)).toEqual({ leftPct: 50, widthPct: 50 });
  });

  it("builds valid SVG polyline coordinates for S-curve", () => {
    const poly = buildScurvePolyline(
      [
        { day: 1, progress_pct: 20 },
        { day: 5, progress_pct: 100 },
      ],
      224,
      124,
      12,
    );
    expect(poly.split(" ").length).toBe(2);
    expect(poly.endsWith("212.0,12.0")).toBe(true);
  });
});
