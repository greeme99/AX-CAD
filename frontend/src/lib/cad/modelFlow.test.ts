import { describe, expect, it } from "vitest";
import type { RenderEntity } from "@/components/cad/CanvasViewport";
import { buildSendTo3dModelUrl, parseFrom2dParams, pickAllClosedProfiles } from "./modelFlow";

describe("modelFlow (2D -> 3D Model handoff)", () => {
  it("builds and parses /model/<docId> handoff URL with revision, action, and persisted handles", () => {
    const url = buildSendTo3dModelUrl("42", 108, {
      action: "extrude",
      handles: ["H1", "new-3", "H2"],
    });
    expect(url).toBe("/model/42?from_rev=108&action=extrude&handles=H1%2CH2");
    const parsed = parseFrom2dParams(new URLSearchParams(url.split("?")[1]));
    expect(parsed).toEqual({ fromRev: "108", action: "extrude", handles: ["H1", "H2"] });
  });

  it("auto-selects closed circles/polylines on visible layers for quick 3D extrusion", () => {
    const ents: RenderEntity[] = [
      { handle: "A1", type: "LWPOLYLINE", layer: "OUTLINE", color: null, paths: [], geom: { type: "LWPOLYLINE", points: [[0, 0, 0], [10, 0, 0], [10, 10, 0]], closed: true } },
      { handle: "A2", type: "CIRCLE", layer: "HIDDEN", color: null, paths: [], geom: { type: "CIRCLE", center: [5, 5], radius: 2 } },
      { handle: "A3", type: "LINE", layer: "OUTLINE", color: null, paths: [], geom: { type: "LINE", start: [0, 0], end: [5, 0] } },
      { handle: "A4", type: "TEXT", layer: "OUTLINE", color: null, paths: [], geom: { type: "TEXT", insert: [0, 0], height: 3, value: "10", rotation: 0 } },
    ];
    expect(pickAllClosedProfiles(ents, new Set(["HIDDEN"]))).toEqual(["A1"]);
    // When no closed entities exist, falls back to visible lines/arcs
    expect(pickAllClosedProfiles([ents[2], ents[3]], new Set())).toEqual(["A3"]);
  });
});
