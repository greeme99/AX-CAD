// W4 (plan §22.4): the ActiveX object model as a table — for each AutoCAD object (Line, Layer,
// Document, DimAligned, ...) the members AX-CAD answers and how. objmodel.ts getProp / putProp /
// invoke look here first, then fall back to their own cases; tools/coverage counts this table
// (frontend/src/lib/cad/axobj.members.json, kept in step by axobj.test.ts).
// A member is "full" when reading and writing work on the drawing, "limited" when AX-CAD has no
// such data (a 2D drawing has no thickness or material): it reads the default and takes only it.
import type { Geom } from "./geom";
import type { Pt } from "./view";
import { mirrorGeom, rotateGeom, scaleGeom } from "./edit";
import { toPaths, translate } from "./geom";
import { intersect, toPrims, type Prim } from "./curves";
import { invoke, matches, ModelError, putProp, type AttDefSpec, type DrawingModel, type Ent, type Group, type GroupInfo, type Obj, type OVal } from "./objmodel";

/** ByRef results: VBA / LISP assign `out[i]` to the i-th argument (GetBoundingBox min max ...). */
export type ByRef = { out: Record<number, OVal>; ret: OVal };
export const isByRef = (v: unknown): v is ByRef => typeof v === "object" && v !== null && !Array.isArray(v) && "out" in v && "ret" in v;

type Ctx = { m: DrawingModel; o: Obj; err: (msg: string) => never };
export type Member = {
  get?: (c: Ctx) => OVal;
  put?: (c: Ctx, v: OVal) => void;
  call?: (c: Ctx, args: OVal[]) => OVal | ByRef;
  event?: true;
  /** AX-CAD keeps no such data: the default is read, and only the default is taken. */
  limited?: true;
  /** Answered by objmodel.ts's own cases (kept there; listed so the table is complete). */
  legacy?: true;
};
type Members = Record<string, Member>;

// --- helpers -----------------------------------------------------------------------------------

const RAD = Math.PI / 180;
const num = (c: Ctx, v: OVal, what: string): number => (typeof v === "number" && Number.isFinite(v) ? v : c.err(`${what}: 숫자가 필요합니다`));
const pt = (c: Ctx, v: OVal, what: string): Pt => (Array.isArray(v) && v.length >= 2 && typeof v[0] === "number" && typeof v[1] === "number" ? [v[0], v[1]] : c.err(`${what}: 점 (x, y[, z])이 필요합니다`));
const p3 = (p: Pt): number[] => [p[0], p[1], 0];
const ent = (c: Ctx): Ent => {
  if (c.o.t !== "ent") return c.err("엔티티가 아닙니다");
  return c.m.get(c.o.handle) ?? c.err(`지워졌거나 없는 객체: ${c.o.handle}`);
};
/** A value only its default may be given to (limited members). */
const only = (want: OVal, what: string): Member => ({
  get: () => want,
  put: (c, v) => {
    if (JSON.stringify(v) !== JSON.stringify(want) && !(typeof want === "string" && typeof v === "string" && v.toUpperCase() === want.toUpperCase())) c.err(`${what}: AX-CAD 2D 도면에서는 ${JSON.stringify(want)}만 쓸 수 있습니다`);
  },
  limited: true,
});
const ro = (get: (c: Ctx) => OVal): Member => ({ get });
const legacy: Member = { legacy: true };
const L = (...names: string[]): Members => Object.fromEntries(names.map((n) => [n, legacy]));
const oid = (h: string | undefined): number => (h && /^[0-9A-F]+$/i.test(h) ? parseInt(h, 16) : 0);

/** The object's DXF handle ("" when AX-CAD has none for it, e.g. made in this run). */
function handleOf(m: DrawingModel, o: Obj): string {
  const ids = m.ids;
  switch (o.t) {
    case "ent":
      return /^[0-9A-F]+$/i.test(o.handle) ? o.handle : "";
    case "layer":
      return ids?.records.layer?.[o.name] ?? "";
    case "rec":
      return ids?.records[o.tk]?.[o.name] ?? "";
    case "layers":
      return ids?.tables.layer ?? "";
    case "table":
      return o.tk === "layout" ? "" : (ids?.tables[o.tk] ?? "");
    case "layout":
      return ids?.layouts[o.name] ?? "";
    case "space":
      return ids?.model_space ?? "";
    case "block":
      return ids?.blocks[o.name] ?? "";
    case "group":
      return ids?.groups[o.name] ?? "";
    case "dicts":
      return ids?.nod ?? "";
    case "dict":
    case "xrec":
      return (o.path.length ? m.dictView(o.owner, o.path)?.handle : o.owner ? "" : ids?.nod) ?? "";
    default:
      return "";
  }
}
function ownerOf(m: DrawingModel, o: Obj): string {
  const ids = m.ids;
  if (o.t === "ent") return ids?.model_space ?? "";
  if (o.t === "layer") return ids?.tables.layer ?? "";
  if (o.t === "rec") return ids?.tables[o.tk] ?? "";
  if (o.t === "layout" || o.t === "dict" || o.t === "xrec") return (o.t !== "layout" && o.owner) || (ids?.nod ?? "");
  if (o.t === "attref") return /^[0-9A-F]+$/i.test(o.handle) ? o.handle : "";
  return "";
}

// --- AcadObject: what every object has ---------------------------------------------------------

const OBJECT: Members = {
  Application: ro(() => ({ t: "app" })),
  Document: ro(() => ({ t: "doc" })),
  Handle: ro((c) => handleOf(c.m, c.o)),
  ObjectID: ro((c) => oid(handleOf(c.m, c.o))),
  OwnerID: ro((c) => oid(ownerOf(c.m, c.o))),
  ObjectName: ro((c) => objectName(c.m, c.o)),
  HasExtensionDictionary: ro((c) => (c.o.t === "ent" ? Boolean(c.m.get(c.o.handle)?.xdict) || c.m.dictChildren(c.o.handle, []).some((k) => !k.deleted) : false)),
  GetExtensionDictionary: {
    call: (c) => {
      if (c.o.t !== "ent" || !/^[0-9A-F]+$/i.test(c.o.handle)) return c.err("GetExtensionDictionary: 저장된 엔티티만 확장 사전을 가집니다");
      return { t: "dict", owner: c.o.handle, path: [] };
    },
  },
  GetXData: {
    // GetXData AppName, XDataType (out), XDataValue (out): "" = every application
    call: (c, args) => {
      const e = ent(c);
      const want = String(args[0] ?? "");
      const types: number[] = [];
      const values: OVal[] = [];
      for (const [app, g] of Object.entries(e.xd ?? {})) {
        if (!g.length || (want && !globMatch(want, app))) continue;
        types.push(1001);
        values.push(app);
        for (const [code, v] of g) {
          types.push(code);
          values.push(v as OVal);
        }
      }
      return { out: { 1: types, 2: values }, ret: null };
    },
  },
  SetXData: {
    // SetXData XDataType, XDataValue: (1001, app) starts each application's groups
    call: (c, args) => {
      const e = ent(c);
      const types = Array.isArray(args[0]) ? (args[0] as OVal[]) : c.err("SetXData: 형식 배열이 필요합니다");
      const values = Array.isArray(args[1]) ? (args[1] as OVal[]) : c.err("SetXData: 값 배열이 필요합니다");
      if (types.length !== values.length) c.err("SetXData: 형식과 값의 개수가 같아야 합니다");
      const xd: Record<string, [number, unknown][]> = Object.create(null);
      let app = "";
      types.forEach((t, i) => {
        const code = num(c, t, "SetXData");
        if (code === 1001) {
          app = String(values[i]);
          if (/^(ACAD|ACDB|EZDXF)/i.test(app)) c.err(`SetXData: AutoCAD·시스템 응용 프로그램은 쓸 수 없습니다 (${app})`);
          if (!c.m.tableFind("regapp", app)) c.err(`SetXData: 등록되지 않은 응용 프로그램입니다 (${app})`);
          xd[app] = [];
        } else if (!app) c.err("SetXData: 첫 그룹은 (1001, 응용 프로그램 이름)입니다");
        else xd[app].push([code, values[i]]);
      });
      c.m.set(e.handle, { xd: { ...(e.xd ?? {}), ...xd } });
      return null;
    },
  },
  Delete: legacy,
  Modified: { event: true },
};

/** `*` / `?` wildcards, case-insensitive; no regular expression (patterns come from scripts). */
const globMatch = (pat: string, str: string) => {
  const [p, s] = [pat.toUpperCase(), str.toUpperCase()];
  let [i, j, star, mark] = [0, 0, -1, 0];
  while (j < s.length) {
    if (i < p.length && (p[i] === "?" || p[i] === s[j])) [i, j] = [i + 1, j + 1];
    else if (i < p.length && p[i] === "*") [star, mark, i] = [i, j, i + 1];
    else if (star >= 0) [i, mark, j] = [star + 1, mark + 1, mark + 1];
    else return false;
  }
  while (p[i] === "*") i++;
  return i === p.length;
};
/** Bounds of points, without spreading them into Math.min (large lists overflow the stack). */
const bounds = (pts: readonly (readonly number[])[]) => {
  const b = [Infinity, Infinity, -Infinity, -Infinity];
  for (const p of pts) [b[0], b[1], b[2], b[3]] = [Math.min(b[0], p[0]), Math.min(b[1], p[1]), Math.max(b[2], p[0]), Math.max(b[3], p[1])];
  return b;
};
/** Copies an array call may make: their vertices together stay within what a drawing can save. */
const ARRAY_VERTICES = 200_000;
const arrayBudget = (c: Ctx, e: Ent, copies: number, what: string) => {
  if (copies * Math.max(1, pathsOf(e).flat().length) > ARRAY_VERTICES) c.err(`${what}: 복사본 정점이 너무 많습니다 (최대 ${ARRAY_VERTICES.toLocaleString()}개)`);
};

const OBJECT_NAMES: Record<string, string> = {
  LINE: "AcDbLine", CIRCLE: "AcDbCircle", ARC: "AcDbArc", LWPOLYLINE: "AcDbPolyline", TEXT: "AcDbText", MTEXT: "AcDbMText", POINT: "AcDbPoint", ELLIPSE: "AcDbEllipse", SPLINE: "AcDbSpline",
  HATCH: "AcDbHatch", LEADER: "AcDbLeader", MULTILEADER: "AcDbMLeader", INSERT: "AcDbBlockReference", DIM_LINEAR: "AcDbRotatedDimension", DIM_ALIGNED: "AcDbAlignedDimension",
  DIM_ANGULAR: "AcDb3PointAngularDimension", DIM_RADIUS: "AcDbRadialDimension", DIM_DIAMETER: "AcDbDiametricDimension",
};
function objectName(m: DrawingModel, o: Obj): string {
  if (o.t === "ent") {
    const e = m.get(o.handle);
    return e ? (OBJECT_NAMES[e.geom.type] ?? `AcDb${e.geom.type}`) : "";
  }
  return { layer: "AcDbLayerTableRecord", layers: "AcDbLayerTable", space: "AcDbBlockTableRecord", layout: "AcDbLayout", doc: "AcDbDatabase", app: "AcadApplication", blocks: "AcDbBlockTable", block: "AcDbBlockTableRecord", attdef: "AcDbAttributeDefinition", attref: "AcDbAttribute", groups: "AcDbDictionary", group: "AcDbGroup", dicts: "AcDbDictionary", dict: "AcDbDictionary", xrec: "AcDbXrecord", ssets: "AcadSelectionSets", sset: "AcadSelectionSet", docs: "AcadDocuments" }[o.t as string] ??
    (o.t === "rec" ? { linetype: "AcDbLinetypeTableRecord", textstyle: "AcDbTextStyleTableRecord", dimstyle: "AcDbDimStyleTableRecord", regapp: "AcDbRegAppTableRecord" }[o.tk] : o.t === "table" ? { linetype: "AcDbLinetypeTable", textstyle: "AcDbTextStyleTable", dimstyle: "AcDbDimStyleTable", regapp: "AcDbRegAppTable", layout: "AcDbDictionary" }[o.tk] : "AcDbObject");
}

// --- AcadEntity: every drawing entity ----------------------------------------------------------

/** Points enough for a bounding box or an intersection: the entity's drawn paths. */
const pathsOf = (e: Ent): Pt[][] => {
  const g = e.geom as Geom & { insert?: Pt };
  const p = toPaths(e.geom);
  return p.length ? p : g.insert ? [[g.insert]] : [];
};
const copyOf = (c: Ctx, e: Ent, g: Geom): Obj => ({ t: "ent", handle: c.m.add(g, e.layer, { aci: e.aci, linetype: e.linetype }).handle });
/** 2D similarity (+ reflection) of a 4x4 matrix; anything else is not a 2D transform. */
function similarity(c: Ctx, v: OVal): (g: Geom) => Geom {
  const rows = Array.isArray(v) ? (v as OVal[]).map((r) => (Array.isArray(r) ? (r as OVal[]).map((x) => num(c, x, "TransformBy")) : c.err("TransformBy: 4×4 행렬이 필요합니다"))) : c.err("TransformBy: 4×4 행렬이 필요합니다");
  if (rows.length !== 4 || rows.some((r) => r.length !== 4)) c.err("TransformBy: 4×4 행렬이 필요합니다");
  const [[a, b, , tx], [cc, d, , ty], z] = rows;
  if (Math.abs(z[0]) + Math.abs(z[1]) + Math.abs(z[3]) > 1e-9 || Math.abs(Math.abs(z[2]) - 1) > 1e-9) c.err("TransformBy: 2D 도면에서는 XY 평면 안의 변환만 됩니다");
  const det = a * d - b * cc;
  const k = Math.sqrt(Math.abs(det));
  // a similarity: [[k cos, -k sin], [k sin, k cos]] (det > 0), or with a reflection (det < 0)
  const sim = det > 0 ? Math.abs(a - d) < 1e-9 && Math.abs(b + cc) < 1e-9 : Math.abs(a + d) < 1e-9 && Math.abs(b - cc) < 1e-9;
  if (!sim || k < 1e-12) c.err("TransformBy: 회전·이동·균일 축척·대칭만 됩니다 (기울이기·비균일 축척 불가)");
  const ang = Math.atan2(cc, a) / RAD;
  return (g: Geom) => {
    let r = det < 0 ? mirrorGeom(g, [0, 0], [1, 0]) : g; // reflect across X first, then turn
    if (Math.abs(k - 1) > 1e-12) r = scaleGeom(r, [0, 0], k);
    if (Math.abs(ang) > 1e-12) r = rotateGeom(r, [0, 0], det < 0 ? -ang : ang);
    return translate(r, tx, ty);
  };
}
const extended = (p: Prim, ext: boolean): Prim => {
  if (!ext) return p;
  if (p.kind === "SEG") {
    const [dx, dy] = [p.b[0] - p.a[0], p.b[1] - p.a[1]];
    return { ...p, a: [p.a[0] - dx * 1e6, p.a[1] - dy * 1e6], b: [p.b[0] + dx * 1e6, p.b[1] + dy * 1e6] };
  }
  return { ...p, a0: 0, sweep: 2 * Math.PI };
};

const ENTITY: Members = {
  ...OBJECT,
  ...L("Layer", "Linetype", "Lineweight", "LinetypeScale", "Visible", "EntityTransparency", "Copy", "Move", "Rotate", "ScaleEntity", "Mirror", "Update"),
  TrueColor: {
    get: (c) => {
      const e = ent(c);
      return { t: "color", aci: e.aci ?? 256, ...(e.tc !== undefined ? { rgb: e.tc } : {}) } as unknown as Obj;
    },
    put: (c, v) => {
      const col = v as unknown as { t?: string; aci?: number; rgb?: number };
      if (col?.t !== "color") c.err("TrueColor: 색 객체(TrueColor로 얻은 값)가 필요합니다");
      const e = ent(c);
      c.m.set(e.handle, col.rgb !== undefined ? { tc: col.rgb } : { aci: col.aci ?? 256, tc: undefined });
    },
  },
  Material: only("ByLayer", "Material"),
  PlotStyleName: only("ByLayer", "PlotStyleName"),
  Hyperlinks: ro((c) => ({ t: "hyperlinks", handle: ent(c).handle }) as unknown as Obj),
  Highlight: { call: () => null }, // the screen highlights the selection itself
  GetBoundingBox: {
    call: (c) => {
      const pts = pathsOf(ent(c)).flat();
      if (!pts.length) return c.err("GetBoundingBox: 범위를 알 수 없는 객체입니다");
      const [x0, y0, x1, y1] = bounds(pts);
      return { out: { 0: [x0, y0, 0], 1: [x1, y1, 0] }, ret: null };
    },
  },
  IntersectWith: {
    call: (c, args) => {
      const e = ent(c);
      const other = args[0] as Obj;
      if (other?.t !== "ent") return c.err("IntersectWith: 다른 엔티티가 필요합니다");
      const f = c.m.get(other.handle) ?? c.err("IntersectWith: 없는 객체입니다");
      const mode = typeof args[1] === "number" ? args[1] : 0; // 0 none, 1 extend this, 2 the other, 3 both
      const pa = toPrims(e.handle, e.geom, toPaths(e.geom)).map((p) => extended(p, mode === 1 || mode === 3));
      const pb = toPrims(f.handle, f.geom, toPaths(f.geom)).map((p) => extended(p, mode === 2 || mode === 3));
      if (pa.length * pb.length > 1_000_000) return c.err("IntersectWith: 두 객체가 너무 복잡합니다");
      const hits = new Map<string, Pt>(); // one point per 1e-9 cell
      for (const p of pa) for (const q of pb) for (const h of intersect(p, q)) hits.set(`${Math.round(h[0] * 1e9)},${Math.round(h[1] * 1e9)}`, h);
      return [...hits.values()].flatMap((h) => [h[0], h[1], 0]);
    },
  },
  ArrayPolar: {
    call: (c, args) => {
      const e = ent(c);
      const n = Math.trunc(num(c, args[0], "ArrayPolar"));
      if (n < 2 || n > 10_000) c.err("ArrayPolar: 개수는 2~10,000입니다");
      arrayBudget(c, e, n - 1, "ArrayPolar");
      const fill = num(c, args[1], "ArrayPolar");
      const center = pt(c, args[2], "ArrayPolar");
      const step = Math.abs(Math.abs(fill) - 2 * Math.PI) < 1e-9 ? fill / n : fill / (n - 1);
      return Array.from({ length: n - 1 }, (_, i) => copyOf(c, e, rotateGeom(e.geom, center, (step * (i + 1)) / RAD)));
    },
  },
  ArrayRectangular: {
    call: (c, args) => {
      const e = ent(c);
      const [rows, cols, levels] = [0, 1, 2].map((i) => Math.trunc(num(c, args[i], "ArrayRectangular")));
      const [dr, dc] = [num(c, args[3], "ArrayRectangular"), num(c, args[4], "ArrayRectangular")];
      if (rows < 1 || cols < 1 || levels < 1 || rows * cols > 10_000) c.err("ArrayRectangular: 행·열은 1 이상, 합계 10,000개까지입니다");
      arrayBudget(c, e, rows * cols - 1, "ArrayRectangular");
      const out: Obj[] = [];
      for (let r = 0; r < rows; r++) for (let k = 0; k < cols; k++) if (r || k) out.push(copyOf(c, e, translate(e.geom, k * dc, r * dr)));
      return out; // ponytail: levels stack along Z, which a 2D drawing has not
    },
  },
  Mirror3D: {
    call: (c, args) => {
      const e = ent(c);
      const [a, b, d] = [0, 1, 2].map((i) => (Array.isArray(args[i]) ? (args[i] as number[]) : c.err("Mirror3D: 점 세 개가 필요합니다")));
      const u = [b[0] - a[0], b[1] - a[1], (b[2] ?? 0) - (a[2] ?? 0)];
      const v = [d[0] - a[0], d[1] - a[1], (d[2] ?? 0) - (a[2] ?? 0)];
      const n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
      const len = Math.hypot(...n);
      if (len < 1e-12) return c.err("Mirror3D: 세 점이 한 직선 위에 있습니다");
      if (Math.abs(n[2]) / len > 1 - 1e-9) return copyOf(c, e, e.geom); // the XY plane itself: a copy
      if (Math.abs(n[2]) / len > 1e-9) return c.err("Mirror3D: 2D 도면에서는 XY 평면에 수직인 평면만 됩니다");
      return copyOf(c, e, mirrorGeom(e.geom, [a[0], a[1]], [a[0] - n[1], a[1] + n[0]]));
    },
  },
  Rotate3D: {
    call: (c, args) => {
      const e = ent(c);
      const a = Array.isArray(args[0]) ? (args[0] as number[]) : c.err("Rotate3D: 축의 점이 필요합니다");
      const b = Array.isArray(args[1]) ? (args[1] as number[]) : c.err("Rotate3D: 축의 점이 필요합니다");
      const ang = num(c, args[2], "Rotate3D");
      const dz = (b[2] ?? 0) - (a[2] ?? 0);
      if (Math.hypot(b[0] - a[0], b[1] - a[1]) > 1e-9 || Math.abs(dz) < 1e-12) return c.err("Rotate3D: 2D 도면에서는 Z축과 나란한 축만 됩니다");
      c.m.set(e.handle, { geom: rotateGeom(e.geom, [a[0], a[1]], (Math.sign(dz) * ang) / RAD) });
      return null;
    },
  },
  TransformBy: {
    call: (c, args) => {
      const e = ent(c);
      c.m.set(e.handle, { geom: similarity(c, args[0])(e.geom) });
      return null;
    },
  },
};
/** Planar entities also have a normal and a thickness (a 2D drawing: +Z and 0). */
const PLANAR: Members = { Normal: only([0, 0, 1], "Normal"), Thickness: only(0, "Thickness") };

// --- per entity ---------------------------------------------------------------------------------

const offsetCall = (fn: (e: Ent, d: number) => Geom | null): Member => ({
  call: (c, args) => {
    const e = ent(c);
    const d = num(c, args[0], "Offset");
    const g = fn(e, d);
    if (!g) return c.err("Offset: 이 거리로는 간격띄우기를 할 수 없습니다");
    return [copyOf(c, e, g)];
  },
});
const LINE: Members = {
  ...ENTITY,
  ...PLANAR,
  ...L("StartPoint", "EndPoint", "Angle", "Delta", "Length"),
  Offset: offsetCall((e, d) => {
    const g = e.geom as Extract<Geom, { type: "LINE" }>;
    const len = Math.hypot(g.end[0] - g.start[0], g.end[1] - g.start[1]);
    if (!len) return null;
    const [nx, ny] = [-(g.end[1] - g.start[1]) / len, (g.end[0] - g.start[0]) / len]; // left of start->end
    return { type: "LINE", start: [g.start[0] + nx * d, g.start[1] + ny * d], end: [g.end[0] + nx * d, g.end[1] + ny * d] };
  }),
};
const circleOffset = offsetCall((e, d) => {
  const g = e.geom as Extract<Geom, { type: "CIRCLE" | "ARC" }>;
  return g.radius + d > 0 ? { ...g, radius: g.radius + d } : null; // + outward, as AutoCAD
});
const CIRCLE: Members = { ...ENTITY, ...PLANAR, ...L("Center", "Radius", "Diameter", "Circumference", "Area"), Offset: circleOffset };
const ARC: Members = { ...ENTITY, ...PLANAR, ...L("Center", "Radius", "StartAngle", "EndAngle", "TotalAngle", "StartPoint", "EndPoint", "ArcLength", "Area"), Offset: circleOffset };
const poly = (c: Ctx) => {
  const e = ent(c);
  if (e.geom.type !== "LWPOLYLINE") return c.err("폴리선이 아닙니다");
  return { e, g: e.geom };
};
const vertex = (c: Ctx, v: OVal, n: number) => {
  const i = Math.trunc(num(c, v, "정점 번호"));
  return i >= 0 && i < n ? i : c.err("정점 번호가 범위를 벗어났습니다");
};
const LWPOLYLINE: Members = {
  ...ENTITY,
  ...PLANAR,
  ...L("Coordinates", "Closed", "Length", "Area"),
  Elevation: only(0, "Elevation"),
  ConstantWidth: only(0, "ConstantWidth"),
  LinetypeGeneration: only(false, "LinetypeGeneration"),
  Coordinate: {
    // indexed property: Coordinate(i) / put via SetCoordinate-style call (index, point)
    call: (c, args) => {
      const { e, g } = poly(c);
      const i = vertex(c, args[0], g.points.length);
      if (args.length < 2) return [g.points[i][0], g.points[i][1]];
      const p = pt(c, args[1], "Coordinate");
      c.m.set(e.handle, { geom: { ...g, points: g.points.map((q, k) => (k === i ? [p[0], p[1], q[2]] : q)) } });
      return null;
    },
  },
  AddVertex: {
    call: (c, args) => {
      const { e, g } = poly(c);
      const i = Math.trunc(num(c, args[0], "AddVertex"));
      if (i < 0 || i > g.points.length) c.err("AddVertex: 번호가 범위를 벗어났습니다");
      const p = pt(c, args[1], "AddVertex");
      c.m.set(e.handle, { geom: { ...g, points: [...g.points.slice(0, i), [p[0], p[1], 0], ...g.points.slice(i)] } });
      return null;
    },
  },
  GetBulge: { call: (c, args) => { const { g } = poly(c); return g.points[vertex(c, args[0], g.points.length)][2]; } },
  SetBulge: {
    call: (c, args) => {
      const { e, g } = poly(c);
      const i = vertex(c, args[0], g.points.length);
      const b = num(c, args[1], "SetBulge");
      c.m.set(e.handle, { geom: { ...g, points: g.points.map((q, k) => (k === i ? [q[0], q[1], b] : q)) } });
      return null;
    },
  },
  GetWidth: { call: () => ({ out: { 1: 0, 2: 0 }, ret: null }), limited: true },
  SetWidth: {
    call: (c, args) => {
      if (num(c, args[1], "SetWidth") || num(c, args[2], "SetWidth")) c.err("SetWidth: AX-CAD 2D 폴리선은 폭이 0입니다");
      return null;
    },
    limited: true,
  },
  Explode: {
    call: (c) => {
      const { e, g } = poly(c);
      const pts = g.closed ? [...g.points, g.points[0]] : g.points;
      return pts.slice(1).flatMap((q, i) => {
        const a = pts[i];
        if (Math.hypot(q[0] - a[0], q[1] - a[1]) < 1e-12) return []; // a repeated vertex: no segment
        if (!a[2]) return [copyOf(c, e, { type: "LINE", start: [a[0], a[1]], end: [q[0], q[1]] })];
        // a bulged segment: an arc through both ends (bulge = tan(sweep / 4))
        const sweep = 4 * Math.atan(a[2]);
        const chord = Math.hypot(q[0] - a[0], q[1] - a[1]);
        const r = chord / (2 * Math.sin(Math.abs(sweep) / 2));
        const mid: Pt = [(a[0] + q[0]) / 2, (a[1] + q[1]) / 2];
        const h = r * Math.cos(sweep / 2);
        const [ux, uy] = [(q[0] - a[0]) / chord, (q[1] - a[1]) / chord];
        const sgn = sweep > 0 ? 1 : -1;
        const center: Pt = [mid[0] - uy * h * sgn, mid[1] + ux * h * sgn];
        const ang = (p: number[]) => (Math.atan2(p[1] - center[1], p[0] - center[0]) / RAD + 360) % 360;
        const [s, t] = sweep > 0 ? [ang(a), ang(q)] : [ang(q), ang(a)];
        return [copyOf(c, e, { type: "ARC", center, radius: Math.abs(r), start_angle: s, end_angle: t })];
      });
    },
  },
  Offset: offsetCall(() => null), // replaced below: needs the polyline's own corners
};
LWPOLYLINE.Offset = {
  call: (c, args) => {
    const { e, g } = poly(c);
    const d = num(c, args[0], "Offset");
    if (g.points.some((p) => p[2])) return c.err("Offset: 호가 있는 폴리선은 아직 간격띄우기를 하지 않습니다");
    const pts = g.points.map((p) => [p[0], p[1]] as Pt);
    const n = pts.length;
    if (n < 2) return c.err("Offset: 점이 2개 이상인 폴리선이어야 합니다");
    const seg = (i: number) => {
      const [a, b] = [pts[i], pts[(i + 1) % n]];
      const len = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
      const [nx, ny] = [-(b[1] - a[1]) / len, (b[0] - a[0]) / len];
      return [[a[0] + nx * d, a[1] + ny * d], [b[0] + nx * d, b[1] + ny * d]] as [Pt, Pt];
    };
    const meet = (s: [Pt, Pt], t: [Pt, Pt]): Pt => {
      const [p, r] = [s[0], [s[1][0] - s[0][0], s[1][1] - s[0][1]]];
      const [q, u] = [t[0], [t[1][0] - t[0][0], t[1][1] - t[0][1]]];
      const den = r[0] * u[1] - r[1] * u[0];
      if (Math.abs(den) < 1e-12) return s[1];
      const k = ((q[0] - p[0]) * u[1] - (q[1] - p[1]) * u[0]) / den;
      return [p[0] + r[0] * k, p[1] + r[1] * k];
    };
    const segs = Array.from({ length: g.closed ? n : n - 1 }, (_, i) => seg(i));
    const out: Pt[] = g.closed ? segs.map((s, i) => meet(segs[(i - 1 + segs.length) % segs.length], s)) : [segs[0][0], ...segs.slice(1).map((s, i) => meet(segs[i], s)), segs[segs.length - 1][1]];
    return [copyOf(c, e, { ...g, points: out.map((p) => [p[0], p[1], 0] as [number, number, number]) })];
  },
};
const TEXT: Members = {
  ...ENTITY,
  ...PLANAR,
  ...L("TextString", "InsertionPoint", "Height", "Rotation"),
  Alignment: only(0, "Alignment"),
  HorizontalAlignment: only(0, "HorizontalAlignment"),
  VerticalAlignment: only(0, "VerticalAlignment"),
  TextAlignmentPoint: { get: (c) => p3((ent(c).geom as Extract<Geom, { type: "TEXT" }>).insert), limited: true },
  Backward: only(false, "Backward"),
  UpsideDown: only(false, "UpsideDown"),
  TextGenerationFlag: only(0, "TextGenerationFlag"),
  ObliqueAngle: only(0, "ObliqueAngle"),
  ScaleFactor: only(1, "ScaleFactor"),
  StyleName: only("Standard", "StyleName"),
  FieldCode: { call: (c) => (ent(c).geom as Extract<Geom, { type: "TEXT" }>).value },
};
const MTEXT: Members = {
  ...ENTITY,
  ...PLANAR,
  ...L("TextString", "InsertionPoint", "Height", "Width", "Rotation", "AttachmentPoint"),
  StyleName: only("Standard", "StyleName"),
  DrawingDirection: only(1, "DrawingDirection"),
  LineSpacingFactor: only(1, "LineSpacingFactor"),
  LineSpacingStyle: only(1, "LineSpacingStyle"),
  LineSpacingDistance: { get: (c) => (ent(c).geom as Extract<Geom, { type: "MTEXT" }>).height * 5 / 3, limited: true },
  BackgroundFill: only(false, "BackgroundFill"),
  FieldCode: { call: (c) => (ent(c).geom as Extract<Geom, { type: "MTEXT" }>).value },
};
const POINT: Members = { ...ENTITY, ...PLANAR, ...L("Coordinates") };
const ELLIPSE: Members = {
  ...ENTITY,
  ...L("Center", "MajorAxis", "RadiusRatio"),
  Normal: only([0, 0, 1], "Normal"),
  MinorAxis: ro((c) => {
    const g = ent(c).geom as Extract<Geom, { type: "ELLIPSE" }>;
    return [-g.major[1] * g.ratio, g.major[0] * g.ratio, 0];
  }),
  MajorRadius: ro((c) => Math.hypot(...((ent(c).geom as Extract<Geom, { type: "ELLIPSE" }>).major))),
  MinorRadius: ro((c) => Math.hypot(...((ent(c).geom as Extract<Geom, { type: "ELLIPSE" }>).major)) * (ent(c).geom as Extract<Geom, { type: "ELLIPSE" }>).ratio),
  StartParameter: { get: (c) => (ent(c).geom as Extract<Geom, { type: "ELLIPSE" }>).start_param, put: (c, v) => { const e = ent(c); c.m.set(e.handle, { geom: { ...(e.geom as Extract<Geom, { type: "ELLIPSE" }>), start_param: num(c, v, "StartParameter") } }); } },
  EndParameter: { get: (c) => (ent(c).geom as Extract<Geom, { type: "ELLIPSE" }>).end_param, put: (c, v) => { const e = ent(c); c.m.set(e.handle, { geom: { ...(e.geom as Extract<Geom, { type: "ELLIPSE" }>), end_param: num(c, v, "EndParameter") } }); } },
  StartAngle: ro((c) => (ent(c).geom as Extract<Geom, { type: "ELLIPSE" }>).start_param),
  EndAngle: ro((c) => (ent(c).geom as Extract<Geom, { type: "ELLIPSE" }>).end_param),
  StartPoint: ro((c) => ellipseAt(ent(c), "start")),
  EndPoint: ro((c) => ellipseAt(ent(c), "end")),
  Area: ro((c) => {
    const g = ent(c).geom as Extract<Geom, { type: "ELLIPSE" }>;
    return Math.PI * Math.hypot(...g.major) ** 2 * g.ratio;
  }),
  Offset: { call: (c) => c.err("Offset: 타원의 간격띄우기는 타원이 아니라 지원하지 않습니다"), limited: true },
};
function ellipseAt(e: Ent, which: "start" | "end"): number[] {
  const g = e.geom as Extract<Geom, { type: "ELLIPSE" }>;
  const t = which === "start" ? g.start_param : g.end_param;
  const [mx, my] = g.major;
  return [g.center[0] + mx * Math.cos(t) - my * g.ratio * Math.sin(t), g.center[1] + my * Math.cos(t) + mx * g.ratio * Math.sin(t), 0];
}
const spline = (c: Ctx) => {
  const e = ent(c);
  if (e.geom.type !== "SPLINE") return c.err("스플라인이 아닙니다");
  return { e, g: e.geom };
};
const SPLINE: Members = {
  ...ENTITY,
  FitPoints: { get: (c) => spline(c).g.fit.flatMap((p) => [p[0], p[1], 0]), put: (c, v) => {
    const { e, g } = spline(c);
    const f = (Array.isArray(v) ? v : []).map((x) => num(c, x as OVal, "FitPoints"));
    if (f.length < 6 || f.length % 3) c.err("FitPoints: x, y, z 점이 2개 이상 필요합니다");
    c.m.set(e.handle, { geom: { ...g, fit: Array.from({ length: f.length / 3 }, (_, i) => [f[3 * i], f[3 * i + 1]] as Pt) } });
  } },
  NumberOfFitPoints: ro((c) => spline(c).g.fit.length),
  GetFitPoint: { call: (c, args) => { const { g } = spline(c); return p3(g.fit[vertex(c, args[0], g.fit.length)]); } },
  SetFitPoint: { call: (c, args) => { const { e, g } = spline(c); const i = vertex(c, args[0], g.fit.length); const p = pt(c, args[1], "SetFitPoint"); c.m.set(e.handle, { geom: { ...g, fit: g.fit.map((q, k) => (k === i ? p : q)) } }); return null; } },
  AddFitPoint: { call: (c, args) => { const { e, g } = spline(c); const i = Math.trunc(num(c, args[0], "AddFitPoint")); if (i < 0 || i > g.fit.length) c.err("AddFitPoint: 번호가 범위를 벗어났습니다"); c.m.set(e.handle, { geom: { ...g, fit: [...g.fit.slice(0, i), pt(c, args[1], "AddFitPoint"), ...g.fit.slice(i)] } }); return null; } },
  DeleteFitPoint: { call: (c, args) => { const { e, g } = spline(c); if (g.fit.length <= 2) c.err("DeleteFitPoint: 맞춤점은 2개 이상 남아야 합니다"); const i = vertex(c, args[0], g.fit.length); c.m.set(e.handle, { geom: { ...g, fit: g.fit.filter((_, k) => k !== i) } }); return null; } },
  Reverse: { call: (c) => { const { e, g } = spline(c); c.m.set(e.handle, { geom: { ...g, fit: [...g.fit].reverse() } }); return null; } },
  Degree: only(3, "Degree"),
  Degree2: only(3, "Degree2"),
  Closed: ro((c) => { const f = spline(c).g.fit; return f.length > 2 && Math.hypot(f[0][0] - f[f.length - 1][0], f[0][1] - f[f.length - 1][1]) < 1e-9; }),
  Closed2: ro((c) => { const f = spline(c).g.fit; return f.length > 2 && Math.hypot(f[0][0] - f[f.length - 1][0], f[0][1] - f[f.length - 1][1]) < 1e-9; }),
  IsPlanar: ro(() => true),
  IsRational: ro(() => false),
  IsPeriodic: ro(() => false),
  FitTolerance: only(0, "FitTolerance"),
  StartTangent: only([0, 0, 0], "StartTangent"),
  EndTangent: only([0, 0, 0], "EndTangent"),
  KnotParameterization: only(0, "KnotParameterization"),
  SplineMethod: only(0, "SplineMethod"),
  SplineFrame: only(0, "SplineFrame"),
  PurgeFitData: { call: (c) => c.err("PurgeFitData: AX-CAD 스플라인은 맞춤점으로만 정의됩니다"), limited: true },
  Area: ro((c) => { const f = spline(c).g.fit; let s = 0; for (let i = 0; i < f.length; i++) { const [a, b] = [f[i], f[(i + 1) % f.length]]; s += a[0] * b[1] - b[0] * a[1]; } return Math.abs(s) / 2; }),
  Offset: { call: (c) => c.err("Offset: 스플라인의 간격띄우기는 지원하지 않습니다"), limited: true },
};
const hatch = (c: Ctx) => {
  const e = ent(c);
  if (e.geom.type !== "HATCH") return c.err("해치가 아닙니다");
  return { e, g: e.geom };
};
const HATCH: Members = {
  ...ENTITY,
  ...PLANAR,
  PatternName: { get: (c) => hatch(c).g.pattern, put: (c, v) => { const { e, g } = hatch(c); c.m.set(e.handle, { geom: { ...g, pattern: String(v) } }); } },
  PatternScale: { get: (c) => hatch(c).g.scale, put: (c, v) => { const { e, g } = hatch(c); const k = num(c, v, "PatternScale"); if (!(k > 0)) c.err("PatternScale: 0보다 커야 합니다"); c.m.set(e.handle, { geom: { ...g, scale: k } }); } },
  PatternAngle: { get: (c) => hatch(c).g.angle * RAD, put: (c, v) => { const { e, g } = hatch(c); c.m.set(e.handle, { geom: { ...g, angle: num(c, v, "PatternAngle") / RAD } }); } },
  PatternType: ro((c) => (/^SOLID$/i.test(hatch(c).g.pattern) ? 0 : 1)), // 0 user, 1 predefined
  PatternSpace: only(0, "PatternSpace"),
  PatternDouble: only(false, "PatternDouble"),
  HatchStyle: only(0, "HatchStyle"),
  HatchObjectType: only(0, "HatchObjectType"),
  AssociativeHatch: only(false, "AssociativeHatch"),
  ISOPenWidth: only(0, "ISOPenWidth"),
  Elevation: only(0, "Elevation"),
  Origin: only([0, 0], "Origin"),
  NumberOfLoops: ro(() => 1),
  BackgroundColor: { get: () => ({ t: "color", aci: 0 }) as unknown as Obj, limited: true },
  GradientName: only("", "GradientName"),
  GradientAngle: only(0, "GradientAngle"),
  GradientCentered: only(false, "GradientCentered"),
  GradientColor1: { get: () => ({ t: "color", aci: 0 }) as unknown as Obj, limited: true },
  GradientColor2: { get: () => ({ t: "color", aci: 0 }) as unknown as Obj, limited: true },
  Area: ro((c) => { const b = hatch(c).g.boundary; let s = 0; for (let i = 0; i < b.length; i++) { const [p, q] = [b[i], b[(i + 1) % b.length]]; s += p[0] * q[1] - q[0] * p[1]; } return Math.abs(s) / 2; }),
  SetPattern: { call: (c, args) => { const { e, g } = hatch(c); c.m.set(e.handle, { geom: { ...g, pattern: String(args[1] ?? g.pattern) } }); return null; } },
  GetLoopAt: { call: () => ({ out: { 1: [] }, ret: null }), limited: true },
  AppendOuterLoop: { call: (c) => c.err("AppendOuterLoop: 경계 객체 대신 해치를 새로 만드세요 (AX-CAD 해치는 경계 하나)"), limited: true },
  AppendInnerLoop: { call: (c) => c.err("AppendInnerLoop: AX-CAD 해치는 경계 하나입니다"), limited: true },
  InsertLoopAt: { call: (c) => c.err("InsertLoopAt: AX-CAD 해치는 경계 하나입니다"), limited: true },
  Evaluate: { call: () => null },
};
const vertsOf = (c: Ctx) => {
  const e = ent(c);
  if (e.geom.type !== "LEADER" && e.geom.type !== "MULTILEADER") return c.err("지시선이 아닙니다");
  return { e, g: e.geom };
};
const LEADER: Members = {
  ...ENTITY,
  ...PLANAR,
  Coordinates: { get: (c) => vertsOf(c).g.vertices.flatMap((p) => [p[0], p[1], 0]), put: (c, v) => {
    const { e, g } = vertsOf(c);
    const f = (Array.isArray(v) ? v : []).map((x) => num(c, x as OVal, "Coordinates"));
    if (f.length < 6 || f.length % 3) c.err("Coordinates: x, y, z 점이 2개 이상 필요합니다");
    c.m.set(e.handle, { geom: { ...g, vertices: Array.from({ length: f.length / 3 }, (_, i) => [f[3 * i], f[3 * i + 1]] as Pt) } });
  } },
  Coordinate: { call: (c, args) => { const { g } = vertsOf(c); return p3(g.vertices[vertex(c, args[0], g.vertices.length)]); } },
  Type: only(0, "Type"),
  ArrowheadSize: only(2.5, "ArrowheadSize"),
  ArrowheadType: only(0, "ArrowheadType"),
  ArrowheadBlock: only("", "ArrowheadBlock"),
  Annotation: ro(() => null),
  DimensionLineColor: { get: (c) => ent(c).aci ?? 256, limited: true },
  DimensionLineWeight: { get: (c) => ent(c).lwt ?? -1, limited: true },
  ScaleFactor: only(1, "ScaleFactor"),
  StyleName: only("Standard", "StyleName"),
  TextGap: only(0.625, "TextGap"),
  VerticalTextPosition: only(0, "VerticalTextPosition"),
  Evaluate: { call: () => null },
};
const MLEADER: Members = {
  ...LEADER,
  TextString: { get: (c) => (vertsOf(c).g as Extract<Geom, { type: "MULTILEADER" }>).value, put: (c, v) => { const { e, g } = vertsOf(c); c.m.set(e.handle, { geom: { ...(g as Extract<Geom, { type: "MULTILEADER" }>), value: String(v) } }); } },
  TextHeight: { get: (c) => (vertsOf(c).g as Extract<Geom, { type: "MULTILEADER" }>).height, put: (c, v) => { const { e, g } = vertsOf(c); const h = num(c, v, "TextHeight"); if (!(h > 0)) c.err("TextHeight: 0보다 커야 합니다"); c.m.set(e.handle, { geom: { ...(g as Extract<Geom, { type: "MULTILEADER" }>), height: h } }); } },
  LeaderCount: ro(() => 1),
  GetLeaderLineVertices: { call: (c) => vertsOf(c).g.vertices.flatMap((p) => [p[0], p[1], 0]) },
  SetLeaderLineVertices: { call: (c, args) => { const { e, g } = vertsOf(c); const f = (Array.isArray(args[1]) ? args[1] : []).map((x) => num(c, x as OVal, "SetLeaderLineVertices")); if (f.length < 6 || f.length % 3) c.err("SetLeaderLineVertices: 점이 2개 이상 필요합니다"); c.m.set(e.handle, { geom: { ...g, vertices: Array.from({ length: f.length / 3 }, (_, i) => [f[3 * i], f[3 * i + 1]] as Pt) } }); return null; } },
  GetVertexCount: { call: (c) => vertsOf(c).g.vertices.length },
  GetLeaderIndex: { call: () => 0 },
  GetLeaderLineIndexes: { call: () => [0] },
  ContentType: only(2, "ContentType"), // acMTextContent
  TextStyleName: only("Standard", "TextStyleName"),
  TextRotation: only(0, "TextRotation"),
  TextWidth: only(0, "TextWidth"),
  TextJustify: only(1, "TextJustify"),
  TextDirection: only(1, "TextDirection"),
  TextFrameDisplay: only(false, "TextFrameDisplay"),
  TextBackgroundFill: only(false, "TextBackgroundFill"),
  TextLineSpacingFactor: only(1, "TextLineSpacingFactor"),
  TextLineSpacingStyle: only(1, "TextLineSpacingStyle"),
  TextLineSpacingDistance: only(0, "TextLineSpacingDistance"),
  TextLeftAttachmentType: only(1, "TextLeftAttachmentType"),
  TextRightAttachmentType: only(1, "TextRightAttachmentType"),
  TextTopAttachmentType: only(0, "TextTopAttachmentType"),
  TextBottomAttachmentType: only(0, "TextBottomAttachmentType"),
  TextAttachmentDirection: only(0, "TextAttachmentDirection"),
  DogLegged: only(true, "DogLegged"),
  DoglegLength: only(0, "DoglegLength"),
  LandingGap: only(0, "LandingGap"),
  LeaderLineColor: { get: (c) => ({ t: "color", aci: ent(c).aci ?? 256 }) as unknown as Obj, limited: true },
  LeaderLineType: only("ByBlock", "LeaderLineType"),
  LeaderLineWeight: only(-2, "LeaderLineWeight"),
  LeaderType: only(1, "LeaderType"),
  BlockConnectionType: only(0, "BlockConnectionType"),
  BlockScale: only(1, "BlockScale"),
  ContentBlockName: only("", "ContentBlockName"),
  ContentBlockType: only(0, "ContentBlockType"),
  GetDoglegDirection: { call: (c) => { const v = vertsOf(c).g.vertices; const [a, b] = [v[v.length - 2], v[v.length - 1]]; return [Math.sign(b[0] - a[0]) || 1, 0, 0]; } },
  SetDoglegDirection: { call: () => null, limited: true },
  AddLeader: { call: (c) => c.err("AddLeader: AX-CAD 멀티리더는 지시선 하나입니다"), limited: true },
  AddLeaderLine: { call: (c) => c.err("AddLeaderLine: AX-CAD 멀티리더는 지시선 하나입니다"), limited: true },
  AddLeaderLineEx: { call: (c) => c.err("AddLeaderLineEx: AX-CAD 멀티리더는 지시선 하나입니다"), limited: true },
  RemoveLeader: { call: (c) => c.err("RemoveLeader: 지시선 하나는 남아야 합니다"), limited: true },
  RemoveLeaderLine: { call: (c) => c.err("RemoveLeaderLine: 지시선 하나는 남아야 합니다"), limited: true },
  GetBlockAttributeValue: { call: () => "", limited: true },
  SetBlockAttributeValue: { call: (c) => c.err("SetBlockAttributeValue: 블록 내용의 멀티리더는 지원하지 않습니다"), limited: true },
};
const INSERT: Members = {
  ...ENTITY,
  ...L("Name", "EffectiveName", "InsertionPoint", "XScaleFactor", "YScaleFactor", "Rotation"),
  Normal: only([0, 0, 1], "Normal"),
  ZScaleFactor: only(1, "ZScaleFactor"),
  XEffectiveScaleFactor: ro((c) => (ent(c).geom as Extract<Geom, { type: "INSERT" }>).xscale),
  YEffectiveScaleFactor: ro((c) => (ent(c).geom as Extract<Geom, { type: "INSERT" }>).yscale),
  ZEffectiveScaleFactor: ro(() => 1),
  InsUnits: ro(() => "Millimeters"),
  InsUnitsFactor: ro(() => 1),
  IsDynamicBlock: ro(() => false),
  HasAttributes: ro((c) => Object.keys(ent(c).att ?? {}).length > 0),
  GetAttributes: { call: (c) => Object.keys(ent(c).att ?? {}).map((tag) => ({ t: "attref", handle: ent(c).handle, tag }) as unknown as Obj) },
  GetConstantAttributes: { call: () => [] },
  GetDynamicBlockProperties: { call: () => [] },
  ResetBlock: { call: () => null },
  ConvertToAnonymousBlock: { call: (c) => c.err("ConvertToAnonymousBlock: 익명 블록은 만들지 않습니다"), limited: true },
  ConvertToStaticBlock: { call: (c) => c.err("ConvertToStaticBlock: 동적 블록이 아닙니다"), limited: true },
  Explode: { call: (c) => c.err("Explode: 블록 내용은 서버가 그리므로 화면에서 분해하지 않습니다 (EXPLODE 명령을 쓰세요)"), limited: true },
};

// --- dimensions: their own dimension variables (server overrides, mm) ---------------------------

type DimGeom = Geom & { overrides?: Record<string, number | string>; text?: string };
const dim = (c: Ctx) => {
  const e = ent(c);
  if (!e.geom.type.startsWith("DIM_")) return c.err("치수가 아닙니다");
  return { e, g: e.geom as DimGeom };
};
const ovr = (c: Ctx, k: string, fallback: number | string) => dim(c).g.overrides?.[k] ?? fallback;
const setOvr = (c: Ctx, k: string, v: number | string) => {
  const { e, g } = dim(c);
  c.m.set(e.handle, { geom: { ...g, overrides: { ...(g.overrides ?? {}), [k]: v } } as Geom });
};
/** A dimension variable as an ActiveX property: plain number, flag (boolean) or text. */
const dv = (k: string, fallback: number | string | boolean, kind: "num" | "bool" | "str" = typeof fallback === "boolean" ? "bool" : typeof fallback === "string" ? "str" : "num"): Member => ({
  get: (c) => {
    const v = ovr(c, k, typeof fallback === "boolean" ? Number(fallback) : fallback);
    return kind === "bool" ? Boolean(v) : v;
  },
  put: (c, v) => setOvr(c, k, kind === "bool" ? (v ? 1 : 0) : kind === "str" ? String(v) : num(c, v, k)),
});
/** Suppression bits of a dimzin-like variable (leading 4, trailing 8, zero feet 1/3, zero inches 2/3). */
const bit = (k: string, mask: number): Member => ({
  get: (c) => (Number(ovr(c, k, 0)) & mask) !== 0,
  put: (c, v) => setOvr(c, k, v ? Number(ovr(c, k, 0)) | mask : Number(ovr(c, k, 0)) & ~mask),
});
const ARROWS = ["CLOSEDFILLED", "CLOSEDBLANK", "CLOSED", "DOT", "ARCHTICK", "OBLIQUE", "OPEN", "ORIGIN", "ORIGIN2", "OPEN90", "OPEN30", "DOTSMALL", "DOTBLANK", "SMALL", "BOXBLANK", "BOXFILLED", "DATUMBLANK", "DATUMFILLED", "INTEGRAL", "NONE"];
const arrowType = (k: string): Member => ({
  get: (c) => Math.max(0, ARROWS.indexOf(String(ovr(c, k, "")).toUpperCase() || "CLOSEDFILLED")),
  put: (c, v) => {
    const i = Math.trunc(num(c, v, k));
    if (i < 0 || i >= ARROWS.length) c.err(`${k}: 화살촉 종류는 0~${ARROWS.length - 1}입니다`);
    setOvr(c, k, i === 0 ? "" : ARROWS[i]);
  },
});
const DIM_COMMON: Members = {
  ...ENTITY,
  Normal: only([0, 0, 1], "Normal"),
  ArrowheadSize: dv("dimasz", 2.5),
  TextHeight: dv("dimtxt", 2.5),
  TextGap: dv("dimgap", 0.625),
  TextColor: dv("dimclrt", 0),
  DimensionLineColor: dv("dimclrd", 0),
  DimensionLineWeight: dv("dimlwd", -2),
  DimensionLinetype: dv("dimltype", "ByBlock", "str"),
  TextStyle: dv("dimtxsty", "Standard", "str"),
  PrimaryUnitsPrecision: dv("dimdec", 2),
  UnitsFormat: dv("dimlunit", 2),
  LinearScaleFactor: dv("dimlfac", 1),
  RoundDistance: dv("dimrnd", 0),
  DecimalSeparator: { get: (c) => String.fromCharCode(Number(ovr(c, "dimdsep", 46))), put: (c, v) => setOvr(c, "dimdsep", String(v).charCodeAt(0) || 46) },
  FractionFormat: dv("dimfrac", 0),
  SuppressLeadingZeros: bit("dimzin", 4),
  SuppressTrailingZeros: bit("dimzin", 8),
  SuppressZeroFeet: bit("dimzin", 1),
  SuppressZeroInches: bit("dimzin", 2),
  TextPrefix: { get: (c) => String(ovr(c, "dimpost", "<>")).split("<>")[0] ?? "", put: (c, v) => setOvr(c, "dimpost", `${String(v)}<>${String(ovr(c, "dimpost", "<>")).split("<>")[1] ?? ""}`) },
  TextSuffix: { get: (c) => String(ovr(c, "dimpost", "<>")).split("<>")[1] ?? "", put: (c, v) => setOvr(c, "dimpost", `${String(ovr(c, "dimpost", "<>")).split("<>")[0] ?? ""}<>${String(v)}`) },
  TextOverride: { get: (c) => dim(c).g.text ?? "", put: (c, v) => { const { e, g } = dim(c); c.m.set(e.handle, { geom: { ...g, text: String(v) || "<>" } as Geom }); } },
  TextInside: dv("dimtix", false),
  TextInsideAlign: dv("dimtih", true),
  TextOutsideAlign: dv("dimtoh", true),
  TextMovement: dv("dimtmove", 0),
  TextFill: dv("dimtfill", false),
  TextFillColor: dv("dimtfillclr", 0),
  ForceLineInside: dv("dimtofl", false),
  Fit: dv("dimatfit", 3),
  VerticalTextPosition: dv("dimtad", 0),
  ToleranceDisplay: { get: (c) => (Number(ovr(c, "dimlim", 0)) ? 3 : Number(ovr(c, "dimtol", 0)) ? 1 : 0), put: (c, v) => { const n = num(c, v, "ToleranceDisplay"); setOvr(c, "dimtol", n === 1 || n === 2 ? 1 : 0); setOvr(c, "dimlim", n === 3 ? 1 : 0); } },
  ToleranceUpperLimit: dv("dimtp", 0),
  ToleranceLowerLimit: dv("dimtm", 0),
  TolerancePrecision: dv("dimtdec", 2),
  ToleranceHeightScale: dv("dimtfac", 1),
  ToleranceJustification: dv("dimtolj", 1),
  ToleranceSuppressLeadingZeros: bit("dimtzin", 4),
  ToleranceSuppressTrailingZeros: bit("dimtzin", 8),
  ToleranceSuppressZeroFeet: bit("dimtzin", 1),
  ToleranceSuppressZeroInches: bit("dimtzin", 2),
  TextRotation: only(0, "TextRotation"),
  TextPosition: { get: (c) => { const pts = pathsOf(dim(c).e).flat(); if (!pts.length) return [0, 0, 0]; const [x0, , x1, y1] = bounds(pts); return p3([(x0 + x1) / 2, y1]); }, limited: true },
  Measurement: ro((c) => {
    const g = dim(c).g;
    if (g.type === "DIM_LINEAR") return Math.abs((g.p2[0] - g.p1[0]) * Math.cos(g.angle * RAD) + (g.p2[1] - g.p1[1]) * Math.sin(g.angle * RAD));
    if (g.type === "DIM_ALIGNED") return Math.hypot(g.p2[0] - g.p1[0], g.p2[1] - g.p1[1]);
    if (g.type === "DIM_RADIUS") return g.radius;
    if (g.type === "DIM_DIAMETER") return 2 * g.radius;
    if (g.type === "DIM_ANGULAR") {
      const a1 = Math.atan2(g.p1[1] - g.center[1], g.p1[0] - g.center[0]);
      const a2 = Math.atan2(g.p2[1] - g.center[1], g.p2[0] - g.center[0]);
      return (((a2 - a1) % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
    }
    return 0;
  }),
  StyleName: only("Standard", "StyleName"),
  ScaleFactor: dv("dimscale", 1),
  DimTxtDirection: only(false, "DimTxtDirection"),
  DimConstrName: only("", "DimConstrName"),
  DimConstrExpression: only("", "DimConstrExpression"),
  DimConstrValue: only("", "DimConstrValue"),
  DimConstrDesc: only("", "DimConstrDesc"),
  DimConstrForm: only(false, "DimConstrForm"),
  DimConstrReference: only(false, "DimConstrReference"),
};
const ALT: Members = {
  AltUnits: dv("dimalt", false),
  AltUnitsScale: dv("dimaltf", 25.4),
  AltUnitsPrecision: dv("dimaltd", 2),
  AltUnitsFormat: dv("dimaltu", 2),
  AltRoundDistance: dv("dimaltrnd", 0),
  AltTextPrefix: { get: (c) => String(ovr(c, "dimapost", "")).split("[]")[0] ?? "", put: (c, v) => setOvr(c, "dimapost", `${String(v)}[]${String(ovr(c, "dimapost", "")).split("[]")[1] ?? ""}`) },
  AltTextSuffix: { get: (c) => String(ovr(c, "dimapost", "")).split("[]")[1] ?? "", put: (c, v) => setOvr(c, "dimapost", `${String(ovr(c, "dimapost", "")).split("[]")[0] ?? ""}[]${String(v)}`) },
  AltSuppressLeadingZeros: bit("dimaltz", 4),
  AltSuppressTrailingZeros: bit("dimaltz", 8),
  AltSuppressZeroFeet: bit("dimaltz", 1),
  AltSuppressZeroInches: bit("dimaltz", 2),
  AltTolerancePrecision: dv("dimalttd", 2),
  AltToleranceSuppressLeadingZeros: bit("dimalttz", 4),
  AltToleranceSuppressTrailingZeros: bit("dimalttz", 8),
  AltToleranceSuppressZeroFeet: bit("dimalttz", 1),
  AltToleranceSuppressZeroInches: bit("dimalttz", 2),
};
const EXT: Members = {
  ExtensionLineColor: dv("dimclre", 0),
  ExtensionLineExtend: dv("dimexe", 1.25),
  ExtensionLineOffset: dv("dimexo", 0.625),
  ExtensionLineWeight: dv("dimlwe", -2),
  ExtLine1Linetype: dv("dimltex1", "ByBlock", "str"),
  ExtLine2Linetype: dv("dimltex2", "ByBlock", "str"),
  ExtLine1Suppress: dv("dimse1", false),
  ExtLine2Suppress: dv("dimse2", false),
  ExtLineFixedLen: dv("dimfxl", 1),
  ExtLineFixedLenSuppress: dv("dimfxlon", false),
  DimLine1Suppress: dv("dimsd1", false),
  DimLine2Suppress: dv("dimsd2", false),
  DimLineInside: dv("dimsoxd", false),
  DimensionLineExtend: dv("dimdle", 0),
  Arrowhead1Type: arrowType("dimblk1"),
  Arrowhead2Type: arrowType("dimblk2"),
  Arrowhead1Block: dv("dimblk1", "", "str"),
  Arrowhead2Block: dv("dimblk2", "", "str"),
  HorizontalTextPosition: dv("dimjust", 0),
};
const linearPoints = (k: "p1" | "p2"): Member => ({
  get: (c) => p3((dim(c).g as Extract<Geom, { type: "DIM_LINEAR" | "DIM_ALIGNED" }>)[k]),
  put: (c, v) => { const { e, g } = dim(c); c.m.set(e.handle, { geom: { ...g, [k]: pt(c, v, k) } as Geom }); },
});
const DIM_ALIGNED: Members = { ...DIM_COMMON, ...ALT, ...EXT, ExtLine1Point: linearPoints("p1"), ExtLine2Point: linearPoints("p2"), ExtLineFixedLenSupress: dv("dimfxlon", false) };
const DIM_ROTATED: Members = {
  ...DIM_COMMON,
  ...ALT,
  ...EXT,
  Rotation: { get: (c) => (dim(c).g as Extract<Geom, { type: "DIM_LINEAR" }>).angle * RAD, put: (c, v) => { const { e, g } = dim(c); c.m.set(e.handle, { geom: { ...g, angle: num(c, v, "Rotation") / RAD } as Geom }); } },
};
const radial = (c: Ctx) => dim(c).g as Extract<Geom, { type: "DIM_RADIUS" | "DIM_DIAMETER" }>;
const RADIAL_COMMON: Members = {
  ...DIM_COMMON,
  ...ALT,
  CenterMarkSize: { get: (c) => Math.abs(Number(ovr(c, "dimcen", 2.5))), put: (c, v) => setOvr(c, "dimcen", Math.sign(Number(ovr(c, "dimcen", 2.5)) || 1) * Math.abs(num(c, v, "CenterMarkSize"))) },
  CenterType: { get: (c) => { const v = Number(ovr(c, "dimcen", 2.5)); return v > 0 ? 0 : v < 0 ? 1 : 2; }, put: (c, v) => { const n = num(c, v, "CenterType"); const size = Math.abs(Number(ovr(c, "dimcen", 2.5))) || 2.5; setOvr(c, "dimcen", n === 2 ? 0 : n === 1 ? -size : size); } },
  LeaderLength: { get: (c) => radial(c).radius, limited: true },
};
const DIM_RADIAL: Members = { ...RADIAL_COMMON, ArrowheadType: arrowType("dimblk"), ArrowheadBlock: dv("dimblk", "", "str"), DimLineSuppress: dv("dimsd2", false) };
const DIM_DIAMETRIC: Members = { ...RADIAL_COMMON, Arrowhead1Type: arrowType("dimblk1"), Arrowhead2Type: arrowType("dimblk2"), Arrowhead1Block: dv("dimblk1", "", "str"), Arrowhead2Block: dv("dimblk2", "", "str"), DimLine1Suppress: dv("dimsd1", false), DimLine2Suppress: dv("dimsd2", false) };
const angPoint = (k: "p1" | "p2" | "center"): Member => ({
  get: (c) => p3((dim(c).g as Extract<Geom, { type: "DIM_ANGULAR" }>)[k]),
  put: (c, v) => { const { e, g } = dim(c); c.m.set(e.handle, { geom: { ...g, [k]: pt(c, v, k) } as Geom }); },
});
const DIM_ANGULAR: Members = {
  ...DIM_COMMON,
  ...EXT,
  AngleFormat: dv("dimaunit", 0),
  TextPrecision: dv("dimadec", 0),
  ExtLine1StartPoint: angPoint("center"),
  ExtLine2StartPoint: angPoint("center"),
  ExtLine1EndPoint: angPoint("p1"),
  ExtLine2EndPoint: angPoint("p2"),
};

// --- records, collections, document, application ----------------------------------------------

const RECORD: Members = { ...OBJECT, ...L("Name") };
const COLLECTION: Members = { ...OBJECT, ...L("Count", "Item") };
const LAYER: Members = {
  ...RECORD,
  ...L("Color", "Lock", "Linetype", "Lineweight", "Plottable", "LayerOn", "Freeze", "Description"),
  TrueColor: { get: (c) => ({ t: "color", aci: (c.o.t === "layer" && c.m.layer(c.o.name)?.color) || 7 }) as unknown as Obj, put: (c, v) => { const col = v as unknown as { t?: string; aci?: number }; if (col?.t !== "color" || !col.aci) c.err("TrueColor: 색 번호가 있는 색 객체가 필요합니다"); if (c.o.t === "layer") c.m.layerSet(c.o.name, { color: col.aci }); } },
  Material: only("Global", "Material"),
  PlotStyleName: only("Normal", "PlotStyleName"),
  ViewportDefault: only(false, "ViewportDefault"),
  Used: ro((c) => c.o.t === "layer" && c.m.all().some((e) => e.layer.toUpperCase() === (c.o as { name: string }).name.toUpperCase())),
};
const LAYERS: Members = { ...COLLECTION, Add: legacy, GenerateUsageData: { call: () => null } };
const LINETYPE: Members = { ...RECORD, ...L("Description") };
const TEXTSTYLE: Members = {
  ...RECORD,
  ...L("FontFile", "Height", "Width", "ObliqueAngle"),
  BigFontFile: only("", "BigFontFile"),
  LastHeight: ro((c) => (c.o.t === "rec" ? (c.m.tableFind("textstyle", c.o.name)?.height ?? 0) || 2.5 : 2.5)),
  TextGenerationFlag: only(0, "TextGenerationFlag"),
  GetFont: { call: (c) => ({ out: { 0: c.o.t === "rec" ? (c.m.tableFind("textstyle", c.o.name)?.font ?? "").replace(/\.[^.]*$/, "") : "", 1: false, 2: false, 3: 0, 4: 0 }, ret: null }) },
  SetFont: { call: (c, args) => { if (c.o.t !== "rec") return null; const face = String(args[0] ?? ""); if (!/^[\w .\-가-힣]+$/.test(face)) c.err("SetFont: 글꼴 이름이 필요합니다"); c.m.tableSet("textstyle", c.o.name, { font: `${face}.ttf` }); return null; } },
};
const DIMSTYLE: Members = {
  ...RECORD,
  CopyFrom: { call: (c) => c.err("CopyFrom: 치수 스타일 복사는 DIMSTYLE 대화상자를 쓰세요"), limited: true },
};
const TABLE_COLLECTION: Members = { ...COLLECTION, Add: legacy };
const LAYOUT: Members = {
  ...RECORD,
  ModelType: ro((c) => c.o.t === "layout" && /^model$/i.test(c.o.name)),
  TabOrder: ro((c) => (c.o.t === "layout" ? ["Model", ...c.m.tables.layouts].findIndex((n) => n === (c.o as { name: string }).name) : 0)),
  Block: ro((c) => (c.o.t === "layout" && /^model$/i.test(c.o.name) ? { t: "space" } : null)),
  CanonicalMediaName: only("ISO_A3_(420.00_x_297.00_MM)", "CanonicalMediaName"),
  CenterPlot: only(true, "CenterPlot"),
  ConfigName: only("None", "ConfigName"),
  PaperUnits: only(1, "PaperUnits"), // millimeters
  PlotHidden: only(false, "PlotHidden"),
  PlotOrigin: only([0, 0], "PlotOrigin"),
  PlotRotation: only(0, "PlotRotation"),
  PlotType: only(1, "PlotType"),
  PlotViewportBorders: only(false, "PlotViewportBorders"),
  PlotViewportsFirst: only(true, "PlotViewportsFirst"),
  PlotWithLineweights: only(true, "PlotWithLineweights"),
  PlotWithPlotStyles: only(false, "PlotWithPlotStyles"),
  ScaleLineweights: only(false, "ScaleLineweights"),
  ShowPlotStyles: only(false, "ShowPlotStyles"),
  StandardScale: only(0, "StandardScale"),
  StyleSheet: only("", "StyleSheet"),
  UseStandardScale: only(true, "UseStandardScale"),
  ViewToPlot: only("", "ViewToPlot"),
  GetPaperSize: { call: () => ({ out: { 0: 420, 1: 297 }, ret: null }), limited: true },
  GetPaperMargins: { call: () => ({ out: { 0: [0, 0], 1: [0, 0] }, ret: null }), limited: true },
  GetCustomScale: { call: () => ({ out: { 0: 1, 1: 1 }, ret: null }), limited: true },
  SetCustomScale: { call: (c) => c.err("SetCustomScale: 출력 설정은 PLOT 대화상자에서 정합니다"), limited: true },
  GetCanonicalMediaNames: { call: () => ["ISO_A4_(297.00_x_210.00_MM)", "ISO_A3_(420.00_x_297.00_MM)", "ISO_A2_(594.00_x_420.00_MM)", "ISO_A1_(841.00_x_594.00_MM)", "ISO_A0_(1189.00_x_841.00_MM)"] },
  GetLocaleMediaName: { call: (_c, args) => String(args[0] ?? "").replace(/^ISO_(A\d).*$/, "$1") },
  GetPlotDeviceNames: { call: () => ["AX-CAD PDF"] },
  GetPlotStyleTableNames: { call: () => [] },
  GetWindowToPlot: { call: () => ({ out: { 0: [0, 0], 1: [0, 0] }, ret: null }), limited: true },
  SetWindowToPlot: { call: (c) => c.err("SetWindowToPlot: 출력 범위는 PLOT 대화상자에서 정합니다"), limited: true },
  RefreshPlotDeviceInfo: { call: () => null },
  CopyFrom: { call: (c) => c.err("CopyFrom: 출력 설정 복사는 지원하지 않습니다"), limited: true },
};
const DOCUMENT: Members = {
  ...L("ModelSpace", "Layers", "ActiveLayer", "Application", "Linetypes", "TextStyles", "DimStyles", "RegisteredApplications", "Layouts"),
  ActiveLinetype: { get: (c) => ({ t: "rec", tk: "linetype", name: c.m.tableFind("linetype", "ByLayer") ? "ByLayer" : (c.m.table("linetype")[0]?.name ?? "ByLayer") }) as Obj, limited: true },
  ActiveTextStyle: { get: (c) => ({ t: "rec", tk: "textstyle", name: c.m.table("textstyle")[0]?.name ?? "Standard" }) as Obj, limited: true },
  ActiveDimStyle: { get: (c) => ({ t: "rec", tk: "dimstyle", name: c.m.table("dimstyle")[0]?.name ?? "Standard" }) as Obj, limited: true },
  ActiveLayout: ro(() => ({ t: "layout", name: "Model" })),
  ActiveSpace: ro(() => 1), // acModelSpace
  MSpace: only(true, "MSpace"),
  Name: ro(() => "Drawing.dwg"),
  FullName: ro(() => ""),
  Path: ro(() => ""),
  ReadOnly: ro(() => false),
  Saved: ro((c) => { const ch = c.m.changes(); return !ch.created.length && !ch.updated.length && !ch.deleted.length; }),
  Active: ro(() => true),
  ElevationModelSpace: only(0, "ElevationModelSpace"),
  ElevationPaperSpace: only(0, "ElevationPaperSpace"),
  Limits: ro(() => [0, 0, 420, 297]),
  ObjectSnapMode: only(false, "ObjectSnapMode"),
  WindowTitle: ro(() => "AX-CAD"),
  HandleToObject: { call: (c, args) => { const h = String(args[0] ?? "").toUpperCase(); return c.m.get(h) ? { t: "ent", handle: h } : c.err(`HandleToObject: 없는 핸들 ${h}`); } },
  ObjectIDToObject: { call: (c, args) => { const h = Math.trunc(num(c, args[0], "ObjectIDToObject")).toString(16).toUpperCase(); return c.m.get(h) ? { t: "ent", handle: h } : c.err("ObjectIDToObject: 없는 객체입니다"); } },
  StartUndoMark: { call: () => null }, // one run is one undo step on the screen
  EndUndoMark: { call: () => null },
  Regen: { call: () => null },
  AuditInfo: { call: () => null },
  PurgeAll: { call: (c) => c.err("PurgeAll: PURGE 명령을 쓰세요 (스크립트로 정리하지 않습니다)"), limited: true },
  GetVariable: { call: (c, args) => sysvar(c, String(args[0] ?? "")) },
  SetVariable: { call: (c, args) => { const n = String(args[0] ?? "").toUpperCase(); if (n === "CLAYER") { const l = c.m.layer(String(args[1])) ?? c.err(`SetVariable: 없는 레이어 ${String(args[1])}`); c.m.current = l.name; return null; } return c.err(`SetVariable: ${n}은(는) 스크립트에서 바꿀 수 없습니다`); } },
  Modified: { event: true },
  // W6
  Blocks: ro(() => ({ t: "blocks" })),
  Groups: ro(() => ({ t: "groups" })),
  Dictionaries: ro(() => ({ t: "dicts" })),
  SelectionSets: ro(() => ({ t: "ssets" })),
  ActiveSelectionSet: ro((c) => { if (!c.m.ssets.has("ACTIVE")) c.m.ssets.set("ACTIVE", []); return { t: "sset", name: "ACTIVE" }; }),
  PickfirstSelectionSet: ro((c) => { if (!c.m.ssets.has("PICKFIRST")) c.m.ssets.set("PICKFIRST", [...c.m.pickfirst]); return { t: "sset", name: "PICKFIRST" }; }),
  Utility: legacy, // vba.ts answers ThisDrawing.Utility
  Database: ro(() => ({ t: "doc" })), // AX-CAD's drawing is its database
  PaperSpace: { get: (c) => c.err("PaperSpace: 종이 공간 객체는 스크립트로 만들지 않습니다 (레이아웃은 Layouts)"), limited: true },
  ActivePViewport: { get: (c) => c.err("ActivePViewport: 뷰포트는 화면의 것입니다"), limited: true },
  ActiveViewport: { get: (c) => c.err("ActiveViewport: 뷰는 화면의 것입니다 (스크립트는 도면을 바꿉니다)"), limited: true },
  ActiveUCS: { get: (c) => c.err("ActiveUCS: AX-CAD 도면은 WCS 하나입니다"), limited: true },
  ActiveMaterial: only("ByLayer", "ActiveMaterial"),
  CopyObjects: {
    // CopyObjects(Objects[, Owner]): copies in the model space (another drawing is not open to a script)
    call: (c, args) => {
      if (args[1] !== undefined && args[1] !== null && (args[1] as Obj).t !== "space") c.err("CopyObjects: 모형 공간으로만 복사합니다");
      return handlesOf(c, args[0], "CopyObjects").map((h) => { const e = c.m.get(h) ?? c.err(`CopyObjects: 없는 객체 ${h}`); return copyOf(c, e, e.geom); });
    },
  },
  // W5: ThisDrawing_<Event> handlers of a VBA project (vba.ts fireVbaEvent; objects during a run, the
  // rest from a loaded project after a user command / around an AX-LISP run)
  BeginCommand: { event: true },
  EndCommand: { event: true },
  BeginLisp: { event: true },
  EndLisp: { event: true },
  LispCancelled: { event: true },
  ObjectAdded: { event: true },
  ObjectErased: { event: true },
  ObjectModified: { event: true },
  WBlock: { call: (c) => c.err("WBlock: 다른 파일로 쓰지 않습니다 (도면 내보내기를 쓰세요)"), limited: true },
};
function sysvar(c: Ctx, name: string): OVal {
  switch (name.toUpperCase()) {
    case "CLAYER":
      return c.m.current;
    case "INSUNITS":
      return 4; // millimeters
    case "LUNITS":
      return 2;
    case "ACADVER":
      return "AX-CAD 2.2";
    case "DWGNAME":
      return "Drawing.dwg";
    case "LTSCALE":
    case "DIMSCALE":
      return 1;
    case "TEXTSIZE":
      return 2.5;
    default:
      return c.err(`GetVariable: ${name}은(는) AX-CAD에 없는 시스템 변수입니다`);
  }
}
const APPLICATION: Members = {
  ...L("ActiveDocument", "Name"),
  Application: ro(() => ({ t: "app" })),
  Caption: ro(() => "AX-CAD"),
  FullName: ro(() => "AX-CAD"),
  Path: ro(() => ""),
  Version: ro(() => "2.2"),
  LocaleID: ro(() => 1042), // ko-KR
  Visible: only(true, "Visible"),
  ZoomAll: { call: () => null }, // the view is the screen's: a script changes the drawing, not the view
  ZoomExtents: { call: () => null },
  ZoomPrevious: { call: () => null },
  ZoomCenter: { call: () => null },
  ZoomScaled: { call: () => null },
  ZoomWindow: { call: () => null },
  ZoomPickWindow: { call: () => null },
  GetAcadState: { call: () => ({ t: "state" }) as unknown as Obj },
  Documents: ro(() => ({ t: "docs" })),
  Update: { call: () => null },
};
const MODELSPACE: Members = {
  ...COLLECTION,
  ...L("AddLine", "AddCircle", "AddArc", "AddLightWeightPolyline", "AddPolyline", "AddText", "AddMText", "AddPoint", "AddEllipse"),
  Name: ro(() => "*Model_Space"),
  IsLayout: ro(() => true),
  Layout: ro(() => ({ t: "layout", name: "Model" })),
  Origin: only([0, 0, 0], "Origin"),
  IsXRef: ro(() => false),
  IsDynamicBlock: ro(() => false),
  Explodable: ro(() => false),
  BlockScaling: ro(() => 0),
  Comments: only("", "Comments"),
  Units: ro(() => 4),
  AddSpline: { call: (c, args) => { const f = (Array.isArray(args[0]) ? args[0] : []).map((x) => num(c, x as OVal, "AddSpline")); if (f.length < 6 || f.length % 3) c.err("AddSpline: x, y, z 점이 2개 이상 필요합니다"); return { t: "ent", handle: c.m.add({ type: "SPLINE", fit: Array.from({ length: f.length / 3 }, (_, i) => [f[3 * i], f[3 * i + 1]] as Pt) }).handle }; } },
  AddLeader: { call: (c, args) => { const f = (Array.isArray(args[0]) ? args[0] : []).map((x) => num(c, x as OVal, "AddLeader")); if (f.length < 6 || f.length % 3) c.err("AddLeader: 점이 2개 이상 필요합니다"); return { t: "ent", handle: c.m.add({ type: "LEADER", vertices: Array.from({ length: f.length / 3 }, (_, i) => [f[3 * i], f[3 * i + 1]] as Pt) }).handle }; } },
  AddMLeader: { call: (c, args) => { const f = (Array.isArray(args[0]) ? args[0] : []).map((x) => num(c, x as OVal, "AddMLeader")); if (f.length < 6 || f.length % 3) c.err("AddMLeader: 점이 2개 이상 필요합니다"); const h = c.m.add({ type: "MULTILEADER", vertices: Array.from({ length: f.length / 3 }, (_, i) => [f[3 * i], f[3 * i + 1]] as Pt), value: "", height: 2.5 }).handle; return { out: { 1: 0 }, ret: { t: "ent", handle: h } }; } },
  AddDimAligned: { call: (c, args) => { const [p1, p2, at] = [0, 1, 2].map((i) => pt(c, args[i], "AddDimAligned")); const len = Math.hypot(p2[0] - p1[0], p2[1] - p1[1]) || c.err("AddDimAligned: 두 점이 같습니다"); const d = ((at[1] - p1[1]) * (p2[0] - p1[0]) - (at[0] - p1[0]) * (p2[1] - p1[1])) / len; return { t: "ent", handle: c.m.add({ type: "DIM_ALIGNED", p1, p2, distance: d || 1 }).handle }; } },
  AddDimRotated: { call: (c, args) => { const [p1, p2, base] = [0, 1, 2].map((i) => pt(c, args[i], "AddDimRotated")); return { t: "ent", handle: c.m.add({ type: "DIM_LINEAR", p1, p2, base, angle: num(c, args[3], "AddDimRotated") / RAD }).handle }; } },
  AddDimRadial: { call: (c, args) => { const [center, chord] = [pt(c, args[0], "AddDimRadial"), pt(c, args[1], "AddDimRadial")]; return { t: "ent", handle: c.m.add({ type: "DIM_RADIUS", center, radius: Math.hypot(chord[0] - center[0], chord[1] - center[1]), angle: Math.atan2(chord[1] - center[1], chord[0] - center[0]) / RAD }).handle }; } },
  AddDimDiametric: { call: (c, args) => { const [a, b] = [pt(c, args[0], "AddDimDiametric"), pt(c, args[1], "AddDimDiametric")]; const center: Pt = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]; return { t: "ent", handle: c.m.add({ type: "DIM_DIAMETER", center, radius: Math.hypot(b[0] - a[0], b[1] - a[1]) / 2, angle: Math.atan2(a[1] - center[1], a[0] - center[0]) / RAD }).handle }; } },
  AddDimAngular: { call: (c, args) => { const [center, p1, p2, base] = [0, 1, 2, 3].map((i) => pt(c, args[i], "AddDimAngular")); return { t: "ent", handle: c.m.add({ type: "DIM_ANGULAR", center, p1, p2, base }).handle }; } },
  AddDim3PointAngular: { call: (c, args) => { const [center, p1, p2, base] = [0, 1, 2, 3].map((i) => pt(c, args[i], "AddDim3PointAngular")); return { t: "ent", handle: c.m.add({ type: "DIM_ANGULAR", center, p1, p2, base }).handle }; } },
  AddHatch: { call: (c, args) => ({ t: "hatchdraft", pattern: String(args[1] ?? "SOLID") }) as unknown as Obj, limited: true },
  InsertBlock: { call: (c, args) => { if (!c.m.blockFind(String(args[1] ?? ""))) c.err(`InsertBlock: 도면에 없는 블록 ${String(args[1] ?? "")}`); const p = pt(c, args[0], "InsertBlock"); const [x, y] = [2, 3].map((i) => num(c, args[i] ?? 1, "InsertBlock")); return { t: "ent", handle: c.m.add({ type: "INSERT", name: String(args[1]), insert: p, xscale: x, yscale: y, rotation: num(c, args[5] ?? 0, "InsertBlock") / RAD }).handle }; } },
  AddAttribute: { call: (c) => c.err("AddAttribute: 속성 정의는 블록 안에 만듭니다 (Blocks.Add → Block.AddAttribute)"), limited: true },
  AddRay: { call: (c) => c.err("AddRay: 무한선은 AX-CAD 2D 도면 객체가 아닙니다 (선을 쓰세요)"), limited: true },
  AddXLine: { call: (c) => c.err("AddXLine: 무한선은 AX-CAD 2D 도면 객체가 아닙니다 (선을 쓰세요)"), limited: true },
};

// --- W6: blocks, attributes, groups, dictionaries, selection sets ------------------------------

const blockOf = (c: Ctx): string => (c.o.t === "block" ? (c.m.blockFind(c.o.name) ?? c.err(`없는 블록: ${c.o.name}`)) : c.err("블록이 아닙니다"));
const madeBlock = (c: Ctx, what: string) => c.m.newBlock(blockOf(c)) ?? c.err(`${what}: 이번 실행에서 만든 블록만 내용을 읽고 바꿀 수 있습니다 (기존 블록 내용은 서버가 가집니다 — BEDIT를 쓰세요)`);
/** The model as seen from a block made in this run: what ModelSpace.Add* makes goes into the block. */
const inBlock = (c: Ctx, what: string): DrawingModel => {
  const name = madeBlock(c, what).name;
  return new Proxy(c.m, {
    get(t, k) {
      if (k === "add") return (g: Geom, layer?: string, props?: { aci?: number; linetype?: string }) => t.blockAddEnt(name, g, layer ?? "0", props);
      const v = Reflect.get(t, k, t);
      return typeof v === "function" ? v.bind(t) : v;
    },
  });
};
const indexOrName = (c: Ctx, v: OVal, names: string[], what: string): number => {
  const i = typeof v === "number" ? Math.trunc(v) : names.findIndex((n) => n.toUpperCase() === String(v).toUpperCase());
  return i >= 0 && i < names.length ? i : c.err(`${what}: 없는 항목 ${String(v)}`);
};
const ATT_MODE = { invisible: 1, constant: 2, verify: 4, preset: 8 } as const;
const modeOf = (a: AttDefSpec) => (Object.keys(ATT_MODE) as (keyof typeof ATT_MODE)[]).reduce((n, k) => n | (a[k] ? ATT_MODE[k] : 0), 0);
const setMode = (a: AttDefSpec, mode: number) => {
  for (const [k, bit] of Object.entries(ATT_MODE)) a[k as keyof typeof ATT_MODE] = Boolean(mode & bit);
};
const ONE_LINE = /^[^\x00-\x1f\x7f-\x9f\u2028\u2029]*$/;
const line = (c: Ctx, v: OVal, what: string, max = 2049): string => {
  const t = String(v ?? "");
  return ONE_LINE.test(t) && t.length <= max ? t : c.err(`${what}: 한 줄 ${max}자 이내의 문자열이 필요합니다`);
};

/** Review L1: a block made in this run may not contain itself, directly or through other new blocks. */
const noCycle = (c: Ctx, inner: string) => {
  const outer = blockOf(c).toUpperCase();
  const seen = new Set<string>();
  const walk = (name: string): boolean => {
    const n = name.toUpperCase();
    if (n === outer) return true;
    if (seen.has(n)) return false;
    seen.add(n);
    return c.m.blockEntsOf(n).some((e) => e.geom.type === "INSERT" && walk(e.geom.name));
  };
  if (walk(inner)) c.err(`InsertBlock: 블록 ${blockOf(c)}이(가) 자기 자신을 품게 됩니다`);
};
const BLOCK_ADDERS = (): Members =>
  Object.fromEntries(
    Object.entries(MODELSPACE)
      .filter(([k]) => /^(Add|InsertBlock$)/.test(k))
      .map(([k, mem]) => [k, mem.limited ? mem : { call: (c: Ctx, args: OVal[]) => (k === "InsertBlock" && noCycle(c, String(args[1] ?? "")), invoke(inBlock(c, k), { t: "space" }, k, args)) }]),
  );
const BLOCK: Members = {
  ...OBJECT,
  ...BLOCK_ADDERS(),
  AddAttribute: {
    // AddAttribute(Height, Mode, Prompt, InsertionPoint, Tag, Value): block coordinates
    call: (c, args) => {
      const b = madeBlock(c, "AddAttribute");
      const p = pt(c, args[3], "AddAttribute");
      const a: AttDefSpec = { tag: line(c, args[4], "AddAttribute", 255), prompt: line(c, args[2], "AddAttribute", 255), default: line(c, args[5], "AddAttribute"), insert: p, height: num(c, args[0], "AddAttribute") };
      if (!(a.height > 0)) c.err("AddAttribute: 높이는 0보다 커야 합니다");
      setMode(a, Math.trunc(num(c, args[1] ?? 0, "AddAttribute")));
      try {
        c.m.blockAttDef(b.name, a);
      } catch (e) {
        if (e instanceof ModelError) c.err(e.message);
        throw e;
      }
      return { t: "attdef", block: b.name, tag: a.tag.toUpperCase() };
    },
  },
  Name: { get: (c) => blockOf(c), put: (c, v) => { c.m.blockRename(blockOf(c), line(c, v, "Name", 255)); (c.o as { name: string }).name = String(v); } },
  Count: ro((c) => c.m.blockEntsOf(madeBlock(c, "Count").name).length),
  Item: { call: (c, args) => { const es = c.m.blockEntsOf(madeBlock(c, "Item").name); return { t: "ent", handle: es[indexOrName(c, args[0] ?? -1, es.map((e) => e.handle), "Item")].handle }; } },
  Origin: { get: (c) => p3(c.m.newBlock(blockOf(c))?.base ?? [0, 0]), put: (c, v) => { madeBlock(c, "Origin").base = pt(c, v, "Origin"); } },
  Delete: { call: (c) => (c.m.blockDelete(blockOf(c)), null) },
  IsLayout: ro(() => false),
  IsXRef: ro(() => false), // AX-CAD lists no external references (plan §22.5)
  IsDynamicBlock: ro(() => false),
  Explodable: only(true, "Explodable"),
  BlockScaling: only(0, "BlockScaling"), // acAny
  Units: ro(() => 4),
  Comments: only("", "Comments"),
  Layout: ro(() => null),
  Path: ro(() => ""),
  XRefDatabase: { get: (c) => c.err("XRefDatabase: 외부 참조는 열지 않습니다 (plan §22.5)"), limited: true },
  Bind: { call: (c) => c.err("Bind: 외부 참조는 다루지 않습니다 (plan §22.5)"), limited: true },
  Detach: { call: (c) => c.err("Detach: 외부 참조는 다루지 않습니다 (plan §22.5)"), limited: true },
  Reload: { call: (c) => c.err("Reload: 외부 참조는 다루지 않습니다 (plan §22.5)"), limited: true },
  Unload: { call: (c) => c.err("Unload: 외부 참조는 다루지 않습니다 (plan §22.5)"), limited: true },
};
const BLOCKS: Members = {
  ...OBJECT,
  Count: ro((c) => c.m.blockNames().length),
  Item: { call: (c, args) => { const ns = c.m.blockNames(); return { t: "block", name: ns[indexOrName(c, args[0] ?? -1, ns, "Blocks.Item")] }; } },
  Add: {
    // Add(InsertionPoint, Name): the new block's base point, in world coordinates
    call: (c, args) => {
      const name = line(c, args[1], "Blocks.Add", 255);
      try {
        c.m.blockAdd(name, pt(c, args[0], "Blocks.Add"));
      } catch (e) {
        if (e instanceof ModelError) c.err(e.message);
        throw e;
      }
      return { t: "block", name };
    },
  },
};

// an attribute definition of a block made in this run (Attribute)
const attdef = (c: Ctx): AttDefSpec => {
  if (c.o.t !== "attdef") return c.err("속성 정의가 아닙니다");
  const { block, tag } = c.o;
  return c.m.newBlock(block)?.attdefs.find((a) => a.tag === tag) ?? c.err(`없는 속성 정의: ${tag}`);
};
const attFlag = (k: keyof typeof ATT_MODE): Member => ({ get: (c) => Boolean(attdef(c)[k]), put: (c, v) => { attdef(c)[k] = Boolean(v); } });
const TEXT_LIMITED: Members = {
  Alignment: only(0, "Alignment"),
  Backward: only(false, "Backward"),
  UpsideDown: only(false, "UpsideDown"),
  ObliqueAngle: only(0, "ObliqueAngle"),
  ScaleFactor: only(1, "ScaleFactor"),
  StyleName: only("Standard", "StyleName"),
  TextGenerationFlag: only(0, "TextGenerationFlag"),
  TextAlignmentPoint: only([0, 0, 0], "TextAlignmentPoint"),
  LockPosition: only(false, "LockPosition"),
  MTextAttribute: only(false, "MTextAttribute"),
  MTextAttributeContent: only("", "MTextAttributeContent"),
  MTextBoundaryWidth: only(0, "MTextBoundaryWidth"),
  MTextDrawingDirection: only(1, "MTextDrawingDirection"),
  FieldLength: only(0, "FieldLength"),
  Normal: only([0, 0, 1], "Normal"),
  Thickness: only(0, "Thickness"),
  Material: only("ByLayer", "Material"),
  PlotStyleName: only("ByLayer", "PlotStyleName"),
  EntityTransparency: only("ByLayer", "EntityTransparency"),
  Linetype: only("ByLayer", "Linetype"),
  LinetypeScale: only(1, "LinetypeScale"),
  Lineweight: only(-1, "Lineweight"),
  Hyperlinks: { get: (c) => c.err("Hyperlinks: 속성에는 하이퍼링크를 붙이지 않습니다"), limited: true },
  UpdateMTextAttribute: { call: () => null, limited: true },
  Highlight: { call: () => null },
  Update: { call: () => null },
  ...Object.fromEntries(["ArrayPolar", "ArrayRectangular", "Copy", "Mirror", "Mirror3D", "Rotate3D", "TransformBy", "IntersectWith"].map((k) => [k, { call: (c: Ctx) => c.err(`${k}: 속성은 블록·블록 참조와 함께 움직입니다`), limited: true } as Member])),
};
const ATTRIBUTE: Members = {
  ...OBJECT,
  ...TEXT_LIMITED,
  TagString: { get: (c) => attdef(c).tag, put: (c, v) => { const a = attdef(c); const t = line(c, v, "TagString", 255).toUpperCase(); if (!/^[^\s]+$/.test(t)) c.err("TagString: 공백 없는 태그가 필요합니다"); if (c.m.newBlock((c.o as { block: string }).block)!.attdefs.some((x) => x !== a && x.tag === t)) c.err(`TagString: 이미 있는 태그 ${t}`); a.tag = t; (c.o as { tag: string }).tag = t; } },
  PromptString: { get: (c) => attdef(c).prompt, put: (c, v) => { attdef(c).prompt = line(c, v, "PromptString", 255); } },
  TextString: { get: (c) => attdef(c).default, put: (c, v) => { attdef(c).default = line(c, v, "TextString"); } },
  Height: { get: (c) => attdef(c).height, put: (c, v) => { const h = num(c, v, "Height"); if (!(h > 0)) c.err("Height: 0보다 커야 합니다"); attdef(c).height = h; } },
  InsertionPoint: { get: (c) => p3(attdef(c).insert), put: (c, v) => { attdef(c).insert = pt(c, v, "InsertionPoint"); } },
  Rotation: { get: (c) => (attdef(c).rotation ?? 0) * RAD, put: (c, v) => { attdef(c).rotation = num(c, v, "Rotation") / RAD; } },
  Layer: { get: (c) => attdef(c).layer ?? "0", put: (c, v) => { const l = c.m.layer(String(v)) ?? c.err(`Layer: 없는 레이어 ${String(v)}`); attdef(c).layer = l.name; } },
  Invisible: attFlag("invisible"),
  Constant: attFlag("constant"),
  Verify: attFlag("verify"),
  Preset: attFlag("preset"),
  Mode: { get: (c) => modeOf(attdef(c)), put: (c, v) => setMode(attdef(c), Math.trunc(num(c, v, "Mode"))) },
  Visible: { get: (c) => !attdef(c).invisible, put: (c, v) => { attdef(c).invisible = !v; } },
  TrueColor: only(null, "TrueColor"),
  Move: { call: (c, args) => { const [p, q] = [pt(c, args[0], "Move"), pt(c, args[1], "Move")]; const a = attdef(c); a.insert = [a.insert[0] + q[0] - p[0], a.insert[1] + q[1] - p[1]]; return null; } },
  Rotate: { call: (c, args) => { const [b, ang] = [pt(c, args[0], "Rotate"), num(c, args[1], "Rotate")]; const a = attdef(c); const [dx, dy] = [a.insert[0] - b[0], a.insert[1] - b[1]]; a.insert = [b[0] + dx * Math.cos(ang) - dy * Math.sin(ang), b[1] + dx * Math.sin(ang) + dy * Math.cos(ang)]; a.rotation = (a.rotation ?? 0) + ang / RAD; return null; } },
  ScaleEntity: { call: (c, args) => { const [b, f] = [pt(c, args[0], "ScaleEntity"), num(c, args[1], "ScaleEntity")]; if (!(f > 0)) c.err("ScaleEntity: 배율은 0보다 커야 합니다"); const a = attdef(c); a.insert = [b[0] + (a.insert[0] - b[0]) * f, b[1] + (a.insert[1] - b[1]) * f]; a.height *= f; return null; } },
  GetBoundingBox: { call: (c) => { const a = attdef(c); return { out: { 0: p3(a.insert), 1: [a.insert[0] + a.default.length * a.height * 0.8, a.insert[1] + a.height, 0] }, ret: null }; }, limited: true },
  Delete: { call: (c) => { const o = c.o as { block: string; tag: string }; const b = c.m.newBlock(o.block)!; b.attdefs.splice(b.attdefs.indexOf(attdef(c)), 1); return null; } },
};

// an attribute of a block reference (AttributeReference): its value is the drawing's
const attref = (c: Ctx): { e: Ent; tag: string } => {
  if (c.o.t !== "attref") return c.err("속성 참조가 아닙니다");
  const e = c.m.get(c.o.handle) ?? c.err(`지워졌거나 없는 객체: ${c.o.handle}`);
  return e.att && Object.hasOwn(e.att, c.o.tag) ? { e, tag: c.o.tag } : c.err(`없는 속성: ${c.o.tag}`);
};
const placed = (what: string): Member => ({ get: (c) => c.err(`${what}: 속성의 위치·크기는 블록의 속성 정의가 정합니다 (서버가 그립니다)`), limited: true });
const ATTREF: Members = {
  ...OBJECT,
  ...TEXT_LIMITED,
  TagString: ro((c) => attref(c).tag),
  TextString: { get: (c) => attref(c).e.att![attref(c).tag], put: (c, v) => { const { e, tag } = attref(c); c.m.set(e.handle, { att: { ...e.att, [tag]: line(c, v, "TextString") } }); } },
  Invisible: only(false, "Invisible"),
  Constant: ro(() => false), // constant attributes have no reference
  Layer: ro((c) => attref(c).e.layer),
  Visible: ro((c) => !attref(c).e.inv),
  TrueColor: only(null, "TrueColor"),
  Height: placed("Height"),
  InsertionPoint: placed("InsertionPoint"),
  Rotation: placed("Rotation"),
  GetBoundingBox: { call: (c) => c.err("GetBoundingBox: 속성의 범위는 블록 참조의 GetBoundingBox를 쓰세요"), limited: true },
  ...Object.fromEntries(["Move", "Rotate", "ScaleEntity"].map((k) => [k, { call: (c: Ctx) => c.err(`${k}: 속성 참조는 블록 참조와 함께 움직입니다`), limited: true } as Member])),
  Delete: { call: (c) => c.err("Delete: 블록의 속성은 지울 수 없습니다 (블록을 재정의하세요)"), limited: true },
};

// groups
const groupOf = (c: Ctx): GroupInfo => (c.o.t === "group" ? (c.m.groupFind(c.o.name) ?? c.err(`없는 그룹: ${c.o.name}`)) : c.err("그룹이 아닙니다"));
const handlesOf = (c: Ctx, v: OVal, what: string): string[] => {
  const list = Array.isArray(v) ? v : [v];
  return list.map((x) => (x && typeof x === "object" && !Array.isArray(x) && (x as Obj).t === "ent" ? (x as { handle: string }).handle : c.err(`${what}: 엔티티 배열이 필요합니다`)));
};
const groupDo = (c: Ctx, op: Parameters<DrawingModel["groupOp"]>[0]) => {
  try {
    c.m.groupOp(op);
  } catch (e) {
    if (e instanceof ModelError) c.err(e.message);
    throw e;
  }
};
/** Group.Layer = x and the like set every member (AutoCAD's are write-only too). */
const toMembers = (prop: string): Member => ({
  put: (c, v) => {
    for (const h of groupOf(c).handles) if (c.m.get(h)) putProp(c.m, { t: "ent", handle: h }, prop, v);
  },
});
const GROUP: Members = {
  ...OBJECT,
  Name: { get: (c) => groupOf(c).name, put: (c, v) => { const n = line(c, v, "Name", 255); groupDo(c, { op: "modify", name: groupOf(c).name, new_name: n }); (c.o as { name: string }).name = n; } },
  Count: ro((c) => groupOf(c).handles.length),
  Item: { call: (c, args) => { const hs = groupOf(c).handles; return { t: "ent", handle: hs[indexOrName(c, args[0] ?? -1, hs, "Item")] }; } },
  AppendItems: { call: (c, args) => { const g = groupOf(c); const add = handlesOf(c, args[0], "AppendItems").filter((h) => !g.handles.includes(h)); groupDo(c, { op: "modify", name: g.name, handles: [...g.handles, ...add] }); return null; } },
  RemoveItems: { call: (c, args) => { const g = groupOf(c); const rm = new Set(handlesOf(c, args[0], "RemoveItems")); groupDo(c, { op: "modify", name: g.name, handles: g.handles.filter((h) => !rm.has(h)) }); return null; } },
  Delete: { call: (c) => (groupDo(c, { op: "delete", name: groupOf(c).name }), null) },
  Highlight: { call: () => null },
  Update: { call: () => null },
  Layer: { put: (c, v) => { for (const h of groupOf(c).handles) if (c.m.get(h)) c.m.set(h, { layer: String(v) }); } },
  Linetype: { put: (c, v) => { for (const h of groupOf(c).handles) if (c.m.get(h)) c.m.set(h, { linetype: String(v) }); } },
  LinetypeScale: toMembers("LinetypeScale"),
  Lineweight: toMembers("Lineweight"),
  Visible: toMembers("Visible"),
  TrueColor: toMembers("TrueColor"),
  Material: { put: (c, v) => { if (String(v).toUpperCase() !== "BYLAYER") c.err("Material: AX-CAD 2D 도면에서는 \"ByLayer\"만 쓸 수 있습니다"); }, limited: true },
  PlotStyleName: { put: (c, v) => { if (String(v).toUpperCase() !== "BYLAYER") c.err("PlotStyleName: AX-CAD 2D 도면에서는 \"ByLayer\"만 쓸 수 있습니다"); }, limited: true },
};
const GROUPS: Members = {
  ...OBJECT,
  Count: ro((c) => c.m.groups().length),
  Item: { call: (c, args) => { const ns = c.m.groups().map((g) => g.name); return { t: "group", name: ns[indexOrName(c, args[0] ?? -1, ns, "Groups.Item")] }; } },
  Add: { call: (c, args) => { const name = line(c, args[0], "Groups.Add", 255); groupDo(c, { op: "create", name, handles: [] }); return { t: "group", name }; } },
};

// dictionaries (the named object dictionary's user entries, and entities' extension dictionaries)
type DictObj = { owner?: string; path: string[] };
const dictObj = (c: Ctx): DictObj => (c.o.t === "dict" || c.o.t === "xrec" ? c.o : c.o.t === "dicts" ? { path: [] } : c.err("사전이 아닙니다"));
const dictNow = (c: Ctx, d: DictObj) => {
  const v = c.m.dictView(d.owner, d.path);
  if (v === undefined) return c.err("이 사전은 도면과 함께 오지 않았습니다 (항목이 너무 많음) — 서버 조회 API를 쓰세요");
  return v ?? c.err(`없는 사전 항목: ${d.path.join("/")}`);
};
const dictTry = <T>(c: Ctx, f: () => T): T => {
  try {
    return f();
  } catch (e) {
    if (e instanceof ModelError) c.err(e.message);
    throw e;
  }
};
const child = (c: Ctx, d: DictObj, key: OVal, what: string): Obj => {
  const names = dictNow(c, d).entries;
  const name = names[indexOrName(c, key ?? -1, names, what)];
  const path = [...d.path, name];
  const v = dictNow(c, { owner: d.owner, path });
  return v.type === "DICTIONARY" ? { t: "dict", ...(d.owner ? { owner: d.owner } : {}), path } : { t: "xrec", ...(d.owner ? { owner: d.owner } : {}), path };
};
const DICT_LIKE: Members = {
  ...OBJECT,
  Count: ro((c) => dictNow(c, dictObj(c)).entries.length),
  Item: { call: (c, args) => child(c, dictObj(c), args[0], "Item") },
};
const keyName = (c: Ctx, v: OVal, what: string) => {
  const k = line(c, v, what, 255);
  return k ? k : c.err(`${what}: 이름이 필요합니다`);
};
const DICTIONARY: Members = {
  ...DICT_LIKE,
  Name: ro((c) => dictObj(c).path.at(-1) ?? ""),
  GetObject: { call: (c, args) => child(c, dictObj(c), String(args[0] ?? ""), "GetObject") },
  GetName: { call: (c, args) => { const o = args[0] as Obj; const d = dictObj(c); if ((o?.t === "dict" || o?.t === "xrec") && o.owner === d.owner && o.path.length === d.path.length + 1 && o.path.slice(0, -1).every((p, i) => p === d.path[i])) return o.path.at(-1)!; return c.err("GetName: 이 사전에 든 객체가 아닙니다"); } },
  AddObject: {
    call: (c, args) => {
      const d = dictObj(c);
      const k = keyName(c, args[0], "AddObject");
      if (!/^AcDbDictionary$/i.test(String(args[1] ?? ""))) c.err("AddObject: AcDbDictionary만 만들 수 있습니다 (XRecord는 AddXRecord)");
      dictTry(c, () => c.m.dictMake(d.owner, [...d.path, k]));
      return { t: "dict", ...(d.owner ? { owner: d.owner } : {}), path: [...d.path, k] };
    },
  },
  AddXRecord: {
    call: (c, args) => {
      const d = dictObj(c);
      const path = [...d.path, keyName(c, args[0], "AddXRecord")];
      dictTry(c, () => c.m.dictSet(d.owner, path, []));
      return { t: "xrec", ...(d.owner ? { owner: d.owner } : {}), path };
    },
  },
  Remove: { call: (c, args) => { const d = dictObj(c); const o = child(c, d, String(args[0] ?? ""), "Remove"); dictTry(c, () => c.m.dictDelete(d.owner, (o as DictObj).path)); return o; } },
  Rename: {
    call: (c, args) => {
      const d = dictObj(c);
      const from = child(c, d, String(args[0] ?? ""), "Rename") as Obj & DictObj;
      if (from.t !== "xrec") return c.err("Rename: 하위 사전의 이름은 바꾸지 않습니다 (XRecord만)");
      const to = [...d.path, keyName(c, args[1], "Rename")];
      if (c.m.dictView(d.owner, to)) c.err(`Rename: 이미 있는 항목 ${to.at(-1)}`);
      const tags = dictNow(c, from).tags;
      dictTry(c, () => (c.m.dictSet(d.owner, to, tags), c.m.dictDelete(d.owner, from.path)));
      return null;
    },
  },
  Replace: { call: (c) => c.err("Replace: 사전 항목은 Remove 뒤 AddXRecord로 바꾸세요"), limited: true },
  Delete: { call: (c) => { const d = dictObj(c); if (!d.path.length) c.err("Delete: 루트 사전은 지울 수 없습니다"); dictTry(c, () => c.m.dictDelete(d.owner, d.path)); return null; } },
};
const DICTIONARIES: Members = {
  ...DICT_LIKE,
  Add: { call: (c, args) => { const k = keyName(c, args[0], "Dictionaries.Add"); dictTry(c, () => c.m.dictMake(undefined, [k])); return { t: "dict", path: [k] }; } },
};
const XRECORD: Members = {
  ...OBJECT,
  Name: ro((c) => dictObj(c).path.at(-1) ?? ""),
  GetXRecordData: { call: (c) => { const t = dictNow(c, dictObj(c)).tags; return { out: { 0: t.map(([k]) => k), 1: t.map(([, v]) => v as OVal) }, ret: null }; } },
  SetXRecordData: {
    call: (c, args) => {
      const d = dictObj(c);
      const types = Array.isArray(args[0]) ? (args[0] as OVal[]) : c.err("SetXRecordData: 형식 배열이 필요합니다");
      const values = Array.isArray(args[1]) ? (args[1] as OVal[]) : c.err("SetXRecordData: 값 배열이 필요합니다");
      if (types.length !== values.length) c.err("SetXRecordData: 형식과 값의 개수가 같아야 합니다");
      dictTry(c, () => c.m.dictSet(d.owner, d.path, types.map((t, i) => [num(c, t, "SetXRecordData"), values[i]])));
      return null;
    },
  },
  TranslateIDs: only(false, "TranslateIDs"),
  Delete: { call: (c) => (dictTry(c, () => c.m.dictDelete(dictObj(c).owner, dictObj(c).path)), null) },
};

// selection sets (this run's; the screen's own selection is PICKFIRST)
const setOf = (c: Ctx): string[] => (c.o.t === "sset" ? (c.m.ssets.get(c.o.name.toUpperCase()) ?? c.err(`없는 선택 집합: ${c.o.name}`)) : c.err("선택 집합이 아닙니다"));
const MAX_SET = 100_000;
const putSet = (c: Ctx, hs: string[]) => {
  if (hs.length > MAX_SET) c.err(`선택 집합은 ${MAX_SET.toLocaleString()}개까지입니다`);
  c.m.ssets.set((c.o as { name: string }).name.toUpperCase(), hs);
};
const filterOf = (c: Ctx, ft: OVal | undefined, fd: OVal | undefined): Group[] => {
  if (ft === undefined || ft === null) return [];
  if (!Array.isArray(ft) || !Array.isArray(fd) || ft.length !== fd.length) return c.err("필터: 같은 길이의 형식·값 배열이 필요합니다");
  return ft.map((t, i) => [num(c, t, "필터"), fd[i]] as Group);
};
const inPoly = (p: Pt, poly: Pt[]) => {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [a, b] = [poly[i], poly[j]];
    if (a[1] > p[1] !== b[1] > p[1] && p[0] < ((b[0] - a[0]) * (p[1] - a[1])) / (b[1] - a[1]) + a[0]) inside = !inside;
  }
  return inside;
};
const cross = (a: Pt, b: Pt, p: Pt, q: Pt) => {
  const d = (b[0] - a[0]) * (q[1] - p[1]) - (b[1] - a[1]) * (q[0] - p[0]);
  if (!d) return false;
  const t = ((p[0] - a[0]) * (q[1] - p[1]) - (p[1] - a[1]) * (q[0] - p[0])) / d;
  const u = ((p[0] - a[0]) * (b[1] - a[1]) - (p[1] - a[1]) * (b[0] - a[0])) / d;
  return t >= 0 && t <= 1 && u >= 0 && u <= 1;
};
const segs = (pts: Pt[], closed: boolean): [Pt, Pt][] => [...pts.slice(1).map((q, i) => [pts[i], q] as [Pt, Pt]), ...(closed && pts.length > 2 ? [[pts[pts.length - 1], pts[0]] as [Pt, Pt]] : [])];
const touches = (e: Ent, poly: Pt[], mode: "inside" | "crossing" | "fence"): boolean => {
  const paths = pathsOf(e);
  const pts = paths.flat();
  if (!pts.length) return false;
  if (mode === "inside") return pts.every((p) => inPoly(p, poly));
  const edges = segs(poly, mode !== "fence");
  const hit = paths.some((path) => segs(path, false).some(([a, b]) => edges.some(([p, q]) => cross(a, b, p, q))));
  return hit || (mode === "crossing" && pts.some((p) => inPoly(p, poly)));
};
const rect = (a: Pt, b: Pt): Pt[] => [a, [b[0], a[1]], b, [a[0], b[1]]];
const selectWith = (c: Ctx, keep: (e: Ent) => boolean, ft?: OVal, fd?: OVal) => {
  const filter = filterOf(c, ft, fd);
  const have = new Set(setOf(c));
  const add = c.m.all().filter((e) => !have.has(e.handle) && keep(e) && matches(e, filter)).map((e) => e.handle);
  putSet(c, [...setOf(c), ...add]);
  return null;
};
const SSET: Members = {
  Application: ro(() => ({ t: "app" })),
  Name: ro((c) => (c.o as { name: string }).name),
  Count: ro((c) => setOf(c).filter((h) => c.m.get(h)).length),
  Item: { call: (c, args) => { const hs = setOf(c).filter((h) => c.m.get(h)); return { t: "ent", handle: hs[indexOrName(c, args[0] ?? -1, hs, "Item")] }; } },
  AddItems: { call: (c, args) => { const have = new Set(setOf(c)); const add = handlesOf(c, args[0], "AddItems").filter((h) => !have.has(h) && (c.m.get(h) ?? c.err(`AddItems: 없는 객체 ${h}`))); putSet(c, [...setOf(c), ...add]); return null; } },
  RemoveItems: { call: (c, args) => { const rm = new Set(handlesOf(c, args[0], "RemoveItems")); putSet(c, setOf(c).filter((h) => !rm.has(h))); return null; } },
  Clear: { call: (c) => (putSet(c, []), null) },
  Delete: { call: (c) => (setOf(c), c.m.ssets.delete((c.o as { name: string }).name.toUpperCase()), null) },
  Erase: { call: (c) => { for (const h of setOf(c)) if (c.m.get(h)) c.m.remove(h); putSet(c, []); return null; } },
  Highlight: { call: () => null },
  Update: { call: () => null },
  Select: {
    // Select(Mode, Point1, Point2, FilterType, FilterData): 0 window, 1 crossing, 3 previous, 4 last, 5 all
    call: (c, args) => {
      const mode = Math.trunc(num(c, args[0], "Select"));
      if (mode === 5) return selectWith(c, () => true, args[3], args[4]);
      if (mode === 4) { const last = c.m.last(); return selectWith(c, (e) => e === last, args[3], args[4]); }
      if (mode === 3) return c.err("Select: 이전 선택(acSelectionSetPrevious)은 화면의 선택을 쓰세요 (PickfirstSelectionSet)");
      if (mode !== 0 && mode !== 1) return c.err("Select: 모드는 0(창)·1(걸침)·4(마지막)·5(전체)입니다");
      const poly = rect(pt(c, args[1], "Select"), pt(c, args[2], "Select"));
      return selectWith(c, (e) => touches(e, poly, mode === 0 ? "inside" : "crossing"), args[3], args[4]);
    },
  },
  SelectAtPoint: {
    // the objects within 1 mm of the point (AX-CAD's pick distance on a 1:1 drawing)
    call: (c, args) => {
      const p = pt(c, args[0], "SelectAtPoint");
      const box = rect([p[0] - 1, p[1] - 1], [p[0] + 1, p[1] + 1]);
      return selectWith(c, (e) => touches(e, box, "crossing"), args[1], args[2]);
    },
  },
  SelectByPolygon: {
    // SelectByPolygon(Mode, Points, FilterType, FilterData): 2 fence, 6 window polygon, 7 crossing polygon
    call: (c, args) => {
      const mode = Math.trunc(num(c, args[0], "SelectByPolygon"));
      const f = (Array.isArray(args[1]) ? args[1] : []).map((x) => num(c, x as OVal, "SelectByPolygon"));
      if (f.length < 6 || f.length % 3) c.err("SelectByPolygon: x, y, z 점이 2개 이상 필요합니다");
      if (f.length > 3000) c.err("SelectByPolygon: 점은 1,000개까지입니다");
      const poly = Array.from({ length: f.length / 3 }, (_, i) => [f[3 * i], f[3 * i + 1]] as Pt);
      const how = ({ 2: "fence", 6: "inside", 7: "crossing" } as const)[mode as 2 | 6 | 7] ?? c.err("SelectByPolygon: 모드는 2(울타리)·6(창 다각형)·7(걸침 다각형)입니다");
      return selectWith(c, (e) => touches(e, poly, how), args[2], args[3]);
    },
  },
  SelectOnScreen: { call: (c) => c.err("SelectOnScreen: 화면 선택은 Utility.GetEntity나 먼저 선택한 객체(PickfirstSelectionSet)를 쓰세요"), limited: true },
};
const SSETS: Members = {
  Application: ro(() => ({ t: "app" })),
  Count: ro((c) => c.m.ssets.size),
  Item: { call: (c, args) => { const ns = [...c.m.ssets.keys()]; return { t: "sset", name: ns[indexOrName(c, args[0] ?? -1, ns, "SelectionSets.Item")] }; } },
  Add: {
    call: (c, args) => {
      const n = line(c, args[0], "SelectionSets.Add", 255);
      if (!n || c.m.ssets.has(n.toUpperCase())) c.err(`SelectionSets.Add: 이름이 없거나 이미 있습니다 (${n})`);
      if (c.m.ssets.size >= 128) c.err("SelectionSets.Add: 선택 집합은 128개까지입니다");
      c.m.ssets.set(n.toUpperCase(), []);
      return { t: "sset", name: n.toUpperCase() };
    },
  },
};
const DOCUMENTS: Members = {
  Application: ro(() => ({ t: "app" })),
  Count: ro(() => 1), // a script works on the open drawing only
  Item: { call: (c, args) => (args[0] === 0 || /^drawing(\.dwg)?$/i.test(String(args[0])) ? { t: "doc" } : c.err("Documents.Item: 스크립트는 열린 도면 하나만 다룹니다")) },
  Add: { call: (c) => c.err("Documents.Add: 새 도면은 프로젝트 화면에서 만듭니다"), limited: true },
  Open: { call: (c) => c.err("Documents.Open: 다른 도면은 열지 않습니다"), limited: true },
  Close: { call: (c) => c.err("Documents.Close: 도면 창은 화면에서 닫습니다"), limited: true },
};

const HYPERLINKS_NOTE = "PE_URL"; // where AutoCAD keeps hyperlinks: XDATA of this application

// --- the table --------------------------------------------------------------------------------

export const CLASSES: Record<string, Members> = {
  Application: APPLICATION,
  Document: DOCUMENT,
  "ModelSpace Entities": MODELSPACE,
  Layers: LAYERS,
  Layer: LAYER,
  Linetypes: TABLE_COLLECTION,
  Linetype: LINETYPE,
  TextStyles: TABLE_COLLECTION,
  TextStyle: TEXTSTYLE,
  DimStyles: TABLE_COLLECTION,
  DimStyle: DIMSTYLE,
  RegisteredApplications: TABLE_COLLECTION,
  RegisteredApplication: RECORD,
  Layouts: COLLECTION,
  Layout: LAYOUT,
  Line: LINE,
  Circle: CIRCLE,
  Arc: ARC,
  LightweightPolyline: LWPOLYLINE,
  Text: TEXT,
  MText: MTEXT,
  Point: POINT,
  Ellipse: ELLIPSE,
  Spline: SPLINE,
  Hatch: HATCH,
  Leader: LEADER,
  MLeader: MLEADER,
  BlockReference: INSERT,
  DimAligned: DIM_ALIGNED,
  DimRotated: DIM_ROTATED,
  DimRadial: DIM_RADIAL,
  DimDiametric: DIM_DIAMETRIC,
  DimAngular: DIM_ANGULAR,
  // W6
  Blocks: BLOCKS,
  Block: BLOCK,
  Attribute: ATTRIBUTE,
  AttributeReference: ATTREF,
  Groups: GROUPS,
  Group: GROUP,
  Dictionaries: DICTIONARIES,
  Dictionary: DICTIONARY,
  XRecord: XRECORD,
  SelectionSets: SSETS,
  SelectionSet: SSET,
  Documents: DOCUMENTS,
};
const BY_GEOM: Record<string, string> = {
  LINE: "Line", CIRCLE: "Circle", ARC: "Arc", LWPOLYLINE: "LightweightPolyline", TEXT: "Text", MTEXT: "MText", POINT: "Point", ELLIPSE: "Ellipse", SPLINE: "Spline", HATCH: "Hatch",
  LEADER: "Leader", MULTILEADER: "MLeader", INSERT: "BlockReference", DIM_ALIGNED: "DimAligned", DIM_LINEAR: "DimRotated", DIM_RADIUS: "DimRadial", DIM_DIAMETER: "DimDiametric", DIM_ANGULAR: "DimAngular",
};
/** The ActiveX object an Obj is (null: one only objmodel.ts knows). */
export function classOf(m: DrawingModel, o: Obj): string | null {
  switch (o.t) {
    case "app":
      return "Application";
    case "doc":
      return "Document";
    case "space":
      return "ModelSpace Entities";
    case "layers":
      return "Layers";
    case "layer":
      return "Layer";
    case "layout":
      return "Layout";
    case "table":
      return { linetype: "Linetypes", textstyle: "TextStyles", dimstyle: "DimStyles", regapp: "RegisteredApplications", layout: "Layouts" }[o.tk];
    case "rec":
      return { linetype: "Linetype", textstyle: "TextStyle", dimstyle: "DimStyle", regapp: "RegisteredApplication" }[o.tk];
    case "ent": {
      const e = m.get(o.handle);
      return e ? (BY_GEOM[e.geom.type] ?? null) : null;
    }
    case "dict":
      return o.path.length || o.owner ? "Dictionary" : "Dictionaries";
  }
  return ({ blocks: "Blocks", block: "Block", attdef: "Attribute", attref: "AttributeReference", groups: "Groups", group: "Group", dicts: "Dictionaries", xrec: "XRecord", ssets: "SelectionSets", sset: "SelectionSet", docs: "Documents" } as Record<string, string>)[o.t] ?? null;
}
const find = (m: DrawingModel, o: Obj, name: string): Member | undefined => {
  const cls = classOf(m, o);
  const table = cls ? CLASSES[cls] : undefined;
  if (!table) return undefined;
  const key = Object.keys(table).find((k) => k.toUpperCase() === name.toUpperCase());
  return key ? table[key] : undefined;
};
export const NOT_HERE = Symbol("not in the table");
const ctx = (m: DrawingModel, o: Obj, err: (msg: string) => never): Ctx => ({ m, o, err });

export function axGet(m: DrawingModel, o: Obj, name: string, err: (msg: string) => never): OVal | typeof NOT_HERE {
  const mem = find(m, o, name);
  return mem?.get ? mem.get(ctx(m, o, err)) : NOT_HERE;
}
export function axPut(m: DrawingModel, o: Obj, name: string, v: OVal, err: (msg: string) => never): boolean {
  const mem = find(m, o, name);
  if (!mem?.put) return false;
  mem.put(ctx(m, o, err), v);
  return true;
}
export function axCall(m: DrawingModel, o: Obj, name: string, args: OVal[], err: (msg: string) => never): OVal | ByRef | typeof NOT_HERE {
  const mem = find(m, o, name);
  return mem?.call ? mem.call(ctx(m, o, err), args) : NOT_HERE;
}
/** Whether the object has this member at all (vlax-property-available-p, vlax-method-applicable-p). */
export function axHas(m: DrawingModel, o: Obj, name: string, kind: "prop" | "method"): boolean | null {
  const mem = find(m, o, name);
  if (!mem) return null;
  return kind === "prop" ? Boolean(mem.get || mem.put || mem.legacy) : Boolean(mem.call || mem.legacy);
}
/** Every member of an object's table (dumpallproperties, vlax-dump-object). */
export function axMembers(m: DrawingModel, o: Obj): { name: string; member: Member }[] {
  const cls = classOf(m, o);
  return cls ? Object.entries(CLASSES[cls] ?? {}).map(([name, member]) => ({ name, member })) : [];
}
/** For the coverage report: object -> member -> "full" | "limited". */
export function memberTable(): Record<string, Record<string, "full" | "limited">> {
  return Object.fromEntries(Object.entries(CLASSES).map(([cls, mem]) => [cls, Object.fromEntries(Object.entries(mem).map(([n, x]) => [n, x.limited ? "limited" : "full"]))]));
}
export { HYPERLINKS_NOTE };
