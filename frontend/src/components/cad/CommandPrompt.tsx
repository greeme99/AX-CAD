"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ALIAS, type Tool } from "@/lib/cad/tools";

type Props = { prompt: string; onSubmit: (text: string) => void };

// ponytail: derive suggestions from ALIAS; short aliases only (skip full-name duplicates)
const LABELS: Record<Tool, string> = {
  SELECT: "선택", LINE: "선", CIRCLE: "원", ARC: "호", PLINE: "폴리라인", RECT: "사각형",
  POLYGON: "다각형", TEXT: "문자", MOVE: "이동", COPY: "복사", ROTATE: "회전", SCALE: "축척",
  MIRROR: "대칭", ARRAY: "배열", ARRAYPOLAR: "원형배열", OFFSET: "간격띄우기", FILLET: "모깎기",
  TRIM: "자르기", EXTEND: "연장", CHAMFER: "모따기", EXPLODE: "분해", JOIN: "결합", BREAK: "끊기",
  STRETCH: "늘리기", HATCH: "해치", BLOCK: "블록", INSERT: "삽입", POINT: "점", ELLIPSE: "타원",
  SPLINE: "스플라인", LEADER: "지시선", SPLIT: "분할", DIST: "거리", AREA: "면적", LIST: "목록",
  DIMLINEAR: "선형치수", DIMALIGNED: "정렬치수", DIMANGULAR: "각도치수", DIMRADIUS: "반지름치수",
  DIMDIAMETER: "지름치수", DIMBASELINE: "기준선치수", DIMCONTINUE: "연속치수",
};
type Suggestion = { alias: string; tool: Tool; label: string };
const SUGGESTIONS: Suggestion[] = Object.entries(ALIAS)
  .filter(([k, v]) => k !== v) // short alias only (L, not LINE)
  .map(([alias, tool]) => ({ alias, tool, label: LABELS[tool] ?? tool }));

const MAX_HISTORY = 50;
const MAX_SHOW = 8;

export default function CommandPrompt({ prompt, onSubmit }: Props) {
  const [value, setValue] = useState("");
  const [history] = useState<string[]>(() => []);
  const histIdx = useRef(-1);
  const [sel, setSel] = useState(-1); // selected suggestion index
  const inputRef = useRef<HTMLInputElement>(null);

  const matches = value.trim()
    ? SUGGESTIONS.filter((s) => s.alias.startsWith(value.trim().toUpperCase()) || s.tool.startsWith(value.trim().toUpperCase())).slice(0, MAX_SHOW)
    : [];

  const submit = useCallback(
    (text: string) => {
      const t = text.trim();
      if (t && (history.length === 0 || history[history.length - 1] !== t)) {
        history.push(t);
        if (history.length > MAX_HISTORY) history.shift();
      }
      histIdx.current = -1;
      setSel(-1);
      setValue("");
      onSubmit(text);
    },
    [history, onSubmit],
  );

  const onKey = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Escape") {
        setSel(-1);
        onSubmit(""); // propagate ESC
        setValue("");
        return;
      }
      // autocomplete navigation
      if (matches.length > 0) {
        if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(s + 1, matches.length - 1)); return; }
        if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(s - 1, -1)); return; }
        if (e.key === "Tab" && sel >= 0) { e.preventDefault(); setValue(matches[sel].alias); setSel(-1); return; }
      }
      // history navigation (only when no autocomplete visible)
      if (matches.length === 0) {
        if (e.key === "ArrowUp") {
          e.preventDefault();
          const idx = histIdx.current < 0 ? history.length - 1 : histIdx.current - 1;
          if (idx >= 0) { histIdx.current = idx; setValue(history[idx]); }
          return;
        }
        if (e.key === "ArrowDown") {
          e.preventDefault();
          if (histIdx.current >= 0) {
            const idx = histIdx.current + 1;
            if (idx < history.length) { histIdx.current = idx; setValue(history[idx]); }
            else { histIdx.current = -1; setValue(""); }
          }
          return;
        }
      }
    },
    [matches, sel, history, onSubmit],
  );

  // focus input on any keypress (AutoCAD-like: type anywhere to command)
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key.length === 1) inputRef.current?.focus();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  return (
    <div className="relative">
      {matches.length > 0 && (
        <ul className="absolute bottom-full left-0 right-0 z-50 max-h-56 overflow-y-auto border border-line bg-card shadow-lg">
          {matches.map((m, i) => (
            <li
              key={m.alias}
              className={`flex cursor-pointer items-center gap-2 px-3 py-1 text-sm ${i === sel ? "bg-[var(--color-primary-light)] text-foreground" : "text-body hover:bg-hover"}`}
              onMouseDown={(e) => { e.preventDefault(); submit(m.alias); }}
              onMouseEnter={() => setSel(i)}
            >
              <kbd className="min-w-[3ch] font-mono font-bold text-foreground">{m.alias}</kbd>
              <span className="text-muted">{m.tool}</span>
              <span>{m.label}</span>
            </li>
          ))}
        </ul>
      )}
      <form
        className="flex items-center gap-2 border-t border-line bg-card px-3 py-1"
        onSubmit={(e) => { e.preventDefault(); if (sel >= 0 && matches[sel]) submit(matches[sel].alias); else submit(value); }}
      >
        <span role="status" className="shrink-0 text-sm text-body">{prompt}</span>
        <input
          ref={inputRef}
          aria-label="명령 입력"
          placeholder="명령 · x,y · @dx,dy · 값"
          autoComplete="off"
          spellCheck={false}
          value={value}
          onChange={(e) => { setValue(e.target.value); setSel(-1); histIdx.current = -1; }}
          onKeyDown={onKey}
          className="min-w-0 flex-1 rounded-md border border-line bg-background px-2 py-1 font-mono text-sm text-foreground focus-visible:outline-2 focus-visible:outline-ring"
        />
      </form>
    </div>
  );
}
