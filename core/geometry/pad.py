"""Track 8 P2-2: Pad / Pocket options (FreeCAD PartDesign) and primitive solids.

Lengths: one side, symmetric to the sketch plane, two sides, through all (pocket), up to a planar
face. Taper: the sides lean by the angle as they leave the sketch plane (positive = the outline
narrows and holes widen, like a mould draft).
"""

import math
from typing import Any

from OCP.Bnd import Bnd_Box
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.BRepOffsetAPI import BRepOffsetAPI_DraftAngle
from OCP.BRepPrimAPI import (
    BRepPrimAPI_MakeBox,
    BRepPrimAPI_MakeCone,
    BRepPrimAPI_MakeCylinder,
    BRepPrimAPI_MakeHalfSpace,
    BRepPrimAPI_MakePrism,
    BRepPrimAPI_MakeSphere,
    BRepPrimAPI_MakeTorus,
)
from OCP.GeomAbs import GeomAbs_Plane
from OCP.gp import gp_Ax2, gp_Dir, gp_Pln, gp_Pnt, gp_Vec
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Face, TopoDS_Shape

from core.geometry.errors import GeomError
from core.geometry.features import DIRECTIONS, MAX_DISTANCE_MM, Plane, _job, _valid, boolean, place
from core.geometry.metrics import measure
from core.geometry.serialize import from_brep
from core.geometry.sketch import profile_face

MODES = ("LENGTH", "SYMMETRIC", "TWO_SIDED", "THROUGH_ALL", "UP_TO_FACE")
MAX_TAPER_DEG = 60
MIN_FACE_COS = 1e-3  # an up-to face almost parallel to the extrusion never stops it
XY: Plane = {"origin": [0.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0], "xdir": [1.0, 0.0, 0.0]}
# axis of a round primitive -> (axis, x direction) of its frame
AXES = {
    "X": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    "Y": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "Z": ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
}


def _bad(msg: str) -> GeomError:
    return GeomError("GEOM_INVALID_PARAM", msg)


def _unit(v: list[float]) -> list[float]:
    n = math.hypot(*v)
    if n < 1e-12:
        raise _bad("zero direction")
    return [a / n for a in v]


def _diag(shape: TopoDS_Shape) -> float:
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    lo, hi = box.CornerMin(), box.CornerMax()
    return math.dist((lo.X(), lo.Y(), lo.Z()), (hi.X(), hi.Y(), hi.Z()))


def _draft(prism: TopoDS_Shape, sign: float, deg: float) -> TopoDS_Shape:
    """Lean every side face of a prism grown along sign*Z, about the sketch plane z = 0."""
    draft = BRepOffsetAPI_DraftAngle(prism)
    pull, neutral = gp_Dir(0, 0, sign), gp_Pln(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))
    ex = TopExp_Explorer(prism, TopAbs_FACE)
    while ex.More():
        face = TopoDS.Face(ex.Current())
        ex.Next()
        s = BRepAdaptor_Surface(face)
        if s.GetType() == GeomAbs_Plane and abs(abs(s.Plane().Axis().Direction().Z()) - 1) < 1e-9:
            continue  # the cap faces
        draft.Add(face, pull, math.radians(deg), neutral)
        if not draft.AddDone():
            raise GeomError("GEOM_INVALID_RESULT", "taper angle too steep for this profile")
    draft.Build()
    if not draft.IsDone() or not _valid(draft.Shape()):
        raise GeomError("GEOM_INVALID_RESULT", "taper angle too steep for this profile")
    return draft.Shape()


def _side(face: TopoDS_Face, length: float, sign: float, taper: float) -> TopoDS_Shape:
    prism = BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, sign * length)).Shape()
    return _draft(prism, sign, taper) if taper else prism


def _reach(face: TopoDS_Face, plane: Plane, sign: float, up_to: Plane) -> float:
    """Length along the extrusion that gets every profile point past the up-to plane."""
    d = [sign * a for a in _unit(plane["normal"])]
    n = _unit(up_to["normal"])
    cos = sum(a * b for a, b in zip(d, n, strict=True))
    if abs(cos) < MIN_FACE_COS:
        raise _bad("the up-to face is parallel to the extrusion")
    gap = [a - b for a, b in zip(up_to["origin"], plane["origin"], strict=True)]
    t = sum(a * b for a, b in zip(gap, n, strict=True)) / cos
    if t <= 0:
        raise _bad("the up-to face is behind the sketch")
    return t + _diag(face) / abs(cos) + 1.0


def solid(
    geoms: list[dict[str, Any]],
    p: dict[str, Any],
    plane: Plane | None,
    through: float = 0.0,
) -> TopoDS_Shape:
    """The extruded solid on its plane. `through` = THROUGH_ALL length (from the pocket target)."""
    mode, sign = p.get("mode") or "LENGTH", DIRECTIONS[p["direction"]]
    taper = float(p.get("taper_deg") or 0.0)
    if mode not in MODES or not abs(taper) < MAX_TAPER_DEG:
        raise _bad("unknown length mode or taper out of range")
    face = profile_face(geoms, plane is not None)
    pl = plane or XY
    d, d2 = float(p.get("distance") or 0), float(p.get("distance2") or 0)
    sides = {
        "LENGTH": [(d, sign)],
        "SYMMETRIC": [(d / 2, 1.0), (d / 2, -1.0)],
        "TWO_SIDED": [(d, sign), (d2, -sign)],
        "THROUGH_ALL": [(through, sign)],
        "UP_TO_FACE": [(_reach(face, pl, sign, p["up_to"]) if p.get("up_to") else 0.0, sign)],
    }[mode]
    if any(not (0 < length <= 4 * MAX_DISTANCE_MM) for length, _ in sides):
        raise _bad("length must be positive")
    parts = [_side(face, length, s, taper) for length, s in sides]
    shape = parts[0]
    if len(parts) == 2:  # one solid: the two halves share the sketch face
        fuse = BRepAlgoAPI_Fuse(parts[0], parts[1])
        unify = ShapeUpgrade_UnifySameDomain(fuse.Shape(), True, True, True)
        unify.Build()
        shape = unify.Shape()
    shape = place(shape, plane)
    if mode == "UP_TO_FACE":  # cut away what passed the face (FreeCAD Pad "Up to face")
        n = _unit(p["up_to"]["normal"])
        d_w = [sign * a for a in _unit(pl["normal"])]
        beyond = 1.0 if sum(a * b for a, b in zip(d_w, n, strict=True)) > 0 else -1.0
        o = p["up_to"]["origin"]
        big = BRepBuilderAPI_MakeFace(gp_Pln(gp_Pnt(*o), gp_Dir(*n))).Face()
        far = gp_Pnt(*(a + beyond * b for a, b in zip(o, n, strict=True)))
        shape = BRepAlgoAPI_Cut(shape, BRepPrimAPI_MakeHalfSpace(big, far).Solid()).Shape()
    if not _valid(shape):
        raise GeomError("GEOM_INVALID_RESULT", "result shape is invalid")
    return shape


def pad_job(
    geoms: list[dict[str, Any]], p: dict[str, Any], plane: Plane | None
) -> tuple[bytes, dict[str, Any]]:
    """Worker entry point for EXTRUDE with the P2-2 options."""
    return _job(solid(geoms, p, plane))


def pocket_job(
    geoms: list[dict[str, Any]], p: dict[str, Any], plane: Plane | None, target: bytes
) -> tuple[bytes, dict[str, Any]]:
    """FreeCAD Pocket: cut the extruded profile out of an earlier body."""
    body = from_brep(target)
    pl = plane or XY
    box = Bnd_Box()
    BRepBndLib.Add_s(body, box)
    corners = [box.CornerMin(), box.CornerMax()]
    far = max(
        math.dist(pl["origin"], (x.X(), y.Y(), z.Z()))
        for x in corners
        for y in corners
        for z in corners
    )
    out = boolean("CUT", body, solid(geoms, p, plane, through=far + 1.0))
    before, after = measure(body)["volume_mm3"], measure(out)["volume_mm3"]
    if after >= before * (1 - 1e-9):
        raise GeomError(
            "GEOM_EMPTY_RESULT", "the pocket does not cut the body (check its direction)"
        )
    return _job(out)


def primitive(p: dict[str, Any]) -> TopoDS_Shape:
    """FreeCAD Part primitives: base at `origin`, round ones standing along `axis`."""
    kind = p["kind"]
    main, xdir = AXES["Z" if kind == "BOX" else p.get("axis") or "Z"]
    ax = gp_Ax2(gp_Pnt(*p["origin"]), gp_Dir(*main), gp_Dir(*xdir))
    if kind == "BOX":  # corner at origin, length X x width Y x height Z (FreeCAD Part Box)
        mk: Any = BRepPrimAPI_MakeBox(ax, p["length"], p["width"], p["height"])
    elif kind == "CYLINDER":
        mk = BRepPrimAPI_MakeCylinder(ax, p["radius"], p["height"])
    elif kind == "CONE":  # radius at the base, radius2 at the top (0 = pointed)
        mk = BRepPrimAPI_MakeCone(ax, p["radius"], p["radius2"], p["height"])
    elif kind == "SPHERE":  # centred on origin
        mk = BRepPrimAPI_MakeSphere(ax, p["radius"])
    elif kind == "TORUS":  # ring radius, tube radius
        mk = BRepPrimAPI_MakeTorus(ax, p["radius"], p["radius2"])
    else:
        raise _bad("unknown primitive")
    shape = mk.Shape()
    if not _valid(shape):
        raise GeomError("GEOM_INVALID_RESULT", "result shape is invalid")
    return shape


def primitive_job(p: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    return _job(primitive(p))
