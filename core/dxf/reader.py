"""DXF validation and parsing into the render payload (mm, JSON-serialisable)."""

import logging
import math
import multiprocessing
import resource
from collections import Counter
from collections.abc import Callable
from typing import Any

import ezdxf
from ezdxf import colors, units
from ezdxf.entities import DXFGraphic
from ezdxf.path import make_path

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_DEPTH = 16
MAX_EXPANDED = 500_000
FLATTEN_MM = 0.01
MAX_POINTS = 1_000_000  # output cap: blocks can amplify a small file into huge payloads
MAX_TEXT_CHARS = 10_000
MAX_WARNINGS = 50
WORKER_MEMORY_BYTES = 2 * 1024**3
BINARY_SIGNATURE = b"AutoCAD Binary DXF\r\n\x1a\x00"
PATH_TYPES = {"LINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE", "LWPOLYLINE", "POLYLINE", "SOLID"}


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


def unit_scale(doc: Any) -> float:
    """mm per drawing unit (unitless = 1.0, same rule for read and write)."""
    if doc.units == 0:
        return 1.0
    return float(
        units.conversion_factor(units.InsertUnits(doc.units), units.InsertUnits.Millimeters)
    )


EDITABLE_TYPES = {"LINE", "CIRCLE", "ARC", "LWPOLYLINE", "TEXT"}


def is_editable(e: Any) -> bool:
    """Editable = supported type, default extrusion and no attribute we could not round-trip."""
    kind = e.dxftype()
    if kind not in EDITABLE_TYPES or not e.dxf.extrusion.isclose((0, 0, 1)):
        return False
    if kind == "TEXT":
        return e.dxf.halign == 0 and e.dxf.valign == 0
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
    return {
        "type": kind,
        "insert": _round(d.insert * s),
        "height": _num(d.height * s),
        "value": d.text[:MAX_TEXT_CHARS],
        "rotation": _num(d.rotation),
    }


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
            }
        )

    entities: list[dict[str, Any]] = []
    unsupported: Counter[str] = Counter()
    pts: list[list[float]] = []
    expanded = 0

    def walk(items: Any, depth: int, parent: tuple[str, str, str | None] | None) -> None:
        nonlocal expanded
        for e in items:
            expanded += 1
            if expanded > MAX_EXPANDED:
                raise DxfError("DXF_BLOCK_LIMIT", "Too many expanded entities", 422)
            kind = e.dxftype()
            layer = e.dxf.layer
            if parent and layer == "0":
                layer = parent[1]
            if kind in ("INSERT", "DIMENSION"):
                if depth + 1 > MAX_DEPTH:
                    raise DxfError("DXF_BLOCK_LIMIT", "Block nesting too deep", 422)
                handle = parent[0] if parent else e.dxf.handle
                ctx = (handle, layer, _color(e, layer, parent))
                cells = e.multi_insert() if kind == "INSERT" and e.mcount > 1 else [e]  # MINSERT
                for cell in cells:
                    walk(cell.virtual_entities(), depth + 1, ctx)
                continue
            rec: dict[str, Any] = {
                "handle": parent[0] if parent else e.dxf.handle,
                "type": kind,
                "layer": layer,
                "color": _color(e, layer, parent),
                "paths": [],
            }
            if kind in PATH_TYPES and not (kind == "POLYLINE" and not e.is_2d_polyline):
                line = [_round(p * scale) for p in make_path(e).flattening(FLATTEN_MM / scale)]
                rec["paths"] = [line]
                pts.extend(line)
                if len(pts) > MAX_POINTS:  # checked after adding: one entity may be huge
                    raise DxfError("DXF_BLOCK_LIMIT", "Drawing too large to render", 422)
            elif kind in ("TEXT", "MTEXT"):
                ins = _round(e.dxf.insert * scale)
                if kind == "TEXT":
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
                pts.append(ins)
            else:
                unsupported[kind] += 1
                continue
            if parent is None and is_editable(e):
                rec["geom"] = _geom(e, scale)
            entities.append(rec)

    def _color(e: DXFGraphic, layer: str, parent: tuple[str, str, str | None] | None) -> str | None:
        tc = e.dxf.get("true_color")
        if tc is not None:
            return colors.int2rgb(tc).to_hex()
        aci = e.dxf.get("color", 256)
        if aci == 0 and parent:
            return parent[2]
        if aci in (0, 256):
            return layer_color.get(layer)
        return _aci_color(aci)

    walk(doc.modelspace(), 0, None)
    warnings += [f"UNSUPPORTED_ENTITY:{k} x{n}" for k, n in sorted(unsupported.items())]
    if len(warnings) > MAX_WARNINGS:
        warnings = warnings[:MAX_WARNINGS] + [f"WARNINGS_TRUNCATED:{len(warnings) - MAX_WARNINGS}"]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return {
        "units": "mm",
        "extents": {"min": [min(xs), min(ys)], "max": [max(xs), max(ys)]}
        if pts
        else {"min": [0, 0], "max": [0, 0]},
        "layers": layers,
        "entities": entities,
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


def _worker(func: Callable[..., Any], args: tuple[Any, ...], fail: tuple[str, str]) -> Any:
    # ponytail: RLIMIT_AS is enforced on Linux only (macOS ignores it), container limits in deploy
    try:
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
