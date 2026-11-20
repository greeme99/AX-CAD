// Pure-TS helpers for the 2D Draft -> 3D Model handoff path, query params, and profile auto-selection.
import type { RenderEntity } from "@/components/cad/CanvasViewport";

export type ModelAction = "extrude" | "revolve";

export const PROFILE_GEOM_TYPES = new Set(["LINE", "ARC", "CIRCLE", "LWPOLYLINE"]);

/** Build the /model/<docId> URL carrying the source 2D revision, desired 3D action, and selected handles. */
export function buildSendTo3dModelUrl(
  docId: string,
  revisionId: string | number,
  opts?: { action?: ModelAction; handles?: string[] },
): string {
  const q = new URLSearchParams();
  q.set("from_rev", String(revisionId));
  q.set("action", opts?.action ?? "extrude");
  const cleanHandles = (opts?.handles ?? []).filter((h) => h && !h.startsWith("new-")).slice(0, 200);
  if (cleanHandles.length > 0) q.set("handles", cleanHandles.join(","));
  return `/model/${encodeURIComponent(docId)}?${q.toString()}`;
}

/** Parse 2D->3D handoff query parameters on the 3D Model screen. */
export function parseFrom2dParams(sp: Pick<URLSearchParams, "get">): {
  fromRev: string | null;
  action: ModelAction | null;
  handles: string[];
} {
  const fromRev = sp.get("from_rev")?.trim() || null;
  const rawAction = sp.get("action")?.toLowerCase();
  const action: ModelAction | null = rawAction === "extrude" || rawAction === "revolve" ? rawAction : null;
  const rawHandles = sp.get("handles") ?? "";
  const handles = rawHandles
    .split(",")
    .map((s) => s.trim())
    .filter((s) => /^[A-Za-z0-9_-]{1,64}$/.test(s))
    .slice(0, 200);
  return { fromRev, action, handles };
}

/** Select all pickable 2D profile entities (preferring closed circles/polylines; falling back to all visible profile lines/arcs). */
export function pickAllClosedProfiles(entities: RenderEntity[], hiddenLayers: Set<string>): string[] {
  const visible = entities.filter((e) => !!e.geom && PROFILE_GEOM_TYPES.has(e.geom.type) && !hiddenLayers.has(e.layer));
  const closed = visible.filter(
    (e) => e.geom?.type === "CIRCLE" || (e.geom?.type === "LWPOLYLINE" && e.geom.closed),
  );
  const chosen = closed.length > 0 ? closed : visible;
  return chosen.map((e) => e.handle);
}
