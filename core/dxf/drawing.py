"""Track 8 P3-1: lay projected views out on a sheet and write them as a DXF drawing.

Geometry is true size in model space; the sheet (limits) is the paper size divided by the scale,
so the P1-4 PDF plot prints it at that scale with its frame and title block, and the 2D tools
measure real lengths. Generated entities go on AXGEN_* layers: "regenerate" replaces only those
and keeps everything the user added. The settings live in the file ($CUSTOMPROPERTY), so a
drawing can be regenerated from itself.
"""

import io
import json
from typing import Any

import ezdxf

from core.dxf.plot import BINDING, MARGIN, PAPER, TITLE_H, fit_scale
from core.dxf.template import LTSCALE, blank_dxf
from core.geometry.drawing import views_job

PROJECTIONS = ("THIRD", "FIRST")
LAYERS = {  # generated layer -> (ACI colour, linetype, lineweight 1/100 mm)
    "AXGEN_OUTLINE": (7, "Continuous", 50),
    "AXGEN_HIDDEN": (2, "DASHED", 25),
    "AXGEN_CENTER": (1, "CENTER", 18),
}
KIND_LAYER = {"visible": "AXGEN_OUTLINE", "hidden": "AXGEN_HIDDEN", "center": "AXGEN_CENTER"}
GAP_MM = 20.0  # paper mm between views
CONFIG_KEY = "AXCAD_DRAWING"


def _bbox(view: dict[str, Any]) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for e in view["visible"] + view["hidden"]:
        if e["type"] == "LINE":
            pts = [e["start"], e["end"]]
        elif e["type"] == "POLYLINE":
            pts = e["points"]
        else:  # whole circle box: enough for layout
            (cx, cy), r = e["center"], e["radius"]
            pts = [[cx - r, cy - r], [cx + r, cy + r]]
        xs += [p[0] for p in pts]
        ys += [p[1] for p in pts]
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))


def sheet_size(cfg: dict[str, Any]) -> tuple[float, float]:
    w, h = PAPER[cfg["sheet"]]  # portrait sizes
    return (w, h) if cfg.get("portrait") else (h, w)


def layout(
    views: list[dict[str, Any]], cfg: dict[str, Any]
) -> tuple[dict[str, tuple[float, float]], float]:
    """Offset of each view (model mm) and the scale. FRONT in the middle; THIRD angle puts TOP
    above and RIGHT to the right of it, FIRST angle the other way round; ISO goes top right."""
    boxes = {v["view"]: _bbox(v) for v in views}
    size = {k: (b[2] - b[0], b[3] - b[1]) for k, b in boxes.items()}
    pw, ph = sheet_size(cfg)
    area = (pw - BINDING - MARGIN - 4, ph - 2 * MARGIN - TITLE_H - 6)
    third = cfg.get("projection", "THIRD") == "THIRD"

    def arrangement(gap: float) -> dict[str, tuple[float, float]]:
        """Lower-left corner of each view box, before centring."""
        fw, fh = size.get("FRONT", (0.0, 0.0))
        pos: dict[str, tuple[float, float]] = {}
        if "FRONT" in size:
            pos["FRONT"] = (0.0, 0.0)
        if "TOP" in size:
            th = size["TOP"][1]
            pos["TOP"] = (0.0, fh + gap) if third else (0.0, -gap - th)
        if "RIGHT" in size:
            rw, _ = size["RIGHT"]
            pos["RIGHT"] = (fw + gap, 0.0) if third else (-gap - rw, 0.0)
        if "ISO" in size:
            x = max((pos[k][0] + size[k][0] for k in pos), default=0.0) + gap
            y = max((pos[k][1] + size[k][1] for k in pos), default=0.0) - size["ISO"][1]
            pos["ISO"] = (x, max(y, 0.0))
        return pos

    def extent(pos: dict[str, tuple[float, float]]) -> tuple[float, float, float, float]:
        return (
            min(p[0] for p in pos.values()),
            min(p[1] for p in pos.values()),
            max(pos[k][0] + size[k][0] for k in pos),
            max(pos[k][1] + size[k][1] for k in pos),
        )

    scale = cfg.get("scale")
    if not scale:  # the gap is paper mm: try each standard scale with its own gap
        x0, y0, x1, y1 = extent(arrangement(0.0))
        scale = fit_scale((x1 - x0, y1 - y0), area)
        for _ in range(3):
            x0, y0, x1, y1 = extent(arrangement(GAP_MM / scale))
            s = fit_scale((x1 - x0, y1 - y0), area)
            if s == scale:
                break
            scale = s
    pos = arrangement(GAP_MM / scale)
    x0, y0, x1, y1 = extent(pos)
    # centre the views in the drawing area (model mm: paper mm / scale)
    cx = (BINDING + 2 + area[0] / 2) / scale - (x0 + x1) / 2
    cy = (MARGIN + TITLE_H + 4 + area[1] / 2) / scale - (y0 + y1) / 2
    return {k: (p[0] - boxes[k][0] + cx, p[1] - boxes[k][1] + cy) for k, p in pos.items()}, scale


def _add(msp: Any, e: dict[str, Any], dx: float, dy: float, layer: str) -> None:
    attribs = {"layer": layer}
    if e["type"] == "LINE":
        msp.add_line(
            (e["start"][0] + dx, e["start"][1] + dy),
            (e["end"][0] + dx, e["end"][1] + dy),
            dxfattribs=attribs,
        )
    elif e["type"] == "CIRCLE":
        msp.add_circle((e["center"][0] + dx, e["center"][1] + dy), e["radius"], dxfattribs=attribs)
    elif e["type"] == "ARC":
        msp.add_arc(
            (e["center"][0] + dx, e["center"][1] + dy),
            e["radius"],
            e["start_angle"],
            e["end_angle"],
            dxfattribs=attribs,
        )
    else:
        msp.add_lwpolyline([(p[0] + dx, p[1] + dy) for p in e["points"]], dxfattribs=attribs)


def read_config(data: bytes) -> tuple[dict[str, Any] | None, bool]:
    """(generation settings stored in a drawing or None, whether its model space is empty)."""
    doc = ezdxf.read(io.StringIO(data.decode("utf-8", errors="replace")))
    empty = len(doc.modelspace()) == 0
    if not doc.header.custom_vars.has_tag(CONFIG_KEY):
        return None, empty
    try:
        cfg = json.loads(doc.header.custom_vars.get(CONFIG_KEY) or "")
    except ValueError:
        return None, empty
    return (cfg if isinstance(cfg, dict) else None), empty


def drawing_dxf(views: list[dict[str, Any]], cfg: dict[str, Any], current: bytes | None) -> bytes:
    """A new drawing, or the current one with its generated layers redrawn (user entities kept)."""
    if current is None:
        doc = ezdxf.read(io.StringIO(blank_dxf(cfg["sheet"], cfg.get("portrait", False)).decode()))
    else:
        doc = ezdxf.read(io.StringIO(current.decode("utf-8", errors="replace")))
    msp = doc.modelspace()
    for e in [e for e in msp if e.dxf.get("layer", "").upper() in LAYERS]:
        msp.delete_entity(e)
    for name, (color, linetype, lineweight) in LAYERS.items():
        if name not in doc.layers:
            doc.layers.add(name, color=color, linetype=linetype, lineweight=lineweight)
    offsets, scale = layout(views, cfg)
    for v in views:
        dx, dy = offsets[v["view"]]
        for kind, layer in KIND_LAYER.items():
            for e in v[kind]:
                _add(msp, e, dx, dy, layer)
    pw, ph = sheet_size(cfg)
    lay = msp.dxf_layout.dxf
    lay.limmin, lay.limmax = (0, 0), (pw / scale, ph / scale)
    doc.header["$LTSCALE"] = LTSCALE / scale  # dashes keep their paper length at any scale
    stored = {**cfg, "scale": cfg.get("scale"), "used_scale": scale}
    if doc.header.custom_vars.has_tag(CONFIG_KEY):
        doc.header.custom_vars.replace(CONFIG_KEY, json.dumps(stored))
    else:
        doc.header.custom_vars.append(CONFIG_KEY, json.dumps(stored))
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")


def drawing_job(breps: list[bytes], cfg: dict[str, Any], current: bytes | None) -> bytes:
    """Worker entry point: project the bodies and write (or redraw) the drawing."""
    return drawing_dxf(views_job(breps, cfg["views"]), cfg, current)
