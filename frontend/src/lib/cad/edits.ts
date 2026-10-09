import type { Geom } from "./geom";

export type EditEntity = { handle: string; layer: string; geom?: Geom };
export type EditBody = {
  created: { layer: string; geom: Geom }[];
  modified: { handle: string; layer: string; geom: Geom }[];
  deleted: string[];
};

export const isEmptyEdit = (b: EditBody) => !b.created.length && !b.modified.length && !b.deleted.length;

/** Edit request body for POST /api/revisions/{id}/edits. Read-only entities (no geom) are never included. */
export function diffEdits(base: EditEntity[], current: EditEntity[]): EditBody {
  const cur = new Map(current.map((e) => [e.handle, e]));
  const body: EditBody = { created: [], modified: [], deleted: [] };
  for (const e of current) if (e.handle.startsWith("new-") && e.geom) body.created.push({ layer: e.layer, geom: e.geom });
  for (const b of base) {
    if (!b.geom) continue;
    const c = cur.get(b.handle);
    if (!c) body.deleted.push(b.handle);
    // ponytail: JSON compare (key-order sensitive), fine while geoms are only built by our own code
    else if (c.geom && (c.layer !== b.layer || JSON.stringify(c.geom) !== JSON.stringify(b.geom)))
      body.modified.push({ handle: b.handle, layer: c.layer, geom: c.geom });
  }
  return body;
}
