"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { entityPaths, entityText, type Geom } from "@/lib/cad/geom";
import { buildSnapIndex, effectiveGridStep, querySnap, type SnapHit, type SnapKind } from "@/lib/cad/snap";
import { fitExtents, screenToWorld, worldToScreen, zoomAt, type Extents, type Pt, type View } from "@/lib/cad/view";

export type RenderEntity = {
  handle: string;
  type: string;
  layer: string;
  color: string | null;
  paths: [number, number][][];
  geom?: Geom; // present only for editable entities
  text?: { insert: [number, number]; height: number; value: string; rotation: number };
};

type Props = {
  entities: RenderEntity[];
  extents: Extents;
  hidden: Set<string>; // hidden layer names
  onCursor: (p: [number, number] | null) => void;
  onZoom: (scale: number) => void;
  selected: Set<string>;
  preview: Pt[][]; // rubber-band paths in world mm
  onPick: (p: Pt, shift: boolean, scale: number, raw: Pt) => void;
  snapOn: boolean; // false for SELECT: no snapping, no markers
  snapKinds: Set<SnapKind>;
  gridStep: number; // world mm
  showGrid: boolean;
  markers?: Pt[]; // red error markers in world mm (e.g. dangling endpoints)
};

const SNAP_PX = 12;
const NO_MARKERS: Pt[] = [];
const MARKER = { END: ["#10b981", "끝점"], MID: ["#06b6d4", "중점"], CEN: ["#f97316", "중심"], GRID: ["", "그리드"] } as const;

export default function CanvasViewport({ entities, extents, hidden, onCursor, onZoom, selected, preview, onPick, snapOn, snapKinds, gridStep, showGrid, markers = NO_MARKERS }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [view, setView] = useState<View | null>(null);
  const pan = useRef<{ x: number; y: number } | null>(null);
  const space = useRef(false);
  const [snap, setSnap] = useState<SnapHit | null>(null);
  // cell = extents diagonal / 200: a 12 px query covers ~1-4 cells at fit zoom
  const index = useMemo(
    () => buildSnapIndex(entities, Math.hypot(extents.max[0] - extents.min[0], extents.max[1] - extents.min[1]) / 200 || 1, hidden),
    [entities, extents, hidden],
  );
  const snapAt = (v: View, sx: number, sy: number) => {
    const raw = screenToWorld(v, sx, sy);
    const hit = snapOn ? querySnap(index, raw, SNAP_PX / v.scale, snapKinds, effectiveGridStep(gridStep, v.scale)) : null;
    return { raw, hit, p: hit ? ([hit.x, hit.y] as Pt) : raw };
  };

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
      const sel = css.getPropertyValue("--color-cad-selection").trim() || "#2563eb";
      if (showGrid) {
        const step = effectiveGridStep(gridStep, view.scale);
        const [x0, y1] = screenToWorld(view, 0, 0);
        const [x1, y0] = screenToWorld(view, size.w, size.h);
        ctx.strokeStyle = css.getPropertyValue("--canvas-grid").trim() || "rgba(255,255,255,0.06)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        for (let x = Math.ceil(x0 / step); x <= x1 / step; x++) {
          const [sx] = worldToScreen(view, x * step, 0);
          ctx.moveTo(sx, 0);
          ctx.lineTo(sx, size.h);
        }
        for (let y = Math.ceil(y0 / step); y <= y1 / step; y++) {
          const [, sy] = worldToScreen(view, 0, y * step);
          ctx.moveTo(0, sy);
          ctx.lineTo(size.w, sy);
        }
        ctx.stroke();
      }
      for (const en of entities) {
        if (hidden.has(en.layer)) continue;
        const isSel = selected.has(en.handle);
        const color = isSel ? sel : (en.color ?? fg);
        ctx.strokeStyle = color;
        ctx.lineWidth = isSel ? 2 : 1;
        for (const path of entityPaths(en)) {
          if (path.length < 2) continue;
          ctx.beginPath();
          path.forEach(([x, y], i) => {
            const [sx, sy] = worldToScreen(view, x, y);
            if (i === 0) ctx.moveTo(sx, sy);
            else ctx.lineTo(sx, sy);
          });
          ctx.stroke();
        }
        const text = entityText(en);
        if (text) {
          const px = text.height * view.scale;
          if (px < 2) continue;
          const [sx, sy] = worldToScreen(view, text.insert[0], text.insert[1]);
          ctx.save();
          ctx.translate(sx, sy);
          ctx.rotate((-text.rotation * Math.PI) / 180); // rotation assumed in degrees (DXF), Y flipped
          ctx.fillStyle = color;
          ctx.font = `${px}px sans-serif`;
          ctx.textBaseline = "alphabetic";
          ctx.fillText(text.value, 0, 0);
          ctx.restore();
        }
      }
      ctx.lineWidth = 1;
      ctx.strokeStyle = sel;
      ctx.setLineDash([6, 4]);
      for (const path of preview) {
        ctx.beginPath();
        path.forEach(([x, y], i) => {
          const [sx, sy] = worldToScreen(view, x, y);
          if (i === 0) ctx.moveTo(sx, sy);
          else ctx.lineTo(sx, sy);
        });
        ctx.stroke();
      }
      ctx.setLineDash([]);
      ctx.strokeStyle = "#ef4444";
      ctx.lineWidth = 2;
      for (const [x, y] of markers) {
        const [sx, sy] = worldToScreen(view, x, y);
        ctx.beginPath();
        ctx.arc(sx, sy, 7, 0, 2 * Math.PI);
        ctx.moveTo(sx - 5, sy - 5);
        ctx.lineTo(sx + 5, sy + 5);
        ctx.moveTo(sx - 5, sy + 5);
        ctx.lineTo(sx + 5, sy - 5);
        ctx.stroke();
      }
      if (snapOn && snap) {
        const [sx, sy] = worldToScreen(view, snap.x, snap.y);
        const [color, label] = MARKER[snap.kind];
        ctx.strokeStyle = color || fg; // GRID cross uses fg: the grid line color is too faint for a marker
        ctx.lineWidth = 2;
        ctx.beginPath();
        if (snap.kind === "END") ctx.rect(sx - 4, sy - 4, 8, 8);
        else if (snap.kind === "MID") {
          ctx.moveTo(sx, sy - 4);
          ctx.lineTo(sx + 4, sy + 4);
          ctx.lineTo(sx - 4, sy + 4);
          ctx.closePath();
        } else if (snap.kind === "CEN") ctx.arc(sx, sy, 4, 0, 2 * Math.PI);
        else {
          ctx.moveTo(sx - 4, sy);
          ctx.lineTo(sx + 4, sy);
          ctx.moveTo(sx, sy - 4);
          ctx.lineTo(sx, sy + 4);
        }
        ctx.stroke();
        ctx.fillStyle = color || fg;
        ctx.font = "11px sans-serif";
        ctx.textBaseline = "alphabetic";
        ctx.fillText(label, sx + 10, sy - 8);
      }
    });
    return () => cancelAnimationFrame(id);
  }, [view, size, entities, hidden, selected, preview, snap, snapOn, showGrid, gridStep, markers]);

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
          } else if (e.button === 0 && view) {
            const p = local(e);
            onPick(snapAt(view, p.x, p.y).p, e.shiftKey, view.scale, screenToWorld(view, p.x, p.y));
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
          if (view) {
            const s = snapAt(view, p.x, p.y);
            setSnap(s.hit);
            onCursor(s.p);
          }
        }}
        onPointerUp={() => (pan.current = null)}
        onPointerLeave={() => {
          setSnap(null);
          onCursor(null);
        }}
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
