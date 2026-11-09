"""DWFx underlays: an OPC package of XPS FixedPages (paths, glyphs, canvases). Only the vectors and
text are read, in page millimetres with the y axis up; images are counted, never decoded here.
The package is read with defusedxml and hard limits (entries, sizes): a zip bomb or a billion
laughs file stops at the limit instead of eating the server."""

from __future__ import annotations

import math
import re
import zipfile
from typing import Any

from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException

from . import MAX_PAGES, Page, Pt, UnderlayError, add_subpath, flatten_cubic

MM = 25.4 / 96  # XPS units are 1/96 inch
MAX_ENTRIES = 20_000
MAX_PART_BYTES = 80 * 1024 * 1024  # one XML part, uncompressed
MAX_TOTAL_BYTES = 400 * 1024 * 1024
MAX_DEPTH = 64  # canvas nesting
MAX_ELEMENTS = 2_000_000  # path / glyph / canvas elements read from one page

_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_TOKEN = re.compile(rf"([MmLlHhVvCcSsQqTtAaZz])|({_NUM})|([FfXx]\s*[01])")
_NS = re.compile(r"^\{[^}]*\}")


def _tag(e: Any) -> str:
    return _NS.sub("", e.tag) if isinstance(e.tag, str) else ""


def open_package(path: str) -> zipfile.ZipFile:
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        raise UnderlayError("UNDERLAY_DWFX_INVALID", "DWFx 패키지를 열 수 없습니다") from None
    infos = z.infolist()
    if len(infos) > MAX_ENTRIES or sum(i.file_size for i in infos) > MAX_TOTAL_BYTES:
        z.close()
        raise UnderlayError("UNDERLAY_TOO_BIG", "패키지가 너무 큽니다")
    if any(i.flag_bits & 0x1 for i in infos):
        z.close()
        raise UnderlayError("UNDERLAY_ENCRYPTED", "암호가 걸린 파일은 쓸 수 없습니다")
    return z


def _part(z: zipfile.ZipFile, name: str) -> Any:
    info = z.getinfo(name.lstrip("/"))
    if info.file_size > MAX_PART_BYTES:
        raise UnderlayError("UNDERLAY_TOO_BIG", "페이지 데이터가 너무 큽니다")
    try:
        with z.open(info) as fh:
            data = fh.read(MAX_PART_BYTES + 1)
        if len(data) > MAX_PART_BYTES:
            raise UnderlayError("UNDERLAY_TOO_BIG", "페이지 데이터가 너무 큽니다")
        return ET.fromstring(data, forbid_dtd=True)
    except (ET.ParseError, DefusedXmlException, zipfile.BadZipFile, KeyError, ValueError):
        raise UnderlayError("UNDERLAY_DWFX_INVALID", "DWFx 페이지를 읽을 수 없습니다") from None


def _join(base: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    parts = base.rsplit("/", 1)[0].split("/") if "/" in base else []
    for seg in target.split("/"):
        if seg == "..":
            if parts:
                parts.pop()
        elif seg not in ("", "."):
            parts.append(seg)
    return "/".join(parts)


def page_parts(z: zipfile.ZipFile) -> list[str]:
    """The FixedPage parts in reading order (fdseq -> fdoc -> pages), else every .fpage sorted."""
    names = {n.lower(): n for n in z.namelist()}
    out: list[str] = []
    seqs = [n for n in z.namelist() if n.lower().endswith(".fdseq")]
    for seq in seqs[:1]:
        for ref in _part(z, seq).iter():
            if _tag(ref) != "DocumentReference":
                continue
            doc = _join(seq, ref.get("Source", ""))
            if doc.lower() not in names:
                continue
            for pc in _part(z, names[doc.lower()]).iter():
                if _tag(pc) == "PageContent":
                    page = _join(names[doc.lower()], pc.get("Source", ""))
                    if page.lower() in names:
                        out.append(names[page.lower()])
    if not out:
        out = sorted(n for n in z.namelist() if n.lower().endswith(".fpage"))
    return out[:MAX_PAGES]


def info(path: str) -> dict[str, Any]:
    z = open_package(path)
    try:
        parts = page_parts(z)
        if not parts:
            raise UnderlayError("UNDERLAY_DWFX_INVALID", "페이지가 없는 DWFx입니다")
        pages = []
        for part in parts:
            root = _part(z, part)
            w, h = _size(root)
            pages.append({"width": round(w * MM, 3), "height": round(h * MM, 3)})
        return {"page_count": len(parts), "pages": pages}
    finally:
        z.close()


def _size(root: Any) -> tuple[float, float]:
    try:
        w, h = float(root.get("Width", "0")), float(root.get("Height", "0"))
    except ValueError:
        w = h = 0.0
    if not (0 < w < 1e6 and 0 < h < 1e6):
        raise UnderlayError("UNDERLAY_DWFX_INVALID", "페이지 크기를 알 수 없습니다")
    return w, h


# --- transforms (row vector, like XPS: x' = x*m11 + y*m21 + dx) ---------------------------------

Mat = tuple[float, float, float, float, float, float]
IDENTITY: Mat = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _mat(text: str | None) -> Mat | None:
    if not text:
        return None
    try:
        v = tuple(float(x) for x in re.split(r"[,\s]+", text.strip()))
    except ValueError:
        return None
    if len(v) != 6 or not all(math.isfinite(x) for x in v):
        return None
    return v  # type: ignore[return-value]


def _mul(a: Mat, b: Mat) -> Mat:
    """a applied first, then b."""
    return (
        a[0] * b[0] + a[1] * b[2],
        a[0] * b[1] + a[1] * b[3],
        a[2] * b[0] + a[3] * b[2],
        a[2] * b[1] + a[3] * b[3],
        a[4] * b[0] + a[5] * b[2] + b[4],
        a[4] * b[1] + a[5] * b[3] + b[5],
    )


def _apply(m: Mat, p: Pt) -> Pt:
    return p[0] * m[0] + p[1] * m[2] + m[4], p[0] * m[1] + p[1] * m[3] + m[5]


def _color(value: str | None) -> str | None:
    if not value or not value.startswith("#"):
        return None
    h = value[1:]
    if len(h) == 8:  # #AARRGGBB; a fully transparent stroke draws nothing
        if h[:2].lower() == "00":
            return None
        h = h[2:]
    return f"#{h.lower()}" if re.fullmatch(r"[0-9a-fA-F]{6}", h) else None


# --- path data ---------------------------------------------------------------------------------


def _arc_points(
    p0: Pt, rx: float, ry: float, rot: float, large: int, sweep: int, p1: Pt
) -> list[Pt]:
    """SVG-style endpoint arc to points (flattened): XPS uses the same parameters."""
    if rx == 0 or ry == 0 or p0 == p1:
        return [p1]
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(rot)
    c, s = math.cos(phi), math.sin(phi)
    dx, dy = (p0[0] - p1[0]) / 2, (p0[1] - p1[1]) / 2
    x1, y1 = c * dx + s * dy, -s * dx + c * dy
    lam = x1 * x1 / (rx * rx) + y1 * y1 / (ry * ry)
    if lam > 1:
        k = math.sqrt(lam)
        rx, ry = rx * k, ry * k
    num = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
    den = rx * rx * y1 * y1 + ry * ry * x1 * x1
    coef = math.sqrt(max(0.0, num / den)) * (-1 if large == sweep else 1)
    cxp, cyp = coef * rx * y1 / ry, -coef * ry * x1 / rx
    cx = c * cxp - s * cyp + (p0[0] + p1[0]) / 2
    cy = s * cxp + c * cyp + (p0[1] + p1[1]) / 2

    def ang(ux: float, uy: float, vx: float, vy: float) -> float:
        return math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)

    t1 = ang(1, 0, (x1 - cxp) / rx, (y1 - cyp) / ry)
    dt = ang((x1 - cxp) / rx, (y1 - cyp) / ry, (-x1 - cxp) / rx, (-y1 - cyp) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    n = max(2, min(96, math.ceil(abs(dt) / (math.pi / 24))))
    out: list[Pt] = []
    for i in range(1, n + 1):
        t = t1 + dt * i / n
        ex, ey = rx * math.cos(t), ry * math.sin(t)
        out.append((c * ex - s * ey + cx, s * ex + c * ey + cy))
    out[-1] = p1
    return out


def parse_path(data: str, limit: int = 200_000) -> list[dict[str, Any]]:
    """Path mini-language -> subpaths {"segs": [("L"|"C", [points])], "closed": bool}."""
    subs: list[dict[str, Any]] = []
    cur: Pt = (0.0, 0.0)
    start: Pt = cur
    last_c: Pt | None = None  # last cubic control (for S)
    last_q: Pt | None = None
    sub: dict[str, Any] | None = None
    cmd = ""
    args: list[float] = []

    def need(k: int) -> bool:
        return len(args) >= k

    def push(kind: str, pts: list[Pt]) -> None:
        nonlocal sub
        if sub is None:
            sub = {"segs": [], "closed": False}
            subs.append(sub)
        sub["segs"].append((kind, pts))

    def run() -> None:
        nonlocal cur, start, last_c, last_q, sub, args
        c = cmd
        low = c.lower()
        rel = c.islower()
        ox, oy = cur if rel else (0.0, 0.0)
        if low == "m" and need(2):
            cur = start = (args[0] + ox, args[1] + oy)
            sub = None
            args = args[2:]
            return run_again("l" if rel else "L")
        if low == "l" and need(2):
            p = (args[0] + ox, args[1] + oy)
            push("L", [cur, p])
            cur, args = p, args[2:]
        elif low == "h" and need(1):
            p = (args[0] + ox, cur[1])
            push("L", [cur, p])
            cur, args = p, args[1:]
        elif low == "v" and need(1):
            p = (cur[0], args[0] + oy)
            push("L", [cur, p])
            cur, args = p, args[1:]
        elif low == "c" and need(6):
            p1, p2, p3 = (
                (args[0] + ox, args[1] + oy),
                (args[2] + ox, args[3] + oy),
                (args[4] + ox, args[5] + oy),
            )
            push("C", [cur, p1, p2, p3])
            last_c, cur, args = p2, p3, args[6:]
        elif low == "s" and need(4):
            p1 = (2 * cur[0] - last_c[0], 2 * cur[1] - last_c[1]) if last_c else cur
            p2, p3 = (args[0] + ox, args[1] + oy), (args[2] + ox, args[3] + oy)
            push("C", [cur, p1, p2, p3])
            last_c, cur, args = p2, p3, args[4:]
        elif low == "q" and need(4):
            q, p3 = (args[0] + ox, args[1] + oy), (args[2] + ox, args[3] + oy)
            push(
                "C",
                [
                    cur,
                    (cur[0] + 2 / 3 * (q[0] - cur[0]), cur[1] + 2 / 3 * (q[1] - cur[1])),
                    (p3[0] + 2 / 3 * (q[0] - p3[0]), p3[1] + 2 / 3 * (q[1] - p3[1])),
                    p3,
                ],
            )
            last_q, cur, args = q, p3, args[4:]
        elif low == "t" and need(2):
            q = (2 * cur[0] - last_q[0], 2 * cur[1] - last_q[1]) if last_q else cur
            p3 = (args[0] + ox, args[1] + oy)
            push(
                "C",
                [
                    cur,
                    (cur[0] + 2 / 3 * (q[0] - cur[0]), cur[1] + 2 / 3 * (q[1] - cur[1])),
                    (p3[0] + 2 / 3 * (q[0] - p3[0]), p3[1] + 2 / 3 * (q[1] - p3[1])),
                    p3,
                ],
            )
            last_q, cur, args = q, p3, args[2:]
        elif low == "a" and need(7):
            p3 = (args[5] + ox, args[6] + oy)
            pts = _arc_points(cur, args[0], args[1], args[2], int(args[3]), int(args[4]), p3)
            prev = cur
            for q in pts:
                push("L", [prev, q])
                prev = q
            cur, args = p3, args[7:]
        else:
            args = []
            return
        if low not in "cs":
            last_c = None
        if low not in "qt":
            last_q = None
        if args:
            run()

    def run_again(next_cmd: str) -> None:
        nonlocal cmd
        cmd = next_cmd
        if args:
            run()

    n = 0
    for m in _TOKEN.finditer(data):
        n += 1
        if n > limit:
            raise UnderlayError("UNDERLAY_TOO_BIG", "경로가 너무 큽니다")
        if m.group(3):
            continue  # fill rule (F0/F1)
        if m.group(1):
            if m.group(1) in "Zz":
                if sub is not None:
                    if cur != start:
                        push("L", [cur, start])
                    sub["closed"] = True
                cur, sub, args = start, None, []
                continue
            cmd, args = m.group(1), []
            continue
        args.append(float(m.group(2)))
        if cmd and cmd.lower() not in "z":
            need_n = {"m": 2, "l": 2, "h": 1, "v": 1, "c": 6, "s": 4, "q": 4, "t": 2, "a": 7}[
                cmd.lower()
            ]
            if len(args) >= need_n:
                run()
    return subs


def page_vectors(path: str, index: int) -> dict[str, Any]:
    z = open_package(path)
    try:
        parts = page_parts(z)
        if not 0 <= index < len(parts):
            raise UnderlayError("UNDERLAY_PAGE", "없는 페이지입니다", 404)
        root = _part(z, parts[index])
        w, h = _size(root)
        page = Page(w * MM, h * MM)
        budget = [MAX_ELEMENTS]
        _walk(root, IDENTITY, page, h, 0, budget, {})
        return page.to_json()
    finally:
        z.close()


def _walk(
    node: Any, m: Mat, page: Page, height: float, depth: int, budget: list[int], res: dict[str, Any]
) -> None:
    if depth > MAX_DEPTH:
        raise UnderlayError("UNDERLAY_TOO_BIG", "캔버스가 너무 깊게 중첩되었습니다")
    for e in node:
        budget[0] -= 1
        if budget[0] < 0:
            page.truncated = True
            return
        tag = _tag(e)
        if tag in ("Canvas", "Path", "Glyphs"):
            own = _mat(e.get("RenderTransform"))
            mm = _mul(own, m) if own else m
            if tag == "Canvas":
                _walk(e, mm, page, height, depth + 1, budget, res)
            elif tag == "Path":
                _path(e, mm, page, height)
            else:
                _glyphs(e, mm, page, height)
        elif tag.endswith(".RenderTransform"):
            for t in e:
                tm = _mat(t.get("Matrix"))
                if tm:
                    m = _mul(tm, m)


def _to_mm(m: Mat, height: float, p: Pt) -> Pt:
    x, y = _apply(m, p)
    return x * MM, (height - y) * MM


def _path(e: Any, m: Mat, page: Page, height: float) -> None:
    data = e.get("Data")
    if data is None:  # <Path.Data><PathGeometry Figures="..."/>
        for child in e:
            if _tag(child) == "Path.Data":
                for g in child:
                    data = g.get("Figures") or data
    fill_attr = e.get("Fill")
    stroke = _color(e.get("Stroke"))
    fill = _color(fill_attr)
    if fill_attr is None:
        for child in e:
            if _tag(child) == "Path.Fill":
                if any(_tag(x) in ("ImageBrush", "VisualBrush") for x in child):
                    page.images += 1
                else:
                    fill = fill or "#000000"
    if not data or (stroke is None and fill is None):
        return
    for sub in parse_path(data):
        segs = [(k, [_to_mm(m, height, p) for p in pts]) for k, pts in sub["segs"]]
        add_subpath(
            page, segs, bool(sub["closed"]), fill is not None and stroke is None, -1, stroke or fill
        )
        if fill is not None and stroke is not None:
            pass  # a filled and stroked shape keeps its outline only (the raster shows the fill)


def _glyphs(e: Any, m: Mat, page: Page, height: float) -> None:
    text = e.get("UnicodeString")
    if not text:
        return
    try:
        ox, oy = float(e.get("OriginX", "0")), float(e.get("OriginY", "0"))
        em = float(e.get("FontRenderingEmSize", "0"))
    except ValueError:
        return
    p0 = _to_mm(m, height, (ox, oy))
    px = _to_mm(m, height, (ox + 1, oy))
    scale = math.hypot(m[0], m[1])
    page.add_text(text, p0, em * scale * MM, math.degrees(math.atan2(px[1] - p0[1], px[0] - p0[0])))


__all__ = ["flatten_cubic", "info", "page_vectors"]
