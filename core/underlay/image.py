"""Convert raster images (PNG/JPEG/WEBP/BMP) and vector SVG into 1-page PDF underlays.

Zero-new-dependency implementation using Pillow (raster -> PDF) and ReportLab + ElementTree
(SVG -> vector PDF). Converting to a 1-page PDF allows images to flow through the existing
underlay placement, clipping, opacity/contrast, vector snapping (for SVG), raster/vector
auto-trace, automatic dimensioning, and PDF plotting pipeline without DB schema changes.
"""

from __future__ import annotations

import io
import math
import re
import xml.etree.ElementTree as ET
from typing import Any

from PIL import Image, UnidentifiedImageError
from reportlab.pdfgen import canvas as rl_canvas

from . import UnderlayError

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".svg", ".webp", ".bmp")
MAX_IMAGE_PIXELS = 40_000_000  # 40 MP cap against decompression bombs
MAX_SVG_BYTES = 20 * 1024 * 1024
MAX_SVG_ELEMENTS = 50_000
DEFAULT_DPI = 96.0
PT_PER_IN = 72.0
PT_PER_MM = PT_PER_IN / 25.4

_SVG_ENTITY_RE = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)
_NUM_UNIT_RE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*(mm|cm|in|pt|px)?\s*$", re.IGNORECASE)
_PATH_TOKEN_RE = re.compile(r"[MmLlHhVvCcQqZz]|[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_TRANSFORM_RE = re.compile(r"(translate|scale)\s*\(([^)]+)\)")


def sniff_image(data: bytes) -> str | None:
    """Identify image format from raw bytes; returns 'png'|'jpeg'|'webp'|'bmp'|'svg'|None."""
    if len(data) < 4:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:2] == b"BM":
        return "bmp"
    head = data[:2048].lstrip(b"\xef\xbb\xbf \t\r\n")
    if head.startswith((b"<svg", b"<?xml")) and b"<svg" in data[:8192].lower():
        return "svg"
    return None


def _raster_to_pdf(data: bytes) -> bytes:
    prev_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            w, h = im.size
            if w <= 0 or h <= 0 or w * h > MAX_IMAGE_PIXELS:
                raise UnderlayError("UNDERLAY_TOO_BIG", "이미지 해상도가 너무 큽니다 (최대 40MP)")
            dpi_info = im.info.get("dpi")
            dpi = DEFAULT_DPI
            if isinstance(dpi_info, tuple) and len(dpi_info) >= 1:
                try:
                    d0 = float(dpi_info[0])
                    if 24.0 <= d0 <= 1200.0:
                        dpi = d0
                except (TypeError, ValueError):
                    pass
            # Composite alpha onto white paper so Otsu binarization & dark-canvas inversion work cleanly
            if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                rgba = im.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.split()[3])
                rgb = bg
            else:
                rgb = im.convert("RGB")
            out = io.BytesIO()
            rgb.save(out, format="PDF", resolution=dpi)
            return out.getvalue()
    except UnderlayError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as exc:
        raise UnderlayError(
            "UNDERLAY_FORMAT", "이미지 파일을 읽을 수 없습니다 (손상되었거나 지원하지 않는 형식)"
        ) from exc
    finally:
        Image.MAX_IMAGE_PIXELS = prev_limit


def _parse_len_pt(raw: str | None, fallback_pt: float) -> float:
    if not raw:
        return fallback_pt
    m = _NUM_UNIT_RE.match(raw)
    if not m:
        return fallback_pt
    val = float(m.group(1))
    unit = (m.group(2) or "px").lower()
    if val <= 0:
        return fallback_pt
    factor = {
        "pt": 1.0,
        "in": PT_PER_IN,
        "mm": PT_PER_MM,
        "cm": PT_PER_MM * 10.0,
        "px": PT_PER_IN / DEFAULT_DPI,
    }.get(unit, PT_PER_IN / DEFAULT_DPI)
    return val * factor


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower() if "}" in tag else tag.lower()


def _parse_points(raw: str) -> list[tuple[float, float]]:
    nums = [float(x) for x in re.findall(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?", raw)]
    return [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]


def _parse_transform(raw: str | None) -> tuple[float, float, float, float]:
    """Return (tx, ty, sx, sy) from simple translate/scale transforms."""
    tx, ty, sx, sy = 0.0, 0.0, 1.0, 1.0
    if not raw:
        return tx, ty, sx, sy
    for kind, body in _TRANSFORM_RE.findall(raw):
        vals = [float(v) for v in re.findall(r"[+-]?(?:\d+\.?\d*|\.\d+)", body)]
        if kind == "translate" and vals:
            tx += vals[0]
            if len(vals) > 1:
                ty += vals[1]
        elif kind == "scale" and vals:
            sx *= vals[0]
            sy *= vals[1] if len(vals) > 1 else vals[0]
    return tx, ty, sx, sy


def _draw_svg_path(
    c: rl_canvas.Canvas,
    d: str,
    xform: Any,
) -> None:
    tokens = _PATH_TOKEN_RE.findall(d)
    if not tokens:
        return
    p = c.beginPath()
    i = 0
    cmd = ""
    cx, cy = 0.0, 0.0
    sx, sy = 0.0, 0.0
    has_move = False

    def next_f() -> float:
        nonlocal i
        v = float(tokens[i])
        i += 1
        return v

    while i < len(tokens):
        t = tokens[i]
        if t.isalpha():
            cmd = t
            i += 1
            if cmd in ("Z", "z"):
                if has_move:
                    p.close()
                    cx, cy = sx, sy
                continue
        if not cmd:
            break
        try:
            if cmd in ("M", "m"):
                x, y = next_f(), next_f()
                if cmd == "m":
                    x, y = cx + x, cy + y
                px, py = xform(x, y)
                p.moveTo(px, py)
                cx, cy = x, y
                sx, sy = x, y
                has_move = True
                cmd = "L" if cmd == "M" else "l"
            elif cmd in ("L", "l"):
                x, y = next_f(), next_f()
                if cmd == "l":
                    x, y = cx + x, cy + y
                px, py = xform(x, y)
                p.lineTo(px, py)
                cx, cy = x, y
            elif cmd in ("H", "h"):
                x = next_f()
                if cmd == "h":
                    x = cx + x
                px, py = xform(x, cy)
                p.lineTo(px, py)
                cx = x
            elif cmd in ("V", "v"):
                y = next_f()
                if cmd == "v":
                    y = cy + y
                px, py = xform(cx, y)
                p.lineTo(px, py)
                cy = y
            elif cmd in ("C", "c"):
                x1, y1, x2, y2, x, y = (
                    next_f(),
                    next_f(),
                    next_f(),
                    next_f(),
                    next_f(),
                    next_f(),
                )
                if cmd == "c":
                    x1, y1 = cx + x1, cy + y1
                    x2, y2 = cx + x2, cy + y2
                    x, y = cx + x, cy + y
                p1x, p1y = xform(x1, y1)
                p2x, p2y = xform(x2, y2)
                px, py = xform(x, y)
                p.curveTo(p1x, p1y, p2x, p2y, px, py)
                cx, cy = x, y
            elif cmd in ("Q", "q"):
                qx, qy, x, y = next_f(), next_f(), next_f(), next_f()
                if cmd == "q":
                    qx, qy = cx + qx, cy + qy
                    x, y = cx + x, cy + y
                # Elevate quadratic Bezier to cubic Bezier
                c1x, c1y = cx + (2.0 / 3.0) * (qx - cx), cy + (2.0 / 3.0) * (qy - cy)
                c2x, c2y = x + (2.0 / 3.0) * (qx - x), y + (2.0 / 3.0) * (qy - y)
                p1x, p1y = xform(c1x, c1y)
                p2x, p2y = xform(c2x, c2y)
                px, py = xform(x, y)
                p.curveTo(p1x, p1y, p2x, p2y, px, py)
                cx, cy = x, y
            else:
                break
        except (IndexError, ValueError):
            break
    if has_move:
        c.drawPath(p, stroke=1, fill=0)


def _svg_to_pdf(data: bytes) -> bytes:
    if len(data) > MAX_SVG_BYTES:
        raise UnderlayError("UNDERLAY_TOO_BIG", "SVG 파일은 20 MB까지 지원합니다")
    if _SVG_ENTITY_RE.search(data):
        raise UnderlayError(
            "UNDERLAY_FORMAT", "보안상 DOCTYPE/ENTITY가 포함된 SVG는 허용되지 않습니다"
        )
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise UnderlayError("UNDERLAY_FORMAT", "SVG XML 형식이 올바르지 않습니다") from exc

    if _local_tag(root.tag) != "svg":
        raise UnderlayError("UNDERLAY_FORMAT", "올바른 SVG 파일이 아닙니다")

    vb = root.attrib.get("viewBox") or root.attrib.get("viewbox")
    vb_x, vb_y, vb_w, vb_h = 0.0, 0.0, 0.0, 0.0
    if vb:
        vals = [float(x) for x in re.findall(r"[+-]?(?:\d+\.?\d*|\.\d+)", vb)]
        if len(vals) == 4 and vals[2] > 0 and vals[3] > 0:
            vb_x, vb_y, vb_w, vb_h = vals

    default_w = vb_w * (PT_PER_IN / DEFAULT_DPI) if vb_w > 0 else 210.0 * PT_PER_MM
    default_h = vb_h * (PT_PER_IN / DEFAULT_DPI) if vb_h > 0 else 297.0 * PT_PER_MM
    page_w = _parse_len_pt(root.attrib.get("width"), default_w)
    page_h = _parse_len_pt(root.attrib.get("height"), default_h)
    page_w = max(10.0, min(page_w, 14400.0))
    page_h = max(10.0, min(page_h, 14400.0))

    if vb_w <= 0 or vb_h <= 0:
        vb_w = page_w / (PT_PER_IN / DEFAULT_DPI)
        vb_h = page_h / (PT_PER_IN / DEFAULT_DPI)

    sx_page = page_w / vb_w
    sy_page = page_h / vb_h

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(page_w, page_h))
    c.setStrokeColorRGB(0.1, 0.1, 0.1)
    c.setLineWidth(0.75)

    count = 0

    def walk(node: ET.Element, tx: float, ty: float, sx: float, sy: float) -> None:
        nonlocal count
        if count >= MAX_SVG_ELEMENTS:
            return
        ntx, nty, nsx, nsy = _parse_transform(node.attrib.get("transform"))
        cur_tx = tx + ntx * sx
        cur_ty = ty + nty * sy
        cur_sx = sx * nsx
        cur_sy = sy * nsy

        def xform(x: float, y: float) -> tuple[float, float]:
            gx = cur_tx + x * cur_sx
            gy = cur_ty + y * cur_sy
            return (gx - vb_x) * sx_page, page_h - (gy - vb_y) * sy_page

        tag = _local_tag(node.tag)
        if tag in ("defs", "style", "script", "metadata", "title", "desc"):
            return

        if tag == "line":
            count += 1
            x1 = float(node.attrib.get("x1", 0))
            y1 = float(node.attrib.get("y1", 0))
            x2 = float(node.attrib.get("x2", 0))
            y2 = float(node.attrib.get("y2", 0))
            p1, p2 = xform(x1, y1), xform(x2, y2)
            c.line(p1[0], p1[1], p2[0], p2[1])
        elif tag == "rect":
            count += 1
            x = float(node.attrib.get("x", 0))
            y = float(node.attrib.get("y", 0))
            w = float(node.attrib.get("width", 0))
            h = float(node.attrib.get("height", 0))
            if w > 0 and h > 0:
                p1, p2 = xform(x, y + h), xform(x + w, y)
                c.rect(p1[0], p1[1], p2[0] - p1[0], p2[1] - p1[1], stroke=1, fill=0)
        elif tag == "circle":
            count += 1
            cx = float(node.attrib.get("cx", 0))
            cy = float(node.attrib.get("cy", 0))
            r = float(node.attrib.get("r", 0))
            if r > 0:
                pc = xform(cx, cy)
                rx = abs(r * cur_sx * sx_page)
                ry = abs(r * cur_sy * sy_page)
                if math.isclose(rx, ry, rel_tol=1e-3):
                    c.circle(pc[0], pc[1], rx, stroke=1, fill=0)
                else:
                    c.ellipse(pc[0] - rx, pc[1] - ry, pc[0] + rx, pc[1] + ry, stroke=1, fill=0)
        elif tag == "ellipse":
            count += 1
            cx = float(node.attrib.get("cx", 0))
            cy = float(node.attrib.get("cy", 0))
            rx_v = float(node.attrib.get("rx", 0))
            ry_v = float(node.attrib.get("ry", 0))
            if rx_v > 0 and ry_v > 0:
                pc = xform(cx, cy)
                rx = abs(rx_v * cur_sx * sx_page)
                ry = abs(ry_v * cur_sy * sy_page)
                c.ellipse(pc[0] - rx, pc[1] - ry, pc[0] + rx, pc[1] + ry, stroke=1, fill=0)
        elif tag in ("polyline", "polygon"):
            count += 1
            pts = _parse_points(node.attrib.get("points", ""))
            if len(pts) >= 2:
                p = c.beginPath()
                p0 = xform(pts[0][0], pts[0][1])
                p.moveTo(p0[0], p0[1])
                for px, py in pts[1:]:
                    q = xform(px, py)
                    p.lineTo(q[0], q[1])
                if tag == "polygon":
                    p.close()
                c.drawPath(p, stroke=1, fill=0)
        elif tag == "path":
            count += 1
            d = node.attrib.get("d", "")
            if d:
                _draw_svg_path(c, d, xform)

        for child in node:
            walk(child, cur_tx, cur_ty, cur_sx, cur_sy)

    walk(root, 0.0, 0.0, 1.0, 1.0)
    c.showPage()
    c.save()
    return buf.getvalue()


def image_to_pdf(data: bytes) -> bytes:
    """Validate and convert PNG/JPEG/WEBP/BMP/SVG bytes into a 1-page PDF."""
    fmt = sniff_image(data)
    if fmt is None:
        raise UnderlayError(
            "UNDERLAY_FORMAT",
            "지원하는 이미지 형식(PNG, JPG, SVG, WEBP, BMP)이 아닙니다",
        )
    if fmt == "svg":
        return _svg_to_pdf(data)
    return _raster_to_pdf(data)
