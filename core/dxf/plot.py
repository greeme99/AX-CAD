"""Track 8 P1-4: model space of a stored render payload as a scaled PDF sheet with a title block
(ISO 216 paper, ISO 5457-style frame: 20 mm binding margin on the left, 10 mm elsewhere)."""

import io
import json
from collections.abc import Callable
from typing import Any

from core.dxf.reader import DxfError

PAPER = {"A4": (210, 297), "A3": (297, 420), "A2": (420, 594), "A1": (594, 841), "A0": (841, 1189)}
# paper mm per model mm, largest first: 10:1 .. 1:10000 (KS/ISO 5455 recommended scales)
SCALES = [10, 5, 2, 1] + [
    1 / n for n in (2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000, 10000)
]
MARGIN, BINDING = 10.0, 20.0
PROJECTION_TEXT = {"THIRD": "제3각법", "FIRST": "제1각법"}
TITLE_W, TITLE_H = 180.0, 28.0
THIN = 0.18  # mm, lines without a lineweight
PT = 72 / 25.4  # PDF points per mm
# budgets (security review BUG-48): text is the costly part of a PDF, so it is capped; text whose
# insertion point is off the sheet is skipped; a PDF over the cap is refused, not sent
MAX_PLOT_TEXT_CHARS = 1_000_000
MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_SHEET_MM = 5000.0  # a sheet's paper side: PDF readers stop at 200 in (5080 mm)


def scale_text(s: float) -> str:
    return f"{round(s)}:1" if s >= 1 else f"1:{round(1 / s)}"


def fit_scale(size: tuple[float, float], area: tuple[float, float]) -> float:
    """Largest standard scale that fits the drawing into the area (the smallest one otherwise)."""
    w, h = size
    for s in SCALES:
        if w * s <= area[0] and h * s <= area[1]:
            return s
    return SCALES[-1]


def plot_pdf(
    payload: dict[str, Any],
    paper: str,
    landscape: bool,
    scale: float | None,
    title: dict[str, str],
    hidden: set[str],
    color: bool = False,
) -> tuple[bytes, float]:
    """PDF bytes and the scale used (paper mm per model mm)."""
    from reportlab.pdfgen import canvas

    from core.quote_engine.report import _fonts

    _fonts()
    pw, ph = PAPER[paper]
    if landscape:
        pw, ph = ph, pw
    fx0, fy0, fx1, fy1 = BINDING, MARGIN, pw - MARGIN, ph - MARGIN  # frame
    area = (fx0 + 2, fy0 + TITLE_H + 4, fx1 - 2, fy1 - 2)  # drawing area above the title block
    shown = {ly["name"] for ly in payload["layers"] if ly.get("visible", True)} - hidden
    ents = [e for e in payload["entities"] if e["layer"] in shown]
    lo, hi = payload["extents"]["min"], payload["extents"]["max"]
    size = (max(hi[0] - lo[0], 1e-9), max(hi[1] - lo[1], 1e-9))
    s = scale or fit_scale(size, (area[2] - area[0], area[3] - area[1]))
    # model centre -> centre of the drawing area
    cx, cy = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
    ax, ay = (area[0] + area[2]) / 2, (area[1] + area[3]) / 2
    drawing = payload.get("drawing") or {}
    if drawing.get("used_scale") and drawing.get("sheet") == paper and not scale:
        # Track 8 P3: a drawing generated from 3D is laid out on its sheet: print it 1:1 to it
        s = drawing["used_scale"]
        cx, cy, ax, ay = 0.0, 0.0, 0.0, 0.0
        title = {
            **title,
            "material": drawing.get("material") or "",
            "projection": PROJECTION_TEXT.get(drawing.get("projection", ""), ""),
        }

    def at(p: list[float]) -> tuple[float, float]:
        return ((ax + (p[0] - cx) * s) * PT, (ay + (p[1] - cy) * s) * PT)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(pw * PT, ph * PT))
    c.setTitle(title.get("title", ""))
    c.setLineCap(1)
    c.setLineJoin(1)
    c.saveState()
    _clip(c, area)  # a fixed scale may not fit: never draw over the frame
    _draw(c, ents, at, s, area, color, [MAX_PLOT_TEXT_CHARS])
    c.restoreState()
    _frame(c, pw, ph, (fx0, fy0, fx1, fy1), {**title, "scale": scale_text(s), "paper": paper})
    c.showPage()
    c.save()
    if buf.tell() > MAX_PDF_BYTES:
        raise DxfError("PLOT_TOO_LARGE", "The PDF would exceed 50 MB", 413)
    return buf.getvalue(), s


def plot_sheet(
    payload: dict[str, Any], name: str, title: str, hidden: set[str], color: bool = False
) -> tuple[bytes, float]:
    """Track 8 P3-4 (C-2): a paper-space sheet as it is laid out, 1:1 on its own paper size:
    model space through each viewport, then the sheet's own lines (its frame and title block)."""
    from reportlab.pdfgen import canvas

    from core.quote_engine.report import _fonts

    sheet = next((sh for sh in payload.get("sheets") or [] if sh["name"] == name), None)
    if sheet is None:
        raise DxfError("PLOT_SHEET_NOT_FOUND", "Sheet not found", 404)
    lo, hi = sheet["paper"]["min"], sheet["paper"]["max"]
    if not (hi[0] - lo[0] > 1 and hi[1] - lo[1] > 1):  # no paper size set: the sheet's extents
        lo, hi = sheet["extents"]["min"], sheet["extents"]["max"]
    pw, ph = hi[0] - lo[0], hi[1] - lo[1]
    if not (1 <= pw <= MAX_SHEET_MM and 1 <= ph <= MAX_SHEET_MM):
        raise DxfError("PLOT_SHEET_INVALID", "Sheet paper size out of range", 422)
    _fonts()
    shown = {ly["name"] for ly in payload["layers"] if ly.get("visible", True)} - hidden
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(pw * PT, ph * PT))
    c.setTitle(title)
    c.setLineCap(1)
    c.setLineJoin(1)
    budget = [MAX_PLOT_TEXT_CHARS]
    page = (0.0, 0.0, pw, ph)
    model = [e for e in payload["entities"] if e["layer"] in shown]
    for v in sheet["viewports"]:
        (x0, y0), (x1, y1) = v["frame"]
        z, (vx, vy) = v["zoom"], v["view_center"]
        # the view centre sits at the viewport centre on the page
        dx, dy = (x0 + x1) / 2 - lo[0] - vx * z, (y0 + y1) / 2 - lo[1] - vy * z

        def at(p: list[float], dx: float = dx, dy: float = dy, z: float = z) -> tuple[float, float]:
            return ((dx + p[0] * z) * PT, (dy + p[1] * z) * PT)

        box = (x0 - lo[0], y0 - lo[1], x1 - lo[0], y1 - lo[1])
        c.saveState()
        _clip(c, box)
        _draw(c, model, at, z, box, color, budget)
        c.restoreState()

    def on_page(p: list[float]) -> tuple[float, float]:
        return ((p[0] - lo[0]) * PT, (p[1] - lo[1]) * PT)

    _draw(
        c, [e for e in sheet["entities"] if e["layer"] in shown], on_page, 1.0, page, color, budget
    )
    c.showPage()
    c.save()
    if buf.tell() > MAX_PDF_BYTES:
        raise DxfError("PLOT_TOO_LARGE", "The PDF would exceed 50 MB", 413)
    return buf.getvalue(), 1.0


def plot_file(path: str, *args: Any) -> tuple[bytes, float]:
    """plot_pdf on a stored render payload, for an isolated worker (the payload is read there)."""
    with open(path, encoding="utf-8") as f:
        return plot_pdf(json.load(f), *args)


def sheet_file(path: str, *args: Any) -> tuple[bytes, float]:
    """plot_sheet on a stored render payload, for an isolated worker."""
    with open(path, encoding="utf-8") as f:
        return plot_sheet(json.load(f), *args)


def _clip(c: Any, box: tuple[float, ...]) -> None:
    x0, y0, x1, y1 = box
    clip = c.beginPath()
    clip.rect(x0 * PT, y0 * PT, (x1 - x0) * PT, (y1 - y0) * PT)
    c.clipPath(clip, stroke=0, fill=0)


def _draw(
    c: Any,
    ents: list[dict[str, Any]],
    at: Callable[[list[float]], tuple[float, float]],
    s: float,
    area: tuple[float, ...],
    color: bool,
    budget: list[int],
) -> None:
    """Entities onto the page: at maps model mm to PDF points, s is paper mm per model mm;
    text whose insertion point is outside area (paper mm) is skipped; budget = text left."""
    from reportlab.lib.colors import HexColor, black

    for e in ents:
        ink = HexColor(e["color"]) if color and e.get("color") else black
        c.setStrokeColor(ink)
        c.setFillColor(ink)
        c.setLineWidth((e.get("lw") or THIN) * PT)
        dash = [d * s * PT for d in e.get("dash") or []]
        # a pattern under 1 mm on paper reads as solid; dots keep a printable length
        c.setDash([max(d, 0.3 * PT) for d in dash] if sum(dash) >= PT else [])
        paths = [p for p in e["paths"] if len(p) > 1]
        if paths:
            pen = c.beginPath()
            for path in paths:
                pen.moveTo(*at(path[0]))
                for q in path[1:]:
                    pen.lineTo(*at(q))
            if e.get("fill"):
                c.drawPath(pen, stroke=0, fill=1, fillMode=0)  # 0 = even-odd, islands stay open
            else:
                c.drawPath(pen, stroke=1, fill=0)
        if e.get("point"):
            x, y = at(e["point"])
            c.circle(x, y, 0.25 * PT, stroke=0, fill=1)
        t = e.get("text")
        if t and t["height"] * s >= 0.5:  # under 0.5 mm on paper is unreadable
            x, y = at(t["insert"])
            if not (area[0] * PT <= x <= area[2] * PT and area[1] * PT <= y <= area[3] * PT):
                continue  # off the sheet: clipped anyway
            budget[0] -= len(t["value"])
            if budget[0] < 0:
                raise DxfError("PLOT_TOO_LARGE", "Too much text to plot", 413)
            size_pt = t["height"] * s * PT
            c.saveState()
            c.translate(x, y)
            c.rotate(t["rotation"])
            c.setFont("Nanum", size_pt)
            for i, line in enumerate(t["value"].split("\n")):  # MTEXT paragraphs stack down
                c.drawString(0, -i * size_pt * 1.4, line)
            c.restoreState()


def _frame(c: Any, pw: float, ph: float, f: tuple[float, ...], info: dict[str, str]) -> None:
    from reportlab.lib.colors import black

    c.setStrokeColor(black)
    c.setFillColor(black)
    c.setDash([])
    c.setLineWidth(0.7 * PT)
    fx0, fy0, fx1, fy1 = f
    c.rect(fx0 * PT, fy0 * PT, (fx1 - fx0) * PT, (fy1 - fy0) * PT)
    # centring marks at the middle of each side (ISO 5457)
    for x0, y0, x1, y1 in (
        (pw / 2, 0, pw / 2, fy0),
        (pw / 2, fy1, pw / 2, ph),
        (0, ph / 2, fx0, ph / 2),
        (fx1, ph / 2, pw, ph / 2),
    ):
        c.line(x0 * PT, y0 * PT, x1 * PT, y1 * PT)
    # title block, bottom right: three rows
    x0, y0 = fx1 - TITLE_W, fy0
    c.rect(x0 * PT, y0 * PT, TITLE_W * PT, TITLE_H * PT)
    c.setLineWidth(0.25 * PT)
    rows = [y0 + 9, y0 + 19]  # bottom row 9 mm, middle 10 mm, top 9 mm
    for y in rows:
        c.line(x0 * PT, y * PT, (x0 + TITLE_W) * PT, y * PT)
    cells = [  # (row bottom, row height, [(x offset, width, label, value)])
        (
            y0 + 19,
            9,
            [
                (0, 120, "도면번호", info.get("doc_no", "")),
                (120, 60, "리비전", info.get("revision", "")),
            ],
        ),
        (
            y0 + 9,
            10,
            [
                (0, 115, "도면명", info.get("title", "")),
                (115, 40, "재질", info.get("material", "")),
                (155, 25, "투상법", info.get("projection", "")),
            ],
        ),
        (
            y0,
            9,
            [
                (0, 30, "축척", info["scale"]),
                (30, 25, "용지", info["paper"]),
                (55, 20, "단위", "mm"),
                (75, 40, "일자", info.get("date", "")),
                (115, 65, "작성", info.get("author", "")),
            ],
        ),
    ]
    for yb, h, row in cells:
        for xo, w, label, value in row:
            if xo:
                c.line((x0 + xo) * PT, yb * PT, (x0 + xo) * PT, (yb + h) * PT)
            c.setFont("Nanum", 2.2 * PT)
            c.drawString((x0 + xo + 1.5) * PT, (yb + h - 3) * PT, label)
            big = 4.5 if label == "도면명" else 3.2
            c.setFont("Nanum-Bold" if label in ("도면명", "도면번호") else "Nanum", big * PT)
            c.drawString((x0 + xo + 1.5) * PT, (yb + 1.6) * PT, _fit(c, value, w - 3, big))


def _fit(c: Any, text: str, width_mm: float, size_mm: float) -> str:
    """Cut a value to its cell (with an ellipsis) instead of running over the next one."""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    if stringWidth(text, "Nanum", size_mm * PT) <= width_mm * PT:
        return text
    while text and stringWidth(text + "…", "Nanum", size_mm * PT) > width_mm * PT:
        text = text[:-1]
    return text + "…"
