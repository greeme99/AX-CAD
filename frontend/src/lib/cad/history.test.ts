import { describe, expect, it } from "vitest";
import { initHistory, push, redo, undo } from "./history";

describe("history", () => {
  it("undo/redo round trip", () => {
    let h = push(push(initHistory(0), 1), 2);
    h = undo(undo(h));
    expect(h.present).toBe(0);
    h = redo(h);
    expect(h.present).toBe(1);
    expect(undo(initHistory(0)).present).toBe(0);
  });
  it("a new action clears the redo stack", () => {
    const h = push(undo(push(initHistory(0), 1)), 9);
    expect(h.future).toEqual([]);
    expect(redo(h).present).toBe(9);
  });
  it("caps past at 200", () => {
    let h = initHistory(0);
    for (let i = 1; i <= 250; i++) h = push(h, i);
    expect(h.past).toHaveLength(200);
    expect(h.past[0]).toBe(50);
  });
});
