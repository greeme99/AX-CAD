import type { Prim } from "./curves";
import { extendAt, filletLines, mirrorGeom, offsetGeom, polygonGeom, rectGeom, rotateGeom, scaleGeom, trimAt } from "./edit";
import { arcFrom3Points, toPaths, translate, type Geom } from "./geom";
import type { Pt } from "./view";

export type Tool =
  | "SELECT" | "LINE" | "CIRCLE" | "ARC" | "PLINE" | "TEXT" | "MOVE" | "COPY"
  | "DIMLINEAR" | "DIMALIGNED" | "DIMANGULAR" | "DIMRADIUS"
  // FreeCAD Draft modify set
  | "RECT" | "POLYGON" | "ROTATE" | "SCALE" | "MIRROR" | "ARRAY" | "ARRAYPOLAR" | "OFFSET" | "FILLET" | "TRIM" | "EXTEND";
export type Sel = { handle: string; layer: string; geom: Geom }; // editable, unlocked selection
// pts = picks of the in-progress command; n = its number (sides, count, distance, radius);
// target = an entity picked earlier in the command, with where it was picked
export type ToolState = { tool: Tool; pts: Pt[]; n?: number; target?: Sel & { at: Pt } };
/** Tools that work on the selection (start refuses without one). */
export const SEL_TOOLS: Tool[] = ["MOVE", "COPY", "ROTATE", "SCALE", "MIRROR", "ARRAY", "ARRAYPOLAR"];
/** Tools whose point picks name an entity: the caller passes the editable entity under the pick as sel[0]. */
export const PICK_TOOLS: Tool[] = ["DIMRADIUS", "FILLET", "TRIM", "EXTEND"];
/** Curves of the other entities (for trim/extend) and how far an extension may look. */
export type Ctx = { bounds: Prim[]; reach: number };
export type ToolEvent =
  | { kind: "point"; p: Pt }
  | { kind: "number"; n: number }
  | { kind: "text"; s: string }
  | { kind: "close" }
  | { kind: "enter" }
  | { kind: "esc" };
export type Result = { created?: { geom: Geom; layer?: string }[]; updated?: { handle: string; geom: Geom }[]; deleted?: string[] };
export type StepOut = { state: ToolState; prompt: string; results: Result[] };

const EPS = 1e-9;
export const TEXT_HEIGHT = 2.5;
const dist = (a: Pt, b: Pt) => Math.hypot(b[0] - a[0], b[1] - a[1]);

const DIM_PROMPTS: Partial<Record<Tool, string[]>> = {
  DIMLINEAR: ["첫 번째 치수 원점 지정", "두 번째 치수 원점 지정", "치수선 위치 지정"],
  DIMALIGNED: ["첫 번째 치수 원점 지정", "두 번째 치수 원점 지정", "치수선 위치 지정"],
  DIMANGULAR: ["각도 중심점 지정", "첫 번째 각도 점 지정", "두 번째 각도 점 지정", "치수 호 위치 지정"],
  DIMRADIUS: ["원 또는 호를 클릭"],
};

/** Error message for the pick just added (pts includes it), null if fine. */
function dimCheck(tool: Tool, pts: Pt[]): string | null {
  const n = pts.length;
  if ((tool === "DIMLINEAR" || tool === "DIMALIGNED") && n === 2 && dist(pts[0], pts[1]) < EPS) return "두 점이 같습니다. 다른 점을 지정하세요";
  if (tool !== "DIMANGULAR" || n < 2) return null;
  if (dist(pts[0], pts[n - 1]) < EPS) return "중심점과 같은 점입니다. 다른 점을 지정하세요";
  if (n === 3) {
    const [u, v] = [[pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]], [pts[2][0] - pts[0][0], pts[2][1] - pts[0][1]]];
    if (Math.abs(u[0] * v[1] - u[1] * v[0]) <= 1e-9 * Math.hypot(...u) * Math.hypot(...v) && u[0] * v[0] + u[1] * v[1] > 0)
      return "두 점이 같은 방향입니다. 다른 점을 지정하세요";
  }
  return null;
}

/** Dimension geom from the confirmed picks + location, or an error message. */
function dimGeom(tool: Tool, pts: Pt[], loc: Pt): Geom | string {
  const [a, b] = pts;
  if (tool === "DIMLINEAR") {
    const m: Pt = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    // AutoCAD-like: dimension line placed above/below -> horizontal measurement
    const angle = Math.abs(loc[1] - m[1]) >= Math.abs(loc[0] - m[0]) ? 0 : 90;
    return { type: "DIM_LINEAR", p1: a, p2: b, base: loc, angle };
  }
  if (tool === "DIMALIGNED") {
    const d = dist(a, b);
    const distance = ((loc[0] - a[0]) * -(b[1] - a[1]) + (loc[1] - a[1]) * (b[0] - a[0])) / d; // + = left of p1->p2
    return Math.abs(distance) < EPS ? "치수선 위치가 두 점을 잇는 선 위에 있습니다" : { type: "DIM_ALIGNED", p1: a, p2: b, distance };
  }
  return dist(a, loc) < EPS ? "치수 호 위치가 중심점과 같습니다" : { type: "DIM_ANGULAR", center: a, p1: pts[1], p2: pts[2], base: loc };
}

function promptFor({ tool, pts, n: num, target }: ToolState): string {
  const n = pts.length;
  switch (tool) {
    case "SELECT":
      return "SELECT  객체 클릭으로 선택 (Shift: 추가) · 명령 L C A PL REC POL T M CO RO SC MI AR ARP O F TR EX DLI DAL DAN DRA";
    case "RECT":
      return n ? "RECT  반대쪽 구석점 지정" : "RECT  첫 번째 구석점 지정";
    case "POLYGON":
      return n ? "POLYGON  꼭짓점 지정" : `POLYGON  중심점 지정 · 변의 수 입력 (현재 ${num ?? 6})`;
    case "ROTATE":
      return n ? "ROTATE  회전 각도 입력(도, 반시계 +) 또는 점 지정" : "ROTATE  기준점 지정";
    case "SCALE":
      return n ? "SCALE  배율 입력 (예: 2, 0.5)" : "SCALE  기준점 지정";
    case "MIRROR":
      return n ? "MIRROR  대칭선 두 번째 점 지정 (원본 유지)" : "MIRROR  대칭선 첫 번째 점 지정";
    case "ARRAY":
      return num === undefined ? "ARRAY  개수 입력 (원본 포함 2~500)" : n ? "ARRAY  다음 복사 위치 지정 (간격)" : "ARRAY  기준점 지정";
    case "ARRAYPOLAR":
      return num === undefined ? "ARRAYPOLAR  개수 입력 (원본 포함 2~360)" : "ARRAYPOLAR  회전 중심점 지정";
    case "OFFSET":
      return num === undefined ? "OFFSET  간격 입력 (mm)" : target ? "OFFSET  간격띄울 쪽 클릭" : `OFFSET  객체 클릭 (간격 ${num} mm)`;
    case "FILLET":
      return target ? "FILLET  두 번째 선 클릭" : `FILLET  첫 번째 선 클릭 · 반지름 입력 (현재 ${num ?? 0} mm)`;
    case "TRIM":
      return "TRIM  잘라낼 부분 클릭 (경계: 다른 모든 객체) · [Esc] 종료";
    case "EXTEND":
      return "EXTEND  연장할 끝 쪽 클릭 (경계: 다른 모든 객체) · [Esc] 종료";
    case "LINE":
      return n ? "LINE  다음 점 지정 또는 [Enter/Esc] 종료" : "LINE  시작점 지정";
    case "CIRCLE":
      return n ? "CIRCLE  반지름 지정 (점 클릭 또는 값 입력)" : "CIRCLE  중심점 지정";
    case "ARC":
      return ["ARC  첫 번째 점 지정", "ARC  두 번째 점 지정", "ARC  세 번째 점 지정"][n];
    case "PLINE":
      return n ? "PLINE  다음 점 지정 · [C] 닫기 · [Enter] 완료 · [Esc] 취소" : "PLINE  시작점 지정";
    case "TEXT":
      return n ? "TEXT  문자열 입력 후 Enter" : "TEXT  삽입점 지정";
    case "MOVE":
    case "COPY":
      return n ? `${tool}  목표점 지정` : `${tool}  기준점 지정`;
    default:
      return `${tool}  ${DIM_PROMPTS[tool]![n]}`;
  }
}

export function start(tool: Tool, selCount: number): StepOut {
  if (SEL_TOOLS.includes(tool) && selCount === 0)
    return { state: { tool: "SELECT", pts: [] }, prompt: `${tool}  먼저 객체를 선택하세요`, results: [] };
  const state = { tool, pts: [] };
  return { state, prompt: promptFor(state), results: [] };
}

export function step(s: ToolState, ev: ToolEvent, sel: Sel[], ctx: Ctx = { bounds: [], reach: 0 }): StepOut {
  const go = (pts: Pt[], results: Result[] = [], tool: Tool = s.tool, extra: Partial<ToolState> = { n: s.n }): StepOut => {
    const state = { tool, pts, ...extra };
    return { state, prompt: promptFor(state), results };
  };
  const keepN = (results: Result[] = []) => go([], results, s.tool, { n: s.n }); // repeatable commands
  const others = (h: string) => ctx.bounds.filter((b) => b.h !== h);
  // replacement of one entity by 1..n geoms: same type updates in place, otherwise delete + create
  const replace = (t: Sel, geoms: Geom[]): Result[] =>
    geoms[0].type === t.geom.type
      ? [{ updated: [{ handle: t.handle, geom: geoms[0] }], created: geoms.slice(1).map((geom) => ({ geom, layer: t.layer })) }]
      : [{ deleted: [t.handle], created: geoms.map((geom) => ({ geom, layer: t.layer })) }];
  const reject = (msg: string): StepOut => ({ state: s, prompt: `${s.tool}  ${msg}`, results: [] });
  const end = () => go([], [], "SELECT");
  const make = (geom: Geom): Result[] => [{ created: [{ geom }] }];
  const last = s.pts[s.pts.length - 1];

  if (s.tool === "SELECT") return go([]);
  if (ev.kind === "esc") return s.tool === "LINE" || !last ? end() : go([]);
  if (ev.kind === "enter" && !last) return end();

  switch (s.tool) {
    case "LINE":
      if (ev.kind === "enter") return end();
      if (ev.kind !== "point") break;
      if (!last) return go([ev.p]);
      if (dist(last, ev.p) < EPS) return reject("길이 0인 선은 만들 수 없습니다. 다른 점을 지정하세요");
      return go([ev.p], make({ type: "LINE", start: last, end: ev.p }));
    case "CIRCLE": {
      if (ev.kind === "point" && !last) return go([ev.p]);
      if (ev.kind !== "point" && ev.kind !== "number") break;
      if (!last) return reject("중심점을 먼저 지정하세요");
      const r = ev.kind === "point" ? dist(last, ev.p) : ev.n;
      if (!(r > EPS)) return reject("반지름은 0보다 커야 합니다");
      return go([], make({ type: "CIRCLE", center: last, radius: r }));
    }
    case "ARC": {
      if (ev.kind !== "point") break;
      if (s.pts.length < 2) return go([...s.pts, ev.p]);
      const arc = arcFrom3Points(s.pts[0], s.pts[1], ev.p);
      return arc ? go([], make(arc)) : reject("세 점이 일직선입니다. 세 번째 점을 다시 지정하세요");
    }
    case "PLINE": {
      const commit = (closed: boolean) =>
        go([], make({ type: "LWPOLYLINE", points: s.pts.map(([x, y]) => [x, y, 0]), closed }));
      if (ev.kind === "point") {
        if (last && dist(last, ev.p) < EPS) return reject("직전 점과 같은 점입니다");
        return go([...s.pts, ev.p]);
      }
      if (ev.kind === "close") return s.pts.length >= 3 ? commit(true) : reject("닫으려면 점이 3개 이상 필요합니다");
      if (ev.kind === "enter") return s.pts.length >= 2 ? commit(false) : reject("점이 2개 이상 필요합니다");
      break;
    }
    case "TEXT":
      if (ev.kind === "point") return go([ev.p]);
      if (ev.kind === "enter" || ev.kind === "text") {
        if (!last) return reject("삽입점을 먼저 지정하세요");
        const value = ev.kind === "text" ? ev.s.trim() : "";
        if (!value) return reject("문자열이 비어 있습니다");
        return go([], make({ type: "TEXT", insert: last, height: TEXT_HEIGHT, value, rotation: 0 }));
      }
      break;
    case "MOVE":
    case "COPY": {
      if (ev.kind !== "point") break;
      if (!last) return go([ev.p]);
      const dx = ev.p[0] - last[0];
      const dy = ev.p[1] - last[1];
      if (Math.hypot(dx, dy) < EPS) return reject("이동 거리가 0입니다. 다른 목표점을 지정하세요");
      if (!sel.length) return reject("선택된 객체가 없습니다");
      const results: Result[] =
        s.tool === "MOVE"
          ? [{ updated: sel.map((e) => ({ handle: e.handle, geom: translate(e.geom, dx, dy) })) }]
          : [{ created: sel.map((e) => ({ geom: translate(e.geom, dx, dy), layer: e.layer })) }];
      return go([], results, "SELECT");
    }
    case "DIMLINEAR":
    case "DIMALIGNED":
    case "DIMANGULAR": {
      if (ev.kind !== "point") break;
      const pts = [...s.pts, ev.p];
      if (pts.length <= (s.tool === "DIMANGULAR" ? 3 : 2)) {
        const err = dimCheck(s.tool, pts);
        return err ? reject(err) : go(pts);
      }
      const g = dimGeom(s.tool, s.pts, ev.p);
      return typeof g === "string" ? reject(g) : go([], make(g));
    }
    case "RECT": {
      if (ev.kind !== "point") break;
      if (!last) return go([ev.p]);
      const g = rectGeom(last, ev.p);
      return g ? go([], make(g)) : reject("가로·세로가 0이 아닌 점을 지정하세요");
    }
    case "POLYGON": {
      if (ev.kind === "number" && !last)
        return Number.isInteger(ev.n) && ev.n >= 3 && ev.n <= 64 ? go([], [], "POLYGON", { n: ev.n }) : reject("변의 수는 3~64 정수입니다");
      if (ev.kind !== "point") break;
      if (!last) return go([ev.p]);
      const g = polygonGeom(last, ev.p, s.n ?? 6);
      return g ? go([], make(g)) : reject("중심과 다른 점을 지정하세요");
    }
    case "ROTATE":
    case "SCALE": {
      if (ev.kind === "point" && !last) return go([ev.p]);
      if (!last || !sel.length) return reject("기준점을 먼저 지정하세요");
      let v: number;
      if (s.tool === "ROTATE" && ev.kind === "point") {
        if (dist(last, ev.p) < EPS) return reject("기준점과 다른 점을 지정하세요");
        v = (Math.atan2(ev.p[1] - last[1], ev.p[0] - last[0]) * 180) / Math.PI;
      } else if (ev.kind === "number") v = ev.n;
      else break;
      if (s.tool === "SCALE" && !(v > 0 && v <= 1e4)) return reject("배율은 0보다 큰 값이어야 합니다");
      if (s.tool === "ROTATE" && Math.abs(v % 360) < EPS) return reject("회전 각도가 0입니다");
      const f = (g: Geom) => (s.tool === "ROTATE" ? rotateGeom(g, last, v) : scaleGeom(g, last, v));
      return go([], [{ updated: sel.map((e) => ({ handle: e.handle, geom: f(e.geom) })) }], "SELECT");
    }
    case "MIRROR": {
      if (ev.kind !== "point") break;
      if (!last) return go([ev.p]);
      if (dist(last, ev.p) < EPS) return reject("대칭선의 두 점이 같습니다");
      return go([], [{ created: sel.map((e) => ({ geom: mirrorGeom(e.geom, last, ev.p), layer: e.layer })) }], "SELECT");
    }
    case "ARRAY":
    case "ARRAYPOLAR": {
      const max = s.tool === "ARRAY" ? 500 : 360;
      if (s.n === undefined) {
        if (ev.kind !== "number") return reject("개수를 먼저 입력하세요");
        if (!Number.isInteger(ev.n) || ev.n < 2 || ev.n > max) return reject(`개수는 2~${max} 정수입니다`);
        if ((ev.n - 1) * sel.length > 10000) return reject("한 번에 만들 수 있는 객체는 10,000개까지입니다");
        return go([], [], s.tool, { n: ev.n });
      }
      if (ev.kind !== "point") break;
      const k = Array.from({ length: s.n - 1 }, (_, i) => i + 1);
      if (s.tool === "ARRAYPOLAR")
        return go([], [{ created: k.flatMap((i) => sel.map((e) => ({ geom: rotateGeom(e.geom, ev.p, (360 * i) / s.n!), layer: e.layer }))) }], "SELECT");
      if (!last) return go([ev.p]);
      const [dx, dy] = [ev.p[0] - last[0], ev.p[1] - last[1]];
      if (Math.hypot(dx, dy) < EPS) return reject("간격이 0입니다");
      return go([], [{ created: k.flatMap((i) => sel.map((e) => ({ geom: translate(e.geom, dx * i, dy * i), layer: e.layer }))) }], "SELECT");
    }
    case "OFFSET": {
      if (s.n === undefined || (ev.kind === "number" && !s.target)) {
        if (ev.kind !== "number") return reject("간격을 먼저 입력하세요");
        return ev.n > 0 && ev.n <= 1e6 ? go([], [], "OFFSET", { n: ev.n }) : reject("간격은 0보다 커야 합니다");
      }
      if (ev.kind !== "point") break;
      if (!s.target) {
        const t = sel[0];
        if (!t) return reject("간격띄울 객체를 클릭하세요");
        return go([], [], "OFFSET", { n: s.n, target: { ...t, at: ev.p } });
      }
      const g = offsetGeom(s.target.geom, s.n, ev.p);
      return typeof g === "string" ? reject(g) : keepN([{ created: [{ geom: g, layer: s.target.layer }] }]);
    }
    case "FILLET": {
      if (ev.kind === "number" && !s.target) return ev.n >= 0 && ev.n <= 1e6 ? go([], [], "FILLET", { n: ev.n }) : reject("반지름은 0 이상입니다");
      if (ev.kind !== "point") break;
      const t = sel[0];
      if (t?.geom.type !== "LINE") return reject("선(LINE)을 클릭하세요");
      if (!s.target) return go([], [], "FILLET", { n: s.n, target: { ...t, at: ev.p } });
      if (t.handle === s.target.handle) return reject("다른 선을 클릭하세요");
      const first = s.target.geom as Extract<Geom, { type: "LINE" }>;
      const r = filletLines(first, s.target.at, t.geom, ev.p, s.n ?? 0);
      if (typeof r === "string") return reject(r);
      const results: Result[] = [{ updated: [{ handle: s.target.handle, geom: r.l1 }, { handle: t.handle, geom: r.l2 }] }];
      if (r.arc) results.push({ created: [{ geom: r.arc, layer: s.target.layer }] });
      return keepN(results);
    }
    case "TRIM":
    case "EXTEND": {
      if (ev.kind !== "point") break;
      const t = sel[0];
      if (!t) return reject(s.tool === "TRIM" ? "잘라낼 객체를 클릭하세요" : "연장할 객체를 클릭하세요");
      if (s.tool === "TRIM") {
        const r = trimAt(t.geom, ev.p, others(t.handle));
        return typeof r === "string" ? reject(r) : keepN(replace(t, r));
      }
      const g = extendAt(t.geom, ev.p, others(t.handle), ctx.reach);
      return typeof g === "string" ? reject(g) : keepN([{ updated: [{ handle: t.handle, geom: g }] }]);
    }
    case "DIMRADIUS": {
      if (ev.kind !== "point") break;
      const c = sel[0]?.geom; // caller passes the entity under the pick
      if (c?.type !== "CIRCLE" && c?.type !== "ARC") return reject("원 또는 호만 선택할 수 있습니다");
      if (!(c.radius > EPS)) return reject("반지름은 0보다 커야 합니다");
      if (dist(c.center, ev.p) < EPS) return reject("중심이 아닌 위치를 클릭하세요");
      const angle = (Math.atan2(ev.p[1] - c.center[1], ev.p[0] - c.center[0]) * 180) / Math.PI;
      return go([], make({ type: "DIM_RADIUS", center: c.center, radius: c.radius, angle }));
    }
  }
  return reject("지원하지 않는 입력입니다");
}

/** Rubber-band paths (world mm) for the in-progress command. */
export function preview(s: ToolState, cur: Pt, sel: Sel[]): Pt[][] {
  const last = s.pts[s.pts.length - 1];
  if (!last) return [];
  switch (s.tool) {
    case "LINE":
    case "PLINE":
      return [[...s.pts, cur]];
    case "CIRCLE":
      return dist(last, cur) > EPS ? toPaths({ type: "CIRCLE", center: last, radius: dist(last, cur) }) : [];
    case "ARC": {
      const arc = s.pts.length === 2 ? arcFrom3Points(s.pts[0], s.pts[1], cur) : null;
      return arc ? toPaths(arc) : [[...s.pts, cur]];
    }
    case "MOVE":
    case "COPY":
      return [[last, cur], ...sel.flatMap((e) => toPaths(translate(e.geom, cur[0] - last[0], cur[1] - last[1])))];
    case "RECT": {
      const g = rectGeom(last, cur);
      return g ? toPaths(g) : [];
    }
    case "POLYGON": {
      const g = polygonGeom(last, cur, s.n ?? 6);
      return g ? toPaths(g) : [];
    }
    case "ROTATE": {
      if (dist(last, cur) < EPS) return [];
      const a = (Math.atan2(cur[1] - last[1], cur[0] - last[0]) * 180) / Math.PI;
      return [[last, cur], ...sel.flatMap((e) => toPaths(rotateGeom(e.geom, last, a)))];
    }
    case "MIRROR":
      return dist(last, cur) < EPS ? [] : [[last, cur], ...sel.flatMap((e) => toPaths(mirrorGeom(e.geom, last, cur)))];
    case "ARRAY": {
      const n = s.n ?? 2;
      const [dx, dy] = [cur[0] - last[0], cur[1] - last[1]];
      return [[last, cur], ...Array.from({ length: n - 1 }, (_, i) => sel.flatMap((e) => toPaths(translate(e.geom, dx * (i + 1), dy * (i + 1))))).flat()];
    }
    case "DIMLINEAR":
    case "DIMALIGNED":
    case "DIMANGULAR": {
      if (s.pts.length < (s.tool === "DIMANGULAR" ? 3 : 2)) return [[last, cur]];
      const g = dimGeom(s.tool, s.pts, cur);
      return typeof g === "string" ? [] : toPaths(g);
    }
    default:
      return [];
  }
}

export type Parsed = { ev: ToolEvent } | { tool: Tool } | { error: string };
const ALIAS: Record<string, Tool> = {
  V: "SELECT", SELECT: "SELECT", L: "LINE", LINE: "LINE", C: "CIRCLE", CIRCLE: "CIRCLE", A: "ARC", ARC: "ARC",
  PL: "PLINE", PLINE: "PLINE", T: "TEXT", TEXT: "TEXT", M: "MOVE", MOVE: "MOVE", CO: "COPY", COPY: "COPY",
  DLI: "DIMLINEAR", DAL: "DIMALIGNED", DAN: "DIMANGULAR", DRA: "DIMRADIUS",
  REC: "RECT", RECT: "RECT", POL: "POLYGON", POLYGON: "POLYGON", RO: "ROTATE", ROTATE: "ROTATE", SC: "SCALE", SCALE: "SCALE",
  MI: "MIRROR", MIRROR: "MIRROR", AR: "ARRAY", ARRAY: "ARRAY", ARP: "ARRAYPOLAR", ARRAYPOLAR: "ARRAYPOLAR",
  O: "OFFSET", OFFSET: "OFFSET", F: "FILLET", FILLET: "FILLET", TR: "TRIM", TRIM: "TRIM", EX: "EXTEND", EXTEND: "EXTEND",
};
const N = "[-+]?(?:\\d+(?:\\.\\d*)?|\\.\\d+)"; // no nested quantifiers: linear on bad input
const COORD = new RegExp(`^(@)?(${N})\\s*,\\s*(${N})$`);
const NUM = new RegExp(`^${N}$`);

/** Command prompt text -> reducer event, tool switch, or error. */
export function parseInput(raw: string, s: ToolState): Parsed {
  const t = raw.trim();
  if (s.tool === "TEXT" && s.pts.length === 1) return t ? { ev: { kind: "text", s: t } } : { ev: { kind: "enter" } };
  if (!t) return { ev: { kind: "enter" } };
  const u = t.toUpperCase();
  if (s.tool === "PLINE" && u === "C") return { ev: { kind: "close" } };
  const m = COORD.exec(t);
  if (m) {
    const [x, y] = [Number(m[2]), Number(m[3])];
    const last = s.pts[s.pts.length - 1];
    if (!m[1]) return { ev: { kind: "point", p: [x, y] } };
    return last ? { ev: { kind: "point", p: [last[0] + x, last[1] + y] } } : { error: "상대 좌표의 기준점이 없습니다" };
  }
  if (NUM.test(t)) return { ev: { kind: "number", n: Number(t) } };
  return ALIAS[u] ? { tool: ALIAS[u] } : { error: `알 수 없는 입력: ${t}` };
}
