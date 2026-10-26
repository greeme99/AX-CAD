import { describe, expect, it } from "vitest";
import { DRAWING_DRAFT, drawingRequest } from "./drawing";

describe("drawing request (P3-5)", () => {
  it("sends the views in sheet order, letters sections then details, and clears empty notes", () => {
    const body = drawingRequest({
      ...DRAWING_DRAFT,
      views: ["TOP", "FRONT"],
      scale: "1:2",
      sections: [{ axis: "Y", offset: "-15" }],
      details: [{ view: "FRONT", x: "5", y: "5", r: "4", factor: "2.5" }],
    });
    expect(body).toEqual({
      views: ["FRONT", "TOP"],
      projection: "THIRD",
      sheet: "A3",
      portrait: false,
      scale: 0.5,
      sections: [{ label: "A", axis: "Y", offset: -15 }],
      details: [{ label: "B", view: "FRONT", center: [5, 5], radius: 4, factor: 2.5 }],
      dimensions: true,
      tolerance: "m",
      roughness: null,
      material: null,
    });
    expect(drawingRequest({ ...DRAWING_DRAFT, roughness: "3.2", material: " SS400 " })).toMatchObject({ roughness: 3.2, material: "SS400", scale: null });
  });
  it("refuses drafts the server would", () => {
    expect(drawingRequest({ ...DRAWING_DRAFT, views: [] })).toContain("투상도");
    expect(drawingRequest({ ...DRAWING_DRAFT, scale: "2" })).toContain("축척");
    expect(drawingRequest({ ...DRAWING_DRAFT, sections: [{ axis: "X", offset: "" }] })).toContain("단면");
    const detail = { view: "RIGHT" as const, x: "0", y: "0", r: "5", factor: "2" };
    expect(drawingRequest({ ...DRAWING_DRAFT, details: [{ ...detail, factor: "1" }] })).toContain("배율");
    expect(drawingRequest({ ...DRAWING_DRAFT, views: ["FRONT"], details: [detail] })).toContain("투상도에 포함");
  });
});
