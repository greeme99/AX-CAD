"""Track 8 P2-3: Sweep (FreeCAD PartDesign AdditivePipe) and Loft (AdditiveLoft).

A sweep moves a profile sketch along a path sketch; a loft skins a solid through two or more
closed profiles in order. Sketch geoms come with the plane they lie on (features.place)."""

from typing import Any

from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipe, BRepOffsetAPI_ThruSections
from OCP.GeomFill import GeomFill_Trihedron
from OCP.TopoDS import TopoDS, TopoDS_Shape

from core.geometry.errors import GeomError
from core.geometry.features import Plane, _job, _valid, place
from core.geometry.sketch import path_wire, profile_face, profile_wire

MAX_SECTIONS = 20


def _checked(shape: TopoDS_Shape | None, what: str) -> TopoDS_Shape:
    if shape is None or shape.IsNull() or not _valid(shape):
        raise GeomError("GEOM_FEATURE_FAILED", f"{what} failed")
    return shape


def sweep(
    profile: list[dict[str, Any]],
    profile_plane: Plane,
    path: list[dict[str, Any]],
    path_plane: Plane,
    frenet: bool = False,
) -> TopoDS_Shape:
    """The profile (holes allowed) as it lies, carried along the path. Frenet keeps the profile
    turning with the path's curvature (helices); otherwise its twist is corrected."""
    face = place(profile_face(profile, True), profile_plane)
    spine = TopoDS.Wire(place(path_wire(path), path_plane))
    mode = (
        GeomFill_Trihedron.GeomFill_IsFrenet
        if frenet
        else GeomFill_Trihedron.GeomFill_IsCorrectedFrenet
    )
    try:
        shape = BRepOffsetAPI_MakePipe(spine, face, mode, False).Shape()
    except Exception:  # noqa: BLE001 - Standard_Failure from the sweep algorithm
        shape = None
    return _checked(shape, "sweep (does the profile cross the path start?)")


def loft(sections: list[tuple[list[dict[str, Any]], Plane]], ruled: bool = False) -> TopoDS_Shape:
    """Solid through the closed profiles in the given order; ruled = straight faces between them."""
    if not 2 <= len(sections) <= MAX_SECTIONS:
        raise GeomError("GEOM_INVALID_PARAM", f"a loft needs 2 to {MAX_SECTIONS} profiles")
    mk = BRepOffsetAPI_ThruSections(True, ruled)
    for geoms, plane in sections:
        mk.AddWire(TopoDS.Wire(place(profile_wire(geoms), plane)))
    try:
        mk.Build()
        shape = mk.Shape() if mk.IsDone() else None
    except Exception:  # noqa: BLE001
        shape = None
    return _checked(shape, "loft (are the profiles crossing each other?)")


def sweep_job(
    profile: list[dict[str, Any]],
    profile_plane: Plane,
    path: list[dict[str, Any]],
    path_plane: Plane,
    frenet: bool,
) -> tuple[bytes, dict[str, Any]]:
    return _job(sweep(profile, profile_plane, path, path_plane, frenet))


def loft_job(
    sections: list[tuple[list[dict[str, Any]], Plane]], ruled: bool
) -> tuple[bytes, dict[str, Any]]:
    return _job(loft(sections, ruled))
