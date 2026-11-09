// 2D automation A4: the command journal — what the user does on the command line, the tool
// palette, the keyboard and the canvas, written as a .axs script (one prompt input per line) so it
// replays through runScript like the NX journal or a recorded AutoCAD .scr.
// Coordinates: WCS, the drawing unit (mm), up to 9 decimals; a pick of TRIM/FILLET/... is written
// as the point on the picked entity, so the replay picks the same one (see runInputs).
import { distToPaths, hitPaths } from "./geom";
import type { Pt } from "./view";
import { ALIAS, type Sel, type Tool, type ToolEvent } from "./tools";

export const MAX_JOURNAL_LINES = 10_000; // = MAX_SCRIPT_LINES: a journal always replays

/** A number as the command line reads it (no exponent; -0 and trailing zeros dropped). */
export function num(n: number): string {
  const s = n.toFixed(9).replace(/\.?0+$/, "");
  return s === "-0" ? "0" : s;
}

/** The command name the command line accepts for a tool (its full name when it has one). */
export function toolText(t: Tool): string {
  if (ALIAS[t] === t) return t;
  return Object.keys(ALIAS).find((k) => ALIAS[k] === t) ?? t;
}

/** The line for an accepted tool event; null when it cannot be written as a line. */
export function eventText(ev: ToolEvent): string | null {
  switch (ev.kind) {
    case "point":
      return `${num(ev.p[0])},${num(ev.p[1])}`;
    case "number":
      return num(ev.n);
    case "text":
      // a line is trimmed, ";" starts a comment and ^C cancels: such text does not replay
      return ev.s === ev.s.trim() && ev.s && !ev.s.startsWith(";") && ev.s !== "^C" ? ev.s : null;
    case "close":
      return "C";
    case "option":
      return ev.s;
    case "enter":
      return "";
    case "esc":
      return "^C";
  }
}

/** The point of the paths nearest to p (a pick written onto the picked entity). */
export function onPaths(paths: Pt[][], p: Pt): Pt {
  let best: Pt = p;
  let bestD = Infinity;
  for (const path of paths)
    for (let i = 0; i + 1 < path.length; i++) {
      const [a, b] = [path[i], path[i + 1]];
      const [dx, dy] = [b[0] - a[0], b[1] - a[1]];
      const len2 = dx * dx + dy * dy;
      const t = len2 ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len2)) : 0;
      const q: Pt = [a[0] + t * dx, a[1] + t * dy];
      const d = Math.hypot(p[0] - q[0], p[1] - q[1]);
      if (d < bestD) [best, bestD] = [q, d];
    }
  return best;
}

/** How near a replayed pick must be to an entity's outline to pick it (drawing units). */
export const PICK_ON = 1e-6;

/** The editable entity a replayed pick lands on (the nearest within PICK_ON). */
export function pickedAt(editable: Sel[], p: Pt): Sel | undefined {
  let best: Sel | undefined;
  let bestD = PICK_ON;
  for (const e of editable) {
    const d = distToPaths(hitPaths({ geom: e.geom, paths: [] }), p);
    if (d <= bestD) [best, bestD] = [e, d];
  }
  return best;
}

export type Journal = { lines: string[]; started: Date; notes: number };

export const newJournal = (): Journal => ({ lines: [], started: new Date(), notes: 0 });

/** Adds a line; false when the journal is full (the recording then stops). */
export function record(j: Journal, line: string): boolean {
  if (j.lines.length >= MAX_JOURNAL_LINES) return false;
  j.lines.push(line);
  return true;
}

/** Adds a comment for something done that a script cannot replay (shown, not run). */
export function note(j: Journal, text: string): boolean {
  j.notes++;
  return record(j, `; ※ ${text.replace(/[\r\n]+/g, " ")}`);
}

/** The .axs text: a header (what, when, units) and the recorded lines. */
export function journalText(j: Journal, about: string): string {
  const head = [
    `; AX-CAD 명령 기록 (.axs) — ${about.replace(/[\r\n]+/g, " ")}`,
    `; 기록 ${j.started.toLocaleString("ko-KR")} · 좌표 WCS·도면 단위(mm) · 각도 도`,
    "; 한 줄에 명령 입력 하나 (빈 줄 = Enter, ^C = 취소). 선택이 필요한 명령은 재생할 때의 선택에 적용됩니다",
  ];
  if (j.notes) head.push(`; ※ 표시 ${j.notes}건은 스크립트로 재생되지 않는 작업입니다`);
  return [...head, ...j.lines, ""].join("\n");
}
