"""PDF underlays through PDFium (pypdfium2): page list, vectors with optional-content layers and
text, and raster rendering with layers switched off (objects deactivated in memory only)."""

from __future__ import annotations

import ctypes
import io
import math
from collections.abc import Iterable
from typing import Any

import pypdfium2 as pdfium
from PIL import Image
from pypdfium2 import raw

from . import MAX_PAGES, MAX_RENDER_AREA, MAX_RENDER_PX, Page, Pt, UnderlayError, add_subpath

MM = 25.4 / 72  # PDF points -> mm

# page object types (fpdf_edit.h)
_TEXT, _PATH, _IMAGE, _SHADING, _FORM = 1, 2, 3, 4, 5
_LINETO, _BEZIERTO, _MOVETO = 0, 1, 2


def _open(path: str) -> Any:
    try:
        return pdfium.PdfDocument(path)
    except pdfium.PdfiumError as exc:
        msg = (
            "암호가 걸린 PDF는 쓸 수 없습니다"
            if "password" in str(exc).lower()
            else "PDF를 열 수 없습니다"
        )
        raise UnderlayError("UNDERLAY_PDF_INVALID", msg) from None


def info(path: str) -> dict[str, Any]:
    """Pages (size in mm as shown, i.e. after the page's rotation)."""
    doc = _open(path)
    try:
        n = len(doc)
        if n == 0:
            raise UnderlayError("UNDERLAY_PDF_INVALID", "페이지가 없는 PDF입니다")
        pages = []
        for i in range(min(n, MAX_PAGES)):
            page = doc[i]
            w, h = _box(page)[2:]
            if page.get_rotation() in (90, 270):
                w, h = h, w
            pages.append({"width": round(w * MM, 3), "height": round(h * MM, 3)})
            page.close()
        return {"page_count": n, "pages": pages}
    finally:
        doc.close()


def _box(page: Any) -> tuple[float, float, float, float]:
    """(left, bottom, width, height) of the crop box in points."""
    left, bottom, right, top = page.get_cropbox()
    return left, bottom, right - left, top - bottom


def _wstr(fn: Any, *args: Any) -> str | None:
    n = ctypes.c_ulong()
    buf = (ctypes.c_ushort * 512)()
    if not fn(*args, buf, ctypes.sizeof(buf), ctypes.byref(n)):
        return None
    return bytes(buf)[: n.value].decode("utf-16-le", "ignore").rstrip("\x00")


def _text_of(obj: Any, text_page: Any) -> str | None:
    """The characters of a text object (UTF-16, sized by a first call with no buffer)."""
    n = raw.FPDFTextObj_GetText(obj, text_page, None, 0)  # bytes, with the terminator
    if n <= 2 or n > 2_000_000:
        return None
    buf = (ctypes.c_ushort * (n // 2 + 1))()
    raw.FPDFTextObj_GetText(obj, text_page, buf, n)
    return bytes(buf)[: n - 2].decode("utf-16-le", "ignore")


def _layer_name(obj: Any) -> str | None:
    """The optional content group (layer) an object is in, from its /OC marked content."""
    for i in range(raw.FPDFPageObj_CountMarks(obj)):
        mark = raw.FPDFPageObj_GetMark(obj, i)
        if _wstr(raw.FPDFPageObjMark_GetName, mark) == "OC":
            return _wstr(raw.FPDFPageObjMark_GetParamStringValue, mark, b"Name")
    return None


def _color(obj: Any, stroke: bool) -> str | None:
    r, g, b, a = (ctypes.c_uint() for _ in range(4))
    fn = raw.FPDFPageObj_GetStrokeColor if stroke else raw.FPDFPageObj_GetFillColor
    if not fn(obj, *(ctypes.byref(x) for x in (r, g, b, a))):
        return None
    return f"#{r.value:02x}{g.value:02x}{b.value:02x}"


def _objects(page: Any) -> Iterable[tuple[Any, int]]:
    """Every page object with its type, form contents included (PDFium gives form children
    matrices that already contain the form's own placement)."""
    for o in page.get_objects(max_depth=8):
        yield o, o.type


def page_vectors(path: str, index: int) -> dict[str, Any]:
    """The vector cache of one page (see core.underlay)."""
    doc = _open(path)
    try:
        if not 0 <= index < len(doc):
            raise UnderlayError("UNDERLAY_PAGE", "없는 페이지입니다", 404)
        page = doc[index]
        left, bottom, w, h = _box(page)
        rot = page.get_rotation()
        W, H = (h, w) if rot in (90, 270) else (w, h)

        def to_mm(x: float, y: float) -> Pt:
            x, y = x - left, y - bottom
            if rot == 90:  # shown turned clockwise
                x, y = y, w - x
            elif rot == 180:
                x, y = w - x, h - y
            elif rot == 270:
                x, y = h - y, x
            return x * MM, y * MM

        out = Page(W * MM, H * MM)
        text_page = page.get_textpage()
        try:
            for o, kind in _objects(page):
                if kind == _IMAGE:
                    out.images += 1
                elif kind == _PATH:
                    _path(o, out, to_mm)
                elif kind == _TEXT:
                    _text(o, out, to_mm, text_page.raw, rot)
        finally:
            text_page.close()
        return out.to_json()
    finally:
        doc.close()


def _path(o: Any, out: Page, to_mm: Any) -> None:
    obj = o.raw
    fill_mode, stroke = ctypes.c_int(), ctypes.c_int()
    if not raw.FPDFPath_GetDrawMode(obj, ctypes.byref(fill_mode), ctypes.byref(stroke)):
        return
    if not stroke.value and not fill_mode.value:
        return  # a clipping path or an invisible one
    a, b, c, d, e, f = o.get_matrix().get()
    layer = out.layer(_layer_name(obj))
    color = _color(obj, bool(stroke.value)) or None

    def tp(x: float, y: float) -> Pt:
        return to_mm(a * x + c * y + e, b * x + d * y + f)

    segs: list[tuple[str, list[Pt]]] = []
    cur: Pt | None = None
    start: Pt | None = None
    pending: list[Pt] = []
    closed = False
    x, y = ctypes.c_float(), ctypes.c_float()

    def flush() -> None:
        nonlocal segs, closed
        add_subpath(out, segs, closed, bool(fill_mode.value) and not stroke.value, layer, color)
        segs, closed = [], False

    for i in range(raw.FPDFPath_CountSegments(obj)):
        seg = raw.FPDFPath_GetPathSegment(obj, i)
        if not raw.FPDFPathSegment_GetPoint(seg, ctypes.byref(x), ctypes.byref(y)):
            continue
        p = tp(x.value, y.value)
        t = raw.FPDFPathSegment_GetType(seg)
        if t == _MOVETO:
            flush()
            cur = start = p
            pending = []
        elif t == _LINETO and cur is not None:
            segs.append(("L", [cur, p]))
            cur = p
        elif t == _BEZIERTO and cur is not None:
            pending.append(p)
            if len(pending) == 3:  # PDFium lists a cubic as three BEZIERTO points
                segs.append(("C", [cur, *pending]))
                cur, pending = pending[2], []
        if raw.FPDFPathSegment_GetClose(seg) and cur is not None and start is not None:
            if math.dist(cur, start) > 1e-9:
                segs.append(("L", [cur, start]))
            cur = start
            closed = True
    flush()


def _text(o: Any, out: Page, to_mm: Any, text_page: Any, rot: int) -> None:
    obj = o.raw
    value = _text_of(obj, text_page)
    if not value:
        return
    size = ctypes.c_float()
    if not raw.FPDFTextObj_GetFontSize(obj, ctypes.byref(size)):
        return
    a, b, c, d, e, f = o.get_matrix().get()
    height = size.value * math.hypot(c, d) * 72 / 72 * (25.4 / 72)
    angle = math.degrees(math.atan2(b, a)) - rot
    out.add_text(value, to_mm(e, f), height, angle, out.layer(_layer_name(obj)))


def render(
    path: str,
    index: int,
    width_px: int,
    hidden_layers: list[str],
    crop_mm: tuple[float, float, float, float] | None = None,
) -> bytes:
    """PNG of a page (or of crop_mm = x0, y0, x1, y1 in page mm) width_px wide, with the given
    optional-content layers left out."""
    doc = _open(path)
    try:
        if not 0 <= index < len(doc):
            raise UnderlayError("UNDERLAY_PAGE", "없는 페이지입니다", 404)
        page = doc[index]
        rot = page.get_rotation()
        _, _, w, h = _box(page)
        W, H = (h, w) if rot in (90, 270) else (w, h)
        if hidden_layers:
            hide = set(hidden_layers)
            for o, kind in _objects(page):
                if kind != _FORM and _layer_name(o.raw) in hide:
                    raw.FPDFPageObj_SetIsActive(o.raw, False)
        x0, y0, x1, y1 = crop_mm or (0.0, 0.0, W * MM, H * MM)
        cw, ch = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
        width_px = max(1, min(int(width_px), MAX_RENDER_PX))
        height_px = max(1, round(width_px * ch / cw))
        if height_px > MAX_RENDER_PX or width_px * height_px > MAX_RENDER_AREA:
            raise UnderlayError("UNDERLAY_RENDER_SIZE", "요청한 이미지가 너무 큽니다")
        scale = width_px / (cw / MM)  # pixels per point
        # crop is measured from the shown (rotated) page edges, in points: left, bottom, right, top
        crop = (x0 / MM, y0 / MM, W - x1 / MM, H - y1 / MM)
        bitmap = page.render(scale=scale, crop=crop, may_draw_forms=True, draw_annots=True)
        img = bitmap.to_pil().convert("RGB")
        if img.size != (
            width_px,
            height_px,
        ):  # PDFium rounds the bitmap up: the screen maps by the asked size
            img = img.resize((width_px, height_px), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=False)
        return buf.getvalue()
    finally:
        doc.close()
