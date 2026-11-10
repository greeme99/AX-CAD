// 2D automation A1: the drawing as objects, shared by AX-LISP (entmake/entmod/vla-) and the VBA
// macros (A2). A run works on a copy of the editable entities; at the end changes() gives what to
// create, update and delete, and the screen applies it like a command (one undo step).
// Names follow the AutoCAD ActiveX object model (AddLine, StartPoint, Layer, ...) so existing
// routines carry over; there is no COM, no file or network access here.
import { mirrorGeom, rotateGeom, scaleGeom } from "./edit";
import { translate, type Geom } from "./geom";
import { measureGeom } from "./measure";
import { ENT_PROPS, type EntProps } from "./edits";
import type { Pt } from "./view";
import { axCall, axGet, axPut, NOT_HERE, type ByRef } from "./axobj";

/** xdict: it has an extension dictionary; att: its attribute values by tag (W4, from the drawing). */
export type Ent = { handle: string; layer: string; geom: Geom; aci?: number; linetype?: string; xdict?: boolean; xdq?: boolean; att?: Record<string, string>; xdtree?: DictTree } & EntProps;
/** W6: a dictionary as the drawing sends it (render payload `dicts`, entity `xdtree`). */
export type DictNode = { type: string; handle: string; entries?: DictTree; tags?: [number, unknown][] };
export type DictTree = Record<string, DictNode>;
/** A dictionary entry as it is now: the drawing's, with the not yet saved changes over it. */
export type DictView = { type: string; handle: string; entries: string[]; tags: [number, unknown][] };
/** W4: handles of the drawing's tables, records and spaces (render payload object_ids). */
export type ObjectIds = {
  model_space: string;
  nod: string;
  tables: Partial<Record<"layer" | TableKind, string>>;
  records: Partial<Record<"layer" | TableKind, Record<string, string>>>;
  blocks: Record<string, string>;
  layouts: Record<string, string>;
  groups: Record<string, string>;
};
export type Layer = { name: string; color?: number | null; locked?: boolean; linetype?: string; lwt?: number; plot?: boolean; off?: boolean; frozen?: boolean; description?: string };
// W7b (plan §22.8): symbol tables as the server sends them (render payload `tables`, lengths in mm)
export type TableKind = "linetype" | "textstyle" | "dimstyle" | "regapp";
export type TableRec = { name: string; description?: string; pattern?: number[]; font?: string; height?: number; width?: number; oblique?: number; dim?: Record<string, number | string> };
export type Tables = { linetypes: TableRec[]; textstyles: TableRec[]; dimstyles: TableRec[]; regapps: string[]; layouts: string[] };
export type TableOp = { kind: TableKind; op: "create" | "modify" | "rename" | "delete"; name: string; new_name?: string } & Omit<TableRec, "name">;
/** A change to an existing layer's record (LayerOp of the edit API, without create/rename). */
export type LayerEdit = { name: string; color?: number; locked?: boolean; linetype?: string; lineweight?: number; plot?: boolean; off?: boolean; frozen?: boolean; description?: string };
/** W2: an XRecord set or an entry deleted in the named object dictionary (or an entity's extension
 * dictionary, `handle`), `path` down from the root — the edit API's `dictionaries`. */
export type DictOp = { path: string[]; op: "set" | "delete"; handle?: string; xrecord?: [number, unknown][] };
export type Changes = { created: Ent[]; updated: Ent[]; deleted: string[]; layers: string[]; tables: TableOp[]; layerOps: LayerEdit[]; dictionaries?: DictOp[] };
export const NO_TABLES: Tables = { linetypes: [], textstyles: [], dimstyles: [], regapps: [], layouts: [] };
/** The tables as they will be once these not yet saved operations are applied (the next run starts from them). */
export function withTableOps(t: Tables, ops: TableOp[]): Tables {
  const out: Tables = { linetypes: t.linetypes.map((r) => ({ ...r })), textstyles: t.textstyles.map((r) => ({ ...r })), dimstyles: t.dimstyles.map((r) => ({ ...r })), regapps: [...t.regapps], layouts: [...t.layouts] };
  for (const o of ops) {
    if (o.kind === "regapp") {
      if (o.op === "create" && !out.regapps.includes(o.name)) out.regapps.push(o.name);
      if (o.op === "delete") out.regapps = out.regapps.filter((n) => n !== o.name);
      continue;
    }
    const list = out[TABLE_KEY[o.kind]];
    const i = list.findIndex((r) => r.name.toUpperCase() === o.name.toUpperCase());
    const { name, new_name } = o;
    const fields: Omit<TableRec, "name"> = clean({ description: o.description, pattern: o.pattern, font: o.font, height: o.height, width: o.width, oblique: o.oblique, dim: o.dim });
    if (o.op === "create" && i < 0) list.push({ name, ...fields });
    else if (o.op === "modify" && i >= 0) Object.assign(list[i], fields);
    else if (o.op === "rename" && i >= 0 && new_name) list[i].name = new_name;
    else if (o.op === "delete" && i >= 0) list.splice(i, 1);
  }
  return out;
}
const TABLE_KEY = { linetype: "linetypes", textstyle: "textstyles", dimstyle: "dimstyles" } as const;
// records AutoCAD itself needs: never renamed or deleted (the server refuses them too)
const PROTECTED: Record<TableKind, string[]> = { linetype: ["BYLAYER", "BYBLOCK", "CONTINUOUS"], textstyle: ["STANDARD"], dimstyle: ["STANDARD"], regapp: ["ACAD"] };
const SYM_NAME = /^[^\x00-\x1f\x7f<>/\\":;?*|=`]{1,255}$/;

// the server takes 10,000 entity edits and 1,000 layer operations per save (backend MAX_OPS)
export const MODEL_LIMITS = { created: 10_000, coord: 1e9, layers: 100 };

export class ModelError extends Error {}

const finite = (v: number, what: string) => {
  if (!Number.isFinite(v) || Math.abs(v) > MODEL_LIMITS.coord) throw new ModelError(`${what}: 좌표·값이 범위를 벗어났습니다`);
  return v;
};

/** Checks numbers in a geometry (NaN, Infinity or absurd sizes never reach the drawing). */
function checkGeom(g: Geom): Geom {
  const walk = (v: unknown): void => {
    if (typeof v === "number") finite(v, g.type);
    else if (Array.isArray(v)) v.forEach(walk);
  };
  Object.values(g).forEach(walk);
  if ((g.type === "CIRCLE" || g.type === "ARC") && !(g.radius > 0)) throw new ModelError(`${g.type}: 반지름은 0보다 커야 합니다`);
  if ((g.type === "TEXT" || g.type === "MTEXT") && !(g.height > 0)) throw new ModelError(`${g.type}: 높이는 0보다 커야 합니다`);
  if (g.type === "MTEXT" && (!(g.width >= 0) || !(g.attachment >= 1 && g.attachment <= 9 && Number.isInteger(g.attachment)))) throw new ModelError("MTEXT: 폭은 0 이상, 부착점은 1~9입니다");
  if (g.type === "LWPOLYLINE" && g.points.length < 2) throw new ModelError("LWPOLYLINE: 점이 2개 이상 필요합니다");
  return g;
}

export class DrawingModel {
  private ents = new Map<string, Ent>();
  private base = new Map<string, Ent>();
  private made = 0;
  private newLayers: string[] = [];
  readonly layers: Layer[];
  readonly tables: Tables;
  private tableOps: TableOp[] = [];
  private layerEdits = new Map<string, LayerEdit>();
  private dictOps: DictOp[] = [];
  /** W2: dictionary entries this run (and earlier unsaved runs) set or deleted, by owner + path. */
  private dictState = new Map<string, { deleted?: boolean; tags?: [number, unknown][]; dict?: boolean }>();
  current: string;
  /** W4: handles for Handle / ObjectID / OwnerID (absent: those read as "" and 0). */
  ids?: ObjectIds;
  /** W6: the named object dictionary's user entries (undefined: not sent, null: too many). */
  dicts?: DictTree | null;

  constructor(entities: Ent[], layers: Layer[], current: string, tables: Tables = NO_TABLES) {
    for (const e of entities) {
      this.ents.set(e.handle, e);
      this.base.set(e.handle, e);
    }
    this.layers = layers.map((l) => ({ ...l }));
    this.current = current;
    this.tables = { linetypes: tables.linetypes.map((r) => ({ ...r })), textstyles: tables.textstyles.map((r) => ({ ...r })), dimstyles: tables.dimstyles.map((r) => ({ ...r })), regapps: [...tables.regapps], layouts: [...tables.layouts] };
  }

  // --- W7b: symbol tables and layer records ------------------------------------------------
  /** The records of a table, as objects (regapps: name only). */
  table(kind: TableKind): TableRec[] {
    return kind === "regapp" ? this.tables.regapps.map((name) => ({ name })) : this.tables[TABLE_KEY[kind]];
  }
  tableFind(kind: TableKind, name: string): TableRec | undefined {
    return this.table(kind).find((r) => r.name.toUpperCase() === name.toUpperCase());
  }
  tableAdd(kind: TableKind, name: string, fields: Omit<TableRec, "name"> = {}): TableRec {
    if (!SYM_NAME.test(name)) throw new ModelError(`이름으로 쓸 수 없습니다: ${name}`);
    if (this.tableFind(kind, name)) throw new ModelError(`이미 있습니다: ${name}`);
    if (this.tableOps.length >= MODEL_LIMITS.layers * 10) throw new ModelError("한 번에 바꿀 수 있는 표 항목이 너무 많습니다");
    const rec: TableRec = { name, ...clean(fields) };
    if (kind === "regapp") this.tables.regapps.push(name);
    else this.tables[TABLE_KEY[kind]].push(rec);
    this.tableOps.push({ kind, op: "create", name, ...clean(fields) });
    return rec;
  }
  tableSet(kind: TableKind, name: string, fields: Omit<TableRec, "name">): void {
    const rec = this.tableFind(kind, name);
    if (!rec || kind === "regapp") throw new ModelError(`없는 항목: ${name}`);
    Object.assign(rec, clean(fields));
    const mine = this.tableOps.find((o) => o.kind === kind && o.name === rec.name && (o.op === "create" || o.op === "modify"));
    if (mine) Object.assign(mine, clean(fields));
    else this.tableOps.push({ kind, op: "modify", name: rec.name, ...clean(fields) });
  }
  tableRename(kind: TableKind, name: string, next: string): void {
    const rec = this.tableFind(kind, name);
    if (!rec || kind === "regapp") throw new ModelError(`없는 항목: ${name}`);
    if (PROTECTED[kind].includes(rec.name.toUpperCase())) throw new ModelError(`${rec.name}의 이름은 바꿀 수 없습니다`);
    if (!SYM_NAME.test(next) || this.tableFind(kind, next)) throw new ModelError(`이름으로 쓸 수 없거나 이미 있습니다: ${next}`);
    this.tableOps.push({ kind, op: "rename", name: rec.name, new_name: next });
    rec.name = next;
  }
  tableRemove(kind: TableKind, name: string): void {
    const rec = this.tableFind(kind, name);
    if (!rec) throw new ModelError(`없는 항목: ${name}`);
    if (PROTECTED[kind].includes(rec.name.toUpperCase())) throw new ModelError(`${rec.name}은 지울 수 없습니다`);
    if (kind === "regapp") this.tables.regapps.splice(this.tables.regapps.indexOf(rec.name), 1);
    else this.tables[TABLE_KEY[kind]].splice(this.tables[TABLE_KEY[kind]].indexOf(rec), 1);
    this.tableOps.push({ kind, op: "delete", name: rec.name }); // the server refuses it while in use
  }
  /** Changes a layer's record (also a locked layer: the lock is one of its properties). */
  layerSet(name: string, patch: Omit<LayerEdit, "name">): void {
    const l = this.layer(name);
    if (!l) throw new ModelError(`없는 레이어: ${name}`);
    Object.assign(l, clean({ color: patch.color, locked: patch.locked, linetype: patch.linetype, lwt: patch.lineweight, plot: patch.plot, off: patch.off, frozen: patch.frozen, description: patch.description }));
    if (this.newLayers.includes(l.name)) return; // created in this run: saved with its create op below
    this.layerEdits.set(l.name, { ...(this.layerEdits.get(l.name) ?? { name: l.name }), ...clean(patch) });
  }

  // --- W2: dictionaries and XRecords (saved through the edit API's `dictionaries`) -------------
  static dictKey = (owner: string | undefined, path: string[]) => `${owner ?? ""}|${path.join("\u0000")}`;
  /** Not yet saved state of one entry: set (tags), deleted, or a dictionary made here. */
  dictLocal(owner: string | undefined, path: string[]) {
    for (let i = 1; i < path.length; i++) if (this.dictState.get(DrawingModel.dictKey(owner, path.slice(0, i)))?.deleted) return { deleted: true };
    return this.dictState.get(DrawingModel.dictKey(owner, path));
  }
  /** Names of the not yet saved entries directly under `path` (deleted ones with deleted: true). */
  dictChildren(owner: string | undefined, path: string[]): { name: string; deleted: boolean }[] {
    const prefix = DrawingModel.dictKey(owner, path);
    const out = new Map<string, boolean>();
    for (const [k, v] of this.dictState) {
      if (!k.startsWith(prefix) || k === prefix) continue;
      const rest = k.slice(prefix.length).replace(/^\u0000/, "");
      if (path.length && !k.startsWith(`${prefix}\u0000`)) continue;
      const [name, ...deeper] = rest.split("\u0000");
      if (!name) continue;
      const del = !deeper.length && !!v.deleted;
      out.set(name, (out.get(name) ?? true) && del);
    }
    return [...out].map(([name, deleted]) => ({ name, deleted }));
  }
  /** Room for n more dictionary operations (a rename is two: never half done). */
  dictRoom(n: number): void {
    if (this.dictOps.length + n > 1000) throw new ModelError("한 번에 바꿀 수 있는 사전 항목은 1,000개까지입니다");
  }
  /** Not yet saved operations of earlier runs: seen by this run's reads, not sent again. */
  seedDicts(ops: DictOp[]): void {
    for (const o of ops) {
      if (o.op === "set") {
        this.dictState.set(DrawingModel.dictKey(o.handle, o.path), { tags: o.xrecord ?? [] });
        for (let i = 1; i < o.path.length; i++) this.dictState.set(DrawingModel.dictKey(o.handle, o.path.slice(0, i)), { dict: true });
      } else this.dictState.set(DrawingModel.dictKey(o.handle, o.path), { deleted: true });
    }
  }
  /** W6: a dictionary entry without asking the server — undefined when the drawing did not send
   * that dictionary (too big): then it must be asked for. */
  dictView(owner: string | undefined, path: string[]): DictView | null | undefined {
    const local = this.dictLocal(owner, path);
    if (local?.deleted) return null;
    const key = `~${DrawingModel.dictKey(owner, path)}`;
    if (local?.tags) return { type: "XRECORD", handle: key, entries: [], tags: local.tags };
    let tree: DictTree | null | undefined;
    if (owner === undefined) tree = this.dicts;
    else {
      const e = this.ents.get(owner) ?? this.base.get(owner);
      tree = e?.xdtree ?? (e && !e.xdict ? {} : undefined);
    }
    if (tree === undefined || tree === null) return undefined;
    let node: DictNode | undefined = { type: "DICTIONARY", handle: owner ? "" : (this.ids?.nod ?? ""), entries: tree };
    for (const k of path) node = node?.entries && Object.hasOwn(node.entries, k) ? node.entries[k] : node?.entries ? Object.entries(node.entries).find(([n]) => n.toUpperCase() === k.toUpperCase())?.[1] : undefined;
    const kids = this.dictChildren(owner, path);
    if (!node && !local?.dict && !kids.some((k) => !k.deleted)) return null;
    if (node && node.type !== "DICTIONARY") return { type: node.type, handle: node.handle, entries: [], tags: node.tags ?? [] };
    const names = new Set(Object.keys(node?.entries ?? {}));
    for (const k of kids) {
      if (k.deleted) names.delete(k.name);
      else names.add(k.name);
    }
    return { type: "DICTIONARY", handle: node?.handle || key, entries: [...names], tags: [] };
  }
  dictSet(owner: string | undefined, path: string[], tags: [number, unknown][]): void {
    checkDictPath(owner, path);
    this.dictRoom(1);
    checkXrecord(tags);
    this.dictState.set(DrawingModel.dictKey(owner, path), { tags });
    for (let i = 1; i < path.length; i++) this.dictState.set(DrawingModel.dictKey(owner, path.slice(0, i)), { dict: true });
    this.dictOps.push({ path, op: "set", ...(owner ? { handle: owner } : {}), xrecord: tags });
  }
  /** A new, empty dictionary: kept here; the server makes it when an XRecord is set inside. */
  dictMake(owner: string | undefined, path: string[]): void {
    checkDictPath(owner, path);
    this.dictState.set(DrawingModel.dictKey(owner, path), { dict: true });
  }
  dictDelete(owner: string | undefined, path: string[]): void {
    checkDictPath(owner, path);
    this.dictRoom(1);
    const prefix = DrawingModel.dictKey(owner, path);
    for (const k of [...this.dictState.keys()]) if (k.startsWith(`${prefix}\u0000`)) this.dictState.delete(k);
    this.dictState.set(prefix, { deleted: true });
    this.dictOps.push({ path, op: "delete", ...(owner ? { handle: owner } : {}) });
  }

  all(): Ent[] {
    return [...this.ents.values()];
  }
  get(handle: string): Ent | undefined {
    return this.ents.get(handle);
  }
  /** The last entity in the drawing (entlast): the newest one created in this run, else the last. */
  last(): Ent | undefined {
    return this.all().at(-1);
  }
  layer(name: string): Layer | undefined {
    return this.layers.find((l) => l.name.toUpperCase() === name.toUpperCase());
  }
  private writable(name: string): string {
    const l = this.layer(name);
    if (!l) throw new ModelError(`도면에 없는 레이어: ${name}`);
    if (l.locked) throw new ModelError(`잠긴 레이어: ${l.name}`);
    return l.name;
  }

  add(geom: Geom, layer = this.current, props: { aci?: number; linetype?: string } & EntProps = {}): Ent {
    if (++this.made > MODEL_LIMITS.created) throw new ModelError(`한 번에 만들 수 있는 객체는 ${MODEL_LIMITS.created.toLocaleString()}개까지입니다`);
    const e: Ent = { handle: `~${this.made}`, layer: this.writable(layer), geom: checkGeom(geom), ...clean(props) };
    this.ents.set(e.handle, e);
    return e;
  }

  set(handle: string, patch: Partial<Omit<Ent, "handle">>): Ent {
    const e = this.ents.get(handle);
    if (!e) throw new ModelError(`없는 객체: ${handle}`);
    this.writable(e.layer);
    const next: Ent = { ...e, ...clean(patch), handle };
    // W7a: a property set to undefined goes back to its default (ByLayer, opaque, scale 1, visible)
    for (const k of ENT_PROPS) if (k in patch && patch[k] === undefined) delete next[k];
    if (patch.layer !== undefined) next.layer = this.writable(patch.layer);
    if (patch.geom) next.geom = checkGeom(patch.geom);
    this.ents.set(handle, next);
    return next;
  }

  remove(handle: string): void {
    const e = this.ents.get(handle);
    if (!e) throw new ModelError(`없는 객체: ${handle}`);
    this.writable(e.layer);
    this.ents.delete(handle);
  }

  addLayer(name: string): Layer {
    const have = this.layer(name);
    if (have) return have;
    // the server's rule (no control characters) plus AutoCAD's forbidden characters
    if (!/^[^\x00-\x1f\x7f<>/\\":;?*|=`]{1,255}$/.test(name)) throw new ModelError(`레이어 이름으로 쓸 수 없습니다: ${name}`);
    if (this.newLayers.length >= MODEL_LIMITS.layers) throw new ModelError(`한 번에 만들 수 있는 레이어는 ${MODEL_LIMITS.layers}개까지입니다`);
    const l: Layer = { name, color: 7, locked: false };
    this.layers.push(l);
    this.newLayers.push(name);
    return l;
  }

  /** What the run changed, against the drawing it started from. */
  changes(): Changes {
    const created: Ent[] = [];
    const updated: Ent[] = [];
    for (const e of this.ents.values()) {
      const b = this.base.get(e.handle);
      if (!b) created.push(e);
      else if (b !== e && JSON.stringify(b) !== JSON.stringify(e)) updated.push(e);
    }
    const deleted = [...this.base.keys()].filter((h) => !this.ents.has(h));
    // a layer made in this run is created with what it was given since (the edit API's LayerOp)
    const made = this.newLayers.map((n) => this.layer(n)!).filter((l) => l.color !== 7 || l.locked || l.linetype || l.lwt !== undefined || l.plot === false || l.off || l.frozen || l.description);
    const madeOps: LayerEdit[] = made.map((l) => clean({ name: l.name, color: l.color === 7 ? undefined : (l.color ?? undefined), locked: l.locked || undefined, linetype: l.linetype, lineweight: l.lwt, plot: l.plot, off: l.off, frozen: l.frozen, description: l.description }));
    return { created, updated, deleted, layers: [...this.newLayers], tables: [...this.tableOps], layerOps: [...madeOps, ...this.layerEdits.values()], ...(this.dictOps.length ? { dictionaries: [...this.dictOps] } : {}) };
  }
}

/** The server's rules (core/dxf/objects.py): no system dictionary, depth 8, a saved entity only. */
function checkDictPath(owner: string | undefined, path: string[]): void {
  if (!path.length || path.length > 8) throw new ModelError("사전 경로는 1~8단계입니다");
  if (/^(ACAD|ACDB|EZDXF)/i.test(path[0])) throw new ModelError(`시스템 사전은 바꿀 수 없습니다: ${path[0]}`);
  if (path.some((k) => !/^[^\x00-\x1f\x7f-\x9f\u2028\u2029]{1,255}$/.test(k))) throw new ModelError("사전 키로 쓸 수 없는 이름입니다");
  if (owner !== undefined && !/^[0-9A-F]{1,16}$/i.test(owner)) throw new ModelError("저장하기 전의 객체에는 확장 사전을 둘 수 없습니다 (먼저 저장하세요)");
}
export const utf8 = (t: string) => new TextEncoder().encode(t).length;
const BAD_TEXT = /[\x00-\x1f\x7f-\x9f\u2028\u2029]/;
/** The server's XRecord rules (core/dxf/objects.py check_tags), so a save is not refused later:
 * codes, value kinds, integer ranges, text without control characters, 10,000 groups, 64 KB. */
export function checkXrecord(tags: [number, unknown][]): void {
  if (tags.length > 10_000) throw new ModelError("XRecord는 그룹 10,000개까지입니다");
  let size = 0;
  for (const [c, v] of tags) {
    if (!xrecordCode(c)) throw new ModelError(`XRecord에 쓸 수 없는 그룹 코드: ${c}`);
    const isPoint = (c >= 10 && c <= 18) || (c >= 1010 && c <= 1013);
    const isReal = (c >= 38 && c <= 59) || (c >= 140 && c <= 149) || (c >= 1040 && c <= 1042);
    const isText = (c >= 1 && c <= 9) || (c >= 300 && c <= 309) || c === 1000;
    const range: [number, number] | null = c >= 290 && c <= 299 ? [0, 1] : c >= 280 && c <= 289 ? [-128, 127] : (c >= 60 && c <= 79) || (c >= 170 && c <= 179) || c === 1070 ? [-32768, 32767] : (c >= 90 && c <= 99) || c === 1071 ? [-2147483648, 2147483647] : null;
    let ok: boolean;
    if (isPoint) ok = Array.isArray(v) && (v.length === 2 || v.length === 3) && v.every((x) => typeof x === "number" && Number.isFinite(x));
    else if (isReal) ok = typeof v === "number" && Number.isFinite(v);
    else if (range) ok = typeof v === "number" && Number.isInteger(v) && v >= range[0] && v <= range[1];
    else if (isText) ok = typeof v === "string" && v.length <= 2048 && !BAD_TEXT.test(v);
    else ok = typeof v === "string" && /^[0-9A-Fa-f]+$/.test(v); // hex / handle codes
    if (!ok) throw new ModelError(`XRecord ${c}: 값이 맞지 않습니다`);
    size += typeof v === "string" ? utf8(v) + 3 : isPoint ? 26 : 10;
  }
  if (size > 65_536) throw new ModelError("XRecord는 64 KB까지입니다");
}
/** XRecord group codes the server takes (data codes; no owner or pointer handles). */
export function xrecordCode(c: number): boolean {
  return [[1, 4], [6, 9], [10, 18], [38, 59], [60, 79], [90, 99], [140, 149], [170, 179], [280, 289], [290, 299], [300, 309], [310, 319], [320, 329], [1000, 1000], [1002, 1005], [1010, 1013], [1040, 1042], [1070, 1071]].some(([lo, hi]) => c >= lo && c <= hi);
}

const clean = <X extends object>(o: X): X => Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined)) as X;

// --- DXF group codes (entget / entmake / entmod) --------------------------------------------

export type Group = [number, unknown]; // (code . value); points are [x, y] or [x, y, z]

const P = (v: unknown, what: string): Pt => {
  if (!Array.isArray(v) || v.length < 2 || typeof v[0] !== "number" || typeof v[1] !== "number") throw new ModelError(`${what}: 점 (x y)이 필요합니다`);
  return [finite(v[0], what), finite(v[1], what)];
};
const N = (v: unknown, what: string): number => {
  if (typeof v !== "number") throw new ModelError(`${what}: 숫자가 필요합니다`);
  return finite(v, what);
};
const S = (v: unknown, what: string): string => {
  if (typeof v !== "string") throw new ModelError(`${what}: 문자열이 필요합니다`);
  return v;
};
const deg = (rad: number) => (rad * 180) / Math.PI;
const rad = (d: number) => (d * Math.PI) / 180;

/** An entity as AutoLISP sees it: (0 type) (5 handle) (8 layer) (62 color) and its geometry. */
export function toGroups(e: Ent): Group[] {
  const g = e.geom;
  const out: Group[] = [[0, g.type === "LWPOLYLINE" ? "LWPOLYLINE" : g.type], [5, e.handle], [8, e.layer]];
  if (e.aci !== undefined) out.push([62, e.aci]);
  if (e.linetype) out.push([6, e.linetype]);
  // W7a: lineweight, true colour, transparency (DXF 440 = 0x02000000 | alpha), linetype scale, invisible
  if (e.lwt !== undefined) out.push([370, e.lwt]);
  if (e.tc !== undefined) out.push([420, e.tc]);
  if (e.tr !== undefined) out.push([440, 0x02000000 | Math.round(255 * (1 - e.tr / 100))]);
  if (e.lts !== undefined) out.push([48, e.lts]);
  if (e.inv) out.push([60, 1]);
  const p3 = (p: Pt): [number, number, number] => [p[0], p[1], 0];
  switch (g.type) {
    case "LINE":
      out.push([10, p3(g.start)], [11, p3(g.end)]);
      break;
    case "CIRCLE":
      out.push([10, p3(g.center)], [40, g.radius]);
      break;
    case "ARC":
      out.push([10, p3(g.center)], [40, g.radius], [50, rad(g.start_angle)], [51, rad(g.end_angle)]);
      break;
    case "LWPOLYLINE":
      out.push([90, g.points.length], [70, g.closed ? 1 : 0]);
      for (const [x, y, b] of g.points) {
        out.push([10, [x, y]]);
        if (b) out.push([42, b]);
      }
      break;
    case "TEXT":
      out.push([10, p3(g.insert)], [40, g.height], [1, g.value], [50, rad(g.rotation)]);
      break;
    case "MTEXT": // 50 is in radians for MTEXT, as in DXF
      out.push([10, p3(g.insert)], [40, g.height], [41, g.width], [71, g.attachment], [1, g.value], [50, rad(g.rotation)]);
      break;
    case "POINT":
      out.push([10, p3(g.point)]);
      break;
    case "ELLIPSE":
      out.push([10, p3(g.center)], [11, p3(g.major)], [40, g.ratio], [41, g.start_param], [42, g.end_param]);
      break;
    case "INSERT":
      out.push([2, g.name], [10, p3(g.insert)], [41, g.xscale], [42, g.yscale], [50, rad(g.rotation)]);
      break;
    default: // dimensions, hatches, splines, leaders: type, handle and layer only
      break;
  }
  return out;
}

const MAKEABLE = ["LINE", "CIRCLE", "ARC", "LWPOLYLINE", "TEXT", "MTEXT", "POINT", "ELLIPSE"];
// W7a: lineweights the server accepts (1/100 mm; -1 BYLAYER, -2 BYBLOCK, -3 default)
export const LINEWEIGHTS = [-3, -2, -1, 0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90, 100, 106, 120, 140, 158, 200, 211];

/** entmake / entmod: groups -> geometry. `old` fills what the list leaves out (entmod). */
export function fromGroups(groups: Group[], old?: Ent): { geom: Geom; layer?: string; aci?: number; linetype?: string; props: EntProps } {
  const first = (code: number) => groups.find(([c]) => c === code)?.[1];
  const type = String(first(0) ?? old?.geom.type ?? "").toUpperCase();
  if (!MAKEABLE.includes(type)) throw new ModelError(`만들거나 고칠 수 없는 객체 종류: ${type || "(0 . 종류) 없음"} — ${MAKEABLE.join("·")}만 됩니다`);
  if (old && old.geom.type !== type) throw new ModelError("entmod: 객체 종류(0)는 바꿀 수 없습니다");
  const o = (old?.geom ?? {}) as Record<string, unknown>;
  const has = (code: number) => first(code) !== undefined;
  const pick = <X>(code: number, conv: (v: unknown) => X, fallback: unknown, what: string): X => {
    if (has(code)) return conv(first(code));
    if (fallback === undefined) throw new ModelError(`${type}: (${code} . ${what})이 필요합니다`);
    return fallback as X;
  };
  const pt = (code: number, key: string) => pick(code, (v) => P(v, `${type} ${code}`), o[key], "점");
  const n = (code: number, key: string, conv = (x: number) => x) => pick(code, (v) => conv(N(v, `${type} ${code}`)), o[key], "숫자");
  let geom: Geom;
  switch (type) {
    case "LINE":
      geom = { type, start: pt(10, "start"), end: pt(11, "end") };
      break;
    case "CIRCLE":
      geom = { type, center: pt(10, "center"), radius: n(40, "radius") };
      break;
    case "ARC":
      geom = { type, center: pt(10, "center"), radius: n(40, "radius"), start_angle: n(50, "start_angle", deg), end_angle: n(51, "end_angle", deg) };
      break;
    case "LWPOLYLINE": {
      const verts: [number, number, number][] = [];
      for (const [c, v] of groups) {
        if (c === 10) verts.push([...P(v, "LWPOLYLINE 10"), 0]);
        else if (c === 42 && verts.length) verts[verts.length - 1][2] = N(v, "LWPOLYLINE 42");
      }
      const flags = has(70) ? N(first(70), "LWPOLYLINE 70") : undefined;
      geom = { type, points: verts.length ? verts : ((o.points as [number, number, number][]) ?? []), closed: flags !== undefined ? (flags & 1) === 1 : Boolean(o.closed) };
      break;
    }
    case "TEXT":
      geom = { type, insert: pt(10, "insert"), height: n(40, "height"), value: pick(1, (v) => S(v, "TEXT 1"), o.value, "문자열"), rotation: has(50) ? deg(N(first(50), "TEXT 50")) : ((o.rotation as number) ?? 0) };
      break;
    case "MTEXT": {
      const width = has(41) ? N(first(41), "MTEXT 41") : ((o.width as number) ?? 0);
      const attachment = has(71) ? N(first(71), "MTEXT 71") : ((o.attachment as number) ?? 1);
      geom = { type, insert: pt(10, "insert"), height: n(40, "height"), width, attachment, value: pick(1, (v) => S(v, "MTEXT 1"), o.value, "문자열"), rotation: has(50) ? deg(N(first(50), "MTEXT 50")) : ((o.rotation as number) ?? 0) };
      break;
    }
    case "POINT":
      geom = { type, point: pt(10, "point") };
      break;
    default: { // ELLIPSE
      const ratio = n(40, "ratio");
      if (!(ratio > 0 && ratio <= 1)) throw new ModelError("ELLIPSE 40: 비율은 0보다 크고 1 이하입니다");
      geom = { type: "ELLIPSE", center: pt(10, "center"), major: pt(11, "major"), ratio, start_param: has(41) ? N(first(41), "ELLIPSE 41") : ((o.start_param as number) ?? 0), end_param: has(42) ? N(first(42), "ELLIPSE 42") : ((o.end_param as number) ?? 2 * Math.PI) };
    }
  }
  const aci = has(62) ? N(first(62), "62") : undefined;
  if (aci !== undefined && !(Number.isInteger(aci) && aci >= 0 && aci <= 256)) throw new ModelError("62: 색 번호는 0~256입니다");
  const props: EntProps = {};
  if (has(370)) {
    const v = N(first(370), "370");
    if (!LINEWEIGHTS.includes(v)) throw new ModelError(`370: 선 두께는 ${LINEWEIGHTS.join(", ")} 중 하나입니다`);
    props.lwt = v;
  }
  if (has(420)) {
    const v = N(first(420), "420");
    if (!(Number.isInteger(v) && v >= 0 && v <= 0xffffff)) throw new ModelError("420: 트루 컬러는 0~16777215입니다");
    props.tc = v;
  }
  if (has(440)) {
    const v = N(first(440), "440");
    if (!(Number.isInteger(v) && v >= 0x02000000 && v <= 0x020000ff)) throw new ModelError("440: 투명도 값이 올바르지 않습니다");
    props.tr = Math.min(90, Math.round(100 * (1 - (v & 0xff) / 255)));
  }
  if (has(48)) {
    const v = N(first(48), "48");
    if (!(v > 0 && v <= 1e6)) throw new ModelError("48: 선종류 축척은 0보다 커야 합니다");
    props.lts = v;
  }
  if (has(60)) props.inv = N(first(60), "60") === 1;
  return { geom: checkGeom(geom), layer: has(8) ? S(first(8), "8") : undefined, aci, linetype: has(6) ? S(first(6), "6") : undefined, props };
}

/** ssget "X" filter: ((0 . "LINE,CIRCLE") (8 . "0") (62 . 1)) — wildcards * ? and comma lists. */
export function matches(e: Ent, filter: Group[]): boolean {
  const groups = toGroups(e);
  return filter.every(([code, want]) => {
    const have = groups.filter(([c]) => c === code).map(([, v]) => v);
    if (typeof want === "string") {
      const pats = want.split(",").map((w) => w.trim().toUpperCase());
      return have.some((v) => typeof v === "string" && pats.some((pt) => glob(pt, v.toUpperCase())));
    }
    return have.some((v) => JSON.stringify(v) === JSON.stringify(want));
  });
}

/** Wildcard match (* any run, ? one character, # one digit) in linear-ish time: no regular expressions, so no
 * catastrophic backtracking on long text values (security review A1). */
export function glob(pat: string, s: string): boolean {
  let p = 0;
  let i = 0;
  let star = -1;
  let mark = 0;
  while (i < s.length) {
    if (p < pat.length && (pat[p] === "?" || pat[p] === s[i] || (pat[p] === "#" && s[i] >= "0" && s[i] <= "9"))) {
      p++;
      i++;
    } else if (p < pat.length && pat[p] === "*") {
      star = p++;
      mark = i;
    } else if (star >= 0) {
      p = star + 1;
      i = ++mark;
    } else return false;
  }
  while (pat[p] === "*") p++;
  return p === pat.length;
}

// --- ActiveX-style object model (vla- / VBA) -------------------------------------------------

export type Obj =
  | { t: "app" }
  | { t: "doc" }
  | { t: "space" } // ModelSpace
  | { t: "layers" }
  | { t: "layer"; name: string }
  | { t: "ent"; handle: string }
  // W7b: Linetypes / TextStyles / DimStyles / RegisteredApplications / Layouts and their items
  | { t: "table"; tk: TableKind | "layout" } // tk, not kind: VBA tags its own values with `kind`
  | { t: "rec"; tk: TableKind; name: string }
  | { t: "layout"; name: string }
  // W4 (axobj.ts): a dictionary / XRecord, a colour value, an entity's hyperlinks, an attribute reference
  | { t: "dict"; owner?: string; path: string[] }
  | { t: "color"; aci?: number; rgb?: number }
  | { t: "hyperlinks"; handle: string }
  | { t: "attref"; handle: string; tag: string }
  | { t: "state" }
  | { t: "hatchdraft"; pattern: string };
const COLLECTION: Record<string, TableKind | "layout"> = { LINETYPES: "linetype", TEXTSTYLES: "textstyle", DIMSTYLES: "dimstyle", REGISTEREDAPPLICATIONS: "regapp", LAYOUTS: "layout" };
const rec = (m: DrawingModel, o: { tk: TableKind; name: string }): TableRec => {
  const r = m.tableFind(o.tk, o.name);
  if (!r) throw new ModelError(`없는 항목: ${o.name}`);
  return r;
};
/** A value crossing the object model: points are number arrays, collections index from 0. */
export type OVal = number | string | boolean | number[] | Obj | null | OVal[];

const OBJECT_NAME: Record<string, string> = {
  LINE: "AcDbLine",
  MTEXT: "AcDbMText",
  CIRCLE: "AcDbCircle",
  ARC: "AcDbArc",
  LWPOLYLINE: "AcDbPolyline",
  TEXT: "AcDbText",
  POINT: "AcDbPoint",
  ELLIPSE: "AcDbEllipse",
  SPLINE: "AcDbSpline",
  INSERT: "AcDbBlockReference",
  HATCH: "AcDbHatch",
  LEADER: "AcDbLeader",
  MULTILEADER: "AcDbMLeader",
};

const isObj = (v: OVal): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v) && "t" in v;
const pt2 = (v: OVal, what: string): Pt => P(v, what);
const num2 = (v: OVal, what: string) => N(v, what);
const ent = (m: DrawingModel, o: Obj): Ent => {
  if (o.t !== "ent") throw new ModelError("객체(엔티티)가 아닙니다");
  const e = m.get(o.handle);
  if (!e) throw new ModelError(`지워졌거나 없는 객체: ${o.handle}`);
  return e;
};
const p3 = (p: Pt): number[] => [p[0], p[1], 0];

/** Property names AutoLISP / VBA may use, per object (case-insensitive). */
const fail = (msg: string): never => {
  throw new ModelError(msg);
};

export function getProp(m: DrawingModel, o: Obj, name: string): OVal {
  const ax = axGet(m, o, name, fail); // W4: the member table first
  if (ax !== NOT_HERE) return ax;
  const k = name.toUpperCase();
  switch (o.t) {
    case "app":
      if (k === "ACTIVEDOCUMENT") return { t: "doc" };
      if (k === "NAME") return "AX-CAD";
      break;
    case "doc":
      if (k === "MODELSPACE") return { t: "space" };
      if (k === "LAYERS") return { t: "layers" };
      if (k === "ACTIVELAYER") return { t: "layer", name: m.current };
      if (k === "APPLICATION") return { t: "app" };
      if (COLLECTION[k]) return { t: "table", tk: COLLECTION[k] };
      break;
    case "space":
    case "layers":
      if (k === "COUNT") return o.t === "space" ? m.all().length : m.layers.length;
      break;
    case "table":
      if (k === "COUNT") return o.tk === "layout" ? m.tables.layouts.length + 1 : m.table(o.tk).length; // + Model
      break;
    case "layout":
      if (k === "NAME") return o.name;
      break;
    case "rec": {
      const r = rec(m, o);
      if (k === "NAME") return r.name;
      if (o.tk === "linetype" && k === "DESCRIPTION") return r.description ?? "";
      if (o.tk === "textstyle") {
        if (k === "FONTFILE") return r.font ?? "";
        if (k === "HEIGHT") return r.height ?? 0;
        if (k === "WIDTH") return r.width ?? 1;
        if (k === "OBLIQUEANGLE") return rad(r.oblique ?? 0);
      }
      break;
    }
    case "layer": {
      const l = m.layer(o.name);
      if (!l) throw new ModelError(`없는 레이어: ${o.name}`);
      if (k === "NAME") return l.name;
      if (k === "COLOR") return l.color ?? 7;
      if (k === "LOCK") return Boolean(l.locked);
      if (k === "LINETYPE") return l.linetype ?? "Continuous";
      if (k === "LINEWEIGHT") return l.lwt ?? -3;
      if (k === "PLOTTABLE") return l.plot !== false;
      if (k === "LAYERON") return !l.off;
      if (k === "FREEZE") return Boolean(l.frozen);
      if (k === "DESCRIPTION") return l.description ?? "";
      break;
    }
    case "ent": {
      const e = ent(m, o);
      const g = e.geom;
      const meas = () => measureGeom(g);
      switch (k) {
        case "HANDLE":
          return e.handle;
        case "LAYER":
          return e.layer;
        case "COLOR":
          return e.aci ?? 256;
        case "LINETYPE":
          return e.linetype ?? "BYLAYER";
        case "LINEWEIGHT": // acLnWt: -1 ByLayer, -2 ByBlock, -3 default, else 1/100 mm
          return e.lwt ?? -1;
        case "LINETYPESCALE":
          return e.lts ?? 1;
        case "VISIBLE":
          return !e.inv;
        case "ENTITYTRANSPARENCY": // ActiveX gives a string: "ByLayer" or 0..90
          return e.tr === undefined ? "ByLayer" : String(e.tr);
        case "OBJECTNAME":
          return OBJECT_NAME[g.type] ?? `AcDb${g.type}`;
        case "ENTITYNAME":
          return OBJECT_NAME[g.type] ?? g.type;
        case "LENGTH":
        case "ARCLENGTH":
        case "CIRCUMFERENCE":
          if (meas()) return meas()!.length;
          break;
        case "AREA":
          if (meas()?.area !== undefined) return meas()!.area!;
          break;
      }
      switch (g.type) {
        case "LINE":
          if (k === "STARTPOINT") return p3(g.start);
          if (k === "ENDPOINT") return p3(g.end);
          if (k === "ANGLE") return (Math.atan2(g.end[1] - g.start[1], g.end[0] - g.start[0]) + 2 * Math.PI) % (2 * Math.PI);
          if (k === "DELTA") return [g.end[0] - g.start[0], g.end[1] - g.start[1], 0];
          break;
        case "CIRCLE":
        case "ARC":
          if (k === "CENTER") return p3(g.center);
          if (k === "RADIUS") return g.radius;
          if (k === "DIAMETER") return 2 * g.radius;
          if (g.type === "ARC") {
            if (k === "STARTANGLE") return rad(g.start_angle);
            if (k === "ENDANGLE") return rad(g.end_angle);
            if (k === "TOTALANGLE") return rad((((g.end_angle - g.start_angle) % 360) + 360) % 360 || 360);
            if (k === "STARTPOINT") return p3([g.center[0] + g.radius * Math.cos(rad(g.start_angle)), g.center[1] + g.radius * Math.sin(rad(g.start_angle))]);
            if (k === "ENDPOINT") return p3([g.center[0] + g.radius * Math.cos(rad(g.end_angle)), g.center[1] + g.radius * Math.sin(rad(g.end_angle))]);
          }
          break;
        case "LWPOLYLINE":
          if (k === "COORDINATES") return g.points.flatMap(([x, y]) => [x, y]);
          if (k === "CLOSED") return g.closed;
          break;
        case "MTEXT":
          if (k === "TEXTSTRING") return g.value;
          if (k === "INSERTIONPOINT") return p3(g.insert);
          if (k === "HEIGHT") return g.height;
          if (k === "WIDTH") return g.width;
          if (k === "ROTATION") return rad(g.rotation);
          if (k === "ATTACHMENTPOINT") return g.attachment;
          break;
        case "TEXT":
          if (k === "TEXTSTRING") return g.value;
          if (k === "INSERTIONPOINT") return p3(g.insert);
          if (k === "HEIGHT") return g.height;
          if (k === "ROTATION") return rad(g.rotation);
          break;
        case "POINT":
          if (k === "COORDINATES") return p3(g.point);
          break;
        case "ELLIPSE":
          if (k === "CENTER") return p3(g.center);
          if (k === "MAJORAXIS") return p3(g.major);
          if (k === "RADIUSRATIO") return g.ratio;
          break;
        case "INSERT":
          if (k === "NAME" || k === "EFFECTIVENAME") return g.name;
          if (k === "INSERTIONPOINT") return p3(g.insert);
          if (k === "XSCALEFACTOR") return g.xscale;
          if (k === "YSCALEFACTOR") return g.yscale;
          if (k === "ROTATION") return rad(g.rotation);
          break;
      }
      break;
    }
  }
  throw new ModelError(`${describe(m, o)}에 ${name} 속성이 없습니다`);
}

export function putProp(m: DrawingModel, o: Obj, name: string, v: OVal): void {
  if (axPut(m, o, name, v, fail)) return; // W4: the member table first
  const k = name.toUpperCase();
  if (o.t === "doc" && k === "ACTIVELAYER") {
    const l = isObj(v) && v.t === "layer" ? v.name : typeof v === "string" ? v : null;
    if (!l || !m.layer(l)) throw new ModelError("ActiveLayer: 도면에 있는 레이어가 필요합니다");
    m.current = m.layer(l)!.name;
    return;
  }
  if (o.t === "layer") {
    const l = m.layer(o.name);
    if (!l) throw new ModelError(`없는 레이어: ${o.name}`);
    if (k === "COLOR") {
      const c = num2(v, "Color");
      if (!(Number.isInteger(c) && c >= 1 && c <= 255)) throw new ModelError("Color: 레이어 색 번호는 1~255입니다");
      return void m.layerSet(l.name, { color: c });
    }
    if (k === "LOCK") return void m.layerSet(l.name, { locked: Boolean(v) });
    if (k === "LINETYPE") {
      const t = String(v);
      if (!/^continuous$/i.test(t) && !m.tableFind("linetype", t)) throw new ModelError(`없는 선종류: ${t}`);
      return void m.layerSet(l.name, { linetype: t });
    }
    if (k === "LINEWEIGHT") {
      const w = num2(v, name);
      if (!LINEWEIGHTS.includes(w) || w === -1 || w === -2) throw new ModelError("Lineweight: 레이어 선 두께가 아닙니다");
      return void m.layerSet(l.name, { lineweight: w });
    }
    if (k === "PLOTTABLE") return void m.layerSet(l.name, { plot: Boolean(v) });
    if (k === "LAYERON") return void m.layerSet(l.name, { off: !v });
    if (k === "FREEZE") {
      if (v && l.name.toUpperCase() === m.current.toUpperCase()) throw new ModelError("Freeze: 현재 레이어는 동결할 수 없습니다");
      return void m.layerSet(l.name, { frozen: Boolean(v) });
    }
    if (k === "DESCRIPTION") {
      const d = String(v);
      if (/[\x00-\x1f\x7f]/.test(d) || d.length > 255) throw new ModelError("Description: 한 줄 255자 이내입니다");
      return void m.layerSet(l.name, { description: d });
    }
    throw new ModelError(`레이어의 ${name} 속성은 바꿀 수 없습니다`);
  }
  if (o.t === "rec") {
    const r = rec(m, o);
    if (k === "NAME") return void m.tableRename(o.tk, r.name, String(v));
    if (o.tk === "linetype" && k === "DESCRIPTION") return void m.tableSet(o.tk, r.name, { description: String(v) });
    if (o.tk === "textstyle") {
      if (k === "FONTFILE") {
        const f = String(v).replace(/^.*[\\/]/, ""); // a file name only: the server looks it up in its font folders
        if (!/^[\w .\-가-힣]+\.(ttf|ttc|otf|shx)$/i.test(f)) throw new ModelError("FontFile: .ttf/.ttc/.otf/.shx 파일 이름이 필요합니다");
        return void m.tableSet(o.tk, r.name, { font: f });
      }
      if (k === "HEIGHT") return void m.tableSet(o.tk, r.name, { height: num2(v, name) });
      if (k === "WIDTH") return void m.tableSet(o.tk, r.name, { width: num2(v, name) });
      if (k === "OBLIQUEANGLE") return void m.tableSet(o.tk, r.name, { oblique: deg(num2(v, name)) });
    }
    throw new ModelError(`${describe(m, o)}의 ${name} 속성은 바꿀 수 없습니다`);
  }
  if (o.t !== "ent") throw new ModelError(`${describe(m, o)}의 ${name} 속성은 바꿀 수 없습니다`);
  const e = ent(m, o);
  const g = e.geom;
  if (k === "LAYER") return void m.set(e.handle, { layer: String(v) });
  if (k === "COLOR") {
    const c = num2(v, "Color");
    if (!(Number.isInteger(c) && c >= 0 && c <= 256)) throw new ModelError("Color: 색 번호는 0~256입니다");
    return void m.set(e.handle, { aci: c });
  }
  if (k === "LINETYPE") return void m.set(e.handle, { linetype: String(v) });
  if (k === "LINEWEIGHT") {
    const w = num2(v, name);
    if (!LINEWEIGHTS.includes(w)) throw new ModelError(`Lineweight: ${LINEWEIGHTS.join(", ")} 중 하나입니다`);
    return void m.set(e.handle, { lwt: w === -1 ? undefined : w });
  }
  if (k === "LINETYPESCALE") {
    const s = num2(v, name);
    if (!(s > 0 && s <= 1e6)) throw new ModelError("LinetypeScale: 0보다 커야 합니다");
    return void m.set(e.handle, { lts: s === 1 ? undefined : s });
  }
  if (k === "VISIBLE") return void m.set(e.handle, { inv: v ? undefined : true });
  if (k === "ENTITYTRANSPARENCY") {
    if (typeof v === "string" && /^bylayer$/i.test(v)) return void m.set(e.handle, { tr: undefined });
    const t = num2(typeof v === "string" ? Number(v) : v, name);
    if (!(Number.isInteger(t) && t >= 0 && t <= 90)) throw new ModelError('EntityTransparency: "ByLayer" 또는 0~90입니다');
    return void m.set(e.handle, { tr: t });
  }
  const geom = ((): Geom | null => {
    switch (g.type) {
      case "LINE":
        if (k === "STARTPOINT") return { ...g, start: pt2(v, name) };
        if (k === "ENDPOINT") return { ...g, end: pt2(v, name) };
        break;
      case "CIRCLE":
      case "ARC":
        if (k === "CENTER") return { ...g, center: pt2(v, name) };
        if (k === "RADIUS") return { ...g, radius: num2(v, name) };
        if (k === "DIAMETER") return { ...g, radius: num2(v, name) / 2 };
        if (g.type === "ARC" && k === "STARTANGLE") return { ...g, start_angle: deg(num2(v, name)) };
        if (g.type === "ARC" && k === "ENDANGLE") return { ...g, end_angle: deg(num2(v, name)) };
        break;
      case "LWPOLYLINE":
        if (k === "CLOSED") return { ...g, closed: Boolean(v) };
        if (k === "COORDINATES") {
          const c = (Array.isArray(v) ? v : []).map((x) => num2(x as OVal, name));
          if (c.length < 4 || c.length % 2) throw new ModelError("Coordinates: x, y 쌍이 2개 이상 필요합니다");
          return { ...g, points: Array.from({ length: c.length / 2 }, (_, i) => [c[2 * i], c[2 * i + 1], 0] as [number, number, number]) };
        }
        break;
      case "MTEXT":
        if (k === "TEXTSTRING") return { ...g, value: String(v) };
        if (k === "INSERTIONPOINT") return { ...g, insert: pt2(v, name) };
        if (k === "HEIGHT") return { ...g, height: num2(v, name) };
        if (k === "WIDTH") return { ...g, width: num2(v, name) };
        if (k === "ROTATION") return { ...g, rotation: deg(num2(v, name)) };
        if (k === "ATTACHMENTPOINT") return { ...g, attachment: num2(v, name) };
        break;
      case "TEXT":
        if (k === "TEXTSTRING") return { ...g, value: String(v) };
        if (k === "INSERTIONPOINT") return { ...g, insert: pt2(v, name) };
        if (k === "HEIGHT") return { ...g, height: num2(v, name) };
        if (k === "ROTATION") return { ...g, rotation: deg(num2(v, name)) };
        break;
      case "POINT":
        if (k === "COORDINATES") return { ...g, point: pt2(v, name) };
        break;
    }
    return null;
  })();
  if (!geom) throw new ModelError(`${describe(m, o)}의 ${name} 속성은 바꿀 수 없습니다`);
  m.set(e.handle, { geom });
}

const PI2 = 2 * Math.PI;
/** Methods: ModelSpace.Add*, Layers.Add/Item, entity Move/Copy/Rotate/ScaleEntity/Mirror/Delete. */
export function invoke(m: DrawingModel, o: Obj, method: string, args: OVal[]): OVal | ByRef {
  const ax = axCall(m, o, method, args, fail); // W4: the member table first
  if (ax !== NOT_HERE) return ax;
  const k = method.toUpperCase();
  const a = (i: number, what: string) => {
    if (args[i] === undefined) throw new ModelError(`${method}: 인수 ${what}가 필요합니다`);
    return args[i];
  };
  const made = (g: Geom): Obj => ({ t: "ent", handle: m.add(g).handle });
  if (o.t === "space") {
    switch (k) {
      case "ADDLINE":
        return made({ type: "LINE", start: pt2(a(0, "시작점"), method), end: pt2(a(1, "끝점"), method) });
      case "ADDCIRCLE":
        return made({ type: "CIRCLE", center: pt2(a(0, "중심"), method), radius: num2(a(1, "반지름"), method) });
      case "ADDARC":
        return made({ type: "ARC", center: pt2(a(0, "중심"), method), radius: num2(a(1, "반지름"), method), start_angle: deg(num2(a(2, "시작 각도"), method)), end_angle: deg(num2(a(3, "끝 각도"), method)) });
      case "ADDLIGHTWEIGHTPOLYLINE":
      case "ADDPOLYLINE": {
        const c = (a(0, "좌표") as OVal[]).map((x) => num2(x, method));
        const step = k === "ADDPOLYLINE" ? 3 : 2; // AddPolyline takes x, y, z
        if (c.length < step * 2 || c.length % step) throw new ModelError(`${method}: 좌표가 모자라거나 짝이 맞지 않습니다`);
        return made({ type: "LWPOLYLINE", points: Array.from({ length: c.length / step }, (_, i) => [c[step * i], c[step * i + 1], 0] as [number, number, number]), closed: false });
      }
      case "ADDTEXT":
        return made({ type: "TEXT", value: String(a(0, "문자열")), insert: pt2(a(1, "삽입점"), method), height: num2(a(2, "높이"), method), rotation: 0 });
      case "ADDMTEXT": // AddMText(InsertionPoint, Width, Text); height = the drawing default 2.5
        return made({ type: "MTEXT", insert: pt2(a(0, "삽입점"), method), width: num2(a(1, "폭"), method), value: String(a(2, "문자열")), height: 2.5, rotation: 0, attachment: 1 });
      case "ADDPOINT":
        return made({ type: "POINT", point: pt2(a(0, "점"), method) });
      case "ADDELLIPSE": {
        const ratio = num2(a(2, "비율"), method);
        if (!(ratio > 0 && ratio <= 1)) throw new ModelError("AddEllipse: 비율은 0보다 크고 1 이하입니다");
        return made({ type: "ELLIPSE", center: pt2(a(0, "중심"), method), major: pt2(a(1, "장축"), method), ratio, start_param: 0, end_param: PI2 });
      }
      case "ITEM": {
        const e = m.all()[num2(a(0, "번호"), method)];
        if (!e) throw new ModelError("Item: 번호가 범위를 벗어났습니다");
        return { t: "ent", handle: e.handle };
      }
    }
  }
  if (o.t === "table") {
    if (k === "ADD") {
      if (o.tk === "layout") throw new ModelError("Layouts.Add: 레이아웃 만들기는 아직 지원하지 않습니다");
      const n = String(a(0, "이름"));
      m.tableAdd(o.tk, n, o.tk === "textstyle" ? { font: "txt.shx" } : {});
      return { t: "rec", tk: o.tk, name: n };
    }
    if (k === "ITEM") {
      const key = a(0, "이름 또는 번호");
      if (o.tk === "layout") {
        const all = ["Model", ...m.tables.layouts];
        const n = typeof key === "number" ? all[key] : all.find((x) => x.toUpperCase() === String(key).toUpperCase());
        if (!n) throw new ModelError(`없는 레이아웃: ${String(key)}`);
        return { t: "layout", name: n };
      }
      const r = typeof key === "number" ? m.table(o.tk)[key] : m.tableFind(o.tk, String(key));
      if (!r) throw new ModelError(`없는 항목: ${String(key)}`);
      return { t: "rec", tk: o.tk, name: r.name };
    }
  }
  if (o.t === "rec" && k === "DELETE") return void m.tableRemove(o.tk, o.name), null;
  if (o.t === "layers") {
    if (k === "ADD") return { t: "layer", name: m.addLayer(String(a(0, "이름"))).name };
    if (k === "ITEM") {
      const key = a(0, "이름 또는 번호");
      const l = typeof key === "number" ? m.layers[key] : m.layer(String(key));
      if (!l) throw new ModelError(`없는 레이어: ${String(key)}`);
      return { t: "layer", name: l.name };
    }
  }
  if (o.t === "ent") {
    const e = ent(m, o);
    switch (k) {
      case "MOVE": {
        const [p, q] = [pt2(a(0, "기준점"), method), pt2(a(1, "목표점"), method)];
        m.set(e.handle, { geom: translate(e.geom, q[0] - p[0], q[1] - p[1]) });
        return null;
      }
      case "COPY":
        return { t: "ent", handle: m.add(e.geom, e.layer, { aci: e.aci, linetype: e.linetype }).handle };
      case "ROTATE":
        m.set(e.handle, { geom: rotateGeom(e.geom, pt2(a(0, "기준점"), method), deg(num2(a(1, "각도"), method))) });
        return null;
      case "SCALEENTITY": {
        const f = num2(a(1, "배율"), method);
        if (!(f > 0)) throw new ModelError("ScaleEntity: 배율은 0보다 커야 합니다");
        m.set(e.handle, { geom: scaleGeom(e.geom, pt2(a(0, "기준점"), method), f) });
        return null;
      }
      case "MIRROR": // AutoCAD keeps the original and returns the mirrored copy
        return { t: "ent", handle: m.add(mirrorGeom(e.geom, pt2(a(0, "점 1"), method), pt2(a(1, "점 2"), method)), e.layer, { aci: e.aci, linetype: e.linetype }).handle };
      case "DELETE":
      case "ERASE":
        m.remove(e.handle);
        return null;
      case "UPDATE":
        return null;
    }
  }
  throw new ModelError(`${describe(m, o)}에 ${method} 메서드가 없습니다`);
}

export function describe(m: DrawingModel, o: Obj): string {
  if (o.t === "ent") {
    const e = m.get(o.handle);
    return e ? (OBJECT_NAME[e.geom.type] ?? e.geom.type) : `객체 ${o.handle}`;
  }
  if (o.t === "table") return { linetype: "Linetypes", textstyle: "TextStyles", dimstyle: "DimStyles", regapp: "RegisteredApplications", layout: "Layouts" }[o.tk];
  if (o.t === "rec") return { linetype: "Linetype", textstyle: "TextStyle", dimstyle: "DimStyle", regapp: "RegisteredApplication" }[o.tk];
  return ({ app: "Application", doc: "Document", space: "ModelSpace", layers: "Layers", layer: "Layer", layout: "Layout", dict: "Dictionary", color: "AcCmColor", hyperlinks: "Hyperlinks", attref: "AttributeReference", state: "AcadState", hatchdraft: "Hatch" } as Record<string, string>)[o.t] ?? o.t;
}

export const isModelObj = isObj;
