"""Track 6 construction/MEP quantity take-off (QTO, 수량산출) from a 2D DXF floor plan.

Layers are mapped to roles (master data QTO_LAYER rows, else DEFAULT_LAYERS):
  ROOM       closed outlines -> room_area_m2, room_perimeter_m, room_count (named by a text inside)
  WALL ...   centre lines    -> {role}_length_m        (walls, partitions, ducts, pipes, cables)
  DOOR ...   block inserts   -> {role}_count           (doors, windows, fixtures)
With a base revision (the existing building) every item is also split by geometry:
  in both -> keep_*, only in the current drawing -> new_* (신설), only in the base -> demo_* (철거).
The unprefixed measure is the current drawing (new + keep). Every item keeps its handle for the
quote trace (CLAUDE.md: Source Entity -> Rule -> Price -> Line)."""

import json
import math
from collections import defaultdict
from typing import Any

import ezdxf

from core.dxf.reader import MAX_EXPANDED, DxfError, unit_scale
from core.quote_engine.metrics2d import (
    MAX_ITEMS,
    MAX_LAYER_CHARS,
    MAX_POINTS,
    MAX_TITLE_CHARS,
    MAX_TITLE_TEXTS,
    Prim,
    _area,
    _inside,
    _k,
    _loops,
    _match,
    _pk,
    _prims,
)

LENGTH_ROLES = ("WALL", "PARTITION", "DUCT", "PIPE", "CABLE")
COUNT_ROLES = ("DOOR", "WINDOW", "FIXTURE")
QTO_ROLES = ("ROOM", *LENGTH_ROLES, *COUNT_ROLES, "IGNORE")
# common Korean/AIA-style layer names until the master data maps the shop's own
DEFAULT_LAYERS: dict[str, list[str]] = {
    "ROOM": ["ROOM*", "A-AREA*", "AREA*", "실*"],
    "WALL": ["WALL*", "A-WALL*", "벽*"],
    "PARTITION": ["PART*", "A-PART*", "칸막이*"],
    "DOOR": ["DOOR*", "A-DOOR*", "문*"],
    "WINDOW": ["WIN*", "A-GLAZ*", "창*"],
    "FIXTURE": ["FIX*", "E-LITE*", "등기구*", "기구*"],
    "DUCT": ["DUCT*", "M-DUCT*", "덕트*"],
    "PIPE": ["PIPE*", "P-*", "배관*"],
    "CABLE": ["CABLE*", "E-POWR*", "전선*"],
    "IGNORE": [],
}
MEASURES = (
    "room_area_m2",
    "room_perimeter_m",
    "room_count",
    *(f"{r.lower()}_length_m" for r in LENGTH_ROLES),
    *(f"{r.lower()}_count" for r in COUNT_ROLES),
)
STATUS = {"NEW": "new", "DEMO": "demo", "KEEP": "keep"}
# names a work item's qty_formula may read (height_m: the ceiling height given with the quote)
QTO_NAMES = (*MEASURES, *(f"{p}_{m}" for p in STATUS.values() for m in MEASURES), "height_m")
MAX_ROOMS = 2_000
NAME_BUDGET = 5_000_000  # rooms x texts-in-box x outline vertices: unnamed beyond it
MAX_SOURCES = 500  # handles kept per room (as per quote trace)


def layers_from_mapping(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    """QTO_LAYER rows (target = role, pattern = comma list); a role without rows keeps its
    default, so a master that maps only walls still finds doors on DOOR*."""
    got: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        if row["rule_type"] == "QTO_LAYER":
            got[row["target"]] += [p.strip() for p in str(row["pattern"]).split(",") if p.strip()]
    return {role: got.get(role) or pats for role, pats in DEFAULT_LAYERS.items()}


def _role(layer: str, layers: dict[str, list[str]]) -> str | None:
    if _match(layer, layers["IGNORE"]):
        return None
    return next((r for r in QTO_ROLES[:-1] if _match(layer, layers[r])), None)


def _read(path: str, layers: dict[str, list[str]]) -> dict[str, Any]:
    """One drawing -> role-tagged primitives, inserts and texts (mm, WCS)."""
    doc = ezdxf.readfile(path)
    s = unit_scale(doc)
    prims: dict[str, list[Prim]] = defaultdict(list)
    inserts: dict[str, list[tuple[str, str, tuple[Any, ...]]]] = defaultdict(list)
    texts: list[tuple[str, float, float]] = []
    expanded = points = 0
    msp: Any = doc.modelspace()  # entity types vary; attributes are checked by dxftype()
    for e in msp:
        expanded += 1
        if expanded > MAX_EXPANDED:
            raise DxfError("DXF_BLOCK_LIMIT", "Too many entities", 422)
        layer = e.dxf.layer
        if len(layer) > MAX_LAYER_CHARS:
            raise DxfError("DXF_INVALID_FILE", "Layer name too long", 422)
        kind = e.dxftype()
        if kind in ("TEXT", "MTEXT"):
            if len(texts) < MAX_TITLE_TEXTS:
                val = e.dxf.text if kind == "TEXT" else e.plain_text()
                p = e.dxf.insert
                # NUL is refused by jsonb (quote metrics): drawing text is untrusted
                texts.append((val.replace("\x00", "").strip()[:MAX_TITLE_CHARS], p.x * s, p.y * s))
            continue
        role = _role(layer, layers)
        if role is None:
            continue
        if kind == "INSERT":
            if role in COUNT_ROLES:
                p = e.dxf.insert
                key = (e.dxf.name, _k(p.x * s), _k(p.y * s), _k(e.dxf.get("rotation", 0)))
                # a MINSERT grid is that many doors/fixtures
                expanded += max(1, e.mcount) - 1
                if expanded > MAX_EXPANDED:
                    raise DxfError("DXF_BLOCK_LIMIT", "Too many entities", 422)
                for i in range(max(1, e.mcount)):
                    inserts[role].append((e.dxf.handle, layer, (*key, i)))
            elif role != "ROOM":  # e.g. a wall drawn as a block: measure its lines
                for v in e.virtual_entities():
                    expanded += 1
                    if expanded > MAX_EXPANDED:
                        raise DxfError("DXF_BLOCK_LIMIT", "Too many entities", 422)
                    if v.dxftype() in ("LINE", "ARC", "LWPOLYLINE"):
                        new = _prims(v, e.dxf.handle, layer, s)
                        prims[role] += new
                        points += sum(len(q.pts) for q in new)
                        if points > MAX_POINTS:
                            raise DxfError("DXF_BLOCK_LIMIT", "Drawing too large to measure", 422)
            continue
        if kind not in ("LINE", "ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE", "SPLINE", "ELLIPSE"):
            continue
        if kind == "POLYLINE" and not e.is_2d_polyline:
            continue
        if role in COUNT_ROLES:  # a door drawn as loose lines is not countable; skip it
            continue
        new = _prims(e, e.dxf.handle, layer, s)
        prims[role] += new
        points += sum(len(q.pts) for q in new)
        if points > MAX_POINTS:
            raise DxfError("DXF_BLOCK_LIMIT", "Drawing too large to measure", 422)
    warnings = ["METRIC_UNITS_ASSUMED_MM"] if doc.units == 0 else []
    return {"prims": prims, "inserts": inserts, "texts": texts, "warnings": warnings}


def _rooms(prims: list[Prim], texts: list[tuple[str, float, float]]) -> tuple[list[Any], int, bool]:
    """-> (rooms, open outlines, names skipped). A closed polyline is one room on its own (neighbouring rooms
    share edges, which loose-line chaining would read as branches); loose lines are chained."""
    by_handle: dict[str, list[Prim]] = defaultdict(list)
    for p in {p.key + (p.handle,): p for p in prims}.values():
        by_handle[p.handle].append(p)
    loose = [g[0] for g in by_handle.values() if len(g) == 1]
    at: dict[tuple[int, int], set[str]] = defaultdict(set)  # vertex -> loose edges from it
    for p in loose:
        at[_pk(p.pts[0])].add(p.handle)
        at[_pk(p.pts[-1])].add(p.handle)
    loops, open_n = _loops(list({p.key: p for p in loose}.values()))
    found: list[tuple[Any, str | None]] = [(lp, None) for lp in loops]
    for h, group in by_handle.items():
        if len(group) > 1:
            lps, n = _loops(group)
            found += [(lp, h) for lp in lps]
            open_n += n
    out = []
    budget = NAME_BUDGET  # point-in-polygon vertex visits for room names (security review)
    for lp, handle in found[:MAX_ROOMS]:
        xs, ys = [p[0] for p in lp.pts], [p[1] for p in lp.pts]
        box = (min(xs), min(ys), max(xs), max(ys))
        cand = [
            (t, x, y) for t, x, y in texts if t and box[0] <= x <= box[2] and box[1] <= y <= box[3]
        ]
        budget -= len(cand) * len(lp.pts)
        names = [t for t, x, y in cand if _inside((x, y), lp.pts)] if budget >= 0 else []
        verts = [_pk(p) for p in lp.pts]
        if lp.prim:  # a circle or a single closed curve
            perimeter, handles = lp.prim.length, [lp.prim.handle]
        else:
            ring = zip(lp.pts, lp.pts[1:] + lp.pts[:1], strict=True)
            perimeter = sum(math.dist(a, b) for a, b in ring)
            # ponytail: chord length for arc edges (outline points are 1 µm-flattened)
            handles = [handle] if handle else sorted(set().union(*(at[v] for v in verts)))
        out.append(
            {
                # geometry key: the outline's vertices, direction- and start-independent
                "key": ("ROOM", tuple(sorted(verts)), _k(abs(_area(lp.pts)))),
                "handles": handles[:MAX_SOURCES],
                "name": min(names, key=len) if names else None,
                "area_m2": lp.area / 1e6,
                "perimeter_m": perimeter / 1000,
            }
        )
    return out, open_n, budget < 0


def _items(d: dict[str, Any]) -> list[dict[str, Any]]:
    """Every measured thing of one drawing: role, geometry key, handle, quantity."""
    rooms, open_n, unnamed = _rooms(d["prims"].get("ROOM", []), d["texts"])
    if open_n:
        d["warnings"].append(f"QTO_OPEN_ROOM:{open_n}")
    if unnamed:
        d["warnings"].append("QTO_ROOM_NAMES_SKIPPED")
    items: list[dict[str, Any]] = [{"role": "ROOM", **r} for r in rooms]
    for role in LENGTH_ROLES:
        seen: dict[tuple[Any, ...], Prim] = {}
        for p in d["prims"].get(role, []):
            seen.setdefault(p.key, p)  # duplicate lines count once, like FN-14
        items += [
            {"role": role, "key": p.key, "handles": [p.handle], "length_m": p.length / 1000}
            for p in seen.values()
        ]
    for role in COUNT_ROLES:
        uniq = {key: h for h, _, key in d["inserts"].get(role, [])}
        items += [{"role": role, "key": k, "handles": [h]} for k, h in uniq.items()]
    return items


def _measures(items: list[dict[str, Any]]) -> dict[str, float]:
    m = dict.fromkeys(MEASURES, 0.0)
    for it in items:
        role = it["role"]
        if role == "ROOM":
            m["room_area_m2"] += it["area_m2"]
            m["room_perimeter_m"] += it["perimeter_m"]
            m["room_count"] += 1
        elif role in LENGTH_ROLES:
            m[f"{role.lower()}_length_m"] += it["length_m"]
        else:
            m[f"{role.lower()}_count"] += 1
    return m


def compute_qto(
    path: str, layers: dict[str, list[str]] | None = None, base_path: str | None = None
) -> dict[str, Any]:
    layers = layers or DEFAULT_LAYERS
    cur = _read(path, layers)
    items = _items(cur)
    warnings = cur["warnings"]  # the base drawing's own warnings concern the old building
    if base_path:
        base = _read(base_path, layers)
        base_items = _items(base)
        before = {it["key"] for it in base_items}
        after = {it["key"] for it in items}
        for it in items:
            it["status"] = "KEEP" if it["key"] in before else "NEW"
        demo = [{**it, "status": "DEMO"} for it in base_items if it["key"] not in after]
    else:
        for it in items:
            it["status"] = "NEW"  # no existing building given: everything is new work
        demo = []
    measures = _measures(items)
    for st, prefix in STATUS.items():
        group = demo if st == "DEMO" else [it for it in items if it["status"] == st]
        measures |= {f"{prefix}_{k}": v for k, v in _measures(group).items()}
    if not any(measures[k] for k in MEASURES):
        warnings.append("QTO_NOTHING_MAPPED")
    every = items + demo
    if len(every) > MAX_ITEMS:
        warnings.append(f"METRIC_ITEMS_TRUNCATED:{len(every) - MAX_ITEMS}")
    rooms = [
        {k: it[k] for k in ("handles", "name", "area_m2", "perimeter_m", "status")}
        for it in every
        if it["role"] == "ROOM"
    ]
    for v in measures.values():
        if not math.isfinite(v):
            raise DxfError("METRIC_FAILED", "Drawing coordinates out of range", 422)
    return {
        "measures": {k: round(v, 6) for k, v in measures.items()},
        "rooms": rooms,
        "items": [
            {k: v for k, v in it.items() if k not in ("key", "area_m2", "perimeter_m", "name")}
            for it in every[:MAX_ITEMS]
        ],
        "has_base": base_path is not None,
        "warnings": warnings,
    }


def qto_job(path: str, layers: dict[str, list[str]], base_path: str | None) -> str:
    """Worker entry: the parent only json.loads a string."""
    return json.dumps(compute_qto(path, layers, base_path), allow_nan=False)
