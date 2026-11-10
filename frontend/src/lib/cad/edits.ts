import type { Geom } from "./geom";

// aci: ACI colour (256 = BYLAYER, 0 = BYBLOCK); linetype: name or BYLAYER (server payload)
export type EditEntity = { handle: string; layer: string; geom?: Geom; aci?: number; linetype?: string; underlay?: unknown } & EntProps; // underlay: a PDF/DWFx reference (underlay.ts), edited as one record
// W7a (plan §22.8), as the server sends them: lwt lineweight (1/100 mm, -1 BYLAYER), tc true colour 0xRRGGBB,
// tr transparency %, inv hidden, lts linetype scale; absent = default
export type EntProps = { lwt?: number; tc?: number; tr?: number; inv?: boolean; lts?: number };
export const ENT_PROPS = ["lwt", "tc", "tr", "inv", "lts"] as const;
/** The W7a properties an entity has set (to carry them along when it is copied or changed). */
export const entProps = (e: EntProps): EntProps => Object.fromEntries(ENT_PROPS.filter((k) => e[k] !== undefined).map((k) => [k, e[k]]));
type Props = { color?: number; linetype?: string; lineweight?: number; true_color?: number; transparency?: number; ltscale?: number; visible?: boolean };

/** Standard ACI colours offered by the editors: [aci, label, hex (null = white/black by theme)]. */
export const ACI_COLORS: [number, string, string | null][] = [
  [1, "빨강", "#ff0000"], [2, "노랑", "#ffff00"], [3, "초록", "#00ff00"], [4, "하늘", "#00ffff"], [5, "파랑", "#0000ff"],
  [6, "자홍", "#ff00ff"], [7, "흰색/검정", null], [8, "회색", "#808080"], [9, "밝은 회색", "#c0c0c0"],
];
export const BYLAYER = 256;

/** Dash pattern (mm) an entity is drawn with, for previews before saving: its own linetype, or its
 * layer's for BYLAYER, from the drawing's catalog (names match case-insensitively); lts = the
 * entity's own linetype scale. Undefined = solid. */
export function dashFor(
  catalog: Record<string, number[] | null> | undefined,
  layers: { name: string; linetype?: string }[],
  linetype: string | undefined,
  layer: string,
  lts = 1,
): number[] | undefined {
  const name = !linetype || /^bylayer$/i.test(linetype) ? layers.find((l) => l.name === layer)?.linetype : linetype;
  if (!name || !catalog) return undefined;
  const key = Object.keys(catalog).find((k) => k.toUpperCase() === name.toUpperCase());
  const dash = key ? catalog[key] : null;
  return dash ? dash.map((v) => v * lts) : undefined;
}
/** Layer change, applied by the server after the entity edits (name = the layer's name at that point). */
export type LayerOp = { name: string; create?: boolean; rename?: string; color?: number; linetype?: string; locked?: boolean; lineweight?: number; true_color?: number; plot?: boolean; off?: boolean; frozen?: boolean; description?: string }; // W7b: the rest of the layer record
/** A new block from entities (world mm); base = its insertion point. */
export type BlockDef = { name: string; base: [number, number]; entities: ({ layer: string; geom: Geom } & Props)[]; description?: string };
export type EditBody = {
  created: ({ layer: string; geom: Geom } & Props)[];
  modified: ({ handle: string; layer: string; geom: Geom } & Props)[];
  deleted: string[];
  layers?: LayerOp[];
  blocks?: BlockDef[];
};

const same = (a: EditEntity, b: EditEntity) =>
  a.layer === b.layer && a.aci === b.aci && a.linetype === b.linetype && ENT_PROPS.every((k) => a[k] === b[k]) && JSON.stringify(a.geom) === JSON.stringify(b.geom) && JSON.stringify(a.underlay) === JSON.stringify(b.underlay);

/** Same drawing content: entity order, handles, layers, colours, linetypes and geometry (render-only fields ignored). */
export const sameEntities = (a: EditEntity[], b: EditEntity[]) =>
  a.length === b.length && a.every((e, i) => e === b[i] || (e.handle === b[i].handle && same(e, b[i])));

export const isEmptyEdit = (b: EditBody) => !b.created.length && !b.modified.length && !b.deleted.length;

/** Colour/linetype that differ from the base (all set ones for new entities). */
function props(e: EditEntity, base?: EditEntity): Props {
  const p: Props = {};
  if (e.aci !== undefined && e.aci !== base?.aci) p.color = e.aci;
  if (e.linetype !== undefined && e.linetype !== base?.linetype) p.linetype = e.linetype;
  // W7a: a property cleared back to its default is sent as the default value
  if (e.lwt !== base?.lwt) p.lineweight = e.lwt ?? -1;
  if (e.tc !== base?.tc && e.tc !== undefined) p.true_color = e.tc;
  if (e.tr !== base?.tr) p.transparency = e.tr ?? 0;
  if (e.lts !== base?.lts) p.ltscale = e.lts ?? 1;
  if (e.inv !== base?.inv) p.visible = !e.inv;
  if (!base) for (const k of ["lineweight", "transparency", "ltscale", "visible"] as const) if (p[k] === ({ lineweight: -1, transparency: 0, ltscale: 1, visible: true } as const)[k]) delete p[k];
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
