// 2D underlay U3: what the editor's underlay entities changed since the revision was opened, as
// the server's edit items (create / modify / delete). Only linked underlays can be written (the
// server needs the file hash); one that names another program's path can be deleted, not edited.
import { sameGeom, toWire, type UnderlayGeom, type UnderlayRec } from "./underlay";

export type UEnt = { handle: string; type: string; layer: string; underlay?: UnderlayRec };
export type UnderlayOp =
  | { op: "create"; layer: string; geom: UnderlayGeom }
  | { op: "modify"; handle: string; layer?: string; geom: UnderlayGeom }
  | { op: "delete"; handle: string };

const isU = (e: UEnt): e is UEnt & { underlay: UnderlayRec } => e.type === "UNDERLAY" && !!e.underlay;

export function underlayOps(base: UEnt[], cur: UEnt[]): UnderlayOp[] {
  const ops: UnderlayOp[] = [];
  const now = new Map(cur.filter(isU).map((e) => [e.handle, e]));
  for (const e of now.values()) if (e.handle.startsWith("new-") && e.underlay.linked) ops.push({ op: "create", layer: e.layer, geom: toWire(e.underlay) });
  for (const b of base.filter(isU)) {
    const c = now.get(b.handle);
    if (!c) ops.push({ op: "delete", handle: b.handle });
    else if (c.underlay.linked && (c.layer !== b.layer || !sameGeom(c.underlay, b.underlay))) ops.push({ op: "modify", handle: b.handle, ...(c.layer !== b.layer ? { layer: c.layer } : {}), geom: toWire(c.underlay) });
  }
  return ops;
}
