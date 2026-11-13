"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type Pt = [number, number];

type Props = {
  enabled: boolean;
  screen: { x: number; y: number } | null;
  cursor: Pt | null;
  base: Pt | null;
  onSubmit: (text: string) => void;
  tool: string;
};

const OFFSET_X = 24;
const OFFSET_Y = 24;

function fmt(n: number) {
  return Math.abs(n) < 0.005 ? "0.00" : n.toFixed(2);
}

export default function DynamicInput({ enabled, screen, cursor, base, onSubmit, tool }: Props) {
  const [mode, setMode] = useState<"abs" | "rel">("abs");
  const [xVal, setXVal] = useState("");
  const [yVal, setYVal] = useState("");
  const [focused, setFocused] = useState<"x" | "y">("x");
  const xRef = useRef<HTMLInputElement>(null);
  const yRef = useRef<HTMLInputElement>(null);
  const prevCursor = useRef<Pt | null>(null);

  // sync display with cursor movement when not focused on input
  useEffect(() => {
    if (!cursor || !enabled) return;
    prevCursor.current = cursor;
    if (document.activeElement === xRef.current || document.activeElement === yRef.current) return;
    if (mode === "rel" && base) {
      setXVal(fmt(cursor[0] - base[0]));
      setYVal(fmt(cursor[1] - base[1]));
    } else {
      setXVal(fmt(cursor[0]));
      setYVal(fmt(cursor[1]));
    }
  }, [cursor, base, mode, enabled]);

  // toggle between abs and rel with Tab when nothing is focused
  const handleKey = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Tab" && !e.shiftKey) {
        e.preventDefault();
        if (focused === "x") {
          setFocused("y");
          yRef.current?.focus();
          yRef.current?.select();
        } else {
          setFocused("x");
          xRef.current?.focus();
          xRef.current?.select();
        }
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        const x = xVal.trim();
        const y = yVal.trim();
        if (!x || !y) return;
        const prefix = mode === "rel" && base ? "@" : "";
        onSubmit(`${prefix}${x},${y}`);
        setXVal("");
        setYVal("");
        return;
      }
      if (e.key === "#") {
        e.preventDefault();
        setMode((m) => (m === "abs" ? "rel" : "abs"));
        return;
      }
    },
    [focused, xVal, yVal, mode, base, onSubmit],
  );

  if (!enabled || !screen || tool === "SELECT") return null;

  const left = screen.x + OFFSET_X;
  const top = screen.y + OFFSET_Y;

  return (
    <div
      className="pointer-events-auto absolute z-50"
      style={{ left, top }}
      onKeyDown={handleKey}
    >
      <div className="flex items-center gap-1 rounded border border-[var(--color-mode-accent,var(--color-primary))] bg-card/95 px-1.5 py-0.5 text-xs font-mono shadow-lg backdrop-blur-sm">
        <button
          type="button"
          onClick={() => setMode((m) => (m === "abs" ? "rel" : "abs"))}
          className="shrink-0 rounded px-1 text-[10px] font-semibold text-[var(--color-mode-accent,var(--color-primary))] hover:bg-hover"
          title="# 키로 전환"
        >
          {mode === "abs" ? "ABS" : "REL"}
        </button>
        <label className="flex items-center gap-0.5">
          <span className="text-muted-foreground">X</span>
          <input
            ref={xRef}
            type="text"
            inputMode="decimal"
            value={xVal}
            onChange={(e) => setXVal(e.target.value)}
            onFocus={() => { setFocused("x"); xRef.current?.select(); }}
            className="w-16 rounded border border-line bg-background px-1 py-0.5 text-right text-xs text-foreground outline-none focus:border-[var(--color-mode-accent,var(--color-primary))]"
          />
        </label>
        <label className="flex items-center gap-0.5">
          <span className="text-muted-foreground">Y</span>
          <input
            ref={yRef}
            type="text"
            inputMode="decimal"
            value={yVal}
            onChange={(e) => setYVal(e.target.value)}
            onFocus={() => { setFocused("y"); yRef.current?.select(); }}
            className="w-16 rounded border border-line bg-background px-1 py-0.5 text-right text-xs text-foreground outline-none focus:border-[var(--color-mode-accent,var(--color-primary))]"
          />
        </label>
      </div>
      {base && mode === "rel" && cursor && (
        <div className="mt-0.5 flex gap-2 rounded bg-card/90 px-1.5 py-0.5 text-[10px] text-muted-foreground font-mono">
          <span>D={fmt(Math.hypot(cursor[0] - base[0], cursor[1] - base[1]))}</span>
          <span>A={fmt((Math.atan2(cursor[1] - base[1], cursor[0] - base[0]) * 180) / Math.PI)}°</span>
        </div>
      )}
    </div>
  );
}
