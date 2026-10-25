import { describe, expect, it } from "vitest";
import { measureGeom, polyArea } from "./measure";
import { runNow, start, step, type Sel } from "./tools";

describe("measure", () => {
  it("gives exact values for lines, circles and arcs, and close ones for curved outlines", () => {
    expect(measureGeom({ type: "LINE", start: [0, 0], end: [3, 4] })).toEqual({ length: 5 });
    const c = measureGeom({ type: "CIRCLE", center: [0, 0], radius: 10 })!;
    expect(c.area).toBeCloseTo(Math.PI * 100);
    expect(measureGeom({ type: "ARC", center: [0, 0], radius: 10, start_angle: 0, end_angle: 90 })!.length).toBeCloseTo(5 * Math.PI);
    // a 20 x 10 slot: 20 x 10 rectangle + a 5 mm circle
    const slot = measureGeom({ type: "LWPOLYLINE", closed: true, points: [[0, 0, 0], [20, 0, 1], [20, 10, 0], [0, 10, 1]] })!;
    expect(slot.area!).toBeCloseTo(200 + Math.PI * 25, 0);
    expect(slot.length).toBeCloseTo(40 + 10 * Math.PI, 1);
    const e = measureGeom({ type: "ELLIPSE", center: [0, 0], major: [10, 0], ratio: 0.5, start_param: 0, end_param: 2 * Math.PI })!;
    expect(e.area).toBeCloseTo(Math.PI * 50);
    expect(polyArea([[0, 0], [4, 0], [4, 3]])).toBe(6);
  });

  it("DIST, AREA and LIST report in the prompt", () => {
    const d = step(step(start("DIST", 0).state, { kind: "point", p: [0, 0] }, []).state, { kind: "point", p: [30, 40] }, []);
    expect(d.prompt).toContain("거리 50 mm");
    expect(d.prompt).toContain("각도 53.13°");
    let a = start("AREA", 0).state;
    for (const p of [[0, 0], [10, 0], [10, 5]] as [number, number][]) a = step(a, { kind: "point", p }, []).state;
    expect(step(a, { kind: "enter" }, []).prompt).toContain("면적 25 mm²");
    const sel: Sel[] = [
      { handle: "1", layer: "CUT", geom: { type: "CIRCLE", center: [0, 0], radius: 1 } },
      { handle: "2", layer: "CUT", geom: { type: "LINE", start: [0, 0], end: [10, 0] } },
    ];
    expect(runNow("LIST", sel.slice(1)).prompt).toBe("LIST  LINE · 레이어 CUT · 길이 10 mm");
    expect(runNow("LIST", sel).prompt).toContain("2개 · 길이 합 16.283 mm · 닫힌 도형 면적 합 3.142 mm²");
  });
});
