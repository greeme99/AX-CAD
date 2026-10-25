import { describe, expect, it } from "vitest";
import { axisLine, booleanCandidates, failedDependent, featureName, MACH_DEFAULT, machDraftOf, machiningParams, PAD_DEFAULT, padDraftOf, padParams, PRIM_DEFAULT, primDraftOf, primitiveParams, revolveParams, type Feature, type MachDraft } from "./features";

const ok = { px: "0", py: "0", dx: "0", dy: "1", angle: "360" };

describe("revolveParams", () => {
  it("parses a valid axis", () => {
    expect(revolveParams(ok)).toEqual({ axis_point: [0, 0], axis_dir: [0, 1], angle_deg: 360 });
  });
  it.each([
    [{ dx: "0", dy: "0" }, "(0, 0)"],
    [{ px: "" }, "숫자"],
    [{ py: "abc" }, "숫자"],
    [{ angle: "0" }, "각도"],
    [{ angle: "361" }, "각도"],
  ])("rejects %o", (patch, msg) => {
    expect(revolveParams({ ...ok, ...patch })).toContain(msg);
  });
});

describe("axisLine", () => {
  const ext = { min: [0, 0] as [number, number], max: [40, 30] as [number, number] };
  it("passes through the point along the direction, beyond the extents", () => {
    const [a, b] = axisLine({ ...ok, px: "10" }, ext);
    expect(a[0]).toBeCloseTo(10);
    expect(b[0]).toBeCloseTo(10);
    expect(a[1]).toBeLessThan(0);
    expect(b[1]).toBeGreaterThan(30);
  });
  it("is empty for a zero or invalid direction", () => {
    expect(axisLine({ ...ok, dy: "0" }, ext)).toEqual([]);
    expect(axisLine({ ...ok, px: "x" }, ext)).toEqual([]);
  });
});

const base = { status: "OK" as const, error_code: null, metrics: null, created_at: "", updated_at: "", inputs: [] as number[] };
const ext = (id: number, seq: number, visible = true): Feature => ({ ...base, feature_id: id, seq, visible, feature_type: "EXTRUDE", params: { source_revision_id: "a".repeat(32), handles: ["1"], distance: 1, direction: "+Z" } });
const cut: Feature = { ...base, feature_id: 3, seq: 3, visible: true, inputs: [1, 2], feature_type: "BOOLEAN", params: { op: "CUT", target_feature_id: 1, tool_feature_id: 2 } };

describe("booleanCandidates", () => {
  const all = [ext(1, 1, false), ext(2, 2, false), cut, ext(4, 4), { ...ext(5, 5), status: "ERROR" as const }];
  it("new BOOLEAN: visible, built features only", () => {
    expect(booleanCandidates(all).map((f) => f.feature_id)).toEqual([3, 4]);
  });
  it("editing a BOOLEAN: its own inputs stay, later features and itself excluded", () => {
    expect(booleanCandidates(all, cut).map((f) => f.feature_id)).toEqual([1, 2]);
  });
});

describe("names and errors", () => {
  it("names by type/op and seq", () => {
    expect(featureName(ext(1, 1))).toBe("Extrude001");
    expect(featureName(cut)).toBe("Cut003");
  });
  it("reads the failed dependent id", () => {
    expect(failedDependent({ feature_id: 7 })).toBe(7);
    expect(failedDependent(undefined)).toBeNull();
    expect(failedDependent({ feature_id: "7" })).toBeNull();
  });
});


describe("machining params (Track 5-3)", () => {
  const d = (o: Partial<MachDraft>): MachDraft => ({ ...MACH_DEFAULT, target: "7", ...o });
  it("each kind builds its API params", () => {
    expect(machiningParams(d({ kind: "FILLET", size: "2" }))).toEqual({ target_feature_id: 7, radius: 2, edges: "VERTICAL" });
    expect(machiningParams(d({ kind: "HOLE", size: "6", positions: "10,10; 30, 20", depth: "" }))).toEqual({ target_feature_id: 7, positions: [[10, 10], [30, 20]], diameter: 6, depth: null });
    expect(machiningParams(d({ kind: "PATTERN", patKind: "POLAR", copies: "4", center: "0,0", angle: "360" }))).toMatchObject({ kind: "POLAR", copies: 4, center: [0, 0] });
    expect(machiningParams(d({ kind: "MIRROR", plane: "ZX", offset: "-5" }))).toEqual({ target_feature_id: 7, plane: "ZX", offset: -5 });
    expect(machiningParams(d({ kind: "SHELL", size: "1", openFace: "BOTTOM" }))).toEqual({ target_feature_id: 7, thickness: 1, open_face: "BOTTOM", outward: false, join: "ARC" });
  });
  it("rejects bad input in Korean", () => {
    expect(machiningParams(d({ target: "" }))).toContain("대상");
    expect(machiningParams(d({ kind: "HOLE", positions: "10;20" }))).toContain("x,y");
    expect(machiningParams(d({ kind: "PATTERN", copies: "1" }))).toContain("2~100");
    expect(machiningParams(d({ kind: "PATTERN", step: "0,0,0" }))).toContain("0이 아닌");
    expect(machiningParams(d({ kind: "CHAMFER", size: "-1" }))).toContain("0보다");
  });
  it("draft round-trips a stored feature", () => {
    const p = { target_feature_id: 3, positions: [[1, 2]] as [number, number][], diameter: 5, depth: 4 };
    expect(machiningParams(machDraftOf("HOLE", p))).toEqual(p);
  });
});

describe("pad / pocket / primitive forms (Track 8 P2-2)", () => {
  it("builds each length mode and refuses missing values", () => {
    expect(padParams({ ...PAD_DEFAULT, mode: "SYMMETRIC", distance: "8" })).toEqual({ mode: "SYMMETRIC", direction: "+Z", taper_deg: 0, distance: 8, distance2: null, up_to: null });
    expect(padParams({ ...PAD_DEFAULT, mode: "TWO_SIDED", distance: "8", distance2: "" })).toMatch(/두 번째/);
    expect(padParams({ ...PAD_DEFAULT, mode: "UP_TO_FACE" })).toMatch(/면/);
    const up = { origin: [0, 0, 9], normal: [0, 0, 1] } as const;
    expect(padParams({ ...PAD_DEFAULT, mode: "UP_TO_FACE", upTo: { origin: [...up.origin], normal: [...up.normal] } })).toMatchObject({ distance: null, up_to: up });
    expect(padParams({ ...PAD_DEFAULT, mode: "THROUGH_ALL", direction: "-Z" })).toMatchObject({ mode: "THROUGH_ALL", distance: null });
    expect(padParams({ ...PAD_DEFAULT, taper: "75" })).toMatch(/테이퍼/);
    expect(padDraftOf({ mode: "TWO_SIDED", distance: 3, distance2: 2, direction: "-Z", taper_deg: 1.5 })).toMatchObject({ distance: "3", distance2: "2", taper: "1.5" });
  });

  it("checks primitive dimensions and names bodies by kind", () => {
    expect(primitiveParams({ ...PRIM_DEFAULT, kind: "CYLINDER", origin: "1,2,3", axis: "X" })).toEqual({ kind: "CYLINDER", origin: [1, 2, 3], axis: "X", radius: 10, height: 10 });
    expect(primitiveParams({ ...PRIM_DEFAULT, kind: "CONE", dims: { radius: "5", radius2: "0", height: "4" } })).toMatchObject({ radius2: 0 });
    expect(primitiveParams({ ...PRIM_DEFAULT, kind: "CONE", dims: { radius: "5", radius2: "5", height: "4" } })).toMatch(/원뿔/);
    expect(primitiveParams({ ...PRIM_DEFAULT, kind: "TORUS", dims: { radius: "2", radius2: "3" } })).toMatch(/관 반지름/);
    expect(primitiveParams({ ...PRIM_DEFAULT, origin: "1,2" })).toMatch(/기준점/);
    const box = { feature_id: 4, seq: 4, feature_type: "PRIMITIVE", params: { kind: "BOX", length: 1, width: 1, height: 1, origin: [0, 0, 0], axis: "Z" } } as unknown as Feature;
    expect(featureName(box)).toBe("Box004");
    expect(primDraftOf((box as Extract<Feature, { feature_type: "PRIMITIVE" }>).params).dims.length).toBe("1");
  });
});

describe("draft and thickness forms (Track 8 P2-3)", () => {
  it("builds draft params and refuses a zero or steep angle", () => {
    const d = { ...MACH_DEFAULT, kind: "DRAFT" as const, target: "3", angle: "5", pull: "-Z" as const, neutral: "TOP" as const };
    expect(machiningParams(d)).toEqual({ target_feature_id: 3, angle_deg: 5, pull: "-Z", neutral: "TOP" });
    expect(machiningParams({ ...d, angle: "0" })).toMatch(/구배/);
    expect(machiningParams({ ...d, angle: "50" })).toMatch(/구배/);
    expect(machDraftOf("DRAFT", { target_feature_id: 3, angle_deg: 5, pull: "-Z", neutral: "TOP" })).toMatchObject({ angle: "5", pull: "-Z", neutral: "TOP" });
  });

  it("carries the thickness options and reads old shells", () => {
    const d = { ...MACH_DEFAULT, kind: "SHELL" as const, target: "2", size: "2", openFace: "NONE" as const, outward: true, join: "INTERSECTION" as const };
    expect(machiningParams(d)).toEqual({ target_feature_id: 2, thickness: 2, open_face: "NONE", outward: true, join: "INTERSECTION" });
    expect(machDraftOf("SHELL", { target_feature_id: 2, thickness: 2, open_face: "TOP" })).toMatchObject({ openFace: "TOP", outward: false, join: "ARC" });
  });
});
