"""DXF validation and parsing into the render payload (mm, JSON-serialisable)."""

import itertools
import json
import logging
import math
import multiprocessing
from collections import Counter
from collections.abc import Callable
from typing import Any

import ezdxf
from ezdxf import colors, units
from ezdxf.entities import BoundaryPathType, DXFGraphic
from ezdxf.math import Vec3
from ezdxf.path import from_hatch, make_path
from ezdxf.render import hatching
from ezdxf.render.mleader import get_style
from ezdxf.tools.pattern import ISO_PATTERN

from core.dxf.template import CONFIG_KEY

try:
    import resource
except ImportError:  # Windows (per-PC install, Track 7): no RLIMIT_AS; job timeouts still apply
    resource = None  # type: ignore[assignment]

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_DEPTH = 16
MAX_EXPANDED = 500_000
FLATTEN_MM = 0.01
MAX_POINTS = 1_000_000  # output cap: blocks can amplify a small file into huge payloads
MAX_TEXT_CHARS = 10_000
MAX_WARNINGS = 50
MAX_LAYER_CHARS = 255  # AutoCAD limit; every entity repeats its layer name in the payload
MAX_SHEETS = 20  # paper-space layouts rendered (title-block sheets)
MAX_LINETYPES = 200  # names offered by the property editor
MAX_BLOCKS = 500  # insertable blocks listed for the editor
MAX_SPLINE_FIT = 1000  # rebuilding a spline is O(n^2) in memory (BUG-48)
MAX_BLOCK_POINTS = 100_000  # preview geometry of all listed blocks together
RENDER_FORMAT = 6  # bump when the payload gains fields: older stored payloads are re-parsed
WORKER_MEMORY_BYTES = 2 * 1024**3
BINARY_SIGNATURE = b"AutoCAD Binary DXF\r\n\x1a\x00"
PATH_TYPES = {
    "LINE",
    "ARC",
    "CIRCLE",
    "ELLIPSE",
    "SPLINE",
    "LWPOLYLINE",
    "POLYLINE",
    "SOLID",
    "TRACE",
}
FILLED_TYPES = {"SOLID", "TRACE"}  # 2D solids (also leader arrowheads): drawn filled
EXPANDED_TYPES = {"INSERT", "DIMENSION", "LEADER", "MULTILEADER"}  # drawn via virtual_entities()
SILENT_TYPES = {"ATTDEF", "VIEWPORT"}  # block-editor-only / handled per sheet: no warning
# 3D data saved as DXF (decision 10): drawn as its top-view (XY) outline only. Never editable, and
# the quote metrics (metrics2d) skip these types, so a projection never turns into a price.
VIEW_3D_TYPES = {"3DFACE", "HELIX"}
SOLID_3D_TYPES = {"3DSOLID", "BODY", "REGION", "MESH"}  # + *SURFACE: models, upload as STEP


class DxfError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(code, message, http_status)  # keeps the exception picklable
        self.code, self.message, self.http_status = code, message, http_status


def validate_dxf_bytes(data: bytes) -> None:
    if not data:
        raise DxfError("DXF_INVALID_FILE", "Empty file")
    if len(data) > MAX_FILE_BYTES:
        raise DxfError("FILE_TOO_LARGE", "File exceeds 50 MB", 413)
    if data.startswith(BINARY_SIGNATURE):
        return
    if b"\x00" in data:
        raise DxfError("DXF_INVALID_FILE", "Not a DXF file")
    text = data[:65536].decode("utf-8-sig", errors="replace")  # header only
    lines = [ln.strip() for ln in text.splitlines()]
    i = 0
    while i + 1 < len(lines):
        if lines[i] == "999":
            i += 2
            continue
        break
    if i + 1 >= len(lines) or lines[i] != "0" or lines[i + 1] != "SECTION":
        raise DxfError("DXF_INVALID_FILE", "Missing DXF header")


def _aci_color(aci: int) -> str | None:
    return None if aci == 7 else colors.aci2rgb(aci).to_hex()


def _round(p: Any) -> list[float]:
    x, y = float(p[0]), float(p[1])
    if not (math.isfinite(x) and math.isfinite(y)):
        raise DxfError("DXF_INVALID_GEOMETRY", "Non-finite coordinate", 422)
    return [round(x, 6), round(y, 6)]


def flatten(e: Any, step: float, limit: int) -> Any:
    """make_path(e).flattening, bounded: adaptive subdivision never ends on a non-finite control
    point or a step below float resolution, so both are ruled out before it starts."""
    return flatten_path(make_path(e), step, limit)


def flatten_path(path: Any, step: float, limit: int) -> Any:
    cv = [c for v in path.control_vertices() for c in v]
    if not all(math.isfinite(c) for c in cv):
        raise DxfError("DXF_INVALID_GEOMETRY", "Non-finite coordinate", 422)
    step = max(step, 1e-9 * max((abs(c) for c in cv), default=0.0))
    return itertools.islice(path.flattening(step), limit)


def unit_scale(doc: Any) -> float:
    """mm per drawing unit (unitless = 1.0, same rule for read and write)."""
    if doc.units == 0:
        return 1.0
    return float(
        units.conversion_factor(units.InsertUnits(doc.units), units.InsertUnits.Millimeters)
    )


EDITABLE_TYPES = {
    *("LINE", "CIRCLE", "ARC", "LWPOLYLINE", "TEXT", "DIMENSION", "HATCH", "INSERT"),
    *("POINT", "ELLIPSE", "SPLINE", "LEADER", "MULTILEADER"),
}
DIM_KINDS = {
    0: "DIM_LINEAR",
    1: "DIM_ALIGNED",
    3: "DIM_DIAMETER",
    4: "DIM_RADIUS",
    5: "DIM_ANGULAR",
}


def is_editable(e: Any) -> bool:
    """Editable = supported type, default extrusion and no attribute we could not round-trip."""
    kind = e.dxftype()
    if kind not in EDITABLE_TYPES:
        return False
    if kind == "MULTILEADER":  # one straight leader to unrotated text in the XY plane
        c = e.context
        return (
            c.mtext is not None
            and c.block is None
            and len(c.leaders) == 1
            and len(c.leaders[0].lines) == 1
            and len(c.leaders[0].lines[0].vertices) >= 1
            and not c.plane_normal_reversed
            and Vec3(c.plane_x_axis).cross(c.plane_y_axis).isclose((0, 0, 1))
            and abs(c.mtext.rotation) < 1e-9
            and get_style(e, e.doc).get("leader_type") != 2  # 2 = spline
        )
    normal = e.dxf.get("normal_vector" if kind == "LEADER" else "extrusion", None)
    if normal is not None and not Vec3(normal).isclose((0, 0, 1)):
        return False
    if kind == "TEXT":
        return e.dxf.halign == 0 and e.dxf.valign == 0
    if kind == "HATCH":  # one closed polyline boundary, a library pattern or solid, no gradient
        paths = e.paths.paths
        return (
            len(paths) == 1
            and paths[0].type == BoundaryPathType.POLYLINE
            and paths[0].is_closed
            and e.gradient is None
            and (
                e.dxf.solid_fill or (e.dxf.pattern_type == 1 and e.dxf.pattern_name in ISO_PATTERN)
            )
        )
    if kind == "INSERT":  # a single, non-degenerate reference to a block of this drawing
        b = e.block()
        return (
            b is not None
            and not b.block.is_xref
            and e.mcount == 1
            and bool(e.dxf.xscale)
            and bool(e.dxf.yscale)
        )
    if kind == "SPLINE":  # a cubic curve through fit points, rebuilt from them when edited
        return e.dxf.degree == 3 and 2 <= e.fit_point_count() <= MAX_SPLINE_FIT and not e.closed
    if kind == "LEADER":
        return len(e.vertices) >= 2
    if kind == "DIMENSION":  # a text override would be lost when the dimension is rebuilt
        return e.dxf.dimtype & 0x0F in DIM_KINDS and e.dxf.get("text", "<>") in ("", "<>")
    if kind == "LWPOLYLINE":  # set_points(xyb) would drop widths: keep such polylines read-only
        return not e.dxf.const_width and not any(p[2] or p[3] for p in e.get_points("xyseb"))
    return True


def _num(v: float) -> float:
    return _round((v, 0))[0]


def _geom(e: Any, s: float) -> dict[str, Any]:
    kind = e.dxftype()
    d = e.dxf
    if kind == "LINE":
        return {"type": kind, "start": _round(d.start * s), "end": _round(d.end * s)}
    if kind == "CIRCLE":
        return {"type": kind, "center": _round(d.center * s), "radius": _num(d.radius * s)}
    if kind == "ARC":
        return {
            "type": kind,
            "center": _round(d.center * s),
            "radius": _num(d.radius * s),
            "start_angle": _num(d.start_angle),
            "end_angle": _num(d.end_angle),
        }
    if kind == "LWPOLYLINE":
        pts = [[*_round((x * s, y * s)), _num(b)] for x, y, b in e.get_points("xyb")]
        return {"type": kind, "points": pts, "closed": bool(e.closed)}
    if kind == "POINT":
        return {"type": kind, "point": _round(d.location * s)}
    if kind == "ELLIPSE":  # parameters in radians (CCW from the major axis)
        return {
            "type": kind,
            "center": _round(d.center * s),
            "major": _round(d.major_axis * s),
            "ratio": _num(d.ratio),
            "start_param": _num(d.start_param),
            "end_param": _num(d.end_param),
        }
    if kind == "SPLINE":
        return {"type": kind, "fit": [_round(Vec3(p) * s) for p in e.fit_points]}
    if kind == "LEADER":
        return {"type": kind, "vertices": [_round(Vec3(p) * s) for p in e.vertices]}
    if kind == "MULTILEADER":  # the landing (dogleg) becomes the last leg
        c = e.context
        leader = c.leaders[0]
        pts = [*leader.lines[0].vertices, leader.last_leader_point]
        if get_style(e, e.doc).get("has_dogleg") and leader.dogleg_length > 0:
            pts.append(
                leader.last_leader_point + leader.dogleg_vector.normalize(leader.dogleg_length)
            )
        return {
            "type": kind,
            "vertices": [_round(Vec3(p) * s) for p in pts],
            "value": c.mtext.default_content[:MAX_TEXT_CHARS],
            "height": _num(c.char_height * s),
        }
    if kind == "INSERT":
        return {
            "type": kind,
            "name": d.name,
            "insert": _round(d.insert * s),
            "xscale": _num(d.xscale),
            "yscale": _num(d.yscale),
            "rotation": _num(d.rotation),
        }
    if kind == "HATCH":
        loop = e.paths.paths[0].vertices
        return {
            "type": kind,
            "boundary": [[*_round((v[0] * s, v[1] * s)), _num(v[2])] for v in loop],
            "pattern": "SOLID" if d.solid_fill else d.pattern_name,
            "scale": _num(d.pattern_scale),
            "angle": _num(d.pattern_angle),
        }
    return {
        "type": kind,
        "insert": _round(d.insert * s),
        "height": _num(d.height * s),
        "value": d.text[:MAX_TEXT_CHARS],
        "rotation": _num(d.rotation),
    }


def _dim_geom(e: Any, s: float) -> dict[str, Any]:
    """Dimension as its defining points (WCS), the same shape the editor creates."""
    d = e.dxf
    kind = DIM_KINDS[d.dimtype & 0x0F]
    p = {k: d.get(k) for k in ("defpoint", "defpoint2", "defpoint3", "defpoint4", "defpoint5")}
    if kind == "DIM_LINEAR":
        return {
            "type": kind,
            "p1": _round(p["defpoint2"] * s),
            "p2": _round(p["defpoint3"] * s),
            "base": _round(p["defpoint"] * s),
            "angle": _num(d.get("angle", 0.0)),
        }
    if kind == "DIM_ALIGNED":  # defpoint sits on the dimension line: signed offset left of p1->p2
        a, b, c = p["defpoint2"], p["defpoint3"], p["defpoint"]
        u = (b - a).normalize()
        return {
            "type": kind,
            "p1": _round(a * s),
            "p2": _round(b * s),
            "distance": _num(((c - a).y * u.x - (c - a).x * u.y) * s),
        }
    if kind == "DIM_ANGULAR":
        return {
            "type": kind,
            "center": _round(p["defpoint4"] * s),
            "p1": _round(p["defpoint2"] * s),
            "p2": _round(p["defpoint3"] * s),
            "base": _round(p["defpoint"] * s),
        }
    a, b = p["defpoint"], p["defpoint4"]  # radius: centre + point on the curve; diameter: both ends
    c = a if kind == "DIM_RADIUS" else a.lerp(b)
    far = b if kind == "DIM_RADIUS" else a
    return {
        "type": kind,
        "center": _round(c * s),
        "radius": _num(c.distance(far) * s),
        "angle": _num(math.degrees(math.atan2(far.y - c.y, far.x - c.x))),
    }


def _edit_geom(e: Any, s: float) -> dict[str, Any] | None:
    """Editable definition of an entity, or None. A malformed one (zero-length dimension, odd
    spline) is still drawn, only not editable: it must not reject a drawing that opened before."""
    if not is_editable(e):
        return None
    try:
        return _dim_geom(e, s) if e.dxftype() == "DIMENSION" else _geom(e, s)
    except Exception as exc:  # noqa: BLE001 - ezdxf/geometry errors on unusual input
        logging.getLogger(__name__).info("entity %s not editable: %r", e.dxf.handle, exc)
        return None


def _block_catalog(doc: Any, s: float) -> list[dict[str, Any]]:
    """Blocks the editor can insert, with their lines/curves in block coordinates (mm, base point
    at 0,0) for previews; nested blocks, text and hatches are left to the server's drawing."""
    out: list[dict[str, Any]] = []
    budget = MAX_BLOCK_POINTS
    for b in doc.blocks:
        if b.name.startswith("*") or b.is_any_layout or b.block.is_xref:
            continue
        if len(out) == MAX_BLOCKS:
            break
        base = b.block.dxf.base_point
        paths: list[list[list[float]]] = []
        for e in b:
            if budget <= 0:
                paths = []  # too big to preview: the editor shows a marker until saved
                break
            if e.dxftype() in PATH_TYPES and e.dxftype() not in FILLED_TYPES:
                try:  # a broken entity in a block (even an unused one) only loses its preview
                    line = [_round((p - base) * s) for p in flatten(e, FLATTEN_MM / s, budget)]
                except Exception:  # noqa: BLE001, S112 - non-finite or degenerate curves
                    continue
                budget -= len(line)
                paths.append(line)
        out.append({"name": b.name, "paths": paths})
    return out


def parse_dxf(path: str) -> dict[str, Any]:
    # readfile (strict) instead of recover: a corrupt drawing must be rejected with
    # DXF_PARSE_ERROR, not silently "repaired" into quantities that feed quotes.
    doc = ezdxf.readfile(path)
    warnings: list[str] = []
    scale = unit_scale(doc)
    if doc.units == 0:
        warnings.append("UNITS_ASSUMED_MM")
    elif abs(scale - 1.0) > 1e-9:
        warnings.append(f"UNITS_CONVERTED:{units.decode(doc.units)}")
    if any(b.block is not None and b.block.dxf.flags & 4 for b in doc.blocks):
        warnings.append("DXF_XREF_IGNORED")

    layers: list[dict[str, Any]] = []
    layer_color: dict[str, str | None] = {}
    for lay in doc.layers:
        tc = lay.dxf.get("true_color")
        col = colors.int2rgb(tc).to_hex() if tc is not None else _aci_color(abs(lay.dxf.color))
        layer_color[lay.dxf.name] = col
        layers.append(
            {
                "name": lay.dxf.name,
                "color": col,
                "visible": lay.is_on() and not lay.is_frozen(),
                "locked": lay.is_locked(),
                "aci": abs(lay.dxf.color),
                "linetype": lay.dxf.linetype,
            }
        )

    # linetypes as simplified dash patterns (drawing units), lineweights in mm (FN-02)
    layer_style = {
        lay.dxf.name: (
            lay.dxf.get("linetype", "CONTINUOUS"),
            _lineweight(lay.dxf.get("lineweight", -3)),
        )
        for lay in doc.layers
    }
    patterns: dict[str, list[float] | None] = {}
    ltscale = float(doc.header.get("$LTSCALE", 1.0)) or 1.0

    def pattern(name: str) -> list[float] | None:
        if name not in patterns:
            patterns[name] = _dash_pattern(doc, name)
        return patterns[name]

    def style(e: Any, layer: str, parent: Ctx | None) -> tuple[list[float] | None, float | None]:
        lt = e.dxf.get("linetype", "BYLAYER")
        if lt.upper() == "BYBLOCK":
            dash = parent[3] if parent else None
        else:
            base = pattern(
                layer_style.get(layer, ("CONTINUOUS", None))[0] if lt.upper() == "BYLAYER" else lt
            )
            k = ltscale * float(e.dxf.get("ltscale", 1.0)) * scale
            dash = [round(v * k, 6) for v in base] if base else None
        lw = e.dxf.get("lineweight", -1)
        width = (
            layer_style.get(layer, ("", None))[1]
            if lw == -1
            else (parent[4] if parent else None)
            if lw == -2
            else _lineweight(lw)
        )
        return dash, width

    unsupported: Counter[str] = Counter()
    expanded = 0
    view_3d = 0
    total = 0  # rendered points over model space and every sheet
    hatch_simplified = 0

    def add_points(line: list[list[float]], pts: list[list[float]]) -> None:
        nonlocal total
        pts.extend(line)
        total += len(line)
        if total > MAX_POINTS:  # checked after adding: one entity may be huge
            raise DxfError("DXF_BLOCK_LIMIT", "Drawing too large to render", 422)

    def walk(
        items: Any,
        depth: int,
        parent: Ctx | None,
        out: list[dict[str, Any]],
        pts: list[list[float]],
        vports: list[dict[str, Any]] | None,
    ) -> None:
        nonlocal expanded, view_3d, hatch_simplified
        for e in items:
            expanded += 1
            if expanded > MAX_EXPANDED:
                raise DxfError("DXF_BLOCK_LIMIT", "Too many expanded entities", 422)
            kind = e.dxftype()
            layer = e.dxf.layer
            if parent and layer == "0":
                layer = parent[1]
            if len(layer) > MAX_LAYER_CHARS:
                raise DxfError("DXF_INVALID_FILE", "Layer name too long", 422)
            if kind == "VIEWPORT":
                if (
                    vports is not None
                    and e.dxf.get("id", 0) > 1
                    and e.dxf.get("view_height", 0) > 0
                ):
                    vports.append(_viewport(e, scale))  # id 1 is the sheet itself
                continue
            mesh = kind == "POLYLINE" and (e.is_poly_face_mesh or e.is_polygon_mesh)
            if kind in EXPANDED_TYPES or mesh:  # a mesh expands into its 3DFACEs
                if depth + 1 > MAX_DEPTH:
                    raise DxfError("DXF_BLOCK_LIMIT", "Block nesting too deep", 422)
                # MINSERT cells / mesh vertices counted before the work they cause is done
                expanded += e.mcount - 1 if kind == "INSERT" else len(e.vertices) if mesh else 0
                if expanded > MAX_EXPANDED:
                    raise DxfError("DXF_BLOCK_LIMIT", "Too many expanded entities", 422)
                handle = parent[0] if parent else e.dxf.handle
                ctx: Ctx = (handle, layer, _color(e, layer, parent), *style(e, layer, parent))
                cells = e.multi_insert() if kind == "INSERT" and e.mcount > 1 else [e]  # MINSERT
                first = len(out)
                for cell in cells:
                    walk(cell.virtual_entities(), depth + 1, ctx, out, pts, vports)
                top = parent is None and vports is None and len(out) > first
                g = _edit_geom(e, scale) if kind in EDITABLE_TYPES else None
                if g and top:
                    out[first]["geom"] = g
                    out[first].update(aci=e.dxf.color, linetype=e.dxf.linetype)
                if kind == "INSERT" and e.mcount <= 1:  # attribute values sit on the INSERT
                    walk(
                        [a for a in e.attribs if not a.is_invisible],
                        depth + 1,
                        ctx,
                        out,
                        pts,
                        vports,
                    )
                continue
            rec: dict[str, Any] = {
                "handle": parent[0] if parent else e.dxf.handle,
                "type": kind,
                "layer": layer,
                "color": _color(e, layer, parent),
                "paths": [],
            }
            dash, width = style(e, layer, parent)
            if dash:
                rec["dash"] = dash
            if width:
                rec["lw"] = width
            is_3d = kind in VIEW_3D_TYPES or (kind == "POLYLINE" and e.is_3d_polyline)
            if kind in PATH_TYPES or is_3d:  # the only POLYLINEs left are 2D or 3D lines
                cap = MAX_POINTS - total + 1  # one past the cap trips the check
                line = [_round(p * scale) for p in flatten(e, FLATTEN_MM / scale, cap)]
                rec["paths"] = [line]
                add_points(line, pts)
                view_3d += is_3d
                if kind in FILLED_TYPES:
                    rec["fill"] = True
            elif kind == "HATCH":
                for hp in from_hatch(e):
                    loop = [
                        _round(p * scale)
                        for p in flatten_path(hp, FLATTEN_MM / scale, MAX_POINTS - total + 1)
                    ]
                    if len(loop) > 1 and loop[0] != loop[-1]:
                        loop.append(loop[0])
                    rec["paths"].append(loop)
                    add_points(loop, pts)
                if e.dxf.solid_fill:  # solid and gradient fills
                    rec["fill"] = True
                else:
                    lines = _hatch_lines(e, scale)
                    if lines is None:
                        hatch_simplified += 1  # too dense to draw: boundary only
                    else:
                        rec["paths"] += lines
                        add_points([p for ln in lines for p in ln], [])
            elif kind == "POINT":
                rec["point"] = _round(e.dxf.location * scale)
                add_points([rec["point"]], pts)
            elif kind in ("TEXT", "ATTRIB", "MTEXT"):
                ins = _round(e.dxf.insert * scale)
                if kind != "MTEXT":
                    text = {"value": e.dxf.text, "height": e.dxf.height, "rotation": e.dxf.rotation}
                else:
                    text = {
                        "value": e.plain_text(),
                        "height": e.dxf.char_height,
                        "rotation": e.get_rotation(),
                    }
                text["height"] = round(text["height"] * scale, 6)
                text["value"] = text["value"][:MAX_TEXT_CHARS]
                rec["text"] = {"insert": ins, **text}
                add_points([ins], pts)
            else:
                if kind not in SILENT_TYPES:
                    unsupported[kind] += 1
                continue
            g = _edit_geom(e, scale) if parent is None and vports is None else None
            if g:  # model space only
                rec["geom"] = g
                rec.update(aci=e.dxf.color, linetype=e.dxf.linetype)  # editable properties
                if e.dxf.get("ltscale", 1.0) != 1.0:  # the editor scales linetype previews by it
                    rec["lts"] = _num(e.dxf.ltscale)
            out.append(rec)

    def _color(e: DXFGraphic, layer: str, parent: Ctx | None) -> str | None:
        tc = e.dxf.get("true_color")
        if tc is not None:
            return colors.int2rgb(tc).to_hex()
        aci = e.dxf.get("color", 256)
        if aci == 0 and parent:
            return parent[2]
        if aci in (0, 256):
            return layer_color.get(layer)
        return _aci_color(aci)

    entities: list[dict[str, Any]] = []
    pts: list[list[float]] = []
    walk(doc.modelspace(), 0, None, entities, pts, None)
    sheets: list[dict[str, Any]] = []
    for name in doc.layouts.names_in_taborder()[1:]:
        layout: Any = doc.layouts.get(name)  # Paperspace
        items: list[dict[str, Any]] = []
        spts: list[list[float]] = []
        vports: list[dict[str, Any]] = []
        walk(layout, 0, None, items, spts, vports)
        if not items and not vports:
            continue  # an unused default layout
        if len(sheets) == MAX_SHEETS:
            warnings.append(f"SHEETS_TRUNCATED:{MAX_SHEETS}")
            break
        lo, hi = layout.get_paper_limits()
        k = 25.4 if layout.dxf.get("plot_paper_units", 1) == 0 else 1.0  # inches -> mm
        frame = [_round(lo * k), _round(hi * k)]
        for v in vports:
            spts += v["frame"]
        sheets.append(
            {
                "name": name[:255],
                "paper": {"min": frame[0], "max": frame[1]},
                "entities": items,
                "viewports": vports,
                "extents": _extents(spts + frame),
            }
        )
    if view_3d:
        warnings.append(f"VIEW_ONLY_3D:{view_3d}")
    if hatch_simplified:
        warnings.append(f"HATCH_PATTERN_SIMPLIFIED:{hatch_simplified}")
    if any(k in SOLID_3D_TYPES or k.endswith("SURFACE") for k in unsupported):
        warnings.append("MODEL_3D_USE_STEP")
    warnings += [f"UNSUPPORTED_ENTITY:{k[:64]} x{n}" for k, n in sorted(unsupported.items())]
    if len(warnings) > MAX_WARNINGS:
        warnings = warnings[:MAX_WARNINGS] + [f"WARNINGS_TRUNCATED:{len(warnings) - MAX_WARNINGS}"]
    names = [lt.dxf.name for lt in doc.linetypes][:MAX_LINETYPES]
    return {
        "format": RENDER_FORMAT,
        "units": "mm",
        "extents": _extents(pts) if pts else _limits(doc),
        "layers": layers,
        # the layer the drawing was saved on (AX-CAD blank drawings start on OUTLINE)
        "current_layer": str(doc.header.get("$CLAYER", "0")),
        # Track 8 P3: the settings of a drawing generated from 3D (title block, sheet placement)
        "drawing": _drawing_config(doc),
        "entities": entities,
        "sheets": sheets,
        "linetypes": names,
        # dash patterns in mm (LTSCALE applied): the editor previews a linetype change before saving
        "linetype_dash": {
            n: [round(v * ltscale * scale, 6) for v in p] if (p := pattern(n)) else None
            for n in names
        },
        "blocks": _block_catalog(doc, scale),
        "summary": {
            "entity_count": len(entities),
            "layer_count": len(layers),
            # ponytail: *Model_Space/*Paper_Space and anonymous blocks counted out only by '*' prefix
            "block_count": sum(1 for b in doc.blocks if not b.name.startswith("*")),
            "paperspace_layouts": len(doc.layouts.names_in_taborder()) - 1,
        },
        "warnings": warnings,
        "parent_revision_id": None,
    }


Ctx = tuple[
    str, str, str | None, list[float] | None, float | None
]  # handle, layer, color, dash, lw
HATCH_MAX_LINES = 20_000  # per hatch; denser patterns are drawn as their boundary only


def _drawing_config(doc: Any) -> dict[str, Any] | None:
    if not doc.header.custom_vars.has_tag(CONFIG_KEY):
        return None
    try:
        cfg = json.loads(doc.header.custom_vars.get(CONFIG_KEY) or "")
    except ValueError:
        return None
    return cfg if isinstance(cfg, dict) else None


def _limits(doc: Any) -> dict[str, list[float]]:
    """An empty drawing shows its sheet (drawing limits) instead of a point at the origin."""
    try:
        lo, hi = doc.header.get("$LIMMIN"), doc.header.get("$LIMMAX")
        box = [float(lo[0]), float(lo[1]), float(hi[0]), float(hi[1])]
    except (TypeError, ValueError, IndexError):
        return _extents([])
    ok = (
        all(math.isfinite(v) and abs(v) <= 1e9 for v in box) and box[2] > box[0] and box[3] > box[1]
    )
    return {"min": box[:2], "max": box[2:]} if ok else _extents([])


def _extents(pts: list[list[float]]) -> dict[str, list[float]]:
    if not pts:
        return {"min": [0, 0], "max": [0, 0]}
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return {"min": [min(xs), min(ys)], "max": [max(xs), max(ys)]}


def _lineweight(v: int) -> float | None:
    """DXF lineweight (1/100 mm; -1 BYLAYER, -2 BYBLOCK, -3 default) -> mm, None for default."""
    return v / 100 if v > 0 else None


def _dash_pattern(doc: Any, name: str) -> list[float] | None:
    """A linetype as an alternating [dash, gap, ...] list in drawing units (dots are 0-length
    dashes; shapes and text inside complex linetypes are left out). None = continuous."""
    if name.upper() in ("CONTINUOUS", "BYLAYER", "BYBLOCK") or name not in doc.linetypes:
        return None
    out: list[float] = []
    for tag in doc.linetypes.get(name).pattern_tags.tags:
        if tag.code != 49:
            continue
        v = float(tag.value)
        if not math.isfinite(v):
            return None
        dash = v >= 0
        if not out and not dash:
            out.append(0.0)  # patterns start with a dash
        if (len(out) % 2 == 0) == dash:
            out.append(abs(v))
        else:
            out[-1] += abs(v)  # two dashes (or gaps) in a row merge
    if len(out) % 2:
        out.append(0.0)
    return out[:32] if any(out[1::2]) else None  # no gaps = continuous


def _hatch_lines(e: Any, scale: float) -> list[list[list[float]]] | None:
    """Pattern lines of a hatch in mm, None when the pattern is too dense to draw."""
    try:
        lines = list(itertools.islice(hatching.hatch_entity(e), HATCH_MAX_LINES + 1))
    except hatching.HatchingError:
        return None
    if len(lines) > HATCH_MAX_LINES:
        return None
    return [[_round(a * scale), _round(b * scale)] for a, b in lines]


def _viewport(e: Any, scale: float) -> dict[str, Any]:
    """A paper-space viewport: where model space shows on the sheet (twist and per-viewport
    layer freezes ignored: ponytail)."""
    cx, cy = _round(e.dxf.center * scale)
    w, h = float(e.dxf.width) * scale, float(e.dxf.height) * scale
    return {
        "frame": [
            [round(cx - w / 2, 6), round(cy - h / 2, 6)],
            [round(cx + w / 2, 6), round(cy + h / 2, 6)],
        ],
        "view_center": _round(e.dxf.view_center_point * scale),
        "zoom": round(float(e.dxf.height) / float(e.dxf.view_height), 9),  # paper mm per model mm
    }


def _worker(func: Callable[..., Any], args: tuple[Any, ...], fail: tuple[str, str]) -> Any:
    # ponytail: RLIMIT_AS is enforced on Linux only (macOS ignores it), container limits in deploy
    try:
        if resource is not None:
            resource.setrlimit(resource.RLIMIT_AS, (WORKER_MEMORY_BYTES, WORKER_MEMORY_BYTES))
    except (ValueError, OSError):
        pass
    try:
        return func(*args)
    except DxfError:
        raise
    except Exception as exc:  # noqa: BLE001 - ezdxf.DXFError, struct/unicode/recursion errors on corrupt input
        logging.getLogger(__name__).warning(
            "DXF worker failed: %r", exc
        )  # detail stays server-side
        raise DxfError(fail[0], fail[1], 422) from None


def run_isolated(
    func: Callable[..., Any],
    *args: Any,
    timeout_s: float = 30,
    fail: tuple[str, str] = ("DXF_PARSE_ERROR", "DXF 파일을 해석할 수 없습니다"),
) -> Any:
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(1) as pool:  # __exit__ terminates the worker
        try:
            return pool.apply_async(_worker, (func, args, fail)).get(timeout_s)
        except multiprocessing.TimeoutError:
            raise DxfError("DXF_PARSE_TIMEOUT", "Parsing timed out", 504) from None


def parse_dxf_with_timeout(path: str, timeout_s: float = 30) -> dict[str, Any]:
    result: dict[str, Any] = run_isolated(parse_dxf, path, timeout_s=timeout_s)
    return result
