"""PDF/DWF underlays in DXF (2D underlay U2): PDFUNDERLAY / DWFUNDERLAY entities and their
PDFDEFINITION / DWFDEFINITION objects.

A definition's file name is `underlay/<sha256>/<name>`: the content hash links the entity to the
project's stored file (see backend/api/routes_underlay.py). A path from any other program is never
opened or followed: such an underlay is shown as "not linked" with its file name, nothing more.

Units: `insert` is in drawing units (converted like every point); the scale is drawing units per
millimetre of the page (so 1.0 shows the page at its printed size in a drawing made in mm); the clip
boundary is in page millimetres, unscaled, from the page's lower-left corner."""

from __future__ import annotations

import math
import re
from typing import Any

from ezdxf import const

UNDERLAY_TYPES = ("PDFUNDERLAY", "DWFUNDERLAY")
DEF_TYPES = {"pdf": "PDFDEFINITION", "dwf": "DWFDEFINITION"}
FORMAT_OF = {"PDFUNDERLAY": "pdf", "DWFUNDERLAY": "dwf"}
INSIDE = 0x10  # DXF flag 16: clip inside (the default) vs outside
FILENAME = re.compile(r"^underlay/([0-9a-f]{64})/(.{0,200})$")
BAD_NAME = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]')
MAX_CLIP_POINTS = 500


def _finite(v: Any, default: float) -> float:
    x = float(v)
    return x if math.isfinite(x) and abs(x) < 1e12 else default


def clean_name(name: str, fmt: str) -> str:
    n = BAD_NAME.sub("_", name.replace("\\", "/").rsplit("/", 1)[-1]).strip()[:120]
    return n or f"underlay.{'pdf' if fmt == 'pdf' else 'dwfx'}"


def def_filename(sha: str, name: str, fmt: str) -> str:
    return f"underlay/{sha}/{clean_name(name, fmt)}"


def parse_filename(filename: str) -> tuple[str | None, str]:
    """(sha256 or None, the name to show) of a definition's file name; a foreign path gives None."""
    m = FILENAME.match(filename or "")
    if m:
        return m.group(1), clean_name(m.group(2), "pdf")
    shown = BAD_NAME.sub("_", (filename or "").replace("\\", "/").rsplit("/", 1)[-1])[:120]
    return None, shown or "(이름 없음)"


def _definition(e: Any) -> Any:
    try:
        return e.get_underlay_def()
    except Exception:  # noqa: BLE001 - a damaged reference must not reject the drawing
        return None


def read(e: Any, s: float) -> dict[str, Any]:
    """The editor's view of one underlay entity (positions in mm; s = drawing unit -> mm)."""
    d = e.dxf
    fmt = FORMAT_OF[e.dxftype()]
    definition = _definition(e)
    filename = definition.dxf.get("filename", "") if definition is not None else ""
    sha, name = parse_filename(filename)
    try:
        page = (
            max(0, int(str(definition.dxf.get("name", "1")).strip() or "1") - 1)
            if definition is not None
            else 0
        )
    except ValueError:
        page = 0
    flags = int(d.get("flags", 0))
    sx, sy = _finite(d.get("scale_x", 1.0), 1.0), _finite(d.get("scale_y", 1.0), 1.0)
    sx = max(
        1e-6, min(1e6, abs(sx))
    )  # a file from elsewhere may hold anything; the edit API's range
    clip = None
    pts = [(float(p[0]), float(p[1])) for p in list(e.boundary_path)[:MAX_CLIP_POINTS]]
    if flags & const.UNDERLAY_CLIPPING and len(pts) >= 2:
        clip = {
            "kind": "rect" if len(pts) == 2 else "poly",
            "points": [[round(x, 4), round(y, 4)] for x, y in pts],
        }
    ins = [_finite(v, 0.0) for v in d.get("insert", (0.0, 0.0, 0.0))]
    return {
        "format": fmt,
        "sha256": sha,
        "linked": sha is not None,
        "name": name,
        "page": page,
        "insert": [round(float(ins[0]) * s, 6), round(float(ins[1]) * s, 6)],
        # one scale for both directions: a non-uniform one is read as its x value and warned about
        "scale": round(sx * s, 9),
        "uniform": abs(sx - sy) <= 1e-9 * max(1.0, abs(sx)),
        "rotation": round(_finite(d.get("rotation", 0.0), 0.0), 6),
        "flags": {
            "on": bool(flags & const.UNDERLAY_ON),
            "monochrome": bool(flags & const.UNDERLAY_MONOCHROME),
            "adjust": bool(flags & const.UNDERLAY_ADJUST_FOR_BG),
            # bit 16 set: the clip keeps what is inside it; files from other programs often leave it unset
            "inside": bool(flags & INSIDE) or not (flags & const.UNDERLAY_CLIPPING),
        },
        "contrast": max(0, min(100, int(d.get("contrast", 50)))),
        "fade": max(0, min(80, int(d.get("fade", 0)))),
        "clip": clip,
    }


def flags_of(f: dict[str, bool], clip: bool) -> int:
    v = 0
    if f.get("on", True):
        v |= const.UNDERLAY_ON
    if f.get("monochrome"):
        v |= const.UNDERLAY_MONOCHROME
    if f.get("adjust"):
        v |= const.UNDERLAY_ADJUST_FOR_BG
    if f.get("inside", True):
        v |= INSIDE
    if clip:
        v |= const.UNDERLAY_CLIPPING
    return v


def find_definition(doc: Any, fmt: str, filename: str, page: int) -> Any:
    wanted = DEF_TYPES[fmt]
    for obj in doc.objects:
        if (
            obj.dxftype() == wanted
            and obj.dxf.get("filename") == filename
            and obj.dxf.get("name") == str(page + 1)
        ):
            return obj
    return None


def ensure_definition(doc: Any, fmt: str, sha: str, name: str, page: int) -> Any:
    filename = def_filename(sha, name, fmt)
    return find_definition(doc, fmt, filename, page) or doc.add_underlay_def(
        filename, fmt=fmt, name=str(page + 1)
    )


def apply(e: Any, doc: Any, g: dict[str, Any], s: float) -> None:
    """Writes a geometry (see main.UnderlayGeom) onto an underlay entity (inverse of read)."""
    fmt = g["format"]
    if FORMAT_OF[e.dxftype()] != fmt:
        raise ValueError("format cannot change")
    definition = ensure_definition(doc, fmt, g["sha256"], g["name"], g["page"])
    e.dxf.underlay_def_handle = definition.dxf.handle
    e.dxf.insert = (g["insert"][0] / s, g["insert"][1] / s, 0.0)
    k = g["scale"] / s
    e.dxf.scale_x = e.dxf.scale_y = e.dxf.scale_z = k
    e.dxf.rotation = g["rotation"]
    e.dxf.contrast = g["contrast"]
    e.dxf.fade = g["fade"]
    clip = g.get("clip")
    if clip:
        e.set_boundary_path([(p[0], p[1]) for p in clip["points"]])
    else:
        e.reset_boundary_path()
    e.dxf.flags = flags_of(g["flags"], bool(clip))
    try:  # keep the definition's reactor list in step (ezdxf does it for add_underlay)
        definition.append_reactor_handle(e.dxf.handle)
    except Exception:  # noqa: BLE001, S110 - only AutoCAD's own bookkeeping
        pass
