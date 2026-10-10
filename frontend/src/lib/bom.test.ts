import { describe, expect, it } from "vitest";
import { PART_NO_RE } from "./bom";

describe("PART_NO_RE (mirrors the server pattern)", () => {
  it("accepts part numbers, rejects formula/control input", () => {
    for (const ok of ["N-M6", "BR-100/A", "볼트_M6", "P 100.2"]) expect(PART_NO_RE.test(ok)).toBe(true);
    for (const bad of ["=cmd", "a'b", "x\ty", "", "x".repeat(65)]) expect(PART_NO_RE.test(bad)).toBe(false);
  });
});
