"""Track 8 P2-4: edges and faces picked in the 3D view, stored by what they look like.

OCCT renumbers edges and faces whenever an earlier feature changes (the topological naming
problem FreeCAD also has), so a pick is stored as a signature: kind, middle point / area centre,
direction or axis, length / area, radius. On every rebuild the nearest edge or face of the same
kind, direction and radius within tolerance is used; none found -> GEOM_REF_LOST.
"""

import math
from typing import Any

from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp
from OCP.collections import IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as ShapeMap
from OCP.GeomAbs import (
    GeomAbs_Circle,
    GeomAbs_Cone,
    GeomAbs_Cylinder,
    GeomAbs_Line,
    GeomAbs_Plane,
    GeomAbs_Sphere,
    GeomAbs_Torus,
)
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
from OCP.TopExp import TopExp
from OCP.TopoDS import TopoDS, TopoDS_Shape

from core.geometry.errors import GeomError
from core.geometry.metrics import measure

EDGE_KINDS = {GeomAbs_Line: "LINE", GeomAbs_Circle: "CIRCLE"}
FACE_KINDS = {
    GeomAbs_Plane: "PLANE",
    GeomAbs_Cylinder: "CYLINDER",
    GeomAbs_Cone: "CONE",
    GeomAbs_Sphere: "SPHERE",
    GeomAbs_Torus: "TORUS",
}
ANGLE_TOL = 1e-6  # |cos| must be within this of 1 for "the same direction"
MOVE_SHARE = (
    0.1  # a pick may drift 10 % of the body's size between rebuilds (a changed sketch dimension)
)
MIN_MOVE_TOL = 1e-4  # mm


def _r(v: float) -> float:
    return round(v, 6)


def _axis(d: Any) -> list[float]:
    """A direction with its sign fixed (first non-zero component positive), so a line read in
    the other direction has the same signature."""
    v = [d.X(), d.Y(), d.Z()]
    for a in v:
        if abs(a) > 1e-9:
            return [_r(x if a > 0 else -x) for x in v]
    return [0.0, 0.0, 0.0]


def edge_sig(edge: TopoDS_Shape) -> dict[str, Any]:
    c = BRepAdaptor_Curve(TopoDS.Edge(edge))
    kind = EDGE_KINDS.get(c.GetType(), "CURVE")
    a, b = c.FirstParameter(), c.LastParameter()
    p = c.Value((a + b) / 2)
    props = GProp_GProps()
    BRepGProp.LinearProperties_s(edge, props)
    sig: dict[str, Any] = {
        "kind": kind,
        "mid": [_r(p.X()), _r(p.Y()), _r(p.Z())],
        "length": _r(props.Mass()),
    }
    if kind == "LINE":
        sig["dir"] = _axis(c.Line().Direction())
    elif kind == "CIRCLE":
        sig["dir"] = _axis(c.Circle().Axis().Direction())
        sig["radius"] = _r(c.Circle().Radius())
    return sig


def face_sig(face: TopoDS_Shape) -> dict[str, Any]:
    s = BRepAdaptor_Surface(TopoDS.Face(face))
    kind = FACE_KINDS.get(s.GetType(), "SURFACE")
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    p = props.CentreOfMass()
    sig: dict[str, Any] = {
        "kind": kind,
        "mid": [_r(p.X()), _r(p.Y()), _r(p.Z())],
        "area": _r(props.Mass()),
    }
    if kind == "PLANE":
        sig["dir"] = _axis(s.Plane().Axis().Direction())
    elif kind in ("CYLINDER", "CONE"):
        ax = s.Cylinder().Axis() if kind == "CYLINDER" else s.Cone().Axis()
        sig["dir"] = _axis(ax.Direction())
        if kind == "CYLINDER":
            sig["radius"] = _r(s.Cylinder().Radius())
    return sig


def _items(shape: TopoDS_Shape, kind: Any) -> list[TopoDS_Shape]:
    """Unique sub-shapes (a shared edge once, not once per face)."""
    m = ShapeMap()
    TopExp.MapShapes_s(shape, kind, m)
    return [m.FindKey(i) for i in range(1, m.Extent() + 1)]


def _diag(shape: TopoDS_Shape) -> float:
    b = measure(shape)["bbox"]
    return math.dist(b["min"], b["max"])


def _score(want: dict[str, Any], have: dict[str, Any], size_key: str) -> float:
    """Distance-like mismatch; infinite when kind, direction or radius differ."""
    if want.get("kind") != have["kind"]:
        return math.inf
    if want.get("dir") is not None:  # stored params carry None for "no direction"
        if "dir" not in have:
            return math.inf
        cos = abs(sum(a * b for a, b in zip(want["dir"], have["dir"], strict=True)))
        if cos < 1 - ANGLE_TOL:
            return math.inf
    r = want.get("radius")
    if r is not None and abs(r - have.get("radius", math.inf)) > 1e-6 * max(1.0, r):
        return math.inf
    return math.dist(want["mid"], have["mid"]) + abs(want[size_key] - have[size_key]) ** (
        0.5 if size_key == "area" else 1
    )


def resolve(shape: TopoDS_Shape, sigs: list[dict[str, Any]], what: str) -> list[TopoDS_Shape]:
    """The body's edges ("EDGE") or faces ("FACE") that the stored signatures point at."""
    kind, sig, size_key = (
        (TopAbs_EDGE, edge_sig, "length") if what == "EDGE" else (TopAbs_FACE, face_sig, "area")
    )
    cands = [(item, sig(item)) for item in _items(shape, kind)]
    tol = max(MIN_MOVE_TOL, MOVE_SHARE * _diag(shape))
    out: list[TopoDS_Shape] = []
    lost: list[int] = []
    for i, want in enumerate(sigs):
        best = min(cands, key=lambda c: _score(want, c[1], size_key), default=None)
        if best is None or _score(want, best[1], size_key) > tol:
            lost.append(i)
        elif not any(best[0].IsSame(o) for o in out):
            out.append(best[0])
    if lost:
        noun = "edges" if what == "EDGE" else "faces"
        raise GeomError(
            "GEOM_REF_LOST",
            f"Picked {noun} no longer exist on the body; pick them again",
            details={"refs": lost},
        )
    return out


def face_edges(faces: list[TopoDS_Shape]) -> list[TopoDS_Shape]:
    """Every edge of the given faces, each once (FreeCAD: a picked face fillets all its edges)."""
    out: list[TopoDS_Shape] = []
    for f in faces:
        for e in _items(f, TopAbs_EDGE):
            if not any(e.IsSame(o) for o in out):
                out.append(e)
    return out
