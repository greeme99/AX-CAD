import { describe, expect, it } from "vitest";
import { runInputs } from "./script";

const made = (inputs: string[]) => {
  const r = runInputs(inputs);
  expect(r.error).toBeUndefined();
  return r.results.flatMap((x) => x.created ?? []).map((c) => c.geom);
};

// the inputs core/copilot/nlcmd.py proposes (tests/test_copilot.py CAD2D) draw what they say
describe("runInputs (Copilot commands)", () => {
  it("rectangle from a corner and a relative size", () => {
    const [g] = made(["REC", "10,20", "@100,50"]);
    expect(g.type).toBe("LWPOLYLINE");
    if (g.type !== "LWPOLYLINE") return;
    const xs = g.points.map((p) => p[0]);
    const ys = g.points.map((p) => p[1]);
    expect([Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]).toEqual([10, 20, 110, 70]);
  });
  it("circle from centre and radius", () => {
    expect(made(["C", "-10,3", "6.25"])).toEqual([{ type: "CIRCLE", center: [-10, 3], radius: 6.25 }]);
  });
  it("lines through points end with Enter", () => {
    const r = runInputs(["L", "0,0", "100,0", "100,50", ""]);
    expect(r.results.flatMap((x) => x.created ?? [])).toHaveLength(2);
    expect(r.state.tool).toBe("SELECT");
  });
  it("polar line", () => {
    const [g] = made(["L", "10,10", "@80<30", ""]);
    expect(g.type).toBe("LINE");
    if (g.type !== "LINE") return;
    expect(g.end[0]).toBeCloseTo(10 + 80 * Math.cos(Math.PI / 6), 9);
    expect(g.end[1]).toBeCloseTo(50, 9);
  });
  it("polygon with n sides around a centre", () => {
    const [g] = made(["POL", "6", "50,50", "@25,0"]);
    expect(g.type).toBe("LWPOLYLINE");
    if (g.type === "LWPOLYLINE") expect(g.points).toHaveLength(6);
  });
  it("text with default height", () => {
    const [g] = made(["T", "0,-20", "", "품번 A-01"]);
    expect(g).toMatchObject({ type: "TEXT", value: "품번 A-01", insert: [0, -20], height: 2.5 });
  });
  it("stops at the first rejected input and applies nothing", () => {
    const r = runInputs(["C", "0,0", "20", "C", "5,5", "0"]);
    expect(r.error?.index).toBe(5);
    expect(r.error?.message).toContain("반지름");
    expect(r.results).toEqual([]);
  });
  it("values without a command and unknown words are errors", () => {
    expect(runInputs(["10,10"]).error?.index).toBe(0);
    expect(runInputs(["L", "0,0", "foo"]).error?.input).toBe("foo");
    expect(runInputs(["", "C", "0,0", "5"]).error).toBeUndefined(); // a leading Enter is harmless
  });
  it("selection tools need a selection", () => {
    expect(runInputs(["M", "0,0", "10,0"]).error?.index).toBe(0);
  });
});
