import { describe, expect, it } from "vitest";
import { contextFromPath, findingsPath, isToggleKey, loadOpen, OPEN_KEY, runnable, saveOpen, type Action } from "./copilot";

const mem = () => {
  const m = new Map<string, string>();
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), m };
};

describe("copilot helpers", () => {
  it("remembers the open state and survives blocked storage", () => {
    const s = mem();
    expect(loadOpen(s)).toBe(false);
    saveOpen(s, true);
    expect(s.m.get(OPEN_KEY)).toBe("1");
    expect(loadOpen(s)).toBe(true);
    saveOpen(s, false);
    expect(loadOpen(s)).toBe(false);
    const blocked = {
      getItem: () => {
        throw new Error("SecurityError");
      },
      setItem: () => {
        throw new Error("SecurityError");
      },
    };
    expect(loadOpen(blocked)).toBe(false);
    expect(() => saveOpen(blocked, true)).not.toThrow();
    expect(loadOpen(undefined)).toBe(false);
  });
  it("Ctrl+. / Cmd+. toggles, nothing else", () => {
    const k = (key: string, o: Partial<KeyboardEvent> = {}) => ({ key, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...o });
    expect(isToggleKey(k(".", { ctrlKey: true }))).toBe(true);
    expect(isToggleKey(k(".", { metaKey: true }))).toBe(true);
    expect(isToggleKey(k("."))).toBe(false);
    expect(isToggleKey(k(",", { ctrlKey: true }))).toBe(false);
    expect(isToggleKey(k(".", { ctrlKey: true, shiftKey: true }))).toBe(false);
  });
  it("findings path prefers quote > revision > document", () => {
    expect(findingsPath({ page: "home" })).toBeNull();
    expect(findingsPath({ page: "model3d", document_id: 3 })).toBe("/copilot/findings?document_id=3");
    expect(findingsPath({ page: "cad2d", document_id: 3, revision_id: "ab" })).toBe("/copilot/findings?revision_id=ab");
    expect(findingsPath({ page: "quote", quote_id: 9, document_id: 3 })).toBe("/copilot/findings?quote_id=9");
  });
  it("a proposal runs only where a handler exists", () => {
    const a: Action = { kind: "cad2d", inputs: ["C", "0,0", "5"], summary: "" };
    expect(runnable(a, {})).toBe(false);
    expect(runnable(a, { cad2d: () => null })).toBe(true);
    expect(runnable({ kind: "dialog", dialog: "HOLE", values: {}, summary: "" }, { cad2d: () => null })).toBe(false);
  });
});

describe("contextFromPath", () => {
  it("reads the page and ids from the route", () => {
    expect(contextFromPath("/")).toEqual({ page: "home" });
    expect(contextFromPath("/projects/4")).toEqual({ page: "project" });
    expect(contextFromPath("/documents/12/bom")).toEqual({ page: "document", document_id: 12 });
    expect(contextFromPath("/model/7")).toEqual({ page: "model3d", document_id: 7 });
    const rev = "0123456789abcdef0123456789abcdef";
    expect(contextFromPath(`/viewer/${rev}`)).toEqual({ page: "cad2d", revision_id: rev });
    expect(contextFromPath("/viewer/../../etc")).toEqual({ page: "cad2d", revision_id: undefined });
    expect(contextFromPath("/quotes/x1")).toEqual({ page: "quote", quote_id: undefined });
    expect(contextFromPath("/admin/users")).toEqual({ page: "admin" });
    expect(contextFromPath("/cad")).toEqual({ page: "other" });
  });
});
