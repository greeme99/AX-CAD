import type { Geom } from "./geom";

// aci: ACI colour (256 = BYLAYER, 0 = BYBLOCK); linetype: name or BYLAYER (server payload)
export type EditEntity = { handle: string; layer: string; geom?: Geom; aci?: number; linetype?: string };
type Props = { color?: number; linetype?: string };

/** Standard ACI colours offered by the editors: [aci, label, hex (null = white/black by theme)]. */
export const ACI_COLORS: [number, string, string | null][] = [
  [1, "빨강", "#ff0000"], [2, "노랑", "#ffff00"], [3, "초록", "#00ff00"], [4, "하늘", "#00ffff"], [5, "파랑", "#0000ff"],
  [6, "자홍", "#ff00ff"], [7, "흰색/검정", null], [8, "회색", "#808080"], [9, "밝은 회색", "#c0c0c0"],
];
export const BYLAYER = 256;
/** Layer change, applied by the server after the entity edits (name = the layer's name at that point). */
export type LayerOp = { name: string; create?: boolean; rename?: string; color?: number; linetype?: string; locked?: boolean };
/** A new block from entities (world mm); base = its insertion point. */
export type BlockDef = { name: string; base: [number, number]; entities: ({ layer: string; geom: Geom } & Props)[] };
export type EditBody = {
  created: ({ layer: string; geom: Geom } & Props)[];
  modified: ({ handle: string; layer: string; geom: Geom } & Props)[];
  deleted: string[];
  layers?: LayerOp[];
  blocks?: BlockDef[];
};

const same = (a: EditEntity, b: EditEntity) =>
  a.layer === b.layer && a.aci === b.aci && a.linetype === b.linetype && JSON.stringify(a.geom) === JSON.stringify(b.geom);

/** Same drawing content: entity order, handles, layers, colours, linetypes and geometry (render-only fields ignored). */
export const sameEntities = (a: EditEntity[], b: EditEntity[]) =>
  a.length === b.length && a.every((e, i) => e === b[i] || (e.handle === b[i].handle && same(e, b[i])));

export const isEmptyEdit = (b: EditBody) => !b.created.length && !b.modified.length && !b.deleted.length;

/** Colour/linetype that differ from the base (all set ones for new entities). */
function props(e: EditEntity, base?: EditEntity): Props {
  const p: Props = {};
  if (e.aci !== undefined && e.aci !== base?.aci) p.color = e.aci;
  if (e.linetype !== undefined && e.linetype !== base?.linetype) p.linetype = e.linetype;
  return p;
}

/** Edit request body for POST /api/revisions/{id}/edits. Read-only entities (no geom) are never included. */
export function diffEdits(base: EditEntity[], current: EditEntity[]): EditBody {
  const cur = new Map(current.map((e) => [e.handle, e]));
  const body: EditBody = { created: [], modified: [], deleted: [] };
  for (const e of current) if (e.handle.startsWith("new-") && e.geom) body.created.push({ layer: e.layer, geom: e.geom, ...props(e) });
  for (const b of base) {
    if (!b.geom) continue;
    const c = cur.get(b.handle);
    if (!c) body.deleted.push(b.handle);
    // ponytail: JSON compare (key-order sensitive), fine while geoms are only built by our own code
    else if (c.geom && !same(c, b)) body.modified.push({ handle: b.handle, layer: c.layer, geom: c.geom, ...props(c, b) });
  }
  return body;
}
