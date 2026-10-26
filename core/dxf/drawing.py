"""Track 8 P3-1: lay projected views out on a sheet and write them as a DXF drawing.

Geometry is true size in model space; the sheet (limits) is the paper size divided by the scale,
so the P1-4 PDF plot prints it at that scale with its frame and title block, and the 2D tools
measure real lengths. Generated entities go on AXGEN_* layers: "regenerate" replaces only those
and keeps everything the user added. The settings live in the file ($CUSTOMPROPERTY), so a
drawing can be regenerated from itself.
"""

import io
import itertools
import json
import math
from collections import Counter
from typing import Any

import ezdxf
from ezdxf.enums import TextEntityAlignment
from ezdxf.math import Vec2
from ezdxf.render import mleader

from core.dxf.plot import BINDING, MARGIN, PAPER, TITLE_H, fit_scale
from core.dxf.template import CONFIG_KEY, LTSCALE, blank_dxf
from core.geometry.drawing import SECTIONS, frame, views_job

PROJECTIONS = ("THIRD", "FIRST")
LAYERS = {  # generated layer -> (ACI colour, linetype, lineweight 1/100 mm)
    "AXGEN_OUTLINE": (7, "Continuous", 50),
    "AXGEN_HIDDEN": (2, "DASHED", 25),
    "AXGEN_CENTER": (1, "CENTER", 18),
    "AXGEN_THIN": (3, "Continuous", 18),  # detail circles, cutting line ends
    "AXGEN_HATCH": (8, "Continuous", 18),
    "AXGEN_TEXT": (7, "Continuous", 25),
    "AXGEN_DIM": (3, "Continuous", 18),
}
KIND_LAYER = {
    "visible": "AXGEN_OUTLINE",
    "hidden": "AXGEN_HIDDEN",
    "center": "AXGEN_CENTER",
    "thin": "AXGEN_THIN",
}
TEXT_MM = 5.0  # paper height of view labels (KS B ISO 3098: 5 mm)
HATCH_MM = 3.0  # paper spacing of the 45 degree section hatch
ANSI31_STEP = 3.175  # ANSI31 line spacing at pattern scale 1
DETAIL_SAMPLES = 64  # segments of a circle or arc clipped into a detail view
DIM_MM = 8.0  # paper distance from the outline to the first dimension line (KS B 0001: 8-10)
TOLERANCE = {"f": "정밀", "m": "보통", "c": "거침", "v": "매우 거침"}  # KS B ISO 2768-1 classes


def _dim_style(scale: float) -> dict[str, Any]:
    """3.5 mm text and 2.5 mm arrows on paper whatever the drawing scale (DIMSCALE)."""
    return {
        "dimtxt": 3.5,
        "dimasz": 2.5,
        "dimexo": 1.0,
        "dimexe": 2.0,
        "dimgap": 1.0,
        "dimscale": 1 / scale,
        "dimdec": 2,
        "dimzin": 8,
    }


def _linear(
    msp: Any,
    p1: tuple[float, float],
    p2: tuple[float, float],
    base: tuple[float, float],
    angle: float,
    scale: float,
) -> None:
    msp.add_linear_dim(
        base=base,
        p1=p1,
        p2=p2,
        angle=angle,
        dimstyle="Standard",
        override=_dim_style(scale),
        dxfattribs={"layer": "AXGEN_DIM"},
    ).render()


def hole_text(h: dict[str, Any]) -> str:
    """KS-style hole callout from a HOLE feature's metrics: 4×M6 깊이 8, 2×Ø9 관통 + head."""
    pre = f"{h['count']}×" if h["count"] > 1 else ""
    if h.get("thread"):
        t = h["thread"]
        text = f"{pre}{t['label']}" + (f" 깊이 {t['length']:g}" if t.get("length") else "")
    else:
        text = f"{pre}Ø{h['diameter']:g}" + (f" 깊이 {h['depth']:g}" if h.get("depth") else " 관통")
    head = h.get("head") or {}
    if head.get("kind") == "COUNTERBORE":
        text += f"\\P카운터보어 Ø{head['diameter']:g} 깊이 {head['depth']:g}"
    elif head.get("kind") == "COUNTERSINK":
        text += f"\\P접시 Ø{head['diameter']:g}×90°"
    return text


def _annotate(
    msp: Any,
    views: list[dict[str, Any]],
    offsets: dict[str, tuple[float, float]],
    scale: float,
    holes: list[dict[str, Any]],
) -> dict[str, str]:
    """Overall sizes (width and height on the front view, depth beside the top view) and a
    callout with its position for each hole group, on the view that sees the holes end-on.
    Returns hole feature id -> callout handle (a quote of the drawing traces its hole lines to
    them, P4-4)."""
    callouts: dict[str, str] = {}
    by = {v["view"]: v for v in views}
    g = DIM_MM / scale

    def box(name: str) -> tuple[float, float, float, float]:
        x0, y0, x1, y1 = _bbox(by[name])
        dx, dy = offsets[name]
        return x0 + dx, y0 + dy, x1 + dx, y1 + dy

    main = next((n for n in ("FRONT", "TOP", "RIGHT") if n in by), None)
    if main:
        x0, y0, x1, y1 = box(main)
        _linear(msp, (x0, y0), (x1, y0), (x0, y0 - g), 0, scale)
        _linear(msp, (x0, y0), (x0, y1), (x0 - g, y0), 90, scale)
    if main == "FRONT" and "TOP" in by:
        x0, y0, x1, y1 = box("TOP")
        _linear(msp, (x1, y0), (x1, y1), (x1 + g, y0), 90, scale)
    for h in holes:
        axis = h["axis"]
        for name in ("TOP", "FRONT", "RIGHT"):
            if name not in by:
                continue
            n, x, y = frame(name)
            if abs(sum(a * b for a, b in zip(axis, (n.X(), n.Y(), n.Z()), strict=True))) < 1 - 1e-6:
                continue
            dx, dy = offsets[name]
            pts = [
                (
                    c[0] * x.X() + c[1] * x.Y() + c[2] * x.Z() + dx,
                    c[0] * y.X() + c[1] * y.Y() + c[2] * y.Z() + dy,
                )
                for c in h["centers"]
            ]
            (cx, cy), r = pts[0], h["diameter"] / 2
            ml = msp.add_multileader_mtext("Standard", dxfattribs={"layer": "AXGEN_DIM"})
            ml.set_content(hole_text(h), char_height=3.5 / scale)
            ml.add_leader_line(
                mleader.ConnectionSide.left, [Vec2(cx + r * 0.7071, cy + r * 0.7071)]
            )
            ml.build(insert=Vec2(cx + r + 2 * g, cy + r + 2 * g))
            if h.get("feature_id") is not None:
                callouts[str(h["feature_id"])] = ml.multileader.dxf.handle
            bx0, by0, _, by1 = box(name)  # where the first hole is, from the lower left corner
            _linear(msp, (bx0, by1), (cx, cy), (bx0, by1 + g), 0, scale)
            _linear(msp, (bx0, by0), (cx, cy), (bx0 - 2 * g, by0), 90, scale)
            break
    return callouts


def _notes(msp: Any, cfg: dict[str, Any], scale: float) -> None:
    """General notes at the top left of the sheet (KS B ISO 2768-1 tolerance, default roughness)."""
    lines = []
    if cfg.get("tolerance"):
        lines.append(f"일반 공차: KS B ISO 2768-{cfg['tolerance']} ({TOLERANCE[cfg['tolerance']]})")
    if cfg.get("roughness"):
        lines.append(f"지시 없는 면의 표면 거칠기: Ra {cfg['roughness']:g}")
    if not lines:
        return
    _, ph = sheet_size(cfg)
    text = "\\P".join(f"{i}. {t}" for i, t in enumerate(lines, 1))
    msp.add_mtext(
        text,
        dxfattribs={
            "layer": "AXGEN_TEXT",
            "char_height": 3.5 / scale,
            "insert": ((BINDING + 6) / scale, (ph - MARGIN - 6) / scale),
        },
    )


GAP_MM = 20.0  # paper mm between views


def _bbox(view: dict[str, Any]) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for e in view["visible"] + view["hidden"] + view.get("thin", []):
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

    extras = [k for k in size if k.startswith(("SECTION", "DETAIL"))]

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
        # sections and details in a row under everything, with room for their labels
        x = min((p[0] for p in pos.values()), default=0.0)
        y = min((p[1] for p in pos.values()), default=0.0) - 2 * gap
        for k in extras:
            pos[k] = (x, y - size[k][1])
            x += size[k][0] + gap
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


def _clip(a: list[float], b: list[float], c: list[float], r: float) -> list[list[float]] | None:
    """The part of segment a-b inside the circle (c, r)."""
    d = (b[0] - a[0], b[1] - a[1])
    f = (a[0] - c[0], a[1] - c[1])
    qa, qb, qc = (
        d[0] ** 2 + d[1] ** 2,
        2 * (f[0] * d[0] + f[1] * d[1]),
        f[0] ** 2 + f[1] ** 2 - r * r,
    )
    if qa == 0:
        return None
    disc = qb * qb - 4 * qa * qc
    if disc <= 0:
        return None
    t0, t1 = (-qb - disc**0.5) / (2 * qa), (-qb + disc**0.5) / (2 * qa)
    t0, t1 = max(t0, 0.0), min(t1, 1.0)
    if t0 >= t1:
        return None
    return [[a[0] + d[0] * t, a[1] + d[1] * t] for t in (t0, t1)]


def _points(e: dict[str, Any]) -> list[list[float]]:
    """An entity as a polyline (circles and arcs sampled)."""
    if e["type"] == "LINE":
        return [e["start"], e["end"]]
    if e["type"] == "POLYLINE":
        return e["points"]
    (cx, cy), r = e["center"], e["radius"]
    a0, a1 = (
        (0.0, 360.0)
        if e["type"] == "CIRCLE"
        else (e["start_angle"], e["end_angle"] + (360 if e["end_angle"] <= e["start_angle"] else 0))
    )
    n = max(2, round(DETAIL_SAMPLES * (a1 - a0) / 360))
    return [
        [
            cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
            cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n)),
        ]
        for i in range(n + 1)
    ]


def detail(parent: dict[str, Any], d: dict[str, Any]) -> dict[str, Any]:
    """A circle of a view, enlarged by the factor around its centre (KS B 0001 detail view)."""
    c, r, k = d["center"], d["radius"], d["factor"]

    def big(p: list[float]) -> list[float]:
        return [(p[0] - c[0]) * k, (p[1] - c[1]) * k]

    out: dict[str, Any] = {
        "view": f"DETAIL {d['label']}",
        "caption": f"{d['label']} ({k:g}:1)",
        "thin": [{"type": "CIRCLE", "center": [0.0, 0.0], "radius": r * k}],
    }
    for kind in ("visible", "hidden", "center"):
        ents = []
        for e in parent[kind]:
            if e["type"] == "CIRCLE" and math.dist(e["center"], c) + e["radius"] <= r:
                ents.append(
                    {"type": "CIRCLE", "center": big(e["center"]), "radius": e["radius"] * k}
                )
                continue
            pts = _points(e)
            for a, b in itertools.pairwise(pts):
                seg = _clip(a, b, c, r)
                if seg:
                    ents.append({"type": "LINE", "start": big(seg[0]), "end": big(seg[1])})
        out[kind] = ents
    return out


def _cut_line(
    view: dict[str, Any], sec: dict[str, Any], pad: float
) -> list[tuple[list[float], list[float]]]:
    """Where the section plane shows on its parent view: the cutting line, past the outline."""
    x0, y0, x1, y1 = _bbox(view)
    o = sec["offset"]
    if sec["axis"] == "Y":  # seen in TOP (x = X, y = Y)
        return [([x0 - pad, o], [x1 + pad, o])]
    if sec["axis"] == "X":  # seen in FRONT (x = X, y = Z)
        return [([o, y0 - pad], [o, y1 + pad])]
    return [([x0 - pad, o], [x1 + pad, o])]  # Z, in FRONT


def drawing_dxf(
    views: list[dict[str, Any]],
    cfg: dict[str, Any],
    current: bytes | None,
    holes: list[dict[str, Any]] | None = None,
) -> bytes:
    """A new drawing, or the current one with its generated layers redrawn (user entities kept)."""
    if current is None:
        doc = ezdxf.read(io.StringIO(blank_dxf(cfg["sheet"], cfg.get("portrait", False)).decode()))
    else:
        doc = ezdxf.read(io.StringIO(current.decode("utf-8", errors="replace")))
    msp = doc.modelspace()
    gone = [e for e in msp if e.dxf.get("layer", "").upper() in LAYERS]
    pictures = Counter(d.dxf.get("geometry") for d in doc.entitydb.query("DIMENSION"))
    for e in gone:
        block = e.dxf.get("geometry") if e.dxftype() == "DIMENSION" else None
        msp.delete_entity(e)
        if not block:
            continue
        # a generated dimension's picture goes with it; the name comes from the file, so only an
        # anonymous dimension block no other dimension uses (like writer.delete, BUG-50)
        pictures[block] -= 1
        if block.upper().startswith("*D") and block in doc.blocks and pictures[block] <= 0:
            doc.blocks.delete_block(block, safe=False)
    for name, (color, linetype, lineweight) in LAYERS.items():
        if name not in doc.layers:
            doc.layers.add(name, color=color, linetype=linetype, lineweight=lineweight)
    by_name = {v["view"]: v for v in views}
    for d in cfg.get("details") or []:
        if d["view"] not in by_name:
            raise ValueError(f"detail {d['label']}: view {d['view']} is not on the drawing")
        views = [*views, detail(by_name[d["view"]], d)]
    offsets, scale = layout(views, cfg)
    th = TEXT_MM / scale
    for v in views:
        dx, dy = offsets[v["view"]]
        for kind, layer in KIND_LAYER.items():
            for e in v.get(kind, []):
                _add(msp, e, dx, dy, layer)
        if v.get("hatch"):
            hatch = msp.add_hatch(dxfattribs={"layer": "AXGEN_HATCH"})
            hatch.set_pattern_fill("ANSI31", scale=HATCH_MM / ANSI31_STEP / scale)
            for loop in v["hatch"]:
                hatch.paths.add_polyline_path(
                    [(p[0] + dx, p[1] + dy) for p in loop], is_closed=True
                )
        if v.get("caption"):  # label under a section or detail view
            x0, y0, x1, _ = _bbox(v)
            msp.add_text(v["caption"], height=th, dxfattribs={"layer": "AXGEN_TEXT"}).set_placement(
                ((x0 + x1) / 2 + dx, y0 + dy - 2 * th), align=TextEntityAlignment.TOP_CENTER
            )
    for sec in cfg.get("sections") or []:  # cutting line and its letters on the parent view
        parent = next((v for v in views if v["view"] == SECTIONS[sec["axis"]][2]), None)
        if parent is None:
            continue
        dx, dy = offsets[parent["view"]]
        for a, b in _cut_line(parent, sec, 2 * th):
            msp.add_line(
                (a[0] + dx, a[1] + dy), (b[0] + dx, b[1] + dy), dxfattribs={"layer": "AXGEN_CENTER"}
            )
            for p in (a, b):
                msp.add_text(
                    sec["label"], height=th, dxfattribs={"layer": "AXGEN_TEXT"}
                ).set_placement(
                    (p[0] + dx, p[1] + dy + th / 2), align=TextEntityAlignment.BOTTOM_CENTER
                )
    for d in cfg.get("details") or []:  # the circle and letter on the parent view
        dx, dy = offsets[d["view"]]
        c = d["center"]
        msp.add_circle((c[0] + dx, c[1] + dy), d["radius"], dxfattribs={"layer": "AXGEN_THIN"})
        msp.add_text(d["label"], height=th, dxfattribs={"layer": "AXGEN_TEXT"}).set_placement(
            (c[0] + dx + d["radius"] * 0.75, c[1] + dy + d["radius"] * 0.75),
            align=TextEntityAlignment.BOTTOM_LEFT,
        )
    callouts = (
        _annotate(msp, views, offsets, scale, holes or []) if cfg.get("dimensions", True) else {}
    )
    _notes(msp, cfg, scale)
    pw, ph = sheet_size(cfg)
    lay = msp.dxf_layout.dxf
    lay.limmin, lay.limmax = (0, 0), (pw / scale, ph / scale)
    doc.header["$LTSCALE"] = LTSCALE / scale  # dashes keep their paper length at any scale
    stored = {**cfg, "scale": cfg.get("scale"), "used_scale": scale, "callouts": callouts}
    if doc.header.custom_vars.has_tag(CONFIG_KEY):
        doc.header.custom_vars.replace(CONFIG_KEY, json.dumps(stored))
    else:
        doc.header.custom_vars.append(CONFIG_KEY, json.dumps(stored))
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")


def drawing_job(
    breps: list[bytes],
    cfg: dict[str, Any],
    current: bytes | None,
    holes: list[dict[str, Any]] | None = None,
) -> bytes:
    """Worker entry point: project the bodies and write (or redraw) the drawing."""
    return drawing_dxf(views_job(breps, cfg["views"], cfg.get("sections")), cfg, current, holes)
