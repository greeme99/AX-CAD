import { toPaths, type Geom } from "./geom";

type Pt = [number, number];

export type PlaceOptions = {
  rotation: number;
  xscale: number;
  yscale: number;
  flipX: boolean;
  flipY: boolean;
  layer?: string;
  cols: number;
  rows: number;
  dx: number;
  dy: number;
  keepOpen: boolean;
};

export const DEFAULT_PLACE_OPTIONS: PlaceOptions = {
  rotation: 0,
  xscale: 1,
  yscale: 1,
  flipX: false,
  flipY: false,
  cols: 1,
  rows: 1,
  dx: 1000,
  dy: 1000,
  keepOpen: false,
};

export function buildInsertSpecs(
  blockName: string,
  at: [number, number],
  opts: Partial<PlaceOptions> = {},
): Array<{ geom: Geom; layer?: string }> {
  const o: PlaceOptions = { ...DEFAULT_PLACE_OPTIONS, ...opts };
  const rot = ((Number.isFinite(o.rotation) ? o.rotation : 0) % 360 + 360) % 360;
  const sxMag = Math.max(0.01, Math.min(1000, Math.abs(Number.isFinite(o.xscale) ? o.xscale : 1)));
  const syMag = Math.max(0.01, Math.min(1000, Math.abs(Number.isFinite(o.yscale) ? o.yscale : 1)));
  const xscale = o.flipX ? -sxMag : sxMag;
  const yscale = o.flipY ? -syMag : syMag;
  const cols = Math.max(1, Math.min(20, Math.trunc(Number.isFinite(o.cols) ? o.cols : 1)));
  const rows = Math.max(1, Math.min(20, Math.trunc(Number.isFinite(o.rows) ? o.rows : 1)));
  const dx = Number.isFinite(o.dx) ? o.dx : 0;
  const dy = Number.isFinite(o.dy) ? o.dy : 0;

  const out: Array<{ geom: Geom; layer?: string }> = [];
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const insert: [number, number] = [at[0] + c * dx, at[1] + r * dy];
      const geom: Geom = { type: "INSERT", name: blockName, insert, xscale, yscale, rotation: rot };
      out.push(o.layer ? { geom, layer: o.layer } : { geom });
    }
  }
  return out;
}

export type PreviewSvg = {
  viewBox: string;
  paths: string[];
  originCross: [number, number, number];
};

function transformPt([x, y]: Pt, sx: number, sy: number, rad: number): Pt {
  const tx = x * sx;
  const ty = y * sy;
  const c = Math.cos(rad);
  const s = Math.sin(rad);
  return [tx * c - ty * s, tx * s + ty * c];
}

/** Convert block geoms (CAD Y-up) into an SVG preview (SVG Y-down) with rotation & flip applied. */
export function geomsToPreviewSvg(
  geoms: Geom[],
  opts: Pick<PlaceOptions, "rotation" | "flipX" | "flipY"> = { rotation: 0, flipX: false, flipY: false },
): PreviewSvg {
  const sx = opts.flipX ? -1 : 1;
  const sy = opts.flipY ? -1 : 1;
  const rad = ((opts.rotation || 0) * Math.PI) / 180;
  const rawPolys = geoms.flatMap((g) => toPaths(g));
  const polys = rawPolys.map((poly) => poly.map((pt) => transformPt(pt, sx, sy, rad)));

  let minX = 0;
  let maxX = 0;
  let minY = 0;
  let maxY = 0;
  for (const poly of polys) {
    for (const [x, y] of poly) {
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (-y < minY) minY = -y;
      if (-y > maxY) maxY = -y;
    }
  }
  const span = Math.max(maxX - minX, maxY - minY, 10);
  const pad = span * 0.14;
  const viewBox = `${(minX - pad).toFixed(2)} ${(minY - pad).toFixed(2)} ${(maxX - minX + pad * 2).toFixed(2)} ${(maxY - minY + pad * 2).toFixed(2)}`;
  const paths = polys
    .filter((p) => p.length >= 2)
    .map((p) =>
      p
        .map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(2)} ${(-y).toFixed(2)}`)
        .join(" "),
    );
  return { viewBox, paths, originCross: [0, 0, span * 0.05] };
}

export function pushRecentBlock(prev: string[], name: string, max = 6): string[] {
  return [name, ...prev.filter((x) => x !== name)].slice(0, max);
}
