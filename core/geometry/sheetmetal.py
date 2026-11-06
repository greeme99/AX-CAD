"""Track 9 Q3: sheet metal — base plate, edge flanges, flat pattern (plan.md §15.4).

Everything is built in sketch coordinates (x, y on the sketch plane, z along its normal) and then
placed on the plane. The base is the sketch's outline (straight lines; holes may be circles or
straight-line loops) extruded by the thickness T. A flange sits on one straight edge of that
outline: the edge's end face is turned about an axis R (inside radius) away from the plate — up
(+z) or down — by the bend angle, then continued straight for L mm (the length after the bend).

The flat pattern is exact for this model: each bend unrolls to its bend allowance
BA = angle(rad) * (R + K*T), so a flange adds a strip BA + L wide along its edge. With K = 0.5
(the neutral fibre in the middle) the folded volume equals the flat area times T.
"""

import io
import math
from typing import Any

from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakePolygon,
    BRepBuilderAPI_Transform,
)
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism, BRepPrimAPI_MakeRevol
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopoDS import TopoDS_Shape

from core.geometry.errors import GeomError
from core.geometry.features import place
from core.geometry.metrics import count, measure
from core.geometry.serialize import to_brep
from core.geometry.sketch import profile_face

Pt = tuple[float, float]
TOL = 1e-6
MAX_EDGES = 500
MAX_FLANGES = 64


def _bad(msg: str, **details: Any) -> GeomError:
    return GeomError("SHEET_INVALID", msg, details=details or None)


# --- 2D outline -------------------------------------------------------------------------------


def _key(p: list[float] | Pt) -> tuple[int, int]:
    return (round(p[0] / 1e-5), round(p[1] / 1e-5))


def _loops(lines: list[dict[str, Any]]) -> list[list[Pt]]:
    """Closed loops from line segments (each endpoint shared by exactly two lines)."""
    ends: dict[tuple[int, int], list[int]] = {}
    for i, ln in enumerate(lines):
        for p in (ln["start"], ln["end"]):
            ends.setdefault(_key(p), []).append(i)
    if any(len(v) != 2 for v in ends.values()):
        raise GeomError("GEOM_OPEN_WIRE", "The sheet outline must be closed straight lines")
    used: set[int] = set()
    loops = []
    for i in range(len(lines)):
        if i in used:
            continue
        loop: list[Pt] = []
        cur, at = i, tuple(lines[i]["start"])
        while cur not in used:
            used.add(cur)
            ln = lines[cur]
            nxt = ln["end"] if _key(ln["start"]) == _key(at) else ln["start"]
            loop.append((float(at[0]), float(at[1])))
            at = tuple(nxt)
            cands = [j for j in ends[_key(at)] if j != cur]
            cur = cands[0]
        loops.append(loop)
    return loops


def area(poly: list[Pt]) -> float:
    return 0.5 * sum(
        a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:] + poly[:1], strict=True)
    )


def _perimeter(poly: list[Pt]) -> float:
    return sum(math.dist(a, b) for a, b in zip(poly, poly[1:] + poly[:1], strict=True))


def _cross_seg(p: Pt, q: Pt, r: Pt, s: Pt) -> bool:
    def orient(a: Pt, b: Pt, c: Pt) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    d1, d2, d3, d4 = orient(r, s, p), orient(r, s, q), orient(p, q, r), orient(p, q, s)
    return d1 * d2 < -TOL and d3 * d4 < -TOL


def _simple(poly: list[Pt]) -> bool:
    n = len(poly)
    segs = [(poly[i], poly[(i + 1) % n]) for i in range(n)]
    boxes = [(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])) for a, b in segs]
    order = sorted(range(n), key=lambda i: boxes[i][0])  # sweep on x: only overlapping boxes test
    for k, i in enumerate(order):
        for j in order[k + 1 :]:
            if boxes[j][0] > boxes[i][2]:
                break
            if (
                j in ((i + 1) % n, (i - 1) % n)
                or boxes[j][1] > boxes[i][3]
                or boxes[j][3] < boxes[i][1]
            ):
                continue
            if _cross_seg(*segs[i], *segs[j]):
                return False
    return True


def _seg_dist(p: Pt, a: Pt, b: Pt) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)))
    return math.dist(p, (a[0] + t * dx, a[1] + t * dy))


def _clearance(p: Pt, poly: list[Pt]) -> float:
    return min(_seg_dist(p, a, b) for a, b in zip(poly, poly[1:] + poly[:1], strict=True))


def _inside(p: Pt, poly: list[Pt]) -> bool:
    x, y, inside = p[0], p[1], False
    for a, b in zip(poly, poly[1:] + poly[:1], strict=True):
        if (a[1] > y) != (b[1] > y) and x < a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1]):
            inside = not inside
    return inside


def outline(geoms: list[dict[str, Any]]) -> dict[str, Any]:
    """The sketch as a sheet: outer polygon (counter-clockwise), hole polygons and hole circles."""
    lines = [g for g in geoms if g["type"] == "LINE"]
    circles = [g for g in geoms if g["type"] == "CIRCLE"]
    if any(g["type"] not in ("LINE", "CIRCLE") for g in geoms):
        raise _bad("A sheet sketch uses straight lines for outlines and circles for holes")
    if not lines:
        raise GeomError("GEOM_OPEN_WIRE", "The sheet sketch has no outline")
    if len(lines) > MAX_EDGES:
        raise _bad(f"At most {MAX_EDGES} lines")
    if any(math.dist(g["start"], g["end"]) <= TOL for g in lines):
        raise _bad("The sheet sketch has a zero-length line")
    loops = sorted(_loops(lines), key=lambda lp: -abs(area(lp)))
    # counter-clockwise, still starting at the sketch's first point (edge numbers stay put)
    outer = loops[0] if area(loops[0]) > 0 else [loops[0][0], *loops[0][:0:-1]]
    if not _simple(outer):
        raise GeomError("GEOM_SELF_INTERSECTION", "The sheet outline crosses itself")
    holes = []
    for lp in loops[1:]:
        if not all(_inside(p, outer) for p in lp):
            raise _bad("Only one outline: other loops must be holes inside it")
        holes.append(lp if area(lp) < 0 else lp[::-1])  # clockwise
    loops_in = [outer, *holes]
    for i, c in enumerate(circles):
        ctr, rad = (float(c["center"][0]), float(c["center"][1])), float(c["radius"])
        if not _inside(ctr, outer) or any(_inside(ctr, h) for h in holes):
            raise _bad("Circle holes must lie inside the sheet")
        if any(_clearance(ctr, lp) <= rad + TOL for lp in loops_in):
            raise _bad("A circle hole cuts the outline or another hole")
        for d in circles[:i]:
            if math.dist(ctr, d["center"]) <= rad + float(d["radius"]) + TOL:
                raise _bad("Circle holes overlap")
    return {
        "outer": outer,
        "holes": holes,
        "circles": [{"center": list(c["center"]), "radius": float(c["radius"])} for c in circles],
    }


def edges(shape2d: dict[str, Any]) -> list[dict[str, Any]]:
    """The outline's edges a flange can go on: index, ends, length."""
    o = shape2d["outer"]
    return [
        {"index": i, "start": list(a), "end": list(b), "length": math.dist(a, b)}
        for i, (a, b) in enumerate(zip(o, o[1:] + o[:1], strict=True))
    ]


# --- spec checks ------------------------------------------------------------------------------


def check(spec: dict[str, Any]) -> None:
    t, r, k = spec["thickness"], spec["bend_radius"], spec["k_factor"]
    if not (0.05 <= t <= 50 and 0.01 <= r <= 1000 and 0 <= k <= 1):
        raise _bad("Thickness 0.05-50 mm, inside radius 0.01-1000 mm, K-factor 0-1")
    es = edges(spec["outline"])
    if len(spec["flanges"]) > MAX_FLANGES:
        raise _bad(f"At most {MAX_FLANGES} flanges")
    seen: set[int] = set()
    for i, f in enumerate(spec["flanges"]):
        e = f["edge"]
        if not 0 <= e < len(es):
            raise _bad("No such outline edge", flange=i)
        if e in seen:
            raise _bad("That edge already has a flange", flange=i)
        seen.add(e)
        if not 1 <= f["angle"] <= 179:
            raise _bad("Bend angle must be between 1 and 179 degrees", flange=i)
        if f["length"] < 0 or f["inset_start"] < 0 or f["inset_end"] < 0:
            raise _bad("Lengths and insets cannot be negative", flange=i)
        if es[e]["length"] - f["inset_start"] - f["inset_end"] <= TOL:
            raise _bad("The insets leave no flange width", flange=i)


def _frame(spec: dict[str, Any], f: dict[str, Any]) -> dict[str, Any]:
    e = edges(spec["outline"])[f["edge"]]
    a, b = e["start"], e["end"]
    ux, uy = (b[0] - a[0]) / e["length"], (b[1] - a[1]) / e["length"]
    return {
        "a": a,
        "dir": (ux, uy),
        "out": (uy, -ux),  # outline is counter-clockwise: the outside is on the right
        "w0": f["inset_start"],
        "w1": e["length"] - f["inset_end"],
    }


# --- 3D ---------------------------------------------------------------------------------------


def _quad(pts: list[tuple[float, float, float]]) -> TopoDS_Shape:
    poly = BRepBuilderAPI_MakePolygon()
    for p in pts:
        poly.Add(gp_Pnt(*p))
    poly.Close()
    return BRepBuilderAPI_MakeFace(poly.Wire()).Face()


def _flange(spec: dict[str, Any], f: dict[str, Any]) -> list[TopoDS_Shape]:
    t, r = spec["thickness"], spec["bend_radius"]
    fr = _frame(spec, f)
    (ax, ay), (dx, dy), (ox, oy) = fr["a"], fr["dir"], fr["out"]
    p0 = (ax + dx * fr["w0"], ay + dy * fr["w0"])
    p1 = (ax + dx * fr["w1"], ay + dy * fr["w1"])
    end = _quad([(p0[0], p0[1], 0.0), (p1[0], p1[1], 0.0), (p1[0], p1[1], t), (p0[0], p0[1], t)])
    up = f["up"]
    axis_z = t + r if up else -r
    # turning the end face from facing outwards towards +z (up) or -z (down)
    d = (-dx, -dy, 0.0) if up else (dx, dy, 0.0)
    axis = gp_Ax1(gp_Pnt(p0[0], p0[1], axis_z), gp_Dir(*d))
    theta = math.radians(f["angle"])
    bend = BRepPrimAPI_MakeRevol(end, axis, theta).Shape()
    parts = [bend]
    if f["length"] > TOL:
        rot = gp_Trsf()
        rot.SetRotation(axis, theta)
        turned = BRepBuilderAPI_Transform(end, rot, True).Shape()
        # straight part leaves along the outward direction turned by the bend angle
        c, s = math.cos(theta), math.sin(theta)
        zsign = 1.0 if up else -1.0
        tv = (ox * c, oy * c, zsign * s)
        vec = gp_Vec(tv[0] * f["length"], tv[1] * f["length"], tv[2] * f["length"])
        parts.append(BRepPrimAPI_MakePrism(turned, vec).Shape())
    return parts


def build(spec: dict[str, Any]) -> TopoDS_Shape:
    """The folded part, placed on the sketch plane."""
    check(spec)
    o = spec["outline"]
    geoms = [
        {"type": "LINE", "start": list(a), "end": list(b)}
        for lp in [o["outer"], *o["holes"]]
        for a, b in zip(lp, lp[1:] + lp[:1], strict=True)
    ] + [{"type": "CIRCLE", **c} for c in o["circles"]]
    face = profile_face(geoms, holes=True)
    shape = BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, spec["thickness"])).Shape()
    for f in spec["flanges"]:
        for piece in _flange(spec, f):
            fuse = BRepAlgoAPI_Fuse(shape, piece)
            if not fuse.IsDone():
                raise GeomError("GEOM_BOOLEAN_FAILED", "Flange could not be joined to the sheet")
            shape = fuse.Shape()
    unify = ShapeUpgrade_UnifySameDomain(shape, True, True, True)
    unify.Build()
    shape = unify.Shape()
    if not BRepCheck_Analyzer(shape).IsValid() or count(shape, TopAbs_SOLID) != 1:
        raise GeomError("GEOM_BOOLEAN_FAILED", "Flanges overlap or leave the sheet in pieces")
    return place(shape, spec.get("plane"))


# --- flat pattern -----------------------------------------------------------------------------


def bend_allowance(spec: dict[str, Any], f: dict[str, Any]) -> float:
    return math.radians(f["angle"]) * (spec["bend_radius"] + spec["k_factor"] * spec["thickness"])


def flat(spec: dict[str, Any]) -> dict[str, Any]:
    """The flat blank: outline with the flange strips spliced in, holes, bend lines, and the
    numbers a quote reads (cut length, pierces, bends, blank area / box)."""
    check(spec)
    o = spec["outline"]
    poly = list(o["outer"])
    bends = []
    # splice from the last edge back so earlier indices stay valid
    for f in sorted(spec["flanges"], key=lambda f: -f["edge"]):
        fr = _frame(spec, f)
        (ax, ay), (dx, dy), (ox, oy) = fr["a"], fr["dir"], fr["out"]
        ba = bend_allowance(spec, f)
        depth = ba + f["length"]

        def at(
            w: float,
            out: float,
            ax: float = ax,
            ay: float = ay,
            dx: float = dx,
            dy: float = dy,
            ox: float = ox,
            oy: float = oy,
        ) -> Pt:
            return (ax + dx * w + ox * out, ay + dy * w + oy * out)

        # the strip: up from the edge at w0, across at depth, back down at w1 (a strip flush
        # with an edge end starts / ends at that corner, which poly already has)
        edge_len = math.dist(o["outer"][f["edge"]], o["outer"][(f["edge"] + 1) % len(o["outer"])])
        ins = [at(fr["w0"], 0)] if fr["w0"] > TOL else []
        ins += [at(fr["w0"], depth), at(fr["w1"], depth)]
        if fr["w1"] < edge_len - TOL:
            ins.append(at(fr["w1"], 0))
        poly[f["edge"] + 1 : f["edge"] + 1] = ins
        bends.append(
            {
                "edge": f["edge"],
                "start": list(at(fr["w0"], ba / 2)),
                "end": list(at(fr["w1"], ba / 2)),
                "length": fr["w1"] - fr["w0"],
                "angle": f["angle"],
                "up": f["up"],
                "radius": spec["bend_radius"],
                "allowance": ba,
            }
        )
    poly = _dedupe(poly)
    if not _simple(poly):
        raise _bad("Flanges overlap in the flat pattern; add insets or shorten them")
    hole_area = sum(-area(h) for h in o["holes"]) + sum(
        math.pi * c["radius"] ** 2 for c in o["circles"]
    )
    hole_cut = sum(_perimeter(h) for h in o["holes"]) + sum(
        2 * math.pi * c["radius"] for c in o["circles"]
    )
    if area(poly) - hole_area <= 0:
        raise _bad("The flat blank has no area")
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return {
        "outline": [list(p) for p in poly],
        "holes": [[list(p) for p in h] for h in o["holes"]],
        "circles": o["circles"],
        "bends": sorted(bends, key=lambda b: b["edge"]),
        "metrics": {
            "thickness_mm": spec["thickness"],
            "blank_area_mm2": area(poly) - hole_area,
            "sheet_cut_length_mm": _perimeter(poly) + hole_cut,
            "pierce_count": len(o["holes"]) + len(o["circles"]),
            "bend_count": len(bends),
            "bend_length_mm": sum(b["length"] for b in bends),
            "flat_bbox": {
                "min": [min(xs), min(ys)],
                "max": [max(xs), max(ys)],
                "size": [max(xs) - min(xs), max(ys) - min(ys)],
            },
        },
    }


def _dedupe(poly: list[Pt]) -> list[Pt]:
    out: list[Pt] = []
    for p in poly:
        if not out or math.dist(out[-1], p) > TOL:
            out.append(p)
    if len(out) > 1 and math.dist(out[0], out[-1]) <= TOL:
        out.pop()
    # drop collinear middle points (a strip flush with an edge end continues the edge)
    res: list[Pt] = []
    n = len(out)
    for i, p in enumerate(out):
        a, b = out[i - 1], out[(i + 1) % n]
        if abs((p[0] - a[0]) * (b[1] - p[1]) - (p[1] - a[1]) * (b[0] - p[0])) > TOL * max(
            1.0, math.dist(a, b)
        ):
            res.append(p)
    return res


def flat_dxf(spec: dict[str, Any], name: str = "FLAT") -> bytes:
    """The blank as DXF: CUT layer (outline + holes), BEND layer (DASHED bend centre lines with
    'UP 90° R2' notes). Units mm."""
    import ezdxf

    fp = flat(spec)
    doc = ezdxf.new(setup=True)
    doc.units = 4
    doc.layers.add("CUT", color=7)
    doc.layers.add("BEND", color=3, linetype="DASHED")
    msp = doc.modelspace()
    msp.add_lwpolyline([tuple(p) for p in fp["outline"]], close=True, dxfattribs={"layer": "CUT"})
    for h in fp["holes"]:
        msp.add_lwpolyline([tuple(p) for p in h], close=True, dxfattribs={"layer": "CUT"})
    for c in fp["circles"]:
        msp.add_circle(tuple(c["center"]), c["radius"], dxfattribs={"layer": "CUT"})
    for b in fp["bends"]:
        msp.add_line(tuple(b["start"]), tuple(b["end"]), dxfattribs={"layer": "BEND"})
        mid = ((b["start"][0] + b["end"][0]) / 2, (b["start"][1] + b["end"][1]) / 2)
        label = f"{'UP' if b['up'] else 'DN'} {b['angle']:g}° R{b['radius']:g}"
        msp.add_text(label, height=2.5, dxfattribs={"layer": "BEND"}).set_placement(mid)
    msp.add_text(
        f"{name} t{spec['thickness']:g} K{spec['k_factor']:g}",
        height=3.5,
        dxfattribs={"layer": "BEND"},
    ).set_placement(
        (fp["metrics"]["flat_bbox"]["min"][0], fp["metrics"]["flat_bbox"]["min"][1] - 8)
    )
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode()


def sheet_job(spec: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    """Worker entry: the folded body and its metrics, with the flat-pattern numbers under 'sheet'."""
    shape = build(spec)
    m = measure(shape)
    fp = flat(spec)
    m["sheet"] = {
        **fp["metrics"],
        "flanges": len(spec["flanges"]),
        "k_factor": spec["k_factor"],
        "bend_radius": spec["bend_radius"],
    }
    return to_brep(shape), m
