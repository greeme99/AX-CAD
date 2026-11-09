import { describe, expect, it } from "vitest";
import { wcmatch } from "./wcmatch";

describe("wcmatch (AutoLISP patterns)", () => {
  it("* ? # @ . match like AutoLISP", () => {
    expect(wcmatch("ABC123", "ABC*")).toBe(true);
    expect(wcmatch("ABC", "A?C")).toBe(true);
    expect(wcmatch("AC", "A?C")).toBe(false);
    expect(wcmatch("A1", "A#")).toBe(true);
    expect(wcmatch("AB", "A#")).toBe(false);
    expect(wcmatch("A-B", "A.B")).toBe(true);
    expect(wcmatch("AxB", "A.B")).toBe(false);
    expect(wcmatch("Ab", "A@")).toBe(true);
    expect(wcmatch("A5", "A@")).toBe(false);
    expect(wcmatch("", "*")).toBe(true);
  });
  it("sets, ranges and negated sets", () => {
    expect(wcmatch("B1", "[ABC]#")).toBe(true);
    expect(wcmatch("D1", "[ABC]#")).toBe(false);
    expect(wcmatch("M", "[A-Z]")).toBe(true);
    expect(wcmatch("m", "[A-Z]")).toBe(false);
    expect(wcmatch("D", "[~ABC]")).toBe(true);
    expect(wcmatch("A", "[~ABC]")).toBe(false);
    expect(wcmatch("A]", "A`]")).toBe(true); // the ` takes the next character literally
    expect(wcmatch("A*", "A`*")).toBe(true);
    expect(wcmatch("AB", "A`*")).toBe(false);
  });
  it("alternatives and a leading ~", () => {
    expect(wcmatch("WALL", "DOOR,WALL")).toBe(true);
    expect(wcmatch("ROOF", "DOOR,WALL")).toBe(false);
    expect(wcmatch("ROOF", "~DOOR")).toBe(true);
    expect(wcmatch("DOOR", "~DOOR")).toBe(false);
    expect(wcmatch("DOOR", "~D*")).toBe(false);
  });
  it("case matters unless asked", () => {
    expect(wcmatch("abc", "ABC")).toBe(false);
    expect(wcmatch("abc", "ABC", true)).toBe(true);
  });
  it("a pattern that cannot be read matches nothing; hostile input stays fast", () => {
    expect(wcmatch("A", "[A")).toBe(false);
    expect(wcmatch("A", "A`")).toBe(false);
    const t = performance.now();
    expect(wcmatch("a".repeat(60_000), "*a*a*a*a*a*a*a*a*b")).toBe(false);
    expect(wcmatch("x".repeat(60_000), "x".repeat(900) + "*y")).toBe(false);
    expect(performance.now() - t).toBeLessThan(1500);
    expect(wcmatch("a".repeat(70_000), "*")).toBe(false); // over the text limit
  });
});
