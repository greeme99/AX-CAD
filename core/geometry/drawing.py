"""Track 8 P3-1: orthographic and isometric views of 3D bodies (OCCT hidden line removal).

Each view is 2D geometry in view coordinates (mm, true size): visible lines, hidden lines and
centre lines, as LINE / CIRCLE / ARC / POLYLINE dicts. Lines and circles stay exact so the drawing
can be dimensioned; other curves are sampled. KS B 0001 views: FRONT looks along +Y with Z up,
TOP looks down with Y up, RIGHT looks along -X with Z up, ISO from (+X, -Y, +Z).
"""

import math
from typing import Any

from OCP.BRep import BRep_Builder
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Line
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Vec
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Compound, TopoDS_Shape

from core.geometry.errors import GeomError
from core.geometry.serialize import from_brep

# view -> (direction towards the viewer, x axis on the sheet); y = direction x x
VIEWS: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]] = {
    "FRONT": ((0, -1, 0), (1, 0, 0)),
    "TOP": ((0, 0, 1), (1, 0, 0)),
    "RIGHT": ((1, 0, 0), (0, 1, 0)),
    "ISO": ((1, -1, 1), (1, 1, 0)),
}
CURVE_POINTS = 49  # samples of a curve that is not a line or a circle
CENTER_OVERRUN = 3.0  # mm a centre line runs past the feature (KS B ISO 128-24: 2-5 mm)
TOL = 1e-7


def _r(v: float) -> float:
    return round(v, 9) + 0.0  # +0.0: no "-0.0" in the output


def _unit(v: tuple[float, float, float]) -> tuple[float, float, float]:
    n = math.hypot(*v)
    return (v[0] / n, v[1] / n, v[2] / n)


def frame(view: str) -> tuple[gp_Dir, gp_Dir, gp_Dir]:
    """Viewer direction, sheet x and sheet y of a view."""
    dn, dx = (gp_Dir(*_unit(v)) for v in VIEWS[view])  # the table's x axes are at right angles
    return dn, dx, dn.Crossed(dx)


def _edge2d(edge: Any) -> dict[str, Any]:
    c = BRepAdaptor_Curve(TopoDS.Edge(edge))
    u0, u1 = c.FirstParameter(), c.LastParameter()
    t = c.GetType()
    if t == GeomAbs_Line:
        a, b = c.Value(u0), c.Value(u1)
        return {"type": "LINE", "start": [_r(a.X()), _r(a.Y())], "end": [_r(b.X()), _r(b.Y())]}
    if t == GeomAbs_Circle:
        circ = c.Circle()
        o, r = circ.Location(), circ.Radius()
        center = [_r(o.X()), _r(o.Y())]
        if abs(u1 - u0 - 2 * math.pi) < 1e-9:
            return {"type": "CIRCLE", "center": center, "radius": _r(r)}
        a, b = c.Value(u0), c.Value(u1)
        angs = [math.degrees(math.atan2(p.Y() - o.Y(), p.X() - o.X())) % 360 for p in (a, b)]
        if circ.Axis().Direction().Z() < 0:  # clockwise on the sheet: the same arc read backwards
            angs.reverse()
        return {
            "type": "ARC",
            "center": center,
            "radius": _r(r),
            "start_angle": _r(angs[0]),
            "end_angle": _r(angs[1]),
        }
    pts = [c.Value(u0 + (u1 - u0) * i / (CURVE_POINTS - 1)) for i in range(CURVE_POINTS)]
    return {"type": "POLYLINE", "points": [[_r(p.X()), _r(p.Y())] for p in pts]}


def _on_line(p: list[float], a: list[float], b: list[float], tol: float = 1e-6) -> bool:
    """p lies on segment a-b."""
    ab = (b[0] - a[0], b[1] - a[1])
    n = math.hypot(*ab)
    if n < TOL:
        return math.dist(p, a) <= tol
    ap = (p[0] - a[0], p[1] - a[1])
    t = (ap[0] * ab[0] + ap[1] * ab[1]) / n
    return abs(ap[0] * ab[1] - ap[1] * ab[0]) / n <= tol and -tol <= t <= n + tol


def _straight(e: dict[str, Any]) -> dict[str, Any]:
    """A sampled curve that is really straight (a circle seen edge-on) as a LINE."""
    if e["type"] == "POLYLINE":
        a, b = e["points"][0], e["points"][-1]
        if math.dist(a, b) > TOL and all(_on_line(p, a, b) for p in e["points"]):
            return {"type": "LINE", "start": a, "end": b}
    return e


def _drop_covered(
    hidden: list[dict[str, Any]], visible: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Hidden lines lying on a visible line are not drawn (KS B ISO 128: the visible line wins),
    nor twice."""
    lines = [(v["start"], v["end"]) for v in visible if v["type"] == "LINE"]
    out: list[dict[str, Any]] = []
    for h in hidden:
        if h["type"] == "LINE" and any(
            _on_line(h["start"], a, b) and _on_line(h["end"], a, b) for a, b in lines
        ):
            continue
        if h["type"] == "LINE" and any(
            o["type"] == "LINE"
            and {tuple(o["start"]), tuple(o["end"])} == {tuple(h["start"]), tuple(h["end"])}
            for o in out
        ):
            continue
        out.append(h)
    return out


def _edges(shape: TopoDS_Shape | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if shape is None or shape.IsNull():
        return out
    ex = TopExp_Explorer(shape, TopAbs_EDGE)
    while ex.More():
        e = _straight(_edge2d(ex.Current()))
        ex.Next()
        if e["type"] == "LINE" and math.dist(e["start"], e["end"]) < TOL:
            continue  # degenerate
        out.append(e)
    return out


def _centers(shape: TopoDS_Shape, n: gp_Dir, x: gp_Dir, y: gp_Dir) -> list[dict[str, Any]]:
    """Centre marks of round features seen end-on, centre lines of those seen from the side."""

    def to2d(p: gp_Pnt) -> list[float]:
        return [_r(p.XYZ().Dot(x.XYZ())), _r(p.XYZ().Dot(y.XYZ()))]

    marks: dict[tuple[float, ...], dict[str, Any]] = {}
    ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        s = BRepAdaptor_Surface(TopoDS.Face(ex.Current()))
        ex.Next()
        if s.GetType() not in (GeomAbs_Cylinder, GeomAbs_Cone):
            continue
        ax = s.Cylinder().Axis() if s.GetType() == GeomAbs_Cylinder else s.Cone().Axis()
        d, loc = ax.Direction(), ax.Location()
        r = s.Cylinder().Radius() if s.GetType() == GeomAbs_Cylinder else s.Cone().RefRadius()
        along = abs(d.Dot(n))
        if along > 1 - 1e-9:  # end-on: a cross over the circle
            c = to2d(loc)
            arm = r + CENTER_OVERRUN
            key = (round(c[0], 6), round(c[1], 6))
            if key not in marks or marks[key]["arm"] < arm:
                marks[key] = {"c": c, "arm": arm}
        elif along < 1e-9:  # side-on: the axis over the face's length
            v0, v1 = s.FirstVParameter(), s.LastVParameter()
            if s.GetType() == GeomAbs_Cone:
                k = math.cos(s.Cone().SemiAngle())
                v0, v1 = v0 * k, v1 * k
            a = loc.Translated(gp_Vec(d).Multiplied(v0 - CENTER_OVERRUN))
            b = loc.Translated(gp_Vec(d).Multiplied(v1 + CENTER_OVERRUN))
            p0, p1 = to2d(a), to2d(b)
            ends = tuple(round(q, 6) for q in (*p0, *p1))
            marks.setdefault(ends, {"line": {"type": "LINE", "start": p0, "end": p1}})
    out = []
    for m in marks.values():
        if "line" in m:
            out.append(m["line"])
            continue
        (cx, cy), arm = m["c"], m["arm"]
        out.append({"type": "LINE", "start": [_r(cx - arm), cy], "end": [_r(cx + arm), cy]})
        out.append({"type": "LINE", "start": [cx, _r(cy - arm)], "end": [cx, _r(cy + arm)]})
    return out


def project(shape: TopoDS_Shape, view: str) -> dict[str, Any]:
    """Visible, hidden and centre lines of one view, in true size on the sheet plane."""
    if view not in VIEWS:
        raise GeomError("GEOM_INVALID_PARAM", f"view must be one of {', '.join(VIEWS)}")
    n, x, y = frame(view)
    algo = HLRBRep_Algo()
    algo.Add(shape)
    algo.Projector(HLRAlgo_Projector(gp_Ax2(gp_Pnt(0, 0, 0), n, x)))
    algo.Update()
    algo.Hide()
    h = HLRBRep_HLRToShape(algo)
    visible = _edges(h.VCompound()) + _edges(h.OutLineVCompound())
    hidden = (
        []
        if view == "ISO"
        else _drop_covered(_edges(h.HCompound()) + _edges(h.OutLineHCompound()), visible)
    )
    center = [] if view == "ISO" else _centers(shape, n, x, y)
    return {"view": view, "visible": visible, "hidden": hidden, "center": center}


def _compound(shapes: list[TopoDS_Shape]) -> TopoDS_Compound:
    comp, b = TopoDS_Compound(), BRep_Builder()
    b.MakeCompound(comp)
    for s in shapes:
        b.Add(comp, s)
    return comp


def views_job(breps: list[bytes], views: list[str]) -> list[dict[str, Any]]:
    """Worker entry point: the bodies drawn together in each view."""
    if not breps:
        raise GeomError("GEOM_INVALID_PARAM", "no bodies to draw")
    shape = _compound([from_brep(b) for b in breps])
    return [project(shape, v) for v in views]
