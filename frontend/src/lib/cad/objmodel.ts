// 2D automation A1: the drawing as objects, shared by AX-LISP (entmake/entmod/vla-) and the VBA
// macros (A2). A run works on a copy of the editable entities; at the end changes() gives what to
// create, update and delete, and the screen applies it like a command (one undo step).
// Names follow the AutoCAD ActiveX object model (AddLine, StartPoint, Layer, ...) so existing
// routines carry over; there is no COM, no file or network access here.
import { mirrorGeom, rotateGeom, scaleGeom } from "./edit";
import { translate, type Geom } from "./geom";
import { measureGeom } from "./measure";
import type { Pt } from "./view";

export type Ent = { handle: string; layer: string; geom: Geom; aci?: number; linetype?: string };
export type Layer = { name: string; color?: number | null; locked?: boolean };
export type Changes = { created: Ent[]; updated: Ent[]; deleted: string[]; layers: string[] };

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
  if (g.type === "TEXT" && !(g.height > 0)) throw new ModelError("TEXT: 높이는 0보다 커야 합니다");
  if (g.type === "LWPOLYLINE" && g.points.length < 2) throw new ModelError("LWPOLYLINE: 점이 2개 이상 필요합니다");
  return g;
}

export class DrawingModel {
  private ents = new Map<string, Ent>();
  private base = new Map<string, Ent>();
  private made = 0;
  private newLayers: string[] = [];
  readonly layers: Layer[];
  current: string;

  constructor(entities: Ent[], layers: Layer[], current: string) {
    for (const e of entities) {
      this.ents.set(e.handle, e);
      this.base.set(e.handle, e);
    }
    this.layers = layers.map((l) => ({ ...l }));
    this.current = current;
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

  add(geom: Geom, layer = this.current, props: { aci?: number; linetype?: string } = {}): Ent {
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
    return { created, updated, deleted, layers: [...this.newLayers] };
  }
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

const MAKEABLE = ["LINE", "CIRCLE", "ARC", "LWPOLYLINE", "TEXT", "POINT", "ELLIPSE"];

/** entmake / entmod: groups -> geometry. `old` fills what the list leaves out (entmod). */
export function fromGroups(groups: Group[], old?: Ent): { geom: Geom; layer?: string; aci?: number; linetype?: string } {
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
  return { geom: checkGeom(geom), layer: has(8) ? S(first(8), "8") : undefined, aci, linetype: has(6) ? S(first(6), "6") : undefined };
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

/** Wildcard match (* any run, ? one character) in linear-ish time: no regular expressions, so no
 * catastrophic backtracking on long text values (security review A1). */
export function glob(pat: string, s: string): boolean {
  let p = 0;
  let i = 0;
  let star = -1;
  let mark = 0;
  while (i < s.length) {
    if (p < pat.length && (pat[p] === "?" || pat[p] === s[i])) {
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
  | { t: "ent"; handle: string };
/** A value crossing the object model: points are number arrays, collections index from 0. */
export type OVal = number | string | boolean | number[] | Obj | null | OVal[];

const OBJECT_NAME: Record<string, string> = {
  LINE: "AcDbLine",
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
export function getProp(m: DrawingModel, o: Obj, name: string): OVal {
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
      break;
    case "space":
    case "layers":
      if (k === "COUNT") return o.t === "space" ? m.all().length : m.layers.length;
      break;
    case "layer": {
      const l = m.layer(o.name);
      if (!l) throw new ModelError(`없는 레이어: ${o.name}`);
      if (k === "NAME") return l.name;
      if (k === "COLOR") return l.color ?? 7;
      if (k === "LOCK") return Boolean(l.locked);
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
    if (k === "COLOR") return void (l.color = num2(v, "Color"));
    throw new ModelError(`레이어의 ${name} 속성은 바꿀 수 없습니다 (Color만)`);
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
export function invoke(m: DrawingModel, o: Obj, method: string, args: OVal[]): OVal {
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
  return { app: "Application", doc: "Document", space: "ModelSpace", layers: "Layers", layer: "Layer" }[o.t];
}

export const isModelObj = isObj;
