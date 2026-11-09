// 2D underlay U3: draws the page rasters under the drawing's entities. The bitmaps come from the
// server (the page file never reaches the browser), cached by what they show: file, page, hidden
// PDF layers and a size bucket; zoomed in far, a window of the page is fetched at screen
// resolution (the level of detail: a 100 MB PDF is never rendered whole at high resolution).
// Appearance (fade, contrast, monochrome, background adjust) and the clip are applied here, on the
// bitmap, so changing them needs no new image.
import { pageImage, type ImageWindow } from "@/lib/underlayApi";
import { clipPolygon, toModel, toPage, type UnderlayGeom } from "./underlay";
import type { Pt, View } from "./view";
import { worldToScreen } from "./view";

export type DrawItem = {
  handle: string;
  g: UnderlayGeom;
  size: [number, number]; // page mm
  fileId: string;
  hidden: string[]; // PDF layer names switched off
};

const BUCKETS = [400, 800, 1600, 2400];
export const LIMITS = { full: 6, windows: 4, windowPx: 3000, settleMs: 200 };
const FILTER_OK = typeof CanvasRenderingContext2D !== "undefined" && "filter" in CanvasRenderingContext2D.prototype;

type Full = { bmp: ImageBitmap; px: number; used: number };
type Win = { bmp: ImageBitmap; box: ImageWindow; used: number };

export class UnderlayView {
  private full = new Map<string, Full>();
  private win = new Map<string, Win>();
  private busy = new Set<string>();
  private failed = new Map<string, string>();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private tick = 0;
  version = 0;

  constructor(private onChange: () => void) {}

  /** What went wrong with an underlay's image (shown in its panel), by handle. */
  error(handle: string): string | undefined {
    return this.failed.get(handle);
  }

  private bump() {
    this.version++;
    this.onChange();
  }

  private key(i: DrawItem, px: number) {
    return `${i.fileId}:${i.g.page}:${i.hidden.slice().sort().join("|")}:${px}`;
  }
  private base(i: DrawItem) {
    return `${i.fileId}:${i.g.page}:${i.hidden.slice().sort().join("|")}:`;
  }

  /** The bucket for how many pixels the whole page covers on screen now. */
  static bucket(pagePx: number): number {
    return BUCKETS.find((b) => b >= pagePx) ?? BUCKETS[BUCKETS.length - 1];
  }

  /** The best bitmap of the page already here: the wanted bucket, else the nearest other one. */
  private pick(i: DrawItem, want: number): Full | undefined {
    const exact = this.full.get(this.key(i, want));
    if (exact) return exact;
    let best: Full | undefined;
    for (const [k, f] of this.full) if (k.startsWith(this.base(i)) && (!best || Math.abs(f.px - want) < Math.abs(best.px - want))) best = f;
    return best;
  }

  /** Makes sure the bitmaps the view needs are on their way; call after every view or item change. */
  request(items: DrawItem[], view: View, settle = false) {
    for (const i of items) {
      if (!i.g.flags.on) continue;
      const pagePx = i.size[0] * i.g.scale * view.scale;
      const want = UnderlayView.bucket(pagePx);
      const k = this.key(i, want);
      if (!this.full.has(k)) this.fetchFull(i, want, k);
      else this.full.get(k)!.used = ++this.tick;
      if (pagePx > BUCKETS[BUCKETS.length - 1] && settle) this.fetchWindow(i, view);
    }
  }

  /** Same as request, but a window is only fetched once the view has stopped moving. */
  requestSettled(items: DrawItem[], view: View, viewport: { w: number; h: number }) {
    this.request(items, view, false);
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => this.request(items, view, true), LIMITS.settleMs);
    this.viewport = viewport;
  }
  private viewport = { w: 0, h: 0 };

  private fetchFull(i: DrawItem, px: number, k: string) {
    if (this.busy.has(k)) return;
    this.busy.add(k);
    pageImage(i.fileId, i.g.page, px, i.hidden).then(
      (bmp) => {
        this.busy.delete(k);
        this.failed.delete(i.handle);
        this.full.set(k, { bmp, px, used: ++this.tick });
        this.evict();
        this.bump();
      },
      (e) => {
        this.busy.delete(k);
        this.failed.set(i.handle, e instanceof Error ? e.message : "페이지 이미지를 만들 수 없습니다");
        this.bump();
      },
    );
  }

  private fetchWindow(i: DrawItem, view: View) {
    // the visible part of the page, from the four screen corners put back into page mm
    const { w, h } = this.viewport;
    if (!w || !h) return;
    const corners: Pt[] = [[0, 0], [w, 0], [w, h], [0, h]].map(([sx, sy]) => toPage(i.g, [(sx - view.offsetX) / view.scale, (view.offsetY - sy) / view.scale]));
    const box: ImageWindow = {
      x0: Math.max(0, Math.min(...corners.map((c) => c[0]))),
      y0: Math.max(0, Math.min(...corners.map((c) => c[1]))),
      x1: Math.min(i.size[0], Math.max(...corners.map((c) => c[0]))),
      y1: Math.min(i.size[1], Math.max(...corners.map((c) => c[1]))),
    };
    if (box.x1 - box.x0 < 1e-3 || box.y1 - box.y0 < 1e-3) return;
    const px = Math.min(LIMITS.windowPx, Math.ceil((box.x1 - box.x0) * i.g.scale * view.scale));
    const r = (v: number) => Math.round(v * 1000) / 1000;
    const k = `${this.base(i)}w${r(box.x0)},${r(box.y0)},${r(box.x1)},${r(box.y1)}@${px}`;
    if (this.win.has(k) || this.busy.has(k)) return;
    this.busy.add(k);
    pageImage(i.fileId, i.g.page, px, i.hidden, box).then(
      (bmp) => {
        this.busy.delete(k);
        for (const [old, wv] of this.win) {
          if (!old.startsWith(this.base(i))) continue;
          wv.bmp.close(); // one window per page: the newest
          this.win.delete(old);
        }
        this.win.set(k, { bmp, box, used: ++this.tick });
        this.bump();
      },
      () => this.busy.delete(k), // the full-page bitmap is still shown
    );
  }

  private evict() {
    while (this.full.size > LIMITS.full) {
      let oldest: [string, Full] | null = null;
      for (const e of this.full) if (!oldest || e[1].used < oldest[1].used) oldest = e;
      if (!oldest) break;
      oldest[1].bmp.close();
      this.full.delete(oldest[0]);
    }
    while (this.win.size > LIMITS.windows) {
      const first = this.win.keys().next().value as string;
      this.win.get(first)!.bmp.close();
      this.win.delete(first);
    }
  }

  /** Forgets every bitmap (a file was replaced or the screen is closing). */
  clear() {
    if (this.timer) clearTimeout(this.timer);
    for (const f of this.full.values()) f.bmp.close();
    for (const w of this.win.values()) w.bmp.close();
    this.full.clear();
    this.win.clear();
  }

  /** The window bitmap of this page (with these layers), if one has arrived. */
  private windowOf(i: DrawItem): Win | undefined {
    const b = this.base(i);
    for (const [k, w] of this.win) if (k.startsWith(b)) return w;
    return undefined;
  }

  /** Draws the visible underlays under the entities (ctx already has the device pixel ratio). */
  draw(ctx: CanvasRenderingContext2D, items: DrawItem[], view: View, dark: boolean) {
    for (const i of items) {
      if (!i.g.flags.on) continue;
      const full = this.pick(i, UnderlayView.bucket(i.size[0] * i.g.scale * view.scale));
      if (!full) continue;
      ctx.save();
      // page mm (y up) -> screen px: the origin and the two unit vectors of the page through the view
      const o = worldToScreen(view, ...toModel(i.g, [0, 0]));
      const ex = worldToScreen(view, ...toModel(i.g, [1, 0]));
      const ey = worldToScreen(view, ...toModel(i.g, [0, 1]));
      ctx.transform(ex[0] - o[0], ex[1] - o[1], ey[0] - o[0], ey[1] - o[1], o[0], o[1]);
      this.clip(ctx, i);
      ctx.globalAlpha = 1 - i.g.fade / 100;
      if (FILTER_OK) {
        const f: string[] = [];
        if (i.g.flags.monochrome) f.push("grayscale(1)");
        if (i.g.contrast !== 50) f.push(`contrast(${(0.5 + i.g.contrast / 100).toFixed(2)})`);
        if (i.g.flags.adjust && dark) f.push("invert(1)", "hue-rotate(180deg)"); // dark lines on white paper -> light lines on the dark canvas
        if (f.length) ctx.filter = f.join(" ");
      }
      // light paper stays out of the way on a light canvas; an inverted (dark) paper on a dark one
      ctx.globalCompositeOperation = i.g.flags.adjust && dark ? "screen" : "multiply";
      const [w, h] = i.size;
      ctx.translate(0, h);
      ctx.scale(1, -1); // the bitmap's top edge is the page's top edge
      ctx.drawImage(full.bmp, 0, 0, w, h);
      const wv = this.windowOf(i);
      if (wv) ctx.drawImage(wv.bmp, wv.box.x0, h - wv.box.y1, wv.box.x1 - wv.box.x0, wv.box.y1 - wv.box.y0);
      ctx.restore();
    }
  }

  /** Clip in page mm: the page itself, and inside or outside the clip boundary. */
  private clip(ctx: CanvasRenderingContext2D, i: DrawItem) {
    const [w, h] = i.size;
    const poly = clipPolygon(i.g);
    ctx.beginPath();
    if (poly && i.g.flags.inside) {
      poly.forEach((p, n) => (n ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
      ctx.closePath();
      ctx.clip();
      return;
    }
    ctx.rect(0, 0, w, h);
    if (poly) {
      poly.forEach((p, n) => (n ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
      ctx.closePath();
      ctx.clip("evenodd"); // the page minus the clip
      return;
    }
    ctx.clip();
  }
}
