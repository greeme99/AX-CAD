import { arcFrom3Points, toPaths, translate, type Geom } from "./geom";
import type { Pt } from "./view";

export type Tool = "SELECT" | "LINE" | "CIRCLE" | "ARC" | "PLINE" | "TEXT" | "MOVE" | "COPY";
export type ToolState = { tool: Tool; pts: Pt[] }; // pts = picks of the in-progress command
export type Sel = { handle: string; layer: string; geom: Geom }; // editable, unlocked selection
export type ToolEvent =
  | { kind: "point"; p: Pt }
  | { kind: "number"; n: number }
  | { kind: "text"; s: string }
  | { kind: "close" }
  | { kind: "enter" }
  | { kind: "esc" };
export type Result = { created?: { geom: Geom; layer?: string }[]; updated?: { handle: string; geom: Geom }[] };
export type StepOut = { state: ToolState; prompt: string; results: Result[] };

const EPS = 1e-9;
export const TEXT_HEIGHT = 2.5;
const dist = (a: Pt, b: Pt) => Math.hypot(b[0] - a[0], b[1] - a[1]);

function promptFor({ tool, pts }: ToolState): string {
  const n = pts.length;
  switch (tool) {
    case "SELECT":
      return "SELECT  객체 클릭으로 선택 (Shift: 추가) · 명령 L C A PL T M CO";
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
  }
}

export function start(tool: Tool, selCount: number): StepOut {
  if ((tool === "MOVE" || tool === "COPY") && selCount === 0)
    return { state: { tool: "SELECT", pts: [] }, prompt: `${tool}  먼저 객체를 선택하세요`, results: [] };
  const state = { tool, pts: [] };
  return { state, prompt: promptFor(state), results: [] };
}

export function step(s: ToolState, ev: ToolEvent, sel: Sel[]): StepOut {
  const go = (pts: Pt[], results: Result[] = [], tool: Tool = s.tool): StepOut => {
    const state = { tool, pts };
    return { state, prompt: promptFor(state), results };
  };
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
    default:
      return [];
  }
}

export type Parsed = { ev: ToolEvent } | { tool: Tool } | { error: string };
const ALIAS: Record<string, Tool> = {
  V: "SELECT", SELECT: "SELECT", L: "LINE", LINE: "LINE", C: "CIRCLE", CIRCLE: "CIRCLE", A: "ARC", ARC: "ARC",
  PL: "PLINE", PLINE: "PLINE", T: "TEXT", TEXT: "TEXT", M: "MOVE", MOVE: "MOVE", CO: "COPY", COPY: "COPY",
};
const N = "[-+]?(?:\\d+\\.?\\d*|\\.\\d+)";
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
