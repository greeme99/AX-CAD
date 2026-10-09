"use client";

import { useEffect, useRef, useState } from "react";
import { fitExtents, screenToWorld, worldToScreen, zoomAt, type Extents, type View } from "@/lib/cad/view";

export type RenderEntity = {
  handle: string;
  type: string;
  layer: string;
  color: string | null;
  paths: [number, number][][];
  text?: { insert: [number, number]; height: number; value: string; rotation: number };
};

type Props = {
  entities: RenderEntity[];
  extents: Extents;
  hidden: Set<string>; // hidden layer names
  onCursor: (p: [number, number] | null) => void;
  onZoom: (scale: number) => void;
};

export default function CanvasViewport({ entities, extents, hidden, onCursor, onZoom }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [view, setView] = useState<View | null>(null);
  const pan = useRef<{ x: number; y: number } | null>(null);
  const space = useRef(false);

  const fit = (s = size) => setView(fitExtents(extents, s.w, s.h, 32));

  // resize
  useEffect(() => {
    const el = wrapRef.current!;
    const ro = new ResizeObserver(([e]) => {
      const { width, height } = e.contentRect;
      setSize({ w: width, h: height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // initial fit once a real size is known
  useEffect(() => {
    if (!view && size.w > 0 && size.h > 0) setView(fitExtents(extents, size.w, size.h, 32));
  }, [view, size, extents]);

  useEffect(() => {
    if (view) onZoom(view.scale);
  }, [view, onZoom]);

  // wheel needs a non-passive listener to preventDefault
  useEffect(() => {
    const c = canvasRef.current!;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = c.getBoundingClientRect();
      const f = e.deltaY < 0 ? 1.2 : 1 / 1.2;
      setView((v) => (v ? zoomAt(v, e.clientX - r.left, e.clientY - r.top, f) : v));
    };
    c.addEventListener("wheel", onWheel, { passive: false });
    return () => c.removeEventListener("wheel", onWheel);
  }, []);

  // F = fit, Space = pan modifier
  useEffect(() => {
    const interactive = (t: EventTarget | null) => t instanceof HTMLElement && /^(INPUT|BUTTON|TEXTAREA|SELECT)$/.test(t.tagName);
    const down = (e: KeyboardEvent) => {
      if (interactive(e.target)) return;
      if (e.code === "Space") {
        space.current = true;
        e.preventDefault();
      } else if (e.key === "f" || e.key === "F") fit();
    };
    const up = (e: KeyboardEvent) => {
      if (e.code === "Space") space.current = false;
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [size, extents]);

  // draw
  useEffect(() => {
    const c = canvasRef.current;
    if (!c || !view || size.w === 0) return;
    const id = requestAnimationFrame(() => {
      const dpr = window.devicePixelRatio || 1;
      c.width = Math.round(size.w * dpr);
      c.height = Math.round(size.h * dpr);
      const ctx = c.getContext("2d")!;
      const css = getComputedStyle(c);
      const bg = css.getPropertyValue("--canvas-bg").trim() || "#181b20";
      const fg = css.getPropertyValue("--canvas-fg").trim() || "#e5e7eb";
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = bg;
      ctx.fillRect(0, 0, size.w, size.h);
      ctx.lineWidth = 1;
      for (const en of entities) {
        if (hidden.has(en.layer)) continue;
        const color = en.color ?? fg;
        ctx.strokeStyle = color;
        for (const path of en.paths) {
          if (path.length < 2) continue;
          ctx.beginPath();
          path.forEach(([x, y], i) => {
            const [sx, sy] = worldToScreen(view, x, y);
            if (i === 0) ctx.moveTo(sx, sy);
            else ctx.lineTo(sx, sy);
          });
          ctx.stroke();
        }
        if (en.text) {
          const px = en.text.height * view.scale;
          if (px < 2) continue;
          const [sx, sy] = worldToScreen(view, en.text.insert[0], en.text.insert[1]);
          ctx.save();
          ctx.translate(sx, sy);
          ctx.rotate((-en.text.rotation * Math.PI) / 180); // rotation assumed in degrees (DXF), Y flipped
          ctx.fillStyle = color;
          ctx.font = `${px}px sans-serif`;
          ctx.textBaseline = "alphabetic";
          ctx.fillText(en.text.value, 0, 0);
          ctx.restore();
        }
      }
    });
    return () => cancelAnimationFrame(id);
  }, [view, size, entities, hidden]);

  const local = (e: React.PointerEvent) => {
    const r = canvasRef.current!.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };

  return (
    <div ref={wrapRef} className="relative h-full w-full overflow-hidden bg-canvas">
      <canvas
        ref={canvasRef}
        aria-label="도면 캔버스"
        style={{ width: size.w, height: size.h, cursor: pan.current ? "grabbing" : "crosshair" }}
        className="block"
        onContextMenu={(e) => e.preventDefault()}
        onPointerDown={(e) => {
          if (e.button === 1 || (e.button === 0 && space.current)) {
            e.preventDefault();
            e.currentTarget.setPointerCapture(e.pointerId);
            pan.current = local(e);
          }
        }}
        onPointerMove={(e) => {
          const p = local(e);
          if (pan.current) {
            const dx = p.x - pan.current.x;
            const dy = p.y - pan.current.y;
            pan.current = p;
            setView((v) => (v ? { ...v, offsetX: v.offsetX + dx, offsetY: v.offsetY + dy } : v));
          }
          if (view) onCursor(screenToWorld(view, p.x, p.y));
        }}
        onPointerUp={() => (pan.current = null)}
        onPointerLeave={() => onCursor(null)}
      />
      <button
        type="button"
        onClick={() => fit()}
        className="absolute right-3 bottom-3 rounded-md border border-line bg-card px-3 py-1 text-sm text-foreground hover:bg-hover focus-visible:outline-2 focus-visible:outline-ring"
      >
        Fit (F)
      </button>
    </div>
  );
}
