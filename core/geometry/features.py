"""Feature builders. S5: Extrude only (Revolve/Boolean arrive in S6)."""

import math
from typing import Any

from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.gp import gp_Vec
from OCP.TopoDS import TopoDS_Shape

from core.geometry.errors import GeomError
from core.geometry.metrics import measure
from core.geometry.serialize import to_brep
from core.geometry.sketch import profile_face

MAX_DISTANCE_MM = 1e6
DIRECTIONS = {"+Z": 1.0, "-Z": -1.0}


def extrude(geoms: list[dict[str, Any]], distance: float, direction: str = "+Z") -> TopoDS_Shape:
    if (
        not (math.isfinite(distance) and 0 < distance <= MAX_DISTANCE_MM)
        or direction not in DIRECTIONS
    ):
        raise GeomError("GEOM_INVALID_PARAM", "distance must be in (0, 1e6] mm, direction +Z/-Z")
    face = profile_face(geoms)
    shape = BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, DIRECTIONS[direction] * distance)).Shape()
    if shape.IsNull() or not BRepCheck_Analyzer(shape).IsValid():
        raise GeomError("GEOM_INVALID_RESULT", "result shape is invalid")
    return shape


def extrude_job(
    geoms: list[dict[str, Any]], distance: float, direction: str
) -> tuple[bytes, dict[str, Any]]:
    """Worker entry point: (BREP bytes, metrics)."""
    shape = extrude(geoms, distance, direction)
    m = measure(shape)
    if m.pop("solids") != 1:
        raise GeomError("GEOM_INVALID_RESULT", "extrude did not produce exactly one solid")
    return to_brep(shape), m
