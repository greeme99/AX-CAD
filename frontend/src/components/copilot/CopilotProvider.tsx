"use client";

import { usePathname } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { contextFromPath, isToggleKey, loadOpen, saveOpen, type CopilotContext, type Handlers } from "@/lib/copilot";
import CopilotPanel from "./CopilotPanel";

type Ctx = {
  open: boolean;
  toggle: () => void;
  setOpen: (v: boolean) => void;
  context: CopilotContext;
  handlers: React.RefObject<Handlers>;
};
const C = createContext<Ctx | null>(null);
const HIDDEN = ["/login"]; // signed-out pages: the panel needs the API

// Track 9 Q1: the right-side AI Copilot on every signed-in page (plan.md §15.2). The page
// context comes from the URL; pages that can run proposals register handlers.
export default function CopilotProvider({ children }: { children: React.ReactNode }) {
  const path = usePathname() ?? "/";
  const [open, setOpenState] = useState(false);
  const handlers = useRef<Handlers>({});
  useEffect(() => { setOpenState(loadOpen(window.localStorage)); }, []); // after hydration: SSR renders closed
  const setOpen = useCallback((v: boolean) => {
    setOpenState(v);
    saveOpen(typeof window === "undefined" ? undefined : window.localStorage, v);
  }, []);
  const toggle = useCallback(() => setOpenState((v) => (saveOpen(window.localStorage, !v), !v)), []);
  const hidden = HIDDEN.some((p) => path.startsWith(p));
  useEffect(() => {
    if (hidden) return;
    const onKey = (e: KeyboardEvent) => {
      if (!isToggleKey(e)) return;
      e.preventDefault();
      toggle();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [hidden, toggle]);
  const context = useMemo(() => contextFromPath(path), [path]);
  const value = useMemo(() => ({ open, toggle, setOpen, context, handlers }), [open, toggle, setOpen, context]);
  if (hidden) return <>{children}</>;
  return (
    <C.Provider value={value}>
      <div className="flex min-h-screen">
        <div className="min-w-0 flex-1">{children}</div>
        {open && <CopilotPanel />}
      </div>
    </C.Provider>
  );
}

export const useCopilot = () => useContext(C);

/** A page offers what it can run (2D commands, new features, dialogs, jumping to a finding). The
 * handlers are re-registered every render so they see the page's fresh state. */
export function useCopilotHandlers(h: Handlers) {
  const ctx = useContext(C);
  useEffect(() => {
    if (!ctx) return;
    ctx.handlers.current = h;
    return () => {
      if (ctx.handlers.current === h) ctx.handlers.current = {};
    };
  });
}

/** The header button that shows / hides the panel (also Ctrl+.). */
export function CopilotToggle({ className = "" }: { className?: string }) {
  const ctx = useContext(C);
  if (!ctx) return null;
  return (
    <button
      type="button"
      onClick={ctx.toggle}
      aria-pressed={ctx.open}
      aria-controls="copilot-panel"
      title={`AI Copilot ${ctx.open ? "감추기" : "보이기"} (Ctrl+.)`}
      className={`inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md border px-2.5 py-1 text-sm focus-visible:outline-2 focus-visible:outline-ring ${
        ctx.open ? "border-[var(--color-primary)] bg-[var(--color-primary-light)] text-[var(--color-primary-hover)]" : "border-line text-body hover:bg-hover"
      } ${className}`}
    >
      <svg aria-hidden="true" viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6">
        <path d="M10 2.5l1.6 3.9 3.9 1.6-3.9 1.6L10 13.5 8.4 9.6 4.5 8l3.9-1.6z" />
        <path d="M15.5 13l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z" />
      </svg>
      AI Copilot
    </button>
  );
}
