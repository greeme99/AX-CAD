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
from OCP.BRepGProp import BRepGProp
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism, BRepPrimAPI_MakeRevol
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCP.GProp import GProp_GProps
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopoDS import TopoDS_Shape

from core.geometry.errors import GeomError
from core.geometry.features import place
from core.geometry.metrics import count, measure
from core.geometry.serialize import to_brep
from core.geometry.sketch import profile_face
from core.geometry.unfold import unfold

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


MAX_STEPS = 8  # bends in one flange (a hem is 1, a jog 2, a contour flange up to this many)
MAX_ANGLE = 270.0  # a rolled hem; past 180 the volume check catches a flange that hits the sheet
STYLES = ("FLANGE", "HEM", "JOG", "CONTOUR")
V3 = tuple[float, float, float]


def steps_of(f: dict[str, Any]) -> list[dict[str, Any]]:
    """A flange as bends in a row (plan §23 S2): its own bend + straight part, then each further
    step. A step: angle, up (towards the sheet's top side), length after the bend, inside radius
    (None = the sheet's)."""
    first = {k: f.get(k) for k in ("angle", "up", "length", "radius")}
    return [first, *(f.get("steps") or [])]


def simple(f: dict[str, Any]) -> bool:
    """A Track 9 flange: one bend on a base edge with the sheet's radius (the analytic layout)."""
    return f.get("parent") is None and not f.get("steps") and f.get("radius") is None


def check(spec: dict[str, Any]) -> None:
    t, r, k = spec["thickness"], spec["bend_radius"], spec["k_factor"]
    if not (0.05 <= t <= 50 and 0.01 <= r <= 1000 and 0 <= k <= 1):
        raise _bad("Thickness 0.05-50 mm, inside radius 0.01-1000 mm, K-factor 0-1")
    es = edges(spec["outline"])
    if len(spec["flanges"]) > MAX_FLANGES:
        raise _bad(f"At most {MAX_FLANGES} flanges")
    seen: set[Any] = set()
    for i, f in enumerate(spec["flanges"]):
        par = f.get("parent")
        steps = steps_of(f)
        if len(steps) > MAX_STEPS:
            raise _bad(f"At most {MAX_STEPS} bends in one flange", flange=i)
        if f.get("style", "FLANGE") not in STYLES:
            raise _bad("Unknown flange style", flange=i)
        if par is None:
            e = f.get("edge")
            if e is None or not 0 <= e < len(es):
                raise _bad("No such outline edge", flange=i)
            where: Any = e
            width = es[e]["length"]
        else:
            j = par["flange"]
            if not 0 <= j < i:
                raise _bad("A flange goes on the side of an earlier flange", flange=i)
            psteps = steps_of(spec["flanges"][j])
            if not 0 <= par["step"] < len(psteps) or par["side"] not in ("LEFT", "RIGHT"):
                raise _bad("No such flange side", flange=i)
            where = (j, par["step"], par["side"])
            width = psteps[par["step"]]["length"]
        if where in seen:
            raise _bad("That edge already has a flange", flange=i)
        seen.add(where)
        for n, st in enumerate(steps):
            if not 1 <= st["angle"] <= MAX_ANGLE:
                raise _bad(f"Bend angle must be between 1 and {MAX_ANGLE:g} degrees", flange=i)
            rr = st.get("radius")
            if rr is not None and not 0.01 <= rr <= 1000:
                raise _bad("Inside radius 0.01-1000 mm", flange=i)
            if st["length"] < 0:
                raise _bad("Lengths and insets cannot be negative", flange=i)
            if st["length"] <= TOL and n < len(steps) - 1:
                raise _bad("Two bends need a straight part between them", flange=i)
        if f["inset_start"] < 0 or f["inset_end"] < 0:
            raise _bad("Lengths and insets cannot be negative", flange=i)
        if width - f["inset_start"] - f["inset_end"] <= TOL:
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


def _quad(pts: list[V3]) -> TopoDS_Shape:
    poly = BRepBuilderAPI_MakePolygon()
    for p in pts:
        poly.Add(gp_Pnt(*p))
    poly.Close()
    return BRepBuilderAPI_MakeFace(poly.Wire()).Face()


def _add3(a: V3, b: V3, k: float = 1.0) -> V3:
    return (a[0] + k * b[0], a[1] + k * b[1], a[2] + k * b[2])


def _cross3(a: V3, b: V3) -> V3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _rot3(v: V3, axis: V3, theta: float) -> V3:
    """v turned about the unit axis through the origin (Rodrigues)."""
    c, s = math.cos(theta), math.sin(theta)
    d = axis[0] * v[0] + axis[1] * v[1] + axis[2] * v[2]
    x = _cross3(axis, v)
    return (
        v[0] * c + x[0] * s + axis[0] * d * (1 - c),
        v[1] * c + x[1] * s + axis[1] * d * (1 - c),
        v[2] * c + x[2] * s + axis[2] * d * (1 - c),
    )


def layout(spec: dict[str, Any]) -> list[list[dict[str, Any]]]:
    """Where every bend of every flange is, in sketch coordinates (plain arithmetic, no kernel).

    A flange starts from an end face: the rectangle p, p + w*width, then + n*T, with n from the
    sheet's underside to its top and o pointing out of the material. A bend turns that face about
    an axis parallel to w, a radius away from the sheet on the side it bends to; the straight
    part then runs along the turned o. Per step: the face before the bend (p, w, n, o, width),
    the axis (c, a), angle (rad), and the face after the bend (p1, n1, o1) with its length."""
    t = spec["thickness"]
    out: list[list[dict[str, Any]]] = []
    for f in spec["flanges"]:
        par = f.get("parent")
        if par is None:
            fr = _frame(spec, f)
            (ax, ay), (dx, dy), (ox, oy) = fr["a"], fr["dir"], fr["out"]
            p: V3 = (ax + dx * fr["w0"], ay + dy * fr["w0"], 0.0)
            w: V3 = (dx, dy, 0.0)
            n: V3 = (0.0, 0.0, 1.0)
            o: V3 = (ox, oy, 0.0)
            width = fr["w1"] - fr["w0"]
        else:
            ps = out[par["flange"]][par["step"]]
            # the straight part's side: its width runs along the part, it leaves sideways
            p = _add3(ps["p1"], ps["o1"], f["inset_start"])
            if par["side"] == "RIGHT":
                p = _add3(p, ps["w"], ps["width"])
            w, n = ps["o1"], ps["n1"]
            o = ps["w"] if par["side"] == "RIGHT" else (-ps["w"][0], -ps["w"][1], -ps["w"][2])
            width = ps["length"] - f["inset_start"] - f["inset_end"]
        steps = []
        for st in steps_of(f):
            r = st.get("radius") or spec["bend_radius"]
            theta = math.radians(st["angle"])
            if st["up"]:
                c, a = _add3(p, n, t + r), _cross3(o, n)
            else:
                c, a = _add3(p, n, -r), _cross3(n, o)
            rel = (p[0] - c[0], p[1] - c[1], p[2] - c[2])
            p1 = _add3(c, _rot3(rel, a, theta))
            n1, o1 = _rot3(n, a, theta), _rot3(o, a, theta)
            steps.append(
                {
                    "p": p,
                    "w": w,
                    "n": n,
                    "o": o,
                    "width": width,
                    "c": c,
                    "a": a,
                    "theta": theta,
                    "radius": r,
                    "up": st["up"],
                    "p1": p1,
                    "n1": n1,
                    "o1": o1,
                    "length": st["length"],
                }
            )
            p, n, o = _add3(p1, o1, st["length"]), n1, o1
        out.append(steps)
    return out


def _flange(t: float, steps: list[dict[str, Any]]) -> list[TopoDS_Shape]:
    parts = []
    for s in steps:
        p, w, n = s["p"], s["w"], s["n"]
        q = _add3(p, w, s["width"])
        end = _quad([p, q, _add3(q, n, t), _add3(p, n, t)])
        axis = gp_Ax1(gp_Pnt(*s["c"]), gp_Dir(*s["a"]))
        parts.append(BRepPrimAPI_MakeRevol(end, axis, s["theta"]).Shape())
        if s["length"] > TOL:
            rot = gp_Trsf()
            rot.SetRotation(axis, s["theta"])
            turned = BRepBuilderAPI_Transform(end, rot, True).Shape()
            o1 = s["o1"]
            vec = gp_Vec(o1[0] * s["length"], o1[1] * s["length"], o1[2] * s["length"])
            parts.append(BRepPrimAPI_MakePrism(turned, vec).Shape())
    return parts


def _volume(shape: TopoDS_Shape) -> float:
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return float(g.Mass())


def build(spec: dict[str, Any]) -> TopoDS_Shape:
    """The folded part, placed on the sketch plane."""
    return place(folded(spec), spec.get("plane"))


def folded(spec: dict[str, Any]) -> TopoDS_Shape:
    """The folded part in sketch coordinates (the base's underside on z = 0)."""
    check(spec)
    o = spec["outline"]
    geoms = [
        {"type": "LINE", "start": list(a), "end": list(b)}
        for lp in [o["outer"], *o["holes"]]
        for a, b in zip(lp, lp[1:] + lp[:1], strict=True)
    ] + [{"type": "CIRCLE", **c} for c in o["circles"]]
    face = profile_face(geoms, holes=True)
    shape = BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, spec["thickness"])).Shape()
    total = _volume(shape)
    for steps in layout(spec):
        for piece in _flange(spec["thickness"], steps):
            total += _volume(piece)
            fuse = BRepAlgoAPI_Fuse(shape, piece)
            if not fuse.IsDone():
                raise GeomError("GEOM_BOOLEAN_FAILED", "Flange could not be joined to the sheet")
            shape = fuse.Shape()
    unify = ShapeUpgrade_UnifySameDomain(shape, True, True, True)
    unify.Build()
    shape = unify.Shape()
    if not BRepCheck_Analyzer(shape).IsValid() or count(shape, TopAbs_SOLID) != 1:
        raise GeomError("GEOM_BOOLEAN_FAILED", "Flanges overlap or leave the sheet in pieces")
    # pieces only ever touch: any volume lost in joining them is material in the same place twice
    if total - _volume(shape) > 1e-6 * max(1.0, total):
        raise GeomError("GEOM_BOOLEAN_FAILED", "Flanges overlap or run into the sheet")
    return shape


# --- flat pattern -----------------------------------------------------------------------------


def bend_allowance(spec: dict[str, Any], f: dict[str, Any]) -> float:
    return math.radians(f["angle"]) * (spec["bend_radius"] + spec["k_factor"] * spec["thickness"])


def _which(steps: list[list[dict[str, Any]]], b: dict[str, Any]) -> tuple[int, int] | None:
    """The flange and step an unfolded bend is: the one whose axis it turns about."""
    c, d = b["axis"]["p"], b["axis"]["d"]
    for i, st in enumerate(steps):
        for j, s in enumerate(st):
            a, v = s["a"], (c[0] - s["c"][0], c[1] - s["c"][1], c[2] - s["c"][2])
            along = v[0] * a[0] + v[1] * a[1] + v[2] * a[2]
            off = math.dist(v, (a[0] * along, a[1] * along, a[2] * along))
            # same line, and the bend's middle lies on this step's width (two flanges inset on
            # one edge share the line)
            m, w = b["axis"]["mid"], s["w"]
            pos = (m[0] - s["c"][0]) * w[0] + (m[1] - s["c"][1]) * w[1] + (m[2] - s["c"][2]) * w[2]
            parallel = abs(d[0] * a[0] + d[1] * a[1] + d[2] * a[2]) > 1 - 1e-9
            if parallel and off <= 1e-5 and -1e-5 <= pos <= s["width"] + 1e-5:
                return i, j
    return None


def flat(spec: dict[str, Any], shape: TopoDS_Shape | None = None) -> dict[str, Any]:
    """The flat blank (plan §23 S1: unfolded from the folded body by core.geometry.unfold):
    outline, holes, bend lines, and the numbers a quote reads (cut length, pierces, bends, blank
    area / box). Each bend says which flange and step it is. For a sheet of plain edge flanges
    the analytic layout runs first: same checks and messages as before, and the regression
    reference the unfolder is tested against."""
    check(spec)
    if all(simple(f) for f in spec["flanges"]):
        flat_analytic(spec)
    u = unfold(
        shape if shape is not None else folded(spec),
        spec["thickness"],
        spec["k_factor"],
        fixed=lambda _f, p, n: abs(p[2]) <= TOL and n[2] < -1 + 1e-9,  # the base's underside
    )
    loops = sorted(
        (_dedupe([tuple(p) for p in lp]) for lp in u["loops"]), key=lambda lp: -abs(area(lp))
    )
    outer = loops[0] if area(loops[0]) > 0 else loops[0][::-1]
    if not _simple(outer):
        raise _bad("Flanges overlap in the flat pattern; add insets or shorten them")
    holes = [h if area(h) < 0 else h[::-1] for h in loops[1:]]
    steps = layout(spec)
    for b in u["bends"]:
        hit = _which(steps, b)
        del b["axis"]
        f = spec["flanges"][hit[0]] if hit else None
        b["flange"], b["step"] = hit if hit else (None, None)
        b["edge"] = f.get("edge") if f and f.get("parent") is None and hit and hit[1] == 0 else None
        b["style"] = f.get("style", "FLANGE") if f else None
    order = lambda b: (b["flange"] is None, b["flange"] or 0, b["step"] or 0)
    return _blank(spec, outer, holes, u["circles"], sorted(u["bends"], key=order))


def flat_analytic(spec: dict[str, Any]) -> dict[str, Any]:
    """Track 9 Q3 layout: the base outline with each flange's strip (BA + length) spliced in."""
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
    return _blank(spec, poly, o["holes"], o["circles"], sorted(bends, key=lambda b: b["edge"]))


def _blank(
    spec: dict[str, Any],
    poly: list[Pt],
    holes: list[list[Pt]],
    circles: list[dict[str, Any]],
    bends: list[dict[str, Any]],
) -> dict[str, Any]:
    hole_area = sum(-area(h) for h in holes) + sum(math.pi * c["radius"] ** 2 for c in circles)
    hole_cut = sum(_perimeter(h) for h in holes) + sum(2 * math.pi * c["radius"] for c in circles)
    if area(poly) - hole_area <= 0:
        raise _bad("The flat blank has no area")
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return {
        "outline": [list(p) for p in poly],
        "holes": [[list(p) for p in h] for h in holes],
        "circles": circles,
        "bends": bends,
        "metrics": {
            "thickness_mm": spec["thickness"],
            "blank_area_mm2": area(poly) - hole_area,
            "sheet_cut_length_mm": _perimeter(poly) + hole_cut,
            "pierce_count": len(holes) + len(circles),
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


def sheet_info(spec: dict[str, Any]) -> dict[str, Any]:
    """Worker entry for GET /sheet: the flat pattern (needs the folded body)."""
    return flat(spec)


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
    shape0 = folded(spec)
    shape = place(shape0, spec.get("plane"))
    m = measure(shape)
    fp = flat(spec, shape0)
    m["sheet"] = {
        **fp["metrics"],
        "flanges": len(spec["flanges"]),
        "k_factor": spec["k_factor"],
        "bend_radius": spec["bend_radius"],
        "bend_source": spec.get("bend_source", "FEATURE"),
    }
    return to_brep(shape), m
