"""FN-10: editable 2D geoms (render payload `geom`, mm) -> one closed wire -> planar face.

Validation order: degenerate edge -> closed -> face -> self-intersection.
"""

import math
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from OCP.BRep import BRep_Tool
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakeWire,
)
from OCP.GC import GC_MakeArcOfCircle
from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt
from OCP.ShapeAnalysis import ShapeAnalysis_Wire
from OCP.TopoDS import TopoDS_Face, TopoDS_Wire

from core.geometry.errors import GeomError

LIN_TOL = 1e-6  # mm, degenerate edge threshold
# ponytail: render JSON is rounded, so arc/line ends differ ~1e-6; endpoints within 1e-4 mm are
# joined (snapped). Make it a drawing-level tolerance setting if shop drawings need looser.
JOIN_TOL = 1e-4

Pt = tuple[float, float]


@dataclass
class Seg:
    a: Pt
    b: Pt
    mid: Pt | None = None  # None = straight line, else a point on the arc between a and b


def _near(p: Pt, q: Pt) -> bool:
    return math.dist(p, q) <= JOIN_TOL


def _pnt(p: Pt) -> gp_Pnt:
    return gp_Pnt(p[0], p[1], 0.0)


def _arc_pt(c: Pt, r: float, deg: float) -> Pt:
    return (c[0] + r * math.cos(math.radians(deg)), c[1] + r * math.sin(math.radians(deg)))


def _bulge_mid(a: Pt, b: Pt, bulge: float) -> Pt:
    """Arc midpoint: positive bulge = CCW, i.e. the arc bulges to the right of a->b."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    s = n / 2 * bulge  # sagitta
    return ((a[0] + b[0]) / 2 + dy / n * s, (a[1] + b[1]) / 2 - dx / n * s)


def _segments(g: dict[str, Any]) -> list[Seg]:
    t = g["type"]
    if t == "LINE":
        return [Seg(tuple(g["start"]), tuple(g["end"]))]  # type: ignore[arg-type]
    if t == "ARC":
        c, r = g["center"], g["radius"]
        a0, a1 = g["start_angle"], g["end_angle"]
        if a1 <= a0:
            a1 += 360.0
        return [Seg(_arc_pt(c, r, a0), _arc_pt(c, r, a1), _arc_pt(c, r, (a0 + a1) / 2))]
    if t == "LWPOLYLINE":
        pts = g["points"]
        pairs = list(pairwise(pts))
        if g["closed"]:
            pairs.append((pts[-1], pts[0]))
        out = []
        for p, q in pairs:
            a, b = (p[0], p[1]), (q[0], q[1])
            deg = math.dist(a, b) < LIN_TOL
            out.append(Seg(a, b, None if p[2] == 0 or deg else _bulge_mid(a, b, p[2])))
        return out
    raise GeomError("GEOM_INVALID_PARAM", f"entity type {t} cannot be a profile")


def _chain(segs: list[Seg]) -> tuple[list[Seg], list[Seg]]:
    """Greedy endpoint chaining from segs[0]; returns (chain, unused). Joints snap to the previous end."""
    chain, rest = [segs[0]], segs[1:]
    while rest:
        end = chain[-1].b
        for i, s in enumerate(rest):
            if _near(s.a, end):
                chain.append(Seg(end, s.b, s.mid))
            elif _near(s.b, end):
                chain.append(Seg(end, s.a, s.mid))
            else:
                continue
            del rest[i]
            break
        else:
            break
    return chain, rest


def _dangling(segs: list[Seg]) -> list[list[float]]:
    """Endpoints used an odd number of times (cluster by JOIN_TOL)."""
    nodes: list[list[Any]] = []
    for p in (q for s in segs for q in (s.a, s.b)):
        for n in nodes:
            if _near(n[0], p):
                n[1] += 1
                break
        else:
            nodes.append([p, 1])
    return [[n[0][0], n[0][1]] for n in nodes if n[1] % 2]


def _edge(s: Seg) -> Any:
    if s.mid is None:
        return BRepBuilderAPI_MakeEdge(_pnt(s.a), _pnt(s.b)).Edge()
    arc = GC_MakeArcOfCircle(_pnt(s.a), _pnt(s.mid), _pnt(s.b)).Value()
    return BRepBuilderAPI_MakeEdge(arc).Edge()


def profile_wire(geoms: list[dict[str, Any]]) -> TopoDS_Wire:
    circles = [g for g in geoms if g["type"] == "CIRCLE"]
    if circles:
        # ponytail: no inner loops/holes yet - add face with multiple wires when needed
        if len(geoms) != 1:
            raise GeomError("GEOM_MULTIPLE_PROFILES", "A circle must be the only profile entity")
        g = circles[0]
        if g["radius"] <= LIN_TOL:
            raise GeomError("GEOM_DEGENERATE_EDGE", "circle radius below 1e-6 mm")
        circ = gp_Circ(gp_Ax2(_pnt(tuple(g["center"])), gp_Dir(0, 0, 1)), g["radius"])  # type: ignore[arg-type]
        mk = BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(circ).Edge())
        return mk.Wire()

    segs = [s for g in geoms for s in _segments(g)]
    for s in segs:
        if math.dist(s.a, s.b) < LIN_TOL:
            raise GeomError(
                "GEOM_DEGENERATE_EDGE", "edge shorter than 1e-6 mm", details={"at": list(s.a)}
            )
    chain, rest = _chain(segs)
    if not _near(chain[-1].b, chain[0].a):
        raise GeomError(
            "GEOM_OPEN_WIRE", "sketch wire is not closed", details={"dangling": _dangling(segs)}
        )
    if rest:
        raise GeomError("GEOM_MULTIPLE_PROFILES", "Profile entities form more than one loop")
    chain[-1].b = chain[0].a
    mk = BRepBuilderAPI_MakeWire()
    for s in chain:
        mk.Add(_edge(s))
    if not mk.IsDone():
        raise GeomError("GEOM_INVALID_WIRE", f"MakeWire error {mk.Error()}")
    return mk.Wire()


def profile_face(geoms: list[dict[str, Any]]) -> TopoDS_Face:
    wire = profile_wire(geoms)
    if not BRep_Tool.IsClosed_s(wire):
        raise GeomError("GEOM_OPEN_WIRE", "sketch wire is not closed")
    mk = BRepBuilderAPI_MakeFace(wire, True)  # OnlyPlane
    if not mk.IsDone():
        raise GeomError("GEOM_INVALID_WIRE", f"MakeFace error {mk.Error()}")
    face = mk.Face()
    if ShapeAnalysis_Wire(wire, face, LIN_TOL).CheckSelfIntersection():
        raise GeomError("GEOM_SELF_INTERSECTION", "sketch wire self-intersects")
    return face
