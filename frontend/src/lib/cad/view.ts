export type View = { scale: number; offsetX: number; offsetY: number };
export type Pt = [number, number];
export type Extents = { min: Pt; max: Pt };

// screen px = world * scale + offset, Y flipped (world Y up, screen Y down)
export function worldToScreen(v: View, x: number, y: number): Pt {
  return [x * v.scale + v.offsetX, -y * v.scale + v.offsetY];
}

export function screenToWorld(v: View, sx: number, sy: number): Pt {
  return [(sx - v.offsetX) / v.scale, (v.offsetY - sy) / v.scale];
}

export function zoomAt(v: View, sx: number, sy: number, factor: number): View {
  return {
    scale: v.scale * factor,
    offsetX: sx - (sx - v.offsetX) * factor,
    offsetY: sy - (sy - v.offsetY) * factor,
  };
}

export function fitExtents(e: Extents, width: number, height: number, margin: number): View {
  const w = e.max[0] - e.min[0];
  const h = e.max[1] - e.min[1];
  const availW = Math.max(width - 2 * margin, 1);
  const availH = Math.max(height - 2 * margin, 1);
  // degenerate extents (single point / line): fall back to scale 1
  const s = Math.min(w > 0 ? availW / w : Infinity, h > 0 ? availH / h : Infinity);
  const scale = Number.isFinite(s) ? s : 1;
  const cx = (e.min[0] + e.max[0]) / 2;
  const cy = (e.min[1] + e.max[1]) / 2;
  return { scale, offsetX: width / 2 - cx * scale, offsetY: height / 2 + cy * scale };
}
