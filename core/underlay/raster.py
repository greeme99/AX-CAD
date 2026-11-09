"""A raster of a page's vector cache with Pillow, for formats PDFium cannot draw (DWFx). Lines,
circles and arcs with their own colours, filled shapes, texts as small marks; layers that are
switched off are left out. PDF pages are rendered by PDFium instead (see pdf.render)."""

from __future__ import annotations

import io
import math
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from . import MAX_RENDER_AREA, MAX_RENDER_PX, UnderlayError


def render(
    vec: dict[str, Any],
    width_px: int,
    hidden: list[int],
    crop_mm: tuple[float, float, float, float] | None = None,
) -> bytes:
    pw, ph = vec["size"]
    x0, y0, x1, y1 = crop_mm or (0.0, 0.0, pw, ph)
    cw, ch = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    width_px = max(1, min(int(width_px), MAX_RENDER_PX))
    height_px = max(1, round(width_px * ch / cw))
    if height_px > MAX_RENDER_PX or width_px * height_px > MAX_RENDER_AREA:
        raise UnderlayError("UNDERLAY_RENDER_SIZE", "요청한 이미지가 너무 큽니다")
    k = width_px / cw
    img = Image.new("RGB", (width_px, height_px), "white")
    d = ImageDraw.Draw(img)
    off = set(hidden)

    def px(p: tuple[float, float]) -> tuple[float, float]:
        return (p[0] - x0) * k, (y1 - p[1]) * k  # page y is up, pixels down

    line = max(1, round(k * 0.18))  # ~0.18 mm pen
    for pth in vec["paths"]:
        if pth.get("l", -1) in off:
            continue
        xy = pth["p"]
        pts = [px((xy[i], xy[i + 1])) for i in range(0, len(xy), 2)]
        col = pth.get("c", "#000000")
        if pth.get("z"):
            pts.append(pts[0])
        if pth.get("f") and len(pts) > 2:
            d.polygon(pts, fill=col)
        else:
            d.line(pts, fill=col, width=line, joint="curve")
    for a in vec["arcs"]:
        if a.get("l", -1) in off:
            continue
        cx, cy = px(tuple(a["c"]))  # type: ignore[arg-type]
        r = a["r"] * k
        box = (cx - r, cy - r, cx + r, cy + r)
        col = a.get("co", "#000000")
        if "a" in a:
            s, e = a["a"]  # CCW degrees with y up -> PIL measures clockwise with y down
            d.arc(box, -e, -s, fill=col, width=line)
        else:
            d.ellipse(box, outline=col, width=line)
    font_cache: dict[int, Any] = {}
    for t in vec["texts"]:
        if t.get("l", -1) in off:
            continue
        size = max(6, round(t["h"] * k))
        if size > 400:
            continue
        font = font_cache.get(size)
        if font is None:
            try:
                font = font_cache[size] = ImageFont.load_default(size=size)
            except (TypeError, OSError):
                font = font_cache[size] = ImageFont.load_default()
        x, y = px(tuple(t["p"]))  # type: ignore[arg-type]
        txt = t["v"]
        rot = t.get("r", 0)
        if abs(rot) < 1e-6:
            d.text((x, y), txt, fill="#000000", font=font, anchor="ls")
        else:  # rotated text: drawn upright on a small layer, then turned
            w = max(8, round(size * 0.65 * len(txt)))
            layer = Image.new("RGBA", (w + 4, size * 2), (0, 0, 0, 0))
            ImageDraw.Draw(layer).text((2, size * 1.5), txt, fill="#000000", font=font, anchor="ls")
            turned = layer.rotate(rot, resample=Image.Resampling.BICUBIC, expand=True)
            img.paste(turned, (round(x - 2), round(y - size * 1.5)), turned)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


__all__ = ["math", "render"]
