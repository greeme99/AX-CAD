"""PDF/DWF underlays (2D U1): a reference file's pages as read-only content for the 2D editor.

A page is kept in two forms, never mixed:
- the raster (`render`): what the screen and the plot show, made on demand at the size asked;
- the vector cache (`Page`): lines, arcs/circles and text in page millimetres (origin at the
  page's lower-left corner, after the page's own rotation), used for snapping, measuring and
  importing as objects. A scanned page has no vectors: it shows but cannot be snapped to.

Formats: PDF (PDFium via pypdfium2), DWFx (XPS FixedPage paths) and classic DWF (W2D opcodes).
Every parse runs in an isolated worker with a time and memory limit (core.dxf.reader.run_isolated).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

MAX_UNDERLAY_BYTES = 100 * 1024 * 1024  # an upload
MAX_PAGES = 500  # pages listed; only the pages placed are parsed
MAX_POINTS = 400_000  # vector points per page kept for snapping (the raster shows everything)
MAX_TEXTS = 20_000
MAX_LAYERS = 500
MAX_RENDER_PX = 8192  # longest side of a rendered image
MAX_RENDER_AREA = 40_000_000  # pixels of one rendered image
FLATTEN_MM = 0.05  # curve flattening tolerance on the page (snap points of unrecognised curves)
ROUND = 4  # decimals kept (0.1 micrometre)

KINDS = ("pdf", "dwfx")  # a classic DWF (W2D) is recognised and refused, see ops.classify
MAX_ENTRIES_CLASSIFY = 20_000


class UnderlayError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status

    def __reduce__(self) -> tuple[Any, ...]:  # crosses the worker process boundary
        return (UnderlayError, (self.code, self.message, self.http_status))


def sniff(data: bytes) -> str:
    """The underlay kind from the file's own bytes (the name is not trusted)."""
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"(DWF V"):
        return "dwf"
    if data.startswith(b"PK\x03\x04"):  # a package: DWF 6 (manifest.xml) or DWFx (OPC/XPS)
        return "zip"
    raise UnderlayError("UNDERLAY_FORMAT", "PDF, DWF 또는 DWFx 파일이 아닙니다")


def _r(v: float) -> float:
    return round(float(v), ROUND)


@dataclass
class Page:
    """One page's vectors in page millimetres; built through the add_* methods (limits)."""

    width: float
    height: float
    layers: list[str] = field(default_factory=list)
    paths: list[dict[str, Any]] = field(default_factory=list)
    arcs: list[dict[str, Any]] = field(default_factory=list)
    texts: list[dict[str, Any]] = field(default_factory=list)
    images: int = 0  # raster images on the page (a page with images and no vectors is a scan)
    points: int = 0
    truncated: bool = False

    def layer(self, name: str | None) -> int:
        """Index of a layer name (-1: no layer)."""
        if not name:
            return -1
        name = name[:255]
        if name in self.layers:
            return self.layers.index(name)
        if len(self.layers) >= MAX_LAYERS:
            return -1
        self.layers.append(name)
        return len(self.layers) - 1

    def _room(self, n: int) -> bool:
        if self.points + n > MAX_POINTS:
            self.truncated = True
            return False
        self.points += n
        return True

    def add_path(
        self,
        pts: list[tuple[float, float]],
        closed: bool = False,
        fill: bool = False,
        layer: int = -1,
        color: str | None = None,
        curve: bool = False,
    ) -> None:
        """A polyline; curve=True marks a flattened curve (only its ends are snap end points)."""
        pts = _dedupe(pts)
        if len(pts) < 2 or not all(math.isfinite(c) for p in pts for c in p):
            return
        if closed and len(pts) > 2 and _close(pts[0], pts[-1]):
            pts = pts[:-1]
        if not self._room(len(pts)):
            return
        rec: dict[str, Any] = {"p": [_r(c) for p in pts for c in p]}
        if closed and len(pts) > 2:
            rec["z"] = 1
        if fill:
            rec["f"] = 1
        if curve:
            rec["k"] = "c"
        if layer >= 0:
            rec["l"] = layer
        if color and color != "#000000":
            rec["c"] = color
        self.paths.append(rec)

    def add_arc(
        self,
        center: tuple[float, float],
        radius: float,
        start: float = 0.0,
        end: float = 360.0,
        layer: int = -1,
        color: str | None = None,
    ) -> None:
        """A circle (0..360) or a CCW arc, angles in degrees."""
        if not (radius > 0 and math.isfinite(radius) and all(map(math.isfinite, center))):
            return
        if not self._room(4):
            return
        rec: dict[str, Any] = {"c": [_r(center[0]), _r(center[1])], "r": _r(radius)}
        if not _full(start, end):
            rec["a"] = [_r(start % 360), _r(end % 360)]
        if layer >= 0:
            rec["l"] = layer
        if color and color != "#000000":
            rec["co"] = color
        self.arcs.append(rec)

    def add_text(
        self, value: str, at: tuple[float, float], height: float, rotation: float, layer: int = -1
    ) -> None:
        value = "".join(ch for ch in value if ch >= " ").strip()[:500]
        if not value or not (height > 0) or len(self.texts) >= MAX_TEXTS:
            return
        if not all(map(math.isfinite, (*at, height, rotation))):
            return
        rec: dict[str, Any] = {"v": value, "p": [_r(at[0]), _r(at[1])], "h": _r(height)}
        if abs(rotation) > 1e-9:
            rec["r"] = _r(rotation)
        if layer >= 0:
            rec["l"] = layer
        self.texts.append(rec)

    def to_json(self) -> dict[str, Any]:
        vectors = bool(self.paths or self.arcs)
        return {
            "size": [_r(self.width), _r(self.height)],
            "layers": self.layers,
            "paths": self.paths,
            "arcs": self.arcs,
            "texts": self.texts,
            "raster_only": not vectors and self.images > 0,
            "truncated": self.truncated,
        }


def _close(a: tuple[float, float], b: tuple[float, float], tol: float = 1e-6) -> bool:
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def _dedupe(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for p in pts:
        if not out or not _close(out[-1], p):
            out.append(p)
    return out


def _full(start: float, end: float) -> bool:
    return abs(end - start) >= 360 - 1e-6


# --- curves -----------------------------------------------------------------------------------

Pt = tuple[float, float]


def bezier(p0: Pt, p1: Pt, p2: Pt, p3: Pt, t: float) -> Pt:
    u = 1 - t
    a, b, c, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
    return (
        a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
        a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1],
    )


def flatten_cubic(p0: Pt, p1: Pt, p2: Pt, p3: Pt, tol: float = FLATTEN_MM) -> list[Pt]:
    """Points after p0 along a cubic, close enough to it for snapping (at most 64)."""
    hull = math.dist(p0, p1) + math.dist(p1, p2) + math.dist(p2, p3)
    n = max(1, min(64, math.ceil(math.sqrt(hull / max(tol, 1e-9)))))
    return [bezier(p0, p1, p2, p3, i / n) for i in range(1, n + 1)]


def _circle3(a: Pt, b: Pt, c: Pt) -> tuple[Pt, float] | None:
    d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(d) < 1e-12:
        return None
    a2, b2, c2 = a[0] ** 2 + a[1] ** 2, b[0] ** 2 + b[1] ** 2, c[0] ** 2 + c[1] ** 2
    x = (a2 * (b[1] - c[1]) + b2 * (c[1] - a[1]) + c2 * (a[1] - b[1])) / d
    y = (a2 * (c[0] - b[0]) + b2 * (a[0] - c[0]) + c2 * (b[0] - a[0])) / d
    return (x, y), math.dist((x, y), a)


def cubic_arc(p0: Pt, p1: Pt, p2: Pt, p3: Pt) -> tuple[Pt, float, float, float] | None:
    """(center, radius, start°, sweep°) when a cubic is a circular arc (as CAD programs write
    circles and arcs into PDF), else None. sweep is signed: + counter-clockwise."""
    c = _circle3(p0, bezier(p0, p1, p2, p3, 0.5), p3)
    if c is None:
        return None
    (cx, cy), r = c
    if r <= 0 or r > 1e7:
        return None
    tol = max(r * 2e-3, 1e-4)
    for t in (0.125, 0.25, 0.375, 0.625, 0.75, 0.875):
        if abs(math.dist((cx, cy), bezier(p0, p1, p2, p3, t)) - r) > tol:
            return None
    a0 = math.degrees(math.atan2(p0[1] - cy, p0[0] - cx))
    am = math.degrees(
        math.atan2(bezier(p0, p1, p2, p3, 0.5)[1] - cy, bezier(p0, p1, p2, p3, 0.5)[0] - cx)
    )
    a1 = math.degrees(math.atan2(p3[1] - cy, p3[0] - cx))
    half1 = (am - a0 + 180) % 360 - 180
    half2 = (a1 - am + 180) % 360 - 180
    sweep = half1 + half2
    if abs(sweep) > 181 or abs(sweep) < 1e-6:  # one cubic spans at most a half circle
        return None
    return (cx, cy), r, a0, sweep


def add_subpath(
    page: Page,
    segs: list[tuple[str, list[Pt]]],
    closed: bool,
    fill: bool,
    layer: int,
    color: str | None,
) -> None:
    """A subpath given as ("L", [p0, p1]) / ("C", [p0, p1, p2, p3]) segments: runs of straight
    segments become polylines, runs of circular cubics arcs or circles, other curves flattened."""
    if not segs:
        return
    if fill and not closed:
        closed = True
    run: list[Pt] = []  # current straight run
    arc: list[Any] | None = None  # [center, r, start, sweep]
    whole_lines = all(k == "L" for k, _ in segs)

    def flush_run() -> None:
        nonlocal run
        if len(run) >= 2:
            page.add_path(run, closed=closed and whole_lines, fill=fill, layer=layer, color=color)
        run = []

    def flush_arc() -> None:
        nonlocal arc
        if arc is None:
            return
        (cx, cy), r, a0, sweep = arc
        if abs(sweep) >= 359.9:
            page.add_arc((cx, cy), r, 0, 360, layer, color)
        else:
            s, e = (a0, a0 + sweep) if sweep > 0 else (a0 + sweep, a0)
            page.add_arc((cx, cy), r, s, e, layer, color)
        arc = None

    for kind, pts in segs:
        if kind == "L":
            flush_arc()
            if not run:
                run = [pts[0]]
            run.append(pts[1])
            continue
        fit = cubic_arc(pts[0], pts[1], pts[2], pts[3])
        if fit is not None:
            flush_run()
            (cx, cy), r, a0, sweep = fit
            if (
                arc is not None
                and math.dist(arc[0], (cx, cy)) <= max(r * 2e-3, 1e-4)
                and abs(arc[1] - r) <= max(r * 2e-3, 1e-4)
                and (arc[3] > 0) == (sweep > 0)
            ):
                arc[3] += sweep
            else:
                flush_arc()
                arc = [(cx, cy), r, a0, sweep]
            continue
        flush_arc()
        flush_run()
        c0, c1, c2, c3 = pts
        page.add_path(
            [c0, *flatten_cubic(c0, c1, c2, c3)], fill=fill, layer=layer, color=color, curve=True
        )
    flush_arc()
    flush_run()
