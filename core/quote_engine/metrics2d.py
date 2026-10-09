"""FN-14 2D quote metrics from a DXF: cutting length, holes, bends, net area, title block.

Every counted item keeps the top-level source handle (INSERT handle for block contents) so a
quote line can be traced back to the drawing (CLAUDE.md: Source Entity -> Rule -> Price -> Line).
"""

import fnmatch
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

import ezdxf
from ezdxf.path import make_path

from core.dxf.reader import MAX_DEPTH, MAX_EXPANDED, DxfError, unit_scale

FLATTEN_MM = 0.001  # splines/ellipses only; lines, arcs, bulges and circles are exact
KEY_MM = 1e-6  # duplicate detection (FN-14 business rule)
JOIN_MM = 1e-4  # endpoint snapping when chaining open edges into loops
MAX_ITEMS = 20_000  # per-entity trace rows kept in the result


@dataclass
class Rules:
    """Layer/linetype mapping (FN-16 master data; defaults until the G1 sheet arrives)."""

    bend_layers: list[str] = field(default_factory=lambda: ["BEND*"])
    bend_linetypes: list[str] = field(default_factory=list)
    ignore_layers: list[str] = field(
        default_factory=lambda: ["DIM*", "TEXT*", "CENTER*", "HIDDEN*", "TITLE*", "FORMAT*"]
    )
    ignore_linetypes: list[str] = field(default_factory=lambda: ["CENTER*", "HIDDEN*"])
    punch_max_dia_mm: float | None = None  # holes up to this diameter are punched, not cut
    title_tags: dict[str, list[str]] = field(
        default_factory=lambda: {
            "part_no": ["PART_NO", "PARTNO", "DWG_NO", "품번"],
            "part_name": ["PART_NAME", "NAME", "TITLE", "품명"],
            "material": ["MATERIAL", "MAT", "재질"],
            "thickness_mm": ["THICKNESS", "THK", "T", "두께"],
            "qty": ["QTY", "Q'TY", "QUANTITY", "수량"],
        }
    )


def _match(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name.upper(), p.upper()) for p in patterns)


# --- primitives (mm, WCS) -------------------------------------------------------------------


@dataclass
class Prim:
    kind: str  # SEG | ARC | CIRCLE | CURVE
    handle: str
    layer: str
    length: float
    pts: list[tuple[float, float]]  # polyline approximation, start..end (closed for CIRCLE)
    key: tuple[Any, ...]
    radius: float = 0.0
    center: tuple[float, float] = (0.0, 0.0)


def _k(v: float) -> int:
    return round(v / KEY_MM)


def _pk(p: tuple[float, float]) -> tuple[int, int]:
    return (_k(p[0]), _k(p[1]))


def _arc_pts(
    c: tuple[float, float], r: float, a0: float, sweep: float
) -> list[tuple[float, float]]:
    n = (
        max(2, math.ceil(abs(sweep) / (2 * math.acos(max(-1.0, 1 - FLATTEN_MM / r))) + 1))
        if r > 0
        else 2
    )
    return [
        (
            c[0] + r * math.cos(a0 + sweep * i / (n - 1)),
            c[1] + r * math.sin(a0 + sweep * i / (n - 1)),
        )
        for i in range(n)
    ]


def _seg(h: str, layer: str, a: tuple[float, float], b: tuple[float, float]) -> Prim:
    key = ("SEG", *sorted((_pk(a), _pk(b))))
    return Prim("SEG", h, layer, math.dist(a, b), [a, b], key)


def _arc(h: str, layer: str, c: tuple[float, float], r: float, a0: float, sweep: float) -> Prim:
    """CCW for sweep > 0. Key is direction-independent."""
    pts = _arc_pts(c, r, a0, sweep)
    lo, hi = (a0, a0 + sweep) if sweep > 0 else (a0 + sweep, a0)
    key = ("ARC", _pk(c), _k(r), _k(math.degrees(lo) % 360), _k(math.degrees(hi - lo)))
    return Prim("ARC", h, layer, abs(sweep) * r, pts, key, r, c)


def _bulge(
    h: str, layer: str, a: tuple[float, float], b: tuple[float, float], bulge: float
) -> Prim:
    if abs(bulge) < 1e-12:
        return _seg(h, layer, a, b)
    chord = math.dist(a, b)
    theta = 4 * math.atan(bulge)  # signed included angle, CCW when bulge > 0
    r = chord / (2 * math.sin(abs(theta) / 2))
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    d = r * math.cos(abs(theta) / 2)  # midpoint -> centre distance
    nx, ny = -(b[1] - a[1]) / chord, (b[0] - a[0]) / chord  # left normal of a->b
    s = 1 if (bulge > 0) == (abs(theta) < math.pi) else -1
    c = (mx + s * nx * d, my + s * ny * d)
    a0 = math.atan2(a[1] - c[1], a[0] - c[0])
    return _arc(h, layer, c, r, a0, theta)


def _prims(e: Any, h: str, layer: str, s: float) -> list[Prim]:
    kind = e.dxftype()
    d = e.dxf
    xy = lambda v: (float(v[0]) * s, float(v[1]) * s)
    flat = not e.dxf.hasattr("extrusion") or e.dxf.extrusion.isclose((0, 0, 1))
    if kind == "LINE":
        return [_seg(h, layer, xy(d.start), xy(d.end))]
    if kind == "CIRCLE" and flat:
        c, r = xy(d.center), d.radius * s
        pts = _arc_pts(c, r, 0.0, 2 * math.pi)
        return [Prim("CIRCLE", h, layer, 2 * math.pi * r, pts, ("CIRCLE", _pk(c), _k(r)), r, c)]
    if kind == "ARC" and flat:
        a0, a1 = math.radians(d.start_angle), math.radians(d.end_angle)
        sweep = (a1 - a0) % (2 * math.pi) or 2 * math.pi
        return [_arc(h, layer, xy(d.center), d.radius * s, a0, sweep)]
    if kind == "LWPOLYLINE" and flat:
        v = [(x * s, y * s, b) for x, y, b in e.get_points("xyb")]
        pairs = list(zip(v, v[1:] + v[:1], strict=True)) if e.closed else list(pairwise(v))
        return [
            _bulge(h, layer, (a[0], a[1]), (b[0], b[1]), a[2])
            for a, b in pairs
            if (a[0], a[1]) != (b[0], b[1])
        ]
    # splines, ellipses, 3D-extruded arcs/circles, POLYLINE: flatten finely
    pts = [(p.x * s, p.y * s) for p in make_path(e).flattening(FLATTEN_MM / s)]
    if len(pts) < 2:
        return []
    length = sum(math.dist(a, b) for a, b in pairwise(pts))
    ends = sorted((_pk(pts[0]), _pk(pts[-1])))
    return [Prim("CURVE", h, layer, length, pts, ("CURVE", *ends, _k(length)))]


# --- loops ---------------------------------------------------------------------------------


def _area(pts: list[tuple[float, float]]) -> float:
    return 0.5 * sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1], strict=True))


def _inside(p: tuple[float, float], poly: list[tuple[float, float]]) -> bool:
    x, y, hit = p[0], p[1], False
    for a, b in zip(poly, poly[1:] + poly[:1], strict=True):
        if (a[1] > y) != (b[1] > y) and x < a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1]):
            hit = not hit
    return hit


Ring = list[tuple[float, float]]


def _arc_fix(p: Prim, pts: Ring) -> float:
    """Exact minus flattened signed area of an arc traversed as `pts` (closed by its chord)."""
    if p.kind != "ARC":
        return 0.0
    flat = _area(pts)
    theta = p.length / p.radius
    return math.copysign(p.radius**2 / 2 * (theta - math.sin(theta)), flat) - flat


@dataclass
class Loop:
    pts: Ring  # flattened outline, for containment tests and bboxes
    area: float  # exact |area|: circles analytic, arc segments corrected
    prim: Prim | None  # the single primitive the loop is made of, if any


def _loops(prims: list[Prim]) -> tuple[list[Loop], int]:
    """Closed outlines + number of open chains. Circles and self-closed curves pass through;
    other edges are chained at shared endpoints (every node must have degree 2)."""
    rings: list[Loop] = []
    snap = lambda p: (round(p[0] / JOIN_MM), round(p[1] / JOIN_MM))
    edges: list[Prim] = []
    for p in prims:
        if p.kind == "CIRCLE" or (len(p.pts) > 2 and snap(p.pts[0]) == snap(p.pts[-1])):
            exact = math.pi * p.radius**2 if p.kind == "CIRCLE" else abs(_area(p.pts[:-1]))
            rings.append(Loop(p.pts[:-1], exact, p))
        else:
            edges.append(p)
    adj: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, p in enumerate(edges):
        adj[snap(p.pts[0])].append(i)
        adj[snap(p.pts[-1])].append(i)
    used, open_chains = set(), 0
    for start in range(len(edges)):
        if start in used:
            continue
        # walk one component
        comp, stack = set(), [start]
        while stack:
            i = stack.pop()
            if i in comp:
                continue
            comp.add(i)
            for end in (edges[i].pts[0], edges[i].pts[-1]):
                stack.extend(adj[snap(end)])
        used |= comp
        nodes = {snap(edges[i].pts[j]) for i in comp for j in (0, -1)}
        if any(len(adj[n]) != 2 for n in nodes):
            open_chains += 1
            continue
        ring: list[tuple[float, float]] = []
        i, at = min(comp), snap(edges[min(comp)].pts[0])
        seen: set[int] = set()
        fix = 0.0
        while i not in seen:
            seen.add(i)
            pts = edges[i].pts if snap(edges[i].pts[0]) == at else edges[i].pts[::-1]
            ring.extend(pts[:-1])
            fix += _arc_fix(edges[i], pts)
            at = snap(pts[-1])
            i = next((j for j in adj[at] if j not in seen), i)
        if len(seen) != len(comp):  # two separate loops touching? treat as open
            open_chains += 1
            continue
        rings.append(Loop(ring, abs(_area(ring) + fix), None))
    return rings, open_chains


# --- title block ---------------------------------------------------------------------------


def _title_block(doc: Any, rules: Rules, s: float) -> dict[str, Any]:
    found: dict[str, str] = {}
    tag_of = {t.upper(): k for k, tags in rules.title_tags.items() for t in tags}
    texts: list[tuple[str, float, float, float]] = []  # value, x, y, height
    for layout in [
        doc.modelspace(),
        *(doc.paperspace(n) for n in doc.layouts.names_in_taborder() if n != "Model"),
    ]:
        for e in layout:
            if e.dxftype() == "INSERT":
                for a in e.attribs:
                    key = tag_of.get(a.dxf.tag.strip().upper())
                    if key and a.dxf.text.strip() and key not in found:
                        found[key] = a.dxf.text.strip()
            elif e.dxftype() in ("TEXT", "MTEXT"):
                val = e.dxf.text if e.dxftype() == "TEXT" else e.plain_text()
                h = e.dxf.height if e.dxftype() == "TEXT" else e.dxf.char_height
                texts.append((val.strip(), e.dxf.insert.x * s, e.dxf.insert.y * s, h * s))
    # label text followed by its value: "품번: X" in one text, or the nearest text to the right
    for val, x, y, h in texts:
        label, _, rest = val.partition(":")
        key = tag_of.get(label.strip().upper())
        if not key or key in found:
            continue
        if rest.strip():
            found[key] = rest.strip()
            continue
        right = [
            (tx - x, tv)
            for tv, tx, ty, _ in texts
            if tx > x and abs(ty - y) <= 0.6 * max(h, 1e-9) and tv
        ]
        if right:
            found[key] = min(right)[1]
    out: dict[str, Any] = {k: found.get(k) for k in rules.title_tags}
    for k, cast in (("thickness_mm", float), ("qty", int)):
        if out.get(k) is not None:
            try:
                out[k] = cast(str(out[k]).lower().removesuffix("t").removesuffix("ea").strip())
            except ValueError:
                out[k] = None
    out["missing"] = [k for k in rules.title_tags if out.get(k) is None]
    return out


# --- main ----------------------------------------------------------------------------------


def compute_metrics(path: str, rules: Rules | None = None) -> dict[str, Any]:
    rules = rules or Rules()
    doc = ezdxf.readfile(path)
    s = unit_scale(doc)
    warnings: list[str] = []
    cut: list[Prim] = []
    bend: list[Prim] = []
    expanded = 0

    def walk(items: Any, depth: int, top: str | None, parent_layer: str | None) -> None:
        nonlocal expanded
        for e in items:
            expanded += 1
            if expanded > MAX_EXPANDED:
                raise DxfError("DXF_BLOCK_LIMIT", "Too many expanded entities", 422)
            layer = e.dxf.layer if not (parent_layer and e.dxf.layer == "0") else parent_layer
            handle = top or e.dxf.handle
            kind = e.dxftype()
            if kind == "INSERT":
                if depth + 1 > MAX_DEPTH:
                    raise DxfError("DXF_BLOCK_LIMIT", "Block nesting too deep", 422)
                if _match(layer, rules.ignore_layers):
                    continue
                for ins in (
                    e.multi_insert() if e.mcount > 1 else [e]
                ):  # D4/BUG-05: every MINSERT cell
                    walk(ins.virtual_entities(), depth + 1, handle, layer)
                continue
            if kind not in ("LINE", "ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE", "SPLINE", "ELLIPSE"):
                continue  # TEXT/DIMENSION/HATCH...: never priced (FN-14)
            if kind == "POLYLINE" and not e.is_2d_polyline:
                continue
            lt = e.dxf.get("linetype", "BYLAYER")
            if lt.upper() == "BYLAYER" and layer in doc.layers:
                lt = doc.layers.get(layer).dxf.get("linetype", "CONTINUOUS")
            if _match(layer, rules.ignore_layers) or _match(lt, rules.ignore_linetypes):
                continue
            target = (
                bend
                if _match(layer, rules.bend_layers) or _match(lt, rules.bend_linetypes)
                else cut
            )
            target.extend(_prims(e, handle, layer, s))

    walk(doc.modelspace(), 0, None, None)

    def dedupe(prims: list[Prim]) -> list[Prim]:
        seen: dict[tuple[Any, ...], Prim] = {}
        for p in prims:
            seen.setdefault(p.key, p)
        if len(seen) < len(prims):
            warnings.append(f"METRIC_DUPLICATE_GEOMETRY:{len(prims) - len(seen)}")
        return list(seen.values())

    cut, bend = dedupe(cut), dedupe(bend)
    rings, open_chains = _loops(cut)
    if open_chains:
        warnings.append(f"METRIC_OPEN_CONTOUR:{open_chains}")
    best = max(rings, key=lambda r: r.area, default=None)
    outer, outer_prim = (best.pts, best.prim) if best else (None, None)
    inner = [r for r in rings if best and r is not best and _inside(r.pts[0], best.pts)]
    if outer is None:
        warnings.append("METRIC_NO_OUTER_CONTOUR")
    elif len(rings) - 1 > len(inner):
        warnings.append(f"METRIC_MULTIPLE_PARTS:{len(rings) - len(inner)}")

    holes: Counter[float] = Counter()
    punched: list[Prim] = []
    items: list[dict[str, Any]] = []
    for p in cut:
        row: dict[str, Any] = {"handle": p.handle, "layer": p.layer}
        if (
            p.kind == "CIRCLE"
            and p is not outer_prim
            and outer is not None
            and _inside(p.center, outer)
        ):
            dia = round(2 * p.radius, 3)
            holes[dia] += 1
            punch = rules.punch_max_dia_mm is not None and dia <= rules.punch_max_dia_mm
            if punch:
                punched.append(p)
            row |= {
                "role": "PUNCH" if punch else "HOLE",
                "dia_mm": dia,
                "length_mm": round(p.length, 6),
            }
        else:
            row |= {"role": "CUT", "length_mm": round(p.length, 6)}
        items.append(row)
    items += [
        {"handle": p.handle, "layer": p.layer, "role": "BEND", "length_mm": round(p.length, 6)}
        for p in bend
    ]
    if len(items) > MAX_ITEMS:
        warnings.append(f"METRIC_ITEMS_TRUNCATED:{len(items) - MAX_ITEMS}")
        items = items[:MAX_ITEMS]

    net = bbox = None
    if best is not None and outer is not None:
        net = best.area - sum(r.area for r in inner)
        xs, ys = [p[0] for p in outer], [p[1] for p in outer]
        lo, hi = [min(xs), min(ys)], [max(xs), max(ys)]
        bbox = {"min": lo, "max": hi, "size": [hi[0] - lo[0], hi[1] - lo[1]]}
    title = _title_block(doc, rules, s)
    return {
        "cutting_length_mm": round(sum(p.length for p in cut) - sum(p.length for p in punched), 6),
        "hole_count": sum(holes.values()),
        "holes_by_dia": {f"{d:g}": n for d, n in sorted(holes.items())},
        "punch_hole_count": len(punched),
        "bend_count": len(bend),
        "bend_length_mm": round(sum(p.length for p in bend), 6),
        "net_area_mm2": None if net is None else round(net, 3),
        "bbox": bbox,
        "title_block": title,
        "status": "INPUT_REQUIRED"
        if {"material", "thickness_mm"} & set(title["missing"])
        else "OK",
        "items": items,
        "warnings": warnings,
    }
