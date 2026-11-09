import { describe, expect, it } from "vitest";
import { defaultGeom, moved, type UnderlayRec } from "./underlay";
import { underlayOps, type UEnt } from "./underlayEdits";

const rec = (over: Partial<UnderlayRec> = {}): UnderlayRec => ({ ...defaultGeom({ format: "pdf", sha256: "a".repeat(64), name: "p.pdf" }, 0, [0, 0]), linked: true, uniform: true, size: [210, 297], ...over });
const ent = (handle: string, r: UnderlayRec, layer = "OUTLINE"): UEnt => ({ handle, type: "UNDERLAY", layer, underlay: r });

describe("underlay edit items", () => {
  const a = ent("A1", rec());
  const b = ent("B2", rec({ page: 1 }));
  it("nothing changed, nothing to save", () => {
    expect(underlayOps([a, b], [a, b])).toEqual([]);
    expect(underlayOps([a], [{ ...a }, { handle: "5", type: "LINE", layer: "0" }])).toEqual([]); // other entities are not its business
  });
  it("created (new-N), modified (geometry or layer) and deleted", () => {
    const fresh = ent("new-3", rec({ name: "q.pdf" }), "PLAN");
    const mv = ent("A1", { ...rec(), ...moved(rec(), [5, 0]) });
    const ops = underlayOps([a, b], [mv, fresh]);
    expect(ops.map((o) => o.op)).toEqual(["create", "modify", "delete"]);
    expect(ops[0]).toMatchObject({ op: "create", layer: "PLAN", geom: { name: "q.pdf", insert: [0, 0] } });
    expect(ops[1]).toMatchObject({ op: "modify", handle: "A1", geom: { insert: [5, 0] } });
    expect(ops[1]).not.toHaveProperty("layer");
    expect(ops[2]).toEqual({ op: "delete", handle: "B2" });
    expect(underlayOps([a], [{ ...a, layer: "PLAN" }])[0]).toMatchObject({ op: "modify", layer: "PLAN" });
    expect(Object.keys((ops[0] as { geom: object }).geom)).not.toContain("linked"); // only the wire fields
  });
  it("an unlinked underlay is never written, only deleted", () => {
    const un = ent("U9", rec({ linked: false, sha256: null as never }));
    expect(underlayOps([un], [{ ...un, underlay: { ...un.underlay!, scale: 2 } }])).toEqual([]);
    expect(underlayOps([un], [])).toEqual([{ op: "delete", handle: "U9" }]);
    expect(underlayOps([], [ent("new-1", rec({ linked: false }))])).toEqual([]);
  });
});
