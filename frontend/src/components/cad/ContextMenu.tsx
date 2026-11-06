"use client";

import { useEffect, useRef } from "react";

export type MenuItem = { label: string; key: string; disabled?: boolean };

type Props = {
  x: number;
  y: number;
  items: MenuItem[];
  onSelect: (key: string) => void;
  onClose: () => void;
};

export default function ContextMenu({ x, y, items, onSelect, onClose }: Props) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const h = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent && e.key === "Escape") { onClose(); return; }
      if (e instanceof MouseEvent && ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", h);
    document.addEventListener("keydown", h);
    return () => { document.removeEventListener("mousedown", h); document.removeEventListener("keydown", h); };
  }, [onClose]);

  return (
    <div
      ref={ref}
      style={{ position: "fixed", left: x, top: y }}
      className="z-50 min-w-[140px] rounded-md border border-line bg-card py-1 shadow-lg"
    >
      {items.map((it) => (
        <button
          key={it.key}
          type="button"
          disabled={it.disabled}
          onClick={() => { onSelect(it.key); onClose(); }}
          className="block w-full px-3 py-1 text-left text-sm text-foreground hover:bg-hover disabled:opacity-40 disabled:hover:bg-transparent"
        >
          {it.label}
        </button>
      ))}
    </div>
  );
}
