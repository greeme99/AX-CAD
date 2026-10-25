// Track 8 P2-1 sketch mode (FreeCAD Sketcher behaviour): plane maths, drawing tools with auto
// constraints, constraint commands and picking. Pure logic, no React or Three.js; the server solves.
import { distToPaths, toPaths, type Geom } from "./geom";
import type { SnapEntity, SnapHit } from "./snap";
import type { Pt } from "./view";

export type V3 = [number, number, number];
export type Plane = { origin: V3; normal: V3; xdir: V3 };
export type PlaneName = "XY" | "XZ" | "YZ";
// FreeCAD base planes: XZ looks from the front (normal -Y), YZ from the right (normal +X)
export const PLANES: Record<PlaneName, Plane> = {
  XY: { origin: [0, 0, 0], normal: [0, 0, 1], xdir: [1, 0, 0] },
  XZ: { origin: [0, 0, 0], normal: [0, -1, 0], xdir: [1, 0, 0] },
  YZ: { origin: [0, 0, 0], normal: [1, 0, 0], xdir: [0, 1, 0] },
};

export type SkPos = "edge" | "start" | "end" | "center";
export type SkRef = { e: number; p: SkPos }; // e -1 / -2: horizontal / vertical axis, (-1, start) = origin
type Base = { id: number; construction?: boolean };
export type SkEntity = Base &
  (
    | { type: "POINT"; point: Pt }
    | { type: "LINE"; start: Pt; end: Pt }
    | { type: "CIRCLE"; center: Pt; radius: number }
    | {
        type: "ARC";
        center: Pt;
        radius: number;
        start_angle: number;
        end_angle: number;
      }
  );
export type CType =
  | "COINCIDENT"
  | "POINT_ON_OBJECT"
  | "HORIZONTAL"
  | "VERTICAL"
  | "PARALLEL"
  | "PERPENDICULAR"
  | "TANGENT"
  | "CONCENTRIC"
  | "EQUAL"
  | "SYMMETRIC"
  | "FIXED"
  | "DISTANCE"
  | "DISTANCE_X"
  | "DISTANCE_Y"
  | "RADIUS"
  | "DIAMETER"
  | "ANGLE";
export type SkConstraint = { type: CType; refs: SkRef[]; value?: number };
export type SketchParams = {
  plane: Plane;
  entities: SkEntity[];
  constraints: SkConstraint[];
};
export type SolveResult = {
  entities: SkEntity[];
  dof: number;
  status: "OK" | "REDUNDANT" | "CONFLICT";
  conflicting: number[];
  redundant: number[];
};

export const C_LABEL: Record<CType, string> = {
  COINCIDENT: "일치",
  POINT_ON_OBJECT: "점-선 위",
  HORIZONTAL: "수평",
  VERTICAL: "수직",
  PARALLEL: "평행",
  PERPENDICULAR: "직각",
  TANGENT: "접선",
  CONCENTRIC: "동심",
  EQUAL: "같음",
  SYMMETRIC: "대칭",
  FIXED: "고정",
  DISTANCE: "거리",
  DISTANCE_X: "가로 거리",
  DISTANCE_Y: "세로 거리",
  RADIUS: "반지름",
  DIAMETER: "지름",
  ANGLE: "각도",
};
export const VALUED: CType[] = ["DISTANCE", "DISTANCE_X", "DISTANCE_Y", "RADIUS", "DIAMETER", "ANGLE"];
// accepted reference signatures (P point, L line, C circle/arc), same table as core/geometry/solver.py
const RULES: Record<CType, string[]> = {
  COINCIDENT: ["PP"],
  CONCENTRIC: ["CC"],
  HORIZONTAL: ["L", "PP"],
  VERTICAL: ["L", "PP"],
  PARALLEL: ["LL"],
  PERPENDICULAR: ["LL"],
  TANGENT: ["LC", "CL", "CC", "PP"],
  EQUAL: ["LL", "CC"],
  FIXED: ["P"],
  DISTANCE: ["PP", "PL", "L"],
  DISTANCE_X: ["PP", "P", "L"],
  DISTANCE_Y: ["PP", "P", "L"],
  RADIUS: ["C"],
  DIAMETER: ["C"],
  ANGLE: ["LL", "L"],
  POINT_ON_OBJECT: ["PL", "PC"],
  SYMMETRIC: ["PPL", "PPP"],
};

// --- plane maths -------------------------------------------------------------------------------
const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const dot = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const unit = (v: V3): V3 => {
  const l = Math.hypot(...v) || 1;
  return [v[0] / l, v[1] / l, v[2] / l];
};
export const ydir = (pl: Plane): V3 => unit(cross(pl.normal, pl.xdir));

export function toWorld(pl: Plane, p: Pt): V3 {
  const [x, y] = [unit(pl.xdir), ydir(pl)];
  return [0, 1, 2].map((k) => pl.origin[k] + p[0] * x[k] + p[1] * y[k]) as V3;
}

export function toLocal(pl: Plane, w: V3): Pt {
  const d: V3 = [w[0] - pl.origin[0], w[1] - pl.origin[1], w[2] - pl.origin[2]];
  return [dot(d, unit(pl.xdir)), dot(d, ydir(pl))];
}

/** Sketch plane on a picked planar face: origin at the picked point, x along the global X axis
 * (or Y when the face faces X) projected onto the face. */
export function facePlane(point: V3, normal: V3): Plane {
  const n = unit(normal);
  const ref: V3 = Math.abs(n[0]) > 0.9 ? [0, 1, 0] : [1, 0, 0];
  const k = dot(ref, n);
  const x = unit([ref[0] - k * n[0], ref[1] - k * n[1], ref[2] - k * n[2]]);
  const r = (v: number) => Math.round(v * 1e6) / 1e6; // picked from a mesh: keep the numbers clean
  return {
    origin: point.map(r) as V3,
    normal: n.map(r) as V3,
    xdir: x.map(r) as V3,
  };
}

export function planeName(pl: Plane): string {
  for (const [k, v] of Object.entries(PLANES))
    if (v.normal.every((a, i) => Math.abs(a - pl.normal[i]) < 1e-9) && v.xdir.every((a, i) => Math.abs(a - pl.xdir[i]) < 1e-9)) return `${k} 평면${pl.origin.some((a) => a !== 0) ? ` (원점 ${pl.origin.join(", ")})` : ""}`;
  return `면 위 (법선 ${pl.normal.map((a) => Math.round(a * 1000) / 1000).join(", ")})`;
}

// --- geometry access ---------------------------------------------------------------------------
export function geomOf(e: SkEntity): Geom {
  switch (e.type) {
    case "POINT":
      return { type: "POINT", point: e.point };
    case "LINE":
      return { type: "LINE", start: e.start, end: e.end };
    case "CIRCLE":
      return { type: "CIRCLE", center: e.center, radius: e.radius };
    case "ARC":
      return {
        type: "ARC",
        center: e.center,
        radius: e.radius,
        start_angle: e.start_angle,
        end_angle: e.end_angle,
      };
  }
}

const polar = (c: Pt, r: number, deg: number): Pt => [c[0] + r * Math.cos((deg * Math.PI) / 180), c[1] + r * Math.sin((deg * Math.PI) / 180)];
const angleOf = (c: Pt, p: Pt) => (Math.atan2(p[1] - c[1], p[0] - c[0]) * 180) / Math.PI;

export function pointOf(ents: SkEntity[], r: SkRef): Pt | null {
  if (r.e === -1 || r.e === -2) return r.p === "end" ? (r.e === -1 ? [1, 0] : [0, 1]) : [0, 0];
  const e = ents.find((x) => x.id === r.e);
  if (!e) return null;
  if (e.type === "POINT") return e.point;
  if (e.type === "LINE") return r.p === "end" ? e.end : r.p === "start" ? e.start : null;
  if (r.p === "center") return e.center;
  if (e.type === "ARC" && (r.p === "start" || r.p === "end")) return polar(e.center, e.radius, r.p === "start" ? e.start_angle : e.end_angle);
  return null;
}

const kindOf = (ents: SkEntity[], r: SkRef): "P" | "L" | "C" | null => {
  if (r.p !== "edge") return "P";
  if (r.e === -1 || r.e === -2) return "L";
  const t = ents.find((x) => x.id === r.e)?.type;
  return t === "LINE" ? "L" : t === "CIRCLE" || t === "ARC" ? "C" : null;
};

const lineOf = (ents: SkEntity[], r: SkRef): [Pt, Pt] => [pointOf(ents, { e: r.e, p: "start" })!, pointOf(ents, { e: r.e, p: "end" })!];
const radiusOf = (ents: SkEntity[], r: SkRef) => {
  const e = ents.find((x) => x.id === r.e);
  return e && (e.type === "CIRCLE" || e.type === "ARC") ? e.radius : 0;
};
const sameRef = (a: SkRef, b: SkRef) => a.e === b.e && a.p === b.p;
export const nextId = (ents: SkEntity[]) => ents.reduce((m, e) => Math.max(m, e.id), 0) + 1;

/** Entities for the shared 2D snap index (handle = entity id; "-1" = the origin). */
export function snapEntities(ents: SkEntity[]): SnapEntity[] {
  const origin: SnapEntity = {
    handle: "-1",
    layer: "0",
    geom: { type: "POINT", point: [0, 0] },
    paths: [],
  };
  return [
    origin,
    ...ents.map((e) => ({
      handle: String(e.id),
      layer: "0",
      geom: geomOf(e),
      paths: toPaths(geomOf(e)),
    })),
  ];
}

/** The point reference a snap landed on (END/CEN), or the curve it is on (NEA). */
export function refAt(ents: SkEntity[], hit: SnapHit | null): { at?: SkRef; on?: SkRef } {
  if (!hit?.handle) return {};
  const id = Number(hit.handle);
  if (id === -1) return { at: { e: -1, p: "start" } };
  const e = ents.find((x) => x.id === id);
  if (!e) return {};
  if (hit.kind === "NEA") return e.type === "POINT" ? {} : { on: { e: id, p: "edge" } };
  if (hit.kind === "CEN" || e.type === "POINT") return { at: { e: id, p: "center" } };
  if (hit.kind !== "END") return {};
  const near = (p: SkPos) => {
    const q = pointOf(ents, { e: id, p });
    return q ? Math.hypot(q[0] - hit.x, q[1] - hit.y) : Infinity;
  };
  return { at: { e: id, p: near("start") <= near("end") ? "start" : "end" } };
}

// --- drawing tools -----------------------------------------------------------------------------
export type SkTool = "LINE" | "RECT" | "CIRCLE" | "ARC" | "POINT";
export const TOOL_PICKS: Record<SkTool, number> = {
  LINE: 2,
  RECT: 2,
  CIRCLE: 2,
  ARC: 3,
  POINT: 1,
};
export type Pick = { p: Pt; at?: SkRef; on?: SkRef };
const AUTO_HV_DEG = 2; // a line drawn within 2 degrees of level gets HORIZONTAL / VERTICAL

function attach(out: SkConstraint[], mine: SkRef, pick: Pick) {
  if (pick.at && !(pick.at.e === mine.e)) out.push({ type: "COINCIDENT", refs: [mine, pick.at] });
  else if (pick.on && pick.on.e !== mine.e) out.push({ type: "POINT_ON_OBJECT", refs: [mine, pick.on] });
}

function hv(out: SkConstraint[], id: number, a: Pt, b: Pt) {
  const deg = Math.abs((Math.atan2(b[1] - a[1], b[0] - a[0]) * 180) / Math.PI) % 180;
  if (Math.min(deg, 180 - deg) <= AUTO_HV_DEG) out.push({ type: "HORIZONTAL", refs: [{ e: id, p: "edge" }] });
  else if (Math.abs(deg - 90) <= AUTO_HV_DEG) out.push({ type: "VERTICAL", refs: [{ e: id, p: "edge" }] });
}

/** New entities and auto constraints once a tool has all its picks (the snapped point becomes a
 * COINCIDENT, a point on a curve a POINT_ON_OBJECT). Returns null for a degenerate shape. */
export function build(tool: SkTool, picks: Pick[], ents: SkEntity[], construction: boolean): { entities: SkEntity[]; constraints: SkConstraint[] } | null {
  const id = nextId(ents);
  const c = construction || undefined;
  const cons: SkConstraint[] = [];
  const [a, b, d] = picks.map((k) => k.p);
  const far = (p: Pt, q: Pt) => Math.hypot(q[0] - p[0], q[1] - p[1]) > 1e-6;
  switch (tool) {
    case "POINT":
      attach(cons, { e: id, p: "center" }, picks[0]);
      return {
        entities: [{ id, type: "POINT", point: a, construction: c }],
        constraints: cons,
      };
    case "LINE":
      if (!far(a, b)) return null;
      attach(cons, { e: id, p: "start" }, picks[0]);
      attach(cons, { e: id, p: "end" }, picks[1]);
      hv(cons, id, a, b);
      return {
        entities: [{ id, type: "LINE", start: a, end: b, construction: c }],
        constraints: cons,
      };
    case "RECT": {
      if (Math.abs(b[0] - a[0]) < 1e-6 || Math.abs(b[1] - a[1]) < 1e-6) return null;
      const pts: Pt[] = [a, [b[0], a[1]], b, [a[0], b[1]]];
      const lines: SkEntity[] = pts.map((p, i) => ({
        id: id + i,
        type: "LINE",
        start: p,
        end: pts[(i + 1) % 4],
        construction: c,
      }));
      for (let i = 0; i < 4; i++)
        cons.push({
          type: "COINCIDENT",
          refs: [
            { e: id + i, p: "end" },
            { e: id + ((i + 1) % 4), p: "start" },
          ],
        });
      cons.push(
        ...[0, 2].map(
          (i): SkConstraint => ({
            type: "HORIZONTAL",
            refs: [{ e: id + i, p: "edge" }],
          }),
        ),
      );
      cons.push(
        ...[1, 3].map(
          (i): SkConstraint => ({
            type: "VERTICAL",
            refs: [{ e: id + i, p: "edge" }],
          }),
        ),
      );
      attach(cons, { e: id, p: "start" }, picks[0]);
      attach(cons, { e: id + 2, p: "start" }, picks[1]);
      return { entities: lines, constraints: cons };
    }
    case "CIRCLE": {
      const r = Math.hypot(b[0] - a[0], b[1] - a[1]);
      if (r < 1e-6) return null;
      attach(cons, { e: id, p: "center" }, picks[0]);
      if (picks[1].at)
        cons.push({
          type: "POINT_ON_OBJECT",
          refs: [picks[1].at, { e: id, p: "edge" }],
        }); // drawn through that point
      return {
        entities: [{ id, type: "CIRCLE", center: a, radius: r, construction: c }],
        constraints: cons,
      };
    }
    case "ARC": {
      const r = Math.hypot(b[0] - a[0], b[1] - a[1]);
      if (r < 1e-6 || !far(b, d)) return null;
      const arc: SkEntity = {
        id,
        type: "ARC",
        center: a,
        radius: r,
        start_angle: angleOf(a, b),
        end_angle: angleOf(a, d),
        construction: c,
      };
      attach(cons, { e: id, p: "center" }, picks[0]);
      attach(cons, { e: id, p: "start" }, picks[1]);
      attach(cons, { e: id, p: "end" }, picks[2]);
      return { entities: [arc], constraints: cons };
    }
  }
}

/** Rubber-band geometry while a tool waits for its next pick. */
export function preview(tool: SkTool, picks: Pick[], cursor: Pt): Geom[] {
  const [a, b] = picks.map((k) => k.p);
  if (!a) return [];
  if (tool === "LINE") return [{ type: "LINE", start: a, end: cursor }];
  if (tool === "RECT") {
    const pts: Pt[] = [a, [cursor[0], a[1]], cursor, [a[0], cursor[1]]];
    return pts.map((p, i) => ({
      type: "LINE",
      start: p,
      end: pts[(i + 1) % 4],
    }));
  }
  const r = Math.hypot((b ?? cursor)[0] - a[0], (b ?? cursor)[1] - a[1]);
  if (tool === "CIRCLE" || !b) return [{ type: "CIRCLE", center: a, radius: r }];
  return [
    {
      type: "ARC",
      center: a,
      radius: r,
      start_angle: angleOf(a, b),
      end_angle: angleOf(a, cursor),
    },
  ];
}

// --- constraint commands -----------------------------------------------------------------------
const deg = (a: Pt, b: Pt) => (Math.atan2(a[0] * b[1] - a[1] * b[0], a[0] * b[0] + a[1] * b[1]) * 180) / Math.PI;
const sub = (a: Pt, b: Pt): Pt => [a[0] - b[0], a[1] - b[1]];

/** Current value of a valued constraint (what FreeCAD offers in its dimension dialog). */
function current(type: CType, refs: SkRef[], ents: SkEntity[], sig: string): number {
  const P = (i: number) => pointOf(ents, refs[i])!;
  const d = (i: number) => {
    const [s, e] = lineOf(ents, refs[i]);
    return sub(e, s);
  };
  switch (type) {
    case "DISTANCE": {
      if (sig === "PP") return Math.hypot(...sub(P(1), P(0)));
      if (sig === "L") return Math.hypot(...d(0));
      const [s] = lineOf(ents, refs[1]);
      const u = d(1);
      return Math.abs(u[0] * (P(0)[1] - s[1]) - u[1] * (P(0)[0] - s[0])) / Math.hypot(...u);
    }
    case "DISTANCE_X":
    case "DISTANCE_Y": {
      const k = type === "DISTANCE_X" ? 0 : 1;
      return sig === "PP" ? P(1)[k] - P(0)[k] : sig === "P" ? P(0)[k] : d(0)[k];
    }
    case "RADIUS":
      return radiusOf(ents, refs[0]);
    case "DIAMETER":
      return 2 * radiusOf(ents, refs[0]);
    case "ANGLE":
      return sig === "L" ? deg([1, 0], d(0)) : deg(d(0), d(1));
    default:
      return 0;
  }
}

/** The constraint a command makes of the selection (in pick order), or a Korean reason it can't. */
export function makeConstraint(type: CType, sel: SkRef[], ents: SkEntity[]): SkConstraint | string {
  let refs = sel;
  // points first where the solver expects a point then a curve (FreeCAD accepts either pick order)
  if (type === "DISTANCE" || type === "POINT_ON_OBJECT" || type === "SYMMETRIC") refs = [...sel.filter((r) => r.p !== "edge"), ...sel.filter((r) => r.p === "edge")];
  const kinds = refs.map((r) => kindOf(ents, r));
  if (kinds.includes(null)) return "선택한 요소를 찾을 수 없습니다.";
  const sig = kinds.join("");
  if (type === "TANGENT" && sig === "PP" && refs.some((r) => r.p === "center" || kindOf(ents, { e: r.e, p: "edge" }) === null)) return "접선(끝점)은 선·호의 끝점 두 개를 고르세요.";
  if (!RULES[type].includes(sig)) return `${C_LABEL[type]}: 고를 수 있는 조합은 ${RULES[type].map((s) => s.replaceAll("P", "점").replaceAll("L", "선").replaceAll("C", "원/호")).join(" · ")} 입니다.`;
  const c: SkConstraint = { type, refs };
  if (VALUED.includes(type)) c.value = Math.round(current(type, refs, ents, sig) * 1e6) / 1e6;
  return c;
}

/** Add a constraint; endpoint tangency replaces the COINCIDENT it implies (FreeCAD does the same). */
export function addConstraint(cons: SkConstraint[], c: SkConstraint): SkConstraint[] {
  if (c.type !== "TANGENT" || c.refs.some((r) => r.p === "edge")) return [...cons, c];
  const [a, b] = c.refs;
  return [...cons.filter((x) => !(x.type === "COINCIDENT" && ((sameRef(x.refs[0], a) && sameRef(x.refs[1], b)) || (sameRef(x.refs[0], b) && sameRef(x.refs[1], a))))), c];
}

/** Remove entities and every constraint that names them. */
export function removeEntities(p: { entities: SkEntity[]; constraints: SkConstraint[] }, ids: Set<number>) {
  return {
    entities: p.entities.filter((e) => !ids.has(e.id)),
    constraints: p.constraints.filter((c) => !c.refs.some((r) => ids.has(r.e))),
  };
}

const POS_LABEL: Record<SkPos, string> = {
  edge: "",
  start: "시작",
  end: "끝",
  center: "중심",
};
export const refLabel = (r: SkRef) => (r.e === -1 ? (r.p === "edge" ? "X축" : "원점") : r.e === -2 ? (r.p === "edge" ? "Y축" : "원점") : `#${r.e}${POS_LABEL[r.p] && "." + POS_LABEL[r.p]}`);
export const constraintLabel = (c: SkConstraint) => `${C_LABEL[c.type]} ${c.refs.map(refLabel).join(", ")}${c.value === undefined ? "" : ` = ${Math.round(c.value * 1000) / 1000}${c.type === "ANGLE" ? "°" : ""}`}`;

// --- picking -----------------------------------------------------------------------------------
/** Point (end/centre/origin) under the cursor first, then the nearest curve or axis, within tol. */
export function pickRef(ents: SkEntity[], p: Pt, tol: number): SkRef | null {
  let best: SkRef | null = null;
  let bd = tol;
  const pts: SkRef[] = [{ e: -1, p: "start" }];
  for (const e of ents) {
    if (e.type === "LINE" || e.type === "ARC") pts.push({ e: e.id, p: "start" }, { e: e.id, p: "end" });
    if (e.type !== "LINE") pts.push({ e: e.id, p: "center" });
  }
  for (const r of pts) {
    const q = pointOf(ents, r)!;
    const dd = Math.hypot(q[0] - p[0], q[1] - p[1]);
    if (dd < bd || (!best && dd <= bd)) [best, bd] = [r, dd]; // shared corner: the first entity
  }
  if (best) return best;
  for (const e of ents) {
    if (e.type === "POINT") continue;
    const dd = distToPaths(toPaths(geomOf(e)), p);
    if (dd <= bd) [best, bd] = [{ e: e.id, p: "edge" }, dd];
  }
  if (best) return best;
  if (Math.abs(p[1]) <= bd) return { e: -1, p: "edge" };
  if (Math.abs(p[0]) <= bd) return { e: -2, p: "edge" };
  return null;
}

/** The profile has something to extrude: non-construction curves. */
export const hasProfile = (ents: SkEntity[]) => ents.some((e) => !e.construction && e.type !== "POINT");
