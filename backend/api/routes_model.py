"""3D features (sketch -> Extrude/Revolve, Boolean, STEP/IGES import, machining features on a body:
Fillet/Chamfer/Hole/Pattern/Mirror/Shell), metrics (FN-15), mesh (FN-13), STEP/IGES export (FN-12)."""

import contextlib
import hashlib
import json
import math
import os
import re
import threading
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

import OCP
from fastapi import APIRouter, File, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import func, select, update

from backend.api.auth import CurrentUser, Db, get_document, need
from backend.api.common import ApiError, body, pick
from backend.db.models import Document, Feature, Material, Revision, SheetBend, User
from core.geometry import holes_std, sheetmetal, solver
from core.geometry.dressup import MAX_COPIES, MAX_DRAFT_DEG, MAX_HOLES, dressup_job
from core.geometry.errors import GeomError
from core.geometry.exchange import check_file, export_job, import_job
from core.geometry.features import MAX_DISTANCE_MM, boolean_job, revolve_job
from core.geometry.measure3d import MAX_PICKS, measure_job
from core.geometry.mesh import mesh_job
from core.geometry.pad import MAX_TAPER_DEG, pad_job, pocket_job, primitive_job
from core.geometry.sheetmetal import sheet_job
from core.geometry.surface import (
    surface_boundary_job,
    surface_extrude_job,
    surface_fill_job,
    surface_loft_job,
    surface_offset_job,
    surface_revolve_job,
    surface_sweep_job,
)
from core.geometry.surface_analysis import (
    continuity_check_job,
    curvature_map_job,
    draft_analysis_job,
    zebra_stripes_job,
)
from core.geometry.surface_edit import (
    surface_extend_job,
    surface_knit_job,
    surface_replace_job,
    surface_split_job,
    surface_thicken_job,
    surface_trim_job,
)
from core.geometry.sweep import MAX_SECTIONS, loft_job, sweep_job
from core.geometry.worker import run_kernel

router = APIRouter()
Handle = Annotated[str, Field(pattern=r"^[0-9A-Fa-f]{1,16}$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Sketch(_Strict):
    """Profile source: DXF entities of a revision (handles), or a SKETCH feature (Track 8 P2-1)."""

    source_revision_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")] | None = None
    handles: Annotated[list[Handle], Field(min_length=1, max_length=1000)] | None = None
    sketch_feature_id: Annotated[int, Field(ge=1)] | None = None

    @model_validator(mode="after")
    def _unique(self) -> "_Sketch":
        dxf = self.source_revision_id is not None and self.handles is not None
        if dxf == (self.sketch_feature_id is not None) or (
            not dxf and (self.source_revision_id or self.handles)
        ):
            raise ValueError("give either source_revision_id + handles or sketch_feature_id")
        if self.handles and len(set(self.handles)) != len(self.handles):
            raise ValueError("duplicate handles")
        return self


class UpTo(_Strict):
    """A planar face to extrude up to (picked on a body in the 3D view)."""

    origin: "Vec3"
    normal: "Vec3"


class ExtrudeParams(_Sketch):
    """FreeCAD Pad: one length, symmetric to the sketch plane, two lengths or up to a face, with
    an optional taper (Track 8 P2-2)."""

    mode: Literal["LENGTH", "SYMMETRIC", "TWO_SIDED", "UP_TO_FACE"] = "LENGTH"
    distance: Annotated[float, Field(allow_inf_nan=False, gt=0, le=MAX_DISTANCE_MM)] | None = None
    distance2: Annotated[float, Field(allow_inf_nan=False, gt=0, le=MAX_DISTANCE_MM)] | None = None
    direction: Literal["+Z", "-Z"]
    taper_deg: Annotated[float, Field(allow_inf_nan=False, gt=-MAX_TAPER_DEG, lt=MAX_TAPER_DEG)] = 0
    up_to: UpTo | None = None

    @model_validator(mode="after")
    def _lengths(self) -> "ExtrudeParams":
        need = {
            "LENGTH": ("distance",),
            "SYMMETRIC": ("distance",),
            "TWO_SIDED": ("distance", "distance2"),
            "UP_TO_FACE": ("up_to",),
            "THROUGH_ALL": (),
        }[self.mode]
        if any(getattr(self, k) is None for k in need):
            raise ValueError(f"{self.mode} needs {', '.join(need)}")
        if self.up_to and math.hypot(*self.up_to.normal) < 1e-9:
            raise ValueError("zero face normal")
        return self


Coord = Annotated[float, Field(allow_inf_nan=False, ge=-MAX_DISTANCE_MM, le=MAX_DISTANCE_MM)]
Vec2 = Annotated[list[Coord], Field(min_length=2, max_length=2)]
FeatureId = Annotated[int, Field(ge=1)]


class RevolveParams(_Sketch):
    axis_point: Vec2
    axis_dir: Vec2
    angle_deg: Annotated[float, Field(allow_inf_nan=False, gt=0, le=360)]

    @model_validator(mode="after")
    def _axis(self) -> "RevolveParams":
        if self.axis_dir == [0, 0]:
            raise ValueError("zero axis direction")
        return self


class BooleanParams(_Strict):
    # ponytail: inputs live only in params (source of truth); "consumed" is derived by scanning the
    # document's BOOLEAN features, no extra column. Add a consumed_by FK if lists get large.
    op: Literal["FUSE", "CUT", "COMMON"]
    target_feature_id: FeatureId
    tool_feature_id: FeatureId


Size = Annotated[float, Field(allow_inf_nan=False, gt=0, le=MAX_DISTANCE_MM)]
Vec3 = Annotated[list[Coord], Field(min_length=3, max_length=3)]
EdgeSet = Literal["ALL", "VERTICAL", "TOP", "BOTTOM"]  # dressup.EDGE_SETS


class _OnBody(_Strict):
    """A machining feature (FreeCAD PartDesign style): changes one earlier body, which it consumes."""

    target_feature_id: FeatureId


MAX_REFS = 500
Area = Annotated[float, Field(allow_inf_nan=False, ge=0, le=MAX_DISTANCE_MM**2 * 6)]


class EdgeRef(_Strict):
    """An edge picked in the 3D view, by what it looks like (refs.edge_sig): found again on rebuild."""

    kind: Literal["LINE", "CIRCLE", "CURVE"]
    mid: Vec3
    length: Annotated[float, Field(allow_inf_nan=False, ge=0, le=MAX_DISTANCE_MM * 10)]
    dir: Vec3 | None = None
    radius: Size | None = None


class FaceRef(_Strict):
    """A face picked in the 3D view (refs.face_sig)."""

    kind: Literal["PLANE", "CYLINDER", "CONE", "SPHERE", "TORUS", "SURFACE"]
    mid: Vec3
    area: Area
    dir: Vec3 | None = None
    radius: Size | None = None


EdgeRefs = Annotated[list[EdgeRef], Field(min_length=1, max_length=MAX_REFS)]
FaceRefs = Annotated[list[FaceRef], Field(min_length=1, max_length=MAX_REFS)]


class _EdgePick(_OnBody):
    """Which edges: a rule (edges), picked edges, or every edge of picked faces - exactly one."""

    edges: EdgeSet | None = None
    edge_refs: EdgeRefs | None = None
    face_refs: FaceRefs | None = None

    @model_validator(mode="after")
    def _one(self) -> "_EdgePick":
        if sum(x is not None for x in (self.edges, self.edge_refs, self.face_refs)) != 1:
            raise ValueError("give exactly one of edges, edge_refs, face_refs")
        return self


class FilletParams(_EdgePick):
    radius: Size


class ChamferParams(_EdgePick):
    distance: Size


class PatternParams(_OnBody):
    kind: Literal["LINEAR", "POLAR"]
    copies: Annotated[int, Field(ge=2, le=MAX_COPIES)]  # bodies in total
    step: Vec3 | None = None  # LINEAR: offset between neighbours
    center: Vec2 | None = None  # POLAR: about the Z axis through this point
    angle_deg: Annotated[float, Field(allow_inf_nan=False, gt=0, le=360)] = 360

    @model_validator(mode="after")
    def _kind(self) -> "PatternParams":
        if self.kind == "LINEAR" and (self.step is None or self.step == [0, 0, 0]):
            raise ValueError("LINEAR needs a non-zero step")
        if self.kind == "POLAR" and self.center is None:
            raise ValueError("POLAR needs a center")
        return self


class MirrorParams(_OnBody):
    plane: Literal["YZ", "ZX", "XY"]
    offset: Coord = 0


class ShellParams(_OnBody):
    """FreeCAD Thickness (Track 8 P2-3 adds BOTH/NONE open faces, outward walls, sharp joins)."""

    thickness: Size
    open_face: Literal["TOP", "BOTTOM", "BOTH", "NONE"]
    outward: bool = False
    join: Literal["ARC", "INTERSECTION"] = "ARC"
    face_refs: FaceRefs | None = None  # picked faces to leave open (instead of open_face)


class DraftParams(_OnBody):
    """FreeCAD Draft (Track 8 P2-3): tilt the side faces about the top or bottom plane."""

    angle_deg: Annotated[float, Field(allow_inf_nan=False, ge=-MAX_DRAFT_DEG, le=MAX_DRAFT_DEG)]
    pull: Literal["+Z", "-Z"] = "+Z"
    neutral: Literal["TOP", "BOTTOM"] = "BOTTOM"
    face_refs: FaceRefs | None = None  # only these side faces (default: all of them)

    @model_validator(mode="after")
    def _angle(self) -> "DraftParams":
        if self.angle_deg == 0:
            raise ValueError("a draft angle of 0 changes nothing")
        return self


SketchId = Annotated[int, Field(ge=1)]


class SweepParams(_Strict):
    """FreeCAD Sweep (AdditivePipe, Track 8 P2-3): a profile sketch carried along a path sketch."""

    profile_sketch_id: SketchId
    path_sketch_id: SketchId
    frenet: bool = False

    @model_validator(mode="after")
    def _two(self) -> "SweepParams":
        if self.profile_sketch_id == self.path_sketch_id:
            raise ValueError("the profile and the path must be different sketches")
        return self


class LoftParams(_Strict):
    """FreeCAD Loft (Track 8 P2-3): a solid through closed profile sketches in order."""

    sketch_ids: Annotated[list[SketchId], Field(min_length=2, max_length=MAX_SECTIONS)]
    ruled: bool = False

    @model_validator(mode="after")
    def _unique(self) -> "LoftParams":
        if len(set(self.sketch_ids)) != len(self.sketch_ids):
            raise ValueError("each sketch once")
        return self


SkId = Annotated[int, Field(ge=1, le=10**6)]
Angle = Annotated[float, Field(allow_inf_nan=False, ge=-720, le=720)]


class _SkEntity(_Strict):
    id: SkId
    construction: bool = False  # helper geometry: constrains, never part of the profile


class SkPoint(_SkEntity):
    type: Literal["POINT"]
    point: Vec2


class SkLine(_SkEntity):
    type: Literal["LINE"]
    start: Vec2
    end: Vec2


class SkCircle(_SkEntity):
    type: Literal["CIRCLE"]
    center: Vec2
    radius: Size


class SkArc(SkCircle):
    type: Literal["ARC"]  # type: ignore[assignment]
    start_angle: Angle
    end_angle: Angle


class SkRef(_Strict):
    e: Annotated[int, Field(ge=-2, le=10**6)]  # -1 / -2: the fixed horizontal / vertical axis
    p: Literal["edge", "start", "end", "center"]


class SkConstraint(_Strict):
    type: Literal[tuple(solver.RULES)]  # type: ignore[valid-type]
    refs: Annotated[list[SkRef], Field(min_length=1, max_length=3)]
    value: Coord | None = None  # mm, or degrees for ANGLE


class SketchBody(_Strict):
    entities: Annotated[
        list[Annotated[SkPoint | SkLine | SkCircle | SkArc, Field(discriminator="type")]],
        Field(max_length=solver.MAX_ENTITIES),
    ]
    constraints: Annotated[list[SkConstraint], Field(max_length=solver.MAX_CONSTRAINTS)]


class SketchPlane(_Strict):
    origin: Vec3
    normal: Vec3
    xdir: Vec3  # the sketch's x axis, at right angles to the normal

    @model_validator(mode="after")
    def _axes(self) -> "SketchPlane":
        n, x = self.normal, self.xdir
        ln, lx = math.hypot(*n), math.hypot(*x)
        if ln < 1e-9 or lx < 1e-9 or abs(sum(a * b for a, b in zip(n, x))) > 1e-6 * ln * lx:
            raise ValueError("normal and xdir must be non-zero and perpendicular")
        return self


class HoleSpec(_Strict):
    """Hole wizard (Track 8 P2-4): a KS/ISO metric size; the server works out the diameters."""

    size: Literal[holes_std.SIZES]  # type: ignore[valid-type]
    kind: Literal["TAPPED", "CLEARANCE"]
    pitch: Size | None = None  # None = coarse
    fit: Literal["FINE", "MEDIUM", "COARSE"] = "MEDIUM"  # clearance series
    head: Literal["NONE", "COUNTERBORE", "COUNTERSINK"] = "NONE"
    head_diameter: Size | None = None  # None = the standard size
    head_depth: Size | None = None  # counterbore
    thread_depth: Size | None = None  # None = the hole depth

    @model_validator(mode="after")
    def _known(self) -> "HoleSpec":
        try:
            holes_std.resolve(self.model_dump())
        except holes_std.SpecError as e:
            raise ValueError(str(e)) from None
        return self


class HoleParams(_OnBody):
    """Positions on a plane (default: XY, drilled down from the top), or the points and circle
    centres of a sketch, which the hole consumes (FreeCAD PartDesign Hole)."""

    positions: Annotated[list[Vec2], Field(min_length=1, max_length=MAX_HOLES)] | None = None
    sketch_feature_id: SketchId | None = None
    plane: SketchPlane | None = None  # e.g. a picked face; drilled against its normal
    diameter: Size | None = None
    spec: HoleSpec | None = None
    depth: Size | None = None  # None = through all

    @model_validator(mode="after")
    def _sources(self) -> "HoleParams":
        if (self.positions is None) == (self.sketch_feature_id is None):
            raise ValueError("give either positions or sketch_feature_id")
        if self.sketch_feature_id is not None and self.plane is not None:
            raise ValueError("a sketch brings its own plane")
        if self.sketch_feature_id == self.target_feature_id:
            raise ValueError("the body and the sketch must differ")
        if (self.diameter is None) == (self.spec is None):
            raise ValueError("give either diameter or spec")
        if (
            self.spec
            and self.spec.thread_depth
            and self.depth
            and self.spec.thread_depth > self.depth
        ):
            raise ValueError("the thread cannot be deeper than the hole")
        return self


class SketchParams(SketchBody):
    """FreeCAD Sketcher: constrained 2D geometry on a plane; stored solved."""

    plane: SketchPlane


class PocketParams(ExtrudeParams):
    """FreeCAD Pocket: the extruded profile is cut out of an earlier body, which it consumes."""

    mode: Literal["LENGTH", "SYMMETRIC", "TWO_SIDED", "UP_TO_FACE", "THROUGH_ALL"] = "LENGTH"  # type: ignore[assignment]
    target_feature_id: FeatureId

    @model_validator(mode="after")
    def _target(self) -> "PocketParams":
        if self.target_feature_id == self.sketch_feature_id:
            raise ValueError("the body and the sketch must differ")
        return self


Radius0 = Annotated[float, Field(allow_inf_nan=False, ge=0, le=MAX_DISTANCE_MM)]


class StandardParams(_Strict):
    """Track 8 P4-5: a purchased standard part (KS fastener) of the assembly: no body, listed in
    the BOM and quoted at its purchase price (core/bom/standard.py)."""

    kind: Literal["HEX_BOLT", "SOCKET_BOLT", "HEX_NUT", "WASHER", "SPRING_WASHER"]
    size: Literal[holes_std.SIZES]  # type: ignore[valid-type]
    length: Annotated[float, Field(allow_inf_nan=False, gt=0, le=1000)] | None = None
    qty: Annotated[int, Field(ge=1, le=100_000)]

    @model_validator(mode="after")
    def _length(self) -> "StandardParams":
        if (self.length is not None) != self.kind.endswith("BOLT"):
            raise ValueError("bolts need a length, nuts and washers have none")
        return self


class PrimitiveParams(_Strict):
    """FreeCAD Part primitives (Track 8 P2-2): a box from its corner, round solids on an axis."""

    kind: Literal["BOX", "CYLINDER", "CONE", "SPHERE", "TORUS"]
    length: Size | None = None
    width: Size | None = None
    height: Size | None = None
    radius: Size | None = None
    radius2: Radius0 | None = None  # cone top radius (0 = pointed) / torus tube radius
    origin: Vec3 = [0, 0, 0]
    axis: Literal["X", "Y", "Z"] = "Z"

    @model_validator(mode="after")
    def _dims(self) -> "PrimitiveParams":
        need = {
            "BOX": ("length", "width", "height"),
            "CYLINDER": ("radius", "height"),
            "CONE": ("radius", "radius2", "height"),
            "SPHERE": ("radius",),
            "TORUS": ("radius", "radius2"),
        }[self.kind]
        if any(getattr(self, k) is None for k in need):
            raise ValueError(f"{self.kind} needs {', '.join(need)}")
        if self.kind == "CONE" and self.radius == self.radius2:
            raise ValueError("a cone needs two different radii (equal ones make a cylinder)")
        if self.kind == "TORUS" and not 0 < self.radius2 < self.radius:  # type: ignore[operator]
            raise ValueError("the tube radius must be smaller than the ring radius")
        return self


class SheetBaseParams(_Strict):
    """Track 9 Q3: a sheet from a sketch outline (straight lines, holes as circles / line loops)."""

    sketch_feature_id: SketchId
    thickness: Annotated[float, Field(allow_inf_nan=False, ge=0.05, le=50)]
    # None: the master data bend table for material x thickness (plan §23 S1), else R = T, K = 0.44
    bend_radius: Annotated[float, Field(allow_inf_nan=False, ge=0.01, le=1000)] | None = None
    k_factor: Annotated[float, Field(allow_inf_nan=False, ge=0, le=1)] | None = None
    material_code: Annotated[str, Field(pattern=r"^[A-Za-z0-9._\-]{1,40}$")] | None = None


class SheetStep(_Strict):
    """plan §23 S2: one more bend of a flange and the straight part after it."""

    angle: Annotated[float, Field(allow_inf_nan=False, ge=1, le=270)] = 90.0
    up: bool = True
    length: Annotated[float, Field(allow_inf_nan=False, ge=0, le=10_000)]
    radius: Annotated[float, Field(allow_inf_nan=False, ge=0.01, le=1000)] | None = None


class SheetRelief(_Strict):
    """plan §23 S2b: a slot beside an inset flange (RECT, ROUND) or a cut on the outline corner."""

    type: Literal["RECT", "ROUND", "CORNER_SQUARE", "CORNER_ROUND"]
    width: Annotated[float, Field(allow_inf_nan=False, ge=0.01, le=1000)]
    depth: Annotated[float, Field(allow_inf_nan=False, ge=0.01, le=1000)]


_Close = Annotated[float, Field(allow_inf_nan=False, ge=0, le=200)]


class SheetFlangeParams(_OnBody):
    """Track 9 Q3: a flange on one straight outline edge of the sheet it consumes. plan §23 S2:
    or on the side of an earlier flange's straight part (parent_*), with its own inside radius
    and further bends in a row (flange on flange, hem, jog, contour flange)."""

    edge: Annotated[int, Field(ge=0, le=499)] | None = None
    parent_feature_id: Annotated[int, Field(ge=1)] | None = None
    parent_step: Annotated[int, Field(ge=0, le=7)] = 0
    parent_side: Literal["LEFT", "RIGHT"] | None = None
    length: Annotated[
        float, Field(allow_inf_nan=False, ge=0, le=10_000)
    ]  # straight, after the bend
    angle: Annotated[float, Field(allow_inf_nan=False, ge=1, le=270)] = 90.0
    up: bool = True
    radius: Annotated[float, Field(allow_inf_nan=False, ge=0.01, le=1000)] | None = None
    inset_start: Annotated[float, Field(allow_inf_nan=False, ge=0, le=10_000)] = 0.0
    inset_end: Annotated[float, Field(allow_inf_nan=False, ge=0, le=10_000)] = 0.0
    steps: Annotated[list[SheetStep], Field(max_length=7)] = []
    style: Literal["FLANGE", "HEM", "JOG", "CONTOUR"] = "FLANGE"
    # S2b. profile: the flange's cross-section (underside, sharp corners; u out of the edge, v
    # up, from 0, 0) — it replaces angle / up / length / steps
    profile: (
        Annotated[
            list[tuple[Annotated[float, Field(allow_inf_nan=False, ge=-10_000, le=10_000)], ...]],
            Field(min_length=2, max_length=9),
        ]
        | None
    ) = None
    relief_start: SheetRelief | None = None
    relief_end: SheetRelief | None = None
    miter_start: _Close | None = None  # gap between the two mitred flanges, mm
    miter_end: _Close | None = None
    close_start: _Close | None = None  # closed corner: the first straight part runs this far on
    close_end: _Close | None = None

    @model_validator(mode="after")
    def _where(self) -> "SheetFlangeParams":
        on_side = self.parent_feature_id is not None
        if on_side == (self.edge is not None) or on_side != (self.parent_side is not None):
            raise ValueError("give an outline edge, or a parent flange and its side")
        if self.profile is not None and (self.steps or any(len(p) != 2 for p in self.profile)):
            raise ValueError("a profile is u, v points and replaces the steps")
        return self


class SheetCutParams(_OnBody):
    """plan §23 S3: a sketch's closed profiles cut out of the folded sheet, along the sketch
    normal: through the sheet under the sketch (default), to a depth either side, or through
    everything. Cutting across a bend unfolds to where the laser cuts it flat."""

    sketch_feature_id: SketchId
    depth: Annotated[float, Field(allow_inf_nan=False, ge=0.01, le=10_000)] | None = None
    through_all: bool = False


class SheetConvertParams(_OnBody):
    """plan §23 S3: a body of constant thickness with cylindrical bends (an imported sheet metal
    part) becomes sheet metal: flat pattern, DXF, bend and laser quote. Thickness: measured
    when not given."""

    thickness: Annotated[float, Field(allow_inf_nan=False, ge=0.05, le=50)] | None = None
    k_factor: Annotated[float, Field(allow_inf_nan=False, ge=0, le=1)] | None = None


class SheetUnfoldParams(_OnBody):
    """plan §23 S3b: switch the sheet to its flat pattern — cuts placed after this and before
    the matching SHEET_REFOLD are applied on the flat plane, then folded back."""


class SheetRefoldParams(_OnBody):
    """plan §23 S3b: return from the flat pattern to the folded state. Cuts between the
    SHEET_UNFOLD and this SHEET_REFOLD are marked ``flat: true`` in the spec."""


class SheetConvertSharpParams(_OnBody):
    """plan §23 S3b: a solid with sharp (un-filleted) bend edges becomes sheet metal.
    Each qualifying edge gets ``bend_radius``; ``rip_edges`` (indices) are gaps, not bends."""

    bend_radius: Annotated[float, Field(allow_inf_nan=False, ge=0.01, le=1000)]
    k_factor: Annotated[float, Field(allow_inf_nan=False, ge=0, le=1)] | None = None
    rip_edges: Annotated[list[Annotated[int, Field(ge=0, le=9999)]], Field(max_length=200)] = []


class SheetFormParams(_OnBody):
    """plan §23 S4: parametric forming tool on a sheet metal body.

    ``form_type`` is one of EMBOSS, DIMPLE, LOUVER, LANCE, BRIDGE, RIB.
    ``sketch_feature_id`` provides the forming profile; ``depth`` is form height (mm)."""

    form_type: Literal["EMBOSS", "DIMPLE", "RIB", "BRIDGE", "LOUVER", "LANCE"]
    sketch_feature_id: SketchId
    depth: Annotated[float, Field(allow_inf_nan=False, ge=0.1, le=100)]
    up: bool = True


# ── S6 Surface feature params ──────────────────────────────────────────────

class SurfLoftParams(_Strict):
    """Surface loft: open/closed wire sections → sheet or solid."""
    sketch_ids: Annotated[list[SketchId], Field(min_length=2, max_length=20)]
    ruled: bool = False
    solid: bool = False


class SurfSweepParams(_Strict):
    """Surface sweep: open profile along spine → sheet."""
    profile_sketch_id: SketchId
    path_sketch_id: SketchId
    frenet: bool = False


class SurfExtrudeParams(_Strict):
    """Surface extrude: open wire → shell."""
    sketch_feature_id: SketchId
    distance: Annotated[float, Field(allow_inf_nan=False, gt=0, le=1e6)]
    direction: Literal["+Z", "-Z"] = "+Z"


class SurfRevolveParams(_Strict):
    """Surface revolve: open wire → shell."""
    sketch_feature_id: SketchId
    axis_point: Annotated[list[float], Field(min_length=2, max_length=2)]
    axis_dir: Annotated[list[float], Field(min_length=2, max_length=2)]
    angle_deg: Annotated[float, Field(allow_inf_nan=False, gt=0, le=360)]


class SurfBoundaryParams(_Strict):
    """Boundary surface: 3-4 edge curves → face."""
    edge_point_sets: Annotated[list[list[list[float]]], Field(min_length=3, max_length=4)]
    continuity: Literal["G0", "G1", "G2"] = "G0"


class SurfFillParams(_Strict):
    """N-sided fill surface: 2-12 edge curves → face."""
    edge_point_sets: Annotated[list[list[list[float]]], Field(min_length=2, max_length=12)]
    continuity: Literal["G0", "G1", "G2"] = "G0"
    interior_points: list[list[float]] | None = None


class SurfOffsetParams(_OnBody):
    """Offset a face of an existing body."""
    offset: Annotated[float, Field(allow_inf_nan=False)]  # non-zero checked at runtime


# ── S7: Surface editing params ──────────────────────────────────────────


class SurfExtendParams(_OnBody):
    """Extend a surface face along its normal or linear direction."""
    distance: Annotated[float, Field(allow_inf_nan=False, gt=0, le=1e6)]
    mode: Literal["natural", "linear"] = "natural"


class SurfTrimParams(_OnBody):
    """Trim a surface by an infinite plane."""
    plane_origin: Annotated[list[float], Field(min_length=3, max_length=3)]
    plane_normal: Annotated[list[float], Field(min_length=3, max_length=3)]
    keep_side: Literal["positive", "negative"] = "positive"


class SurfSplitParams(_OnBody):
    """Split a surface into two halves by a plane (returns positive half)."""
    plane_origin: Annotated[list[float], Field(min_length=3, max_length=3)]
    plane_normal: Annotated[list[float], Field(min_length=3, max_length=3)]


class SurfKnitParams(_Strict):
    """Sew faces/shells together; if closed shell → promote to solid."""
    face_feature_ids: Annotated[list[FeatureId], Field(min_length=1, max_length=500)]
    tolerance: Annotated[float, Field(allow_inf_nan=False, gt=0, le=1.0)] = 1e-3


class SurfThickenParams(_OnBody):
    """Thicken a sheet body into a solid by offsetting."""
    thickness: Annotated[float, Field(allow_inf_nan=False)]  # non-zero checked at runtime


class SurfReplaceParams(_OnBody):
    """Replace a face in a body with a new face shape."""
    new_face_feature_id: FeatureId
    face_index: Annotated[int, Field(ge=0)]


class ImportParams(_Strict):
    # created only by POST /imports, never from client JSON: file_key points at a stored upload
    file_key: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    format: Literal["STEP", "IGES"]
    filename: Annotated[str, Field(min_length=1, max_length=200)]


Params = (
    SketchParams
    | StandardParams
    | SweepParams
    | LoftParams
    | PocketParams
    | PrimitiveParams
    | ExtrudeParams
    | RevolveParams
    | BooleanParams
    | ImportParams
    | FilletParams
    | ChamferParams
    | HoleParams
    | PatternParams
    | MirrorParams
    | ShellParams
    | DraftParams
    | SheetBaseParams
    | SheetFlangeParams
    | SheetCutParams
    | SheetConvertParams
    | SheetUnfoldParams
    | SheetRefoldParams
    | SheetConvertSharpParams
    | SheetFormParams
    | SurfLoftParams
    | SurfSweepParams
    | SurfExtrudeParams
    | SurfRevolveParams
    | SurfBoundaryParams
    | SurfFillParams
    | SurfOffsetParams
)
DRESSUP: dict[str, type[Params]] = {
    "FILLET": FilletParams,
    "CHAMFER": ChamferParams,
    "HOLE": HoleParams,
    "PATTERN": PatternParams,
    "MIRROR": MirrorParams,
    "SHELL": ShellParams,
    "DRAFT": DraftParams,
}
PARAMS: dict[str, type[Params]] = {
    "SKETCH": SketchParams,
    "EXTRUDE": ExtrudeParams,
    "POCKET": PocketParams,
    "PRIMITIVE": PrimitiveParams,
    "SWEEP": SweepParams,
    "LOFT": LoftParams,
    "REVOLVE": RevolveParams,
    "BOOLEAN": BooleanParams,
    "IMPORT": ImportParams,
    "STANDARD": StandardParams,
    "SHEET_BASE": SheetBaseParams,
    "SHEET_FLANGE": SheetFlangeParams,
    "SHEET_CUT": SheetCutParams,
    "SHEET_CONVERT": SheetConvertParams,
    "SHEET_UNFOLD": SheetUnfoldParams,
    "SHEET_REFOLD": SheetRefoldParams,
    "SHEET_CONVERT_SHARP": SheetConvertSharpParams,
    "SHEET_FORM": SheetFormParams,
    "SURF_LOFT": SurfLoftParams,
    "SURF_SWEEP": SurfSweepParams,
    "SURF_EXTRUDE": SurfExtrudeParams,
    "SURF_REVOLVE": SurfRevolveParams,
    "SURF_BOUNDARY": SurfBoundaryParams,
    "SURF_FILL": SurfFillParams,
    "SURF_OFFSET": SurfOffsetParams,
    "SURF_EXTEND": SurfExtendParams,
    "SURF_TRIM": SurfTrimParams,
    "SURF_SPLIT": SurfSplitParams,
    "SURF_KNIT": SurfKnitParams,
    "SURF_THICKEN": SurfThickenParams,
    "SURF_REPLACE": SurfReplaceParams,
    **DRESSUP,
}
# feature types that consume earlier features (a sketch-based EXTRUDE/REVOLVE consumes its sketch)
WITH_INPUTS = (
    "EXTRUDE", "REVOLVE", "POCKET", "SWEEP", "LOFT", "BOOLEAN", "SHEET_BASE", "SHEET_FLANGE",
    "SHEET_CUT", "SHEET_CONVERT", "SHEET_UNFOLD", "SHEET_REFOLD", "SHEET_CONVERT_SHARP", "SHEET_FORM",
    "SURF_LOFT", "SURF_SWEEP", "SURF_EXTRUDE", "SURF_REVOLVE", "SURF_OFFSET",
    "SURF_EXTEND", "SURF_TRIM", "SURF_SPLIT", "SURF_KNIT", "SURF_THICKEN", "SURF_REPLACE",
    *DRESSUP,
)  # fmt: skip
SHEET_TYPES = (
    "SHEET_BASE", "SHEET_FLANGE", "SHEET_CUT", "SHEET_CONVERT",
    "SHEET_UNFOLD", "SHEET_REFOLD", "SHEET_CONVERT_SHARP", "SHEET_FORM",
)  # fmt: skip
SHEET_ROOTS = ("SHEET_BASE", "SHEET_CONVERT", "SHEET_CONVERT_SHARP")
MAX_CHAIN_DEPTH = 64
MAX_IMPORT_BYTES = 100 * 1024**2  # FN-12
EXCHANGE_TIMEOUT_S = 120
Fmt = Literal["STEP", "IGES"]
EXTENSIONS: dict[str, Fmt] = {".step": "STEP", ".stp": "STEP", ".iges": "IGES", ".igs": "IGES"}
SUFFIX = {"STEP": "step", "IGES": "igs", "STL": "stl", "BREP": "brep"}
MEDIA = {
    "STEP": "model/step",
    "IGES": "model/iges",
    "STL": "model/stl",
    "BREP": "application/octet-stream",
}
IMPORT_SLOTS = threading.Semaphore(1)  # one import at a time (up to 100 MB, 120 s)
EXPORT_SLOTS = threading.Semaphore(1)  # one export at a time: with an import, both kernel workers
SLOT_WAIT_S = 60


class FeatureCreate(_Strict):
    feature_type: Literal[
        "SKETCH",
        "EXTRUDE",
        "REVOLVE",
        "POCKET",
        "PRIMITIVE",
        "SWEEP",
        "LOFT",
        "BOOLEAN",
        "FILLET",
        "CHAMFER",
        "HOLE",
        "PATTERN",
        "MIRROR",
        "SHELL",
        "DRAFT",
        "STANDARD",
        "SHEET_BASE",
        "SHEET_FLANGE",
        "SHEET_CUT",
        "SHEET_CONVERT",
        "SHEET_UNFOLD",
        "SHEET_REFOLD",
        "SHEET_CONVERT_SHARP",
        "SHEET_FORM",
        "SURF_LOFT",
        "SURF_SWEEP",
        "SURF_EXTRUDE",
        "SURF_REVOLVE",
        "SURF_BOUNDARY",
        "SURF_FILL",
        "SURF_OFFSET",
        "SURF_EXTEND",
        "SURF_TRIM",
        "SURF_SPLIT",
        "SURF_KNIT",
        "SURF_THICKEN",
        "SURF_REPLACE",
    ]
    params: dict[str, Any]
    # P2-5 rollback bar: insert right after this seq instead of at the end
    insert_after: Annotated[int, Field(ge=0)] | None = None


class FeatureUpdate(_Strict):
    params: dict[str, Any]


def _params(ftype: str, raw: dict[str, Any]) -> Params:
    try:
        return PARAMS[ftype].model_validate(raw)
    except ValidationError:
        raise GeomError("GEOM_INVALID_PARAM", "Invalid feature parameters") from None


def _var() -> Path:
    from backend.api import main  # late: main imports this module, tests patch main.VAR_DIR

    return main.VAR_DIR


def _brep_path(key: str) -> Path:
    return _var() / "brep" / f"{key}.brep"


def _sketch_inputs(p: Params) -> list[int]:
    """The inputs that must be sketches (every other input must be a body)."""
    if isinstance(p, (SweepParams, SurfSweepParams)):
        return [p.profile_sketch_id, p.path_sketch_id]
    if isinstance(p, (LoftParams, SurfLoftParams)):
        return list(p.sketch_ids)
    if isinstance(p, HoleParams):
        return [p.sketch_feature_id] if p.sketch_feature_id else []
    if isinstance(p, (SheetBaseParams, SheetCutParams, SheetFormParams)):
        return [p.sketch_feature_id]
    if isinstance(p, (SurfExtrudeParams, SurfRevolveParams)):
        return [p.sketch_feature_id]
    return [p.sketch_feature_id] if isinstance(p, _Sketch) and p.sketch_feature_id else []


def _inputs(p: Params) -> list[int]:
    if isinstance(p, (SweepParams, LoftParams, SheetBaseParams)):
        return _sketch_inputs(p)
    if isinstance(p, (SurfLoftParams, SurfSweepParams, SurfExtrudeParams, SurfRevolveParams)):
        return _sketch_inputs(p)
    if isinstance(p, _Sketch):
        ids = [p.sketch_feature_id] if p.sketch_feature_id else []
        return [*ids, p.target_feature_id] if isinstance(p, PocketParams) else ids
    if isinstance(p, BooleanParams):
        return [p.target_feature_id, p.tool_feature_id]
    if isinstance(p, (HoleParams, SheetCutParams, SheetFormParams)):
        return [p.target_feature_id, *_sketch_inputs(p)]
    if isinstance(p, (SurfBoundaryParams, SurfFillParams)):
        return []  # standalone: edge points in params, no sketch input
    return [p.target_feature_id] if isinstance(p, _OnBody) else []


def _key(db: Db, p: Params, suppressed: bool = False) -> str:
    # chain-aware: a BOOLEAN key includes its inputs' keys, so a changed input changes the output key
    ins = [db.get(Feature, i) for i in _inputs(p)]
    raw = json.dumps(p.model_dump(), sort_keys=True) + OCP.__version__ + ("S" if suppressed else "")
    raw += "".join(i.brep_key or "" for i in ins if i)
    return hashlib.sha256(raw.encode()).hexdigest()


def _import_path(key: str, fmt: str) -> Path:
    return _var() / "imports" / f"{key}.{SUFFIX[fmt]}"


def _lock_import(db: Db, p: "ImportParams") -> None:
    """Serialise write/build/commit and delete/count/unlink of one shared upload file, across
    documents (the document row lock does not cover two documents importing the same file)."""
    db.execute(select(func.pg_advisory_xact_lock(func.hashtext(p.file_key))))


def _exchange(fn: Any, *args: Any) -> Any:
    try:
        return run_kernel(fn, *args, timeout_s=EXCHANGE_TIMEOUT_S)
    except GeomError as e:
        if e.code == "GEOM_KERNEL_TIMEOUT":
            raise GeomError("STEP_TIMEOUT", "STEP/IGES processing timed out", 504) from None
        raise


def _store_brep(key: str, brep: bytes) -> None:
    path = _brep_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(brep)


def _feature_brep(db: Db, f: Feature, mem: dict[int, bytes], depth: int = 0) -> bytes:
    """Body of a feature: this request's fresh builds, else the disk cache, else rebuilt from params."""
    if f.feature_id in mem:
        return mem[f.feature_id]
    path = _brep_path(f.brep_key or "")
    if f.brep_key and path.is_file():
        return path.read_bytes()
    doc = db.get(Document, f.document_id)
    assert doc is not None
    brep, _ = _build_row(db, doc, f, _params(f.feature_type, f.params), mem, depth + 1)
    if f.brep_key:
        _store_brep(f.brep_key, brep)
    return brep


def _build(
    db: Db, doc: Document, ftype: str, p: Params, mem: dict[int, bytes], depth: int = 0
) -> tuple[bytes, dict[str, Any] | None]:
    """Resolve sketch handles / input bodies and run the kernel in the worker pool."""

    if depth > MAX_CHAIN_DEPTH:
        raise GeomError("GEOM_INVALID_PARAM", "Feature chain too deep")
    if isinstance(p, ImportParams):
        path = _import_path(p.file_key, p.format)
        if not path.is_file():
            raise GeomError("STEP_READ_FAILED", "Imported source file is missing")
        return _exchange(import_job, str(path), p.format)  # type: ignore[no-any-return]
    if isinstance(p, SHEET_PARAMS):
        return run_kernel(sheet_job, _sheet_spec(db, doc, p, mem, depth))  # type: ignore[no-any-return]
    # ── S6 surface features ────────────────────────────────────────────────
    if isinstance(p, SURF_PARAMS):
        return _build_surf(db, doc, p, mem, depth)
    if isinstance(p, (BooleanParams, _OnBody)):
        bodies = []
        for i in _inputs(p):
            inp = db.get(Feature, i)
            if inp is None or inp.document_id != doc.document_id:
                raise GeomError("GEOM_INVALID_PARAM", "Input feature not found in this document")
            bodies.append(_feature_brep(db, inp, mem, depth))
        if isinstance(p, BooleanParams):
            return run_kernel(boolean_job, p.op, *bodies)
        raw = p.model_dump()
        if isinstance(p, HoleParams) and p.sketch_feature_id:
            raw |= _hole_points(db, doc, p.sketch_feature_id)
        return run_kernel(dressup_job, ftype, bodies[0], raw)

    if isinstance(p, PrimitiveParams):
        return run_kernel(primitive_job, p.model_dump())  # type: ignore[no-any-return]
    if isinstance(p, (SketchParams, StandardParams)):
        return b"", None  # no body: the solved geometry / the part's spec lives in its params
    if isinstance(p, SweepParams):
        prof, pl = _sketch_geoms(db, doc, p.profile_sketch_id)
        spine, spine_pl = _sketch_geoms(db, doc, p.path_sketch_id)
        return run_kernel(sweep_job, prof, pl, spine, spine_pl, p.frenet)  # type: ignore[no-any-return]
    if isinstance(p, LoftParams):
        sections = [_sketch_geoms(db, doc, i) for i in p.sketch_ids]
        return run_kernel(loft_job, sections, p.ruled)  # type: ignore[no-any-return]
    prof, plane = _profile(db, doc, p)
    if isinstance(p, RevolveParams):
        return run_kernel(  # type: ignore[no-any-return]
            revolve_job, prof, tuple(p.axis_point), tuple(p.axis_dir), p.angle_deg, plane
        )
    assert isinstance(p, ExtrudeParams)
    if isinstance(p, PocketParams):
        target = db.get(Feature, p.target_feature_id)
        if target is None or target.document_id != doc.document_id:
            raise GeomError("GEOM_INVALID_PARAM", "Input feature not found in this document")
        body = _feature_brep(db, target, mem, depth)
        return run_kernel(pocket_job, prof, p.model_dump(), plane, body)  # type: ignore[no-any-return]
    return run_kernel(pad_job, prof, p.model_dump(), plane)  # type: ignore[no-any-return]


DEFAULT_K = 0.44
SHEET_PARAMS = (
    SheetBaseParams,
    SheetFlangeParams,
    SheetCutParams,
    SheetConvertParams,
    SheetUnfoldParams,
    SheetRefoldParams,
    SheetConvertSharpParams,
    SheetFormParams,
)

SURF_PARAMS = (
    SurfLoftParams,
    SurfSweepParams,
    SurfExtrudeParams,
    SurfRevolveParams,
    SurfBoundaryParams,
    SurfFillParams,
    SurfOffsetParams,
    SurfExtendParams,
    SurfTrimParams,
    SurfSplitParams,
    SurfKnitParams,
    SurfThickenParams,
    SurfReplaceParams,
)
SURF_TYPES = (
    "SURF_LOFT", "SURF_SWEEP", "SURF_EXTRUDE", "SURF_REVOLVE",
    "SURF_BOUNDARY", "SURF_FILL", "SURF_OFFSET",
    "SURF_EXTEND", "SURF_TRIM", "SURF_SPLIT", "SURF_KNIT", "SURF_THICKEN", "SURF_REPLACE",
)  # fmt: skip


def _build_surf(
    db: Db, doc: Document, p: Params, mem: dict[int, bytes], depth: int
) -> tuple[bytes, dict[str, Any] | None]:
    """Dispatch S6 surface feature builds to the kernel worker pool."""
    if isinstance(p, SurfLoftParams):
        sections = [_sketch_geoms(db, doc, i) for i in p.sketch_ids]
        return run_kernel(surface_loft_job, sections, p.ruled, p.solid)  # type: ignore[no-any-return]
    if isinstance(p, SurfSweepParams):
        prof, pl = _sketch_geoms(db, doc, p.profile_sketch_id)
        spine, spine_pl = _sketch_geoms(db, doc, p.path_sketch_id)
        return run_kernel(surface_sweep_job, prof, pl, spine, spine_pl, p.frenet)  # type: ignore[no-any-return]
    if isinstance(p, SurfExtrudeParams):
        geoms, plane = _sketch_geoms(db, doc, p.sketch_feature_id)
        return run_kernel(surface_extrude_job, geoms, p.distance, p.direction, plane)  # type: ignore[no-any-return]
    if isinstance(p, SurfRevolveParams):
        geoms, plane = _sketch_geoms(db, doc, p.sketch_feature_id)
        return run_kernel(  # type: ignore[no-any-return]
            surface_revolve_job, geoms, tuple(p.axis_point), tuple(p.axis_dir), p.angle_deg, plane
        )
    if isinstance(p, SurfBoundaryParams):
        return run_kernel(surface_boundary_job, p.edge_point_sets, p.continuity)  # type: ignore[no-any-return]
    if isinstance(p, SurfFillParams):
        return run_kernel(surface_fill_job, p.edge_point_sets, p.continuity, p.interior_points)  # type: ignore[no-any-return]
    if isinstance(p, SurfOffsetParams):
        target = db.get(Feature, p.target_feature_id)
        if target is None or target.document_id != doc.document_id:
            raise GeomError("GEOM_INVALID_PARAM", "Input feature not found in this document")
        body = _feature_brep(db, target, mem, depth)
        return run_kernel(surface_offset_job, body, p.offset)  # type: ignore[no-any-return]
    # ── S7 surface editing ──────────────────────────────────────────────
    if isinstance(p, (SurfExtendParams, SurfTrimParams, SurfSplitParams, SurfThickenParams)):
        target = db.get(Feature, p.target_feature_id)
        if target is None or target.document_id != doc.document_id:
            raise GeomError("GEOM_INVALID_PARAM", "Input feature not found in this document")
        body = _feature_brep(db, target, mem, depth)
        if isinstance(p, SurfExtendParams):
            return run_kernel(surface_extend_job, body, p.distance, p.mode)  # type: ignore[no-any-return]
        if isinstance(p, SurfTrimParams):
            return run_kernel(surface_trim_job, body, list(p.plane_origin), list(p.plane_normal), p.keep_side)  # type: ignore[no-any-return]
        if isinstance(p, SurfSplitParams):
            return run_kernel(surface_split_job, body, list(p.plane_origin), list(p.plane_normal))  # type: ignore[no-any-return]
        assert isinstance(p, SurfThickenParams)
        return run_kernel(surface_thicken_job, body, p.thickness)  # type: ignore[no-any-return]
    if isinstance(p, SurfKnitParams):
        brep_list: list[bytes] = []
        for fid in p.face_feature_ids:
            feat = db.get(Feature, fid)
            if feat is None or feat.document_id != doc.document_id:
                raise GeomError("GEOM_INVALID_PARAM", f"Feature {fid} not found in this document")
            brep_list.append(_feature_brep(db, feat, mem, depth))
        return run_kernel(surface_knit_job, brep_list, p.tolerance)  # type: ignore[no-any-return]
    if isinstance(p, SurfReplaceParams):
        target = db.get(Feature, p.target_feature_id)
        if target is None or target.document_id != doc.document_id:
            raise GeomError("GEOM_INVALID_PARAM", "Input feature not found in this document")
        body = _feature_brep(db, target, mem, depth)
        new_face_feat = db.get(Feature, p.new_face_feature_id)
        if new_face_feat is None or new_face_feat.document_id != doc.document_id:
            raise GeomError("GEOM_INVALID_PARAM", "Replacement face feature not found in this document")
        new_face = _feature_brep(db, new_face_feat, mem, depth)
        return run_kernel(surface_replace_job, body, new_face, p.face_index)  # type: ignore[no-any-return]
    raise GeomError("GEOM_INVALID_PARAM", f"Unknown surface feature: {type(p)}")


def _bend_values(db: Db, p: SheetBaseParams) -> dict[str, Any]:
    """Inside radius and K-factor of a sheet (plan §23 S1): the feature's own values, else the
    active master data bend table row for its material and thickness, else R = T and K = 0.44.
    A row with a 90-degree bend deduction gives the K-factor that unfolds a 90-degree bend to it:
    BA90 = 2 (R + T) - BD90 = pi/2 (R + K T)."""
    t, r, k = p.thickness, p.bend_radius, p.k_factor
    source = "FEATURE" if r is not None and k is not None else "DEFAULT"
    if (r is None or k is None) and p.material_code:
        from backend.api.routes_master import KST, active_version  # late, like mass below

        v = active_version(db, datetime.now(KST).date())
        row = None
        if v is not None:
            row = db.scalar(
                select(SheetBend).where(
                    SheetBend.version_id == v.version_id,
                    SheetBend.material_code == p.material_code,
                    func.abs(SheetBend.thickness_mm - Decimal(str(t))) < Decimal("0.0005"),
                )
            )
        if row is not None:
            source = f"TABLE:{v.version_code}" if v is not None else "TABLE"
            r = float(row.inner_radius_mm) if r is None else r
            if k is None:
                if row.k_factor is not None:
                    k = float(row.k_factor)
                else:
                    assert row.bd90_mm is not None
                    ba90 = 2 * (r + t) - float(row.bd90_mm)
                    k = (ba90 / (math.pi / 2) - r) / t
                    if not 0 <= k <= 1:
                        raise ApiError(
                            422,
                            "SHEET_TABLE_INVALID",
                            f"기준정보 판금 절곡 {p.material_code} t{t:g}: BD90이 K-factor 0~1을 벗어납니다",
                        )
    return {
        "bend_radius": t if r is None else r,
        "k_factor": DEFAULT_K if k is None else k,
        "bend_source": source,
    }


def _sheet_spec(
    db: Db, doc: Document, p: Params, mem: dict[int, bytes] | None = None, depth: int = 0
) -> dict[str, Any]:
    """The whole sheet a sheet feature stands for: its base (or the body turned into a sheet) and
    every flange and cut up to it, in order (built from scratch each time, so the flat pattern is
    exact). Suppressed flanges and cuts are left out."""
    flanges: list[dict[str, Any]] = []
    cuts: list[tuple[int, dict[str, Any]]] = []  # (flanges after it in the chain, the cut)
    forms: list[dict[str, Any]] = []
    mode: str | None = None  # the nearest UNFOLD/REFOLD before the feature: FLAT shows the blank
    cur: Params = p
    cur_id: int | None = None  # the feature being built or read has no parent pointing at it

    def placed_cuts() -> list[dict[str, Any]]:
        return [{**c, "after": len(flanges) - later} for later, c in cuts]

    def extras() -> dict[str, Any]:
        return {"forms": forms, "show_flat": mode == "FLAT"}

    for _ in range(MAX_CHAIN_DEPTH):
        if isinstance(cur, SheetBaseParams):
            geoms, plane = _sketch_geoms(db, doc, cur.sketch_feature_id)
            ids = _link_flanges(flanges)
            bend = _bend_values(db, cur)
            for fl in flanges:  # a drawn cross-section becomes the bends it stands for
                prof = fl.pop("profile", None)
                if prof is not None:
                    first, *rest = sheetmetal.profile_steps(
                        prof, cur.thickness, fl["radius"] or bend["bend_radius"]
                    )
                    fl.update(first, steps=rest)
            return {
                "flange_ids": ids,
                "outline": sheetmetal.outline(geoms),
                "thickness": cur.thickness,
                **bend,
                "flanges": flanges,
                "cuts": placed_cuts(),
                "plane": plane,
                **extras(),
            }
        if isinstance(cur, SheetConvertParams):
            if flanges:
                raise GeomError(
                    "SHEET_INVALID", "A body turned into sheet metal takes cuts, not flanges"
                )
            src = db.get(Feature, cur.target_feature_id)
            if src is None or src.document_id != doc.document_id:
                raise GeomError("GEOM_INVALID_PARAM", "Input feature not found in this document")
            brep = _feature_brep(db, src, {} if mem is None else mem, depth)
            k = DEFAULT_K if cur.k_factor is None else cur.k_factor
            spec = run_kernel(sheetmetal.convert_spec, brep, cur.thickness, k)
            return {
                **spec,
                "flange_ids": [],
                "cuts": placed_cuts(),
                "bend_source": "FEATURE" if cur.k_factor is not None else "DEFAULT",
                **extras(),
            }
        if isinstance(cur, SheetConvertSharpParams):
            if flanges:
                raise GeomError("SHEET_INVALID", "A sharp-edge conversion takes cuts, not flanges")
            src = db.get(Feature, cur.target_feature_id)
            if src is None or src.document_id != doc.document_id:
                raise GeomError("GEOM_INVALID_PARAM", "Input feature not found in this document")
            brep = _feature_brep(db, src, {} if mem is None else mem, depth)
            k = DEFAULT_K if cur.k_factor is None else cur.k_factor
            spec = run_kernel(
                sheetmetal.convert_sharp_spec,
                brep,
                cur.bend_radius,
                k,
                cur.rip_edges if cur.rip_edges else None,
            )
            return {
                **spec,
                "flange_ids": [],
                "cuts": placed_cuts(),
                "bend_source": "FEATURE" if cur.k_factor is not None else "DEFAULT",
                **extras(),
            }
        if isinstance(cur, SheetRefoldParams):  # cuts after it stay in folded space
            mode = mode or "FOLDED"
            for _later, c in cuts:
                c.setdefault("flat", False)
        elif isinstance(cur, SheetUnfoldParams):  # cuts after it (up to a refold) are on the blank
            mode = mode or "FLAT"
            for _later, c in cuts:
                c.setdefault("flat", True)
        elif isinstance(cur, SheetFormParams):
            geoms, plane = _sketch_geoms(db, doc, cur.sketch_feature_id)
            forms.insert(
                0,
                {
                    "form_type": cur.form_type,
                    "geoms": geoms,
                    "plane": plane,
                    "depth": cur.depth,
                    "up": cur.up,
                },
            )
        elif isinstance(cur, SheetCutParams):
            geoms, plane = _sketch_geoms(db, doc, cur.sketch_feature_id)
            cut = {"geoms": geoms, "plane": plane, "depth": cur.depth}
            cuts.insert(0, (len(flanges), {**cut, "through_all": cur.through_all}))
        else:
            assert isinstance(cur, SheetFlangeParams)
            flanges.insert(0, {**cur.model_dump(exclude={"target_feature_id"}), "_id": cur_id})
        row = db.get(Feature, cur.target_feature_id)
        if row is None or row.document_id != doc.document_id or row.feature_type not in SHEET_TYPES:
            raise GeomError("GEOM_INVALID_PARAM", "A flange or cut goes on a sheet metal feature")
        for _ in range(MAX_CHAIN_DEPTH):  # a suppressed flange or cut passes its sheet on
            if not row.suppressed or row.feature_type in SHEET_ROOTS:
                break
            row = db.get(Feature, row.params["target_feature_id"])
            if (
                row is None
                or row.document_id != doc.document_id
                or row.feature_type not in SHEET_TYPES
            ):
                raise GeomError(
                    "GEOM_INVALID_PARAM", "A flange or cut goes on a sheet metal feature"
                )
        cur, cur_id = _params(row.feature_type, row.params), row.feature_id
    raise GeomError("GEOM_INVALID_PARAM", "Feature chain too deep")


def _link_flanges(flanges: list[dict[str, Any]]) -> list[int | None]:
    """parent_feature_id -> the parent's place in the list (an earlier, unsuppressed flange).
    Returns the flanges' feature ids (None: the one being built)."""
    ids = [f.pop("_id") for f in flanges]
    index = {fid: i for i, fid in enumerate(ids)}
    for i, f in enumerate(flanges):
        pid, step, side = f.pop("parent_feature_id"), f.pop("parent_step"), f.pop("parent_side")
        if pid is None:
            continue
        j = index.get(pid)
        if j is None or j >= i:
            raise GeomError(
                "GEOM_INVALID_PARAM", "The parent flange is not part of this sheet (or suppressed)"
            )
        f["parent"] = {"flange": j, "step": step, "side": side}
    return ids


def _base(p: Params) -> int | None:
    """The body a feature changes; a suppressed feature passes it on unchanged (FreeCAD)."""
    return p.target_feature_id if isinstance(p, (_OnBody, PocketParams, BooleanParams)) else None


def _build_row(
    db: Db, doc: Document, f: Feature, p: Params, mem: dict[int, bytes], depth: int = 0
) -> tuple[bytes, dict[str, Any] | None]:
    """_build, honouring suppression: the base body (or nothing, for a body-making feature)."""
    if not f.suppressed:
        return _build(db, doc, f.feature_type, p, mem, depth)
    base = _base(p)
    row = db.get(Feature, base) if base else None
    if row is None:
        return b"", None
    return _feature_brep(db, row, mem, depth + 1), row.metrics


def _rebuild(db: Db, doc: Document, f: Feature, p: Params, mem: dict[int, bytes]) -> None:
    """Build f from p, then every later feature that depends on it, in seq order. A failure of f
    raises before any row changes; a failure of a dependent rolls the whole request back."""
    brep, metrics = _build_row(db, doc, f, p, mem)
    mem[f.feature_id] = brep
    f.params, f.brep_key, f.metrics = p.model_dump(), _key(db, p, f.suppressed), metrics
    f.updated_at = func.now()
    changed = {f.feature_id}
    later = db.scalars(
        select(Feature)
        .where(
            Feature.document_id == doc.document_id,
            Feature.feature_type.in_(WITH_INPUTS),
            Feature.seq > f.seq,
        )
        .order_by(Feature.seq)
    ).all()
    for g in later:
        gp = _params(g.feature_type, g.params)
        if not changed.intersection(_inputs(gp)):
            continue
        try:
            mem[g.feature_id], g.metrics = _build_row(db, doc, g, gp, mem)
        except GeomError as e:
            db.rollback()
            raise GeomError(
                e.code, e.message, e.http, {**(e.details or {}), "feature_id": g.feature_id}
            ) from None
        g.brep_key = _key(db, gp, g.suppressed)
        g.updated_at = func.now()
        changed.add(g.feature_id)
    for fid in changed:
        row = db.get(Feature, fid)
        assert row is not None and row.brep_key is not None
        if mem[fid]:  # a sketch (or a suppressed body-maker) has no body file
            _store_brep(row.brep_key, mem[fid])


def _sketch_geoms(
    db: Db, doc: Document, sketch_id: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """A sketch feature's real (non-construction) curves and its plane."""
    sk = db.get(Feature, sketch_id)
    if sk is None or sk.document_id != doc.document_id or sk.feature_type != "SKETCH":
        raise GeomError("GEOM_INVALID_PARAM", "Sketch not found in this document")
    sp = SketchParams.model_validate(sk.params)
    prof = [e.model_dump() for e in sp.entities if not e.construction and e.type != "POINT"]
    if not prof:
        raise GeomError("GEOM_OPEN_WIRE", "The sketch has no profile geometry")
    return prof, sp.plane.model_dump()


def _hole_points(db: Db, doc: Document, sketch_id: int) -> dict[str, Any]:
    """Hole positions from a sketch: its points and circle centres (construction ones too, as in
    FreeCAD), on the sketch plane."""
    sk = db.get(Feature, sketch_id)
    if sk is None or sk.document_id != doc.document_id or sk.feature_type != "SKETCH":
        raise GeomError("GEOM_INVALID_PARAM", "Sketch not found in this document")
    sp = SketchParams.model_validate(sk.params)
    pts = [
        e.point if isinstance(e, SkPoint) else e.center
        for e in sp.entities
        if isinstance(e, SkPoint) or type(e) is SkCircle
    ]
    if not 1 <= len(pts) <= MAX_HOLES:
        raise GeomError("GEOM_INVALID_PARAM", f"The sketch needs 1..{MAX_HOLES} points or circles")
    return {"positions": pts, "plane": sp.plane.model_dump()}


def _profile(
    db: Db, doc: Document, p: _Sketch
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Profile geoms and the sketch plane (None: DXF entities on the XY plane)."""
    from backend.api import main

    if p.sketch_feature_id is not None:
        return _sketch_geoms(db, doc, p.sketch_feature_id)
    assert p.source_revision_id is not None and p.handles is not None
    rev = db.get(Revision, p.source_revision_id)
    payload = main._read_json(p.source_revision_id) if rev else None
    if rev is None or rev.document_id != doc.document_id or payload is None:
        raise GeomError("GEOM_INVALID_PARAM", "Source revision not found in this document")
    geoms = {e["handle"]: e["geom"] for e in payload["entities"] if "geom" in e}
    missing = [h for h in p.handles if h not in geoms]
    if missing:
        raise GeomError(
            "GEOM_INVALID_PARAM",
            "Handles not found or not editable",
            details={"handles": missing},
        )
    return [geoms[h] for h in p.handles], None


def _solved(p: Params) -> Params:
    """A sketch is stored solved; a conflicting one is refused (FreeCAD shows it red)."""
    if not isinstance(p, SketchParams):
        return p
    raw = p.model_dump()
    res = run_kernel(solver.solve_job, raw["entities"], raw["constraints"])
    if res["status"] == "CONFLICT":
        raise GeomError(
            "SKETCH_CONFLICT",
            "Sketch constraints contradict each other",
            details={"constraints": res["conflicting"]},
        )
    try:  # the solution must still be in range
        return SketchParams.model_validate({**raw, "entities": res["entities"]})
    except ValidationError:
        raise GeomError("GEOM_INVALID_PARAM", "Solved sketch is out of range") from None


@router.post("/api/sketches/solve")
def solve_sketch(req: SketchBody, user: CurrentUser) -> Any:
    """Interactive solve while sketching (nothing stored): geometry, degrees of freedom, status."""
    need(user, "DESIGNER")
    raw = req.model_dump()
    return body(run_kernel(solver.solve_job, raw["entities"], raw["constraints"]))


def _consumed(db: Db, document_id: int, exclude: int | None = None) -> dict[int, int]:
    """body feature_id -> the BOOLEAN or machining feature_id that consumed it."""
    out: dict[int, int] = {}
    rows = db.scalars(
        select(Feature).where(
            Feature.document_id == document_id, Feature.feature_type.in_(WITH_INPUTS)
        )
    )
    for g in rows:
        if g.feature_id != exclude:
            for i in _inputs(_params(g.feature_type, g.params)):
                out[i] = g.feature_id
    return out


def _check_inputs(
    db: Db, doc: Document, p: Params, seq: int, own: int | None, free: frozenset[int] = frozenset()
) -> None:
    def bad(msg: str) -> GeomError:
        return GeomError("GEOM_INVALID_PARAM", msg)

    if isinstance(p, BooleanParams) and p.target_feature_id == p.tool_feature_id:
        raise bad("target and tool must differ")
    taken = _consumed(db, doc.document_id, own)
    for i in _inputs(p):
        inp = db.get(Feature, i)
        if inp is None or inp.document_id != doc.document_id:
            raise bad("Input feature not found in this document")
        if inp.seq >= seq:
            raise bad("Input feature must be earlier than the feature using it")
        if (inp.feature_type == "SKETCH") != (i in _sketch_inputs(p)):
            raise bad("Only the sketch input takes a sketch, and it takes nothing else")
        if i in taken and i not in free:
            raise bad("Input feature is already used by another feature")


def _out(f: Feature, consumed: dict[int, int]) -> dict[str, Any]:
    cols = (
        *("feature_id", "seq", "feature_type", "params", "status", "error_code", "metrics"),
        "suppressed",
    )
    ins = _inputs(_params(f.feature_type, f.params))
    return {
        **pick(f, *cols),
        **pick(f, "created_at", "updated_at"),
        "visible": f.feature_id not in consumed,
        "inputs": ins,
    }


def _feature(db: Db, user: User, feature_id: int, lock: bool = False) -> tuple[Feature, Document]:
    f = db.get(Feature, feature_id)
    if f is None:
        raise ApiError(404, "FEATURE_NOT_FOUND", "Feature not found")
    try:
        return f, get_document(db, user, f.document_id, lock)
    except ApiError:
        raise ApiError(404, "FEATURE_NOT_FOUND", "Feature not found") from None


def _changed(doc: Document) -> None:
    """A 3D change marks the document as being worked on, like a DXF save does."""
    doc.status = "DRAFT"


def _chain_depth(db: Db, ids: list[int], depth: int = 0) -> int:
    """Longest input chain below these features (checked at create: rebuilds recurse that deep)."""
    if depth > MAX_CHAIN_DEPTH:
        return depth
    best = depth
    for i in ids:
        f = db.get(Feature, i)
        if f is not None and f.feature_type in WITH_INPUTS:
            best = max(
                best, _chain_depth(db, _inputs(_params(f.feature_type, f.params)), depth + 1)
            )
    return best


@router.get("/api/documents/{document_id}/features")
def list_features(document_id: int, user: CurrentUser, db: Db) -> Any:
    get_document(db, user, document_id)
    rows = db.scalars(
        select(Feature).where(Feature.document_id == document_id).order_by(Feature.seq)
    ).all()
    consumed = _consumed(db, document_id)
    return body({"items": [_out(f, consumed) for f in rows], "total": len(rows)})


@router.post("/api/documents/{document_id}/features")
def create_feature(document_id: int, req: FeatureCreate, user: CurrentUser, db: Db) -> Any:
    doc = get_document(db, user, document_id, lock=True)
    need(user, "DESIGNER")
    p = _solved(_params(req.feature_type, req.params))
    last = db.scalar(select(func.max(Feature.seq)).where(Feature.document_id == document_id)) or 0
    # race-free: the document row lock is held
    seq = last + 1 if req.insert_after is None else min(req.insert_after, last) + 1
    # inserted before later features (FreeCAD rollback): a body a later feature uses is taken over,
    # and that feature is re-linked to the new one (Box -> Fillet becomes Box -> Hole -> Fillet)
    taken = _consumed(db, document_id) if seq <= last else {}
    body_ins = [i for i in _inputs(p) if i not in _sketch_inputs(p)]
    relink = {i: taken[i] for i in body_ins if i in taken}
    if len(set(relink.values())) < len(relink):
        raise GeomError("GEOM_INVALID_PARAM", "Cannot insert: one later feature uses both inputs")
    if relink:
        first = db.scalar(
            select(func.min(Feature.seq)).where(Feature.feature_id.in_(relink.values()))
        )
        if first is None or first < seq:
            raise GeomError(
                "GEOM_INVALID_PARAM", "Input feature is already used by another feature"
            )
    if _inputs(p):
        _check_inputs(db, doc, p, seq, None, frozenset(relink))
        if _chain_depth(db, _inputs(p)) >= MAX_CHAIN_DEPTH:
            raise GeomError("GEOM_INVALID_PARAM", "Feature chain too deep")
    brep, metrics = _build(db, doc, req.feature_type, p, {})
    if seq <= last:  # make room; two steps keep (document_id, seq) unique in between
        later = (Feature.document_id == document_id) & (Feature.seq >= seq)
        db.execute(update(Feature).where(later).values(seq=-Feature.seq - 1))
        db.execute(
            update(Feature)
            .where(Feature.document_id == document_id, Feature.seq < 0)
            .values(seq=-Feature.seq)
        )
    key = _key(db, p)
    f = Feature(
        document_id=document_id,
        seq=seq,
        feature_type=req.feature_type,
        params=p.model_dump(),
        brep_key=key,
        metrics=metrics,
        created_by=user.user_id,
    )
    db.add(f)
    db.flush()
    if brep:
        _store_brep(key, brep)
    mem = {f.feature_id: brep}
    for old, user_id in relink.items():
        g = db.get(Feature, user_id)
        assert g is not None
        raw = {
            k: f.feature_id if k in ("target_feature_id", "tool_feature_id") and v == old else v
            for k, v in g.params.items()
        }
        _rebuild(db, doc, g, _params(g.feature_type, raw), mem)
    _changed(doc)
    db.commit()
    return body(_out(f, _consumed(db, document_id)))


class MoveRequest(_Strict):
    direction: Literal["UP", "DOWN"]


@router.post("/api/features/{feature_id}/move")
def move_feature(feature_id: int, req: MoveRequest, user: CurrentUser, db: Db) -> Any:
    """Swap with the neighbouring feature in the tree; a feature stays after its inputs. The
    bodies do not change (each feature is built from its inputs, not from the order)."""
    f, doc = _feature(db, user, feature_id, lock=True)
    need(user, "DESIGNER")
    up = req.direction == "UP"
    q = select(Feature).where(
        Feature.document_id == doc.document_id, Feature.seq < f.seq if up else Feature.seq > f.seq
    )
    other = db.scalars(q.order_by(Feature.seq.desc() if up else Feature.seq).limit(1)).first()
    if other is None:
        raise ApiError(409, "FEATURE_NOT_MOVABLE", "Already at that end of the tree")
    first, second = (other, f) if up else (f, other)
    if first.feature_id in _inputs(_params(second.feature_type, second.params)):
        raise ApiError(409, "FEATURE_NOT_MOVABLE", "A feature must stay after the features it uses")
    a, b = f.seq, other.seq
    f.seq = -1
    db.flush()
    other.seq = a
    db.flush()
    f.seq = b
    _changed(doc)
    db.commit()
    return body(_out(f, _consumed(db, doc.document_id)))


@router.patch("/api/features/{feature_id}")
def update_feature(feature_id: int, req: FeatureUpdate, user: CurrentUser, db: Db) -> Any:
    f, doc = _feature(db, user, feature_id, lock=True)
    need(user, "DESIGNER")
    if f.feature_type == "IMPORT":
        raise ApiError(409, "FEATURE_NOT_EDITABLE", "Imported bodies have no parameters to edit")
    p = _solved(_params(f.feature_type, req.params))
    if _inputs(p):
        _check_inputs(db, doc, p, f.seq, f.feature_id)
    _rebuild(db, doc, f, p, {})
    _changed(doc)
    db.commit()
    return body(_out(f, _consumed(db, doc.document_id)))


class SuppressRequest(_Strict):
    suppressed: bool


@router.post("/api/features/{feature_id}/suppress")
def suppress_feature(feature_id: int, req: SuppressRequest, user: CurrentUser, db: Db) -> Any:
    """FreeCAD Suppress: the feature is skipped; a machining/Boolean/Pocket feature passes its
    base body on, a body-making one has no body (refused while another feature uses it)."""
    f, doc = _feature(db, user, feature_id, lock=True)
    need(user, "DESIGNER")
    p = _params(f.feature_type, f.params)
    if req.suppressed and _base(p) is None and f.feature_id in _consumed(db, doc.document_id):
        raise ApiError(
            409, "FEATURE_IN_USE", "Another feature uses this one's body; suppress that one first"
        )
    if f.suppressed != req.suppressed:
        f.suppressed = req.suppressed
        _rebuild(db, doc, f, p, {})
        _changed(doc)
    db.commit()
    return body(_out(f, _consumed(db, doc.document_id)))


@router.delete("/api/features/{feature_id}")
def delete_feature(feature_id: int, user: CurrentUser, db: Db) -> Any:
    f, doc = _feature(db, user, feature_id, lock=True)
    need(user, "DESIGNER")
    if f.feature_id in _consumed(db, doc.document_id):
        raise ApiError(
            409, "FEATURE_IN_USE", "Feature is an input of another feature; delete that first"
        )
    key = f.brep_key
    src = _params(f.feature_type, f.params)
    if isinstance(src, ImportParams):
        _lock_import(db, src)
    db.delete(f)
    db.flush()
    # BREP files are a content-addressed cache: keep it while another feature still uses the key
    if key and db.scalar(select(func.count()).where(Feature.brep_key == key)) == 0:
        _brep_path(key).unlink(missing_ok=True)
    if isinstance(src, ImportParams):
        _drop_import(db, src)
    _changed(doc)
    db.commit()
    return body({"feature_id": feature_id})


@router.get("/api/features/{feature_id}/mesh")
def feature_mesh(feature_id: int, user: CurrentUser, db: Db) -> Any:
    f, _ = _feature(db, user, feature_id)
    if f.metrics is None or f.brep_key is None:
        raise ApiError(409, "FEATURE_NOT_BUILT", "Feature has no geometry")
    brep = _feature_brep(db, f, {})  # cache miss: params JSON is the source of truth
    mesh = run_kernel(mesh_job, brep)
    return body({**mesh, "bbox": f.metrics["bbox"]})


@router.get("/api/features/{feature_id}/sheet")
def feature_sheet(
    feature_id: int,
    user: CurrentUser,
    db: Db,
    format: Annotated[Literal["json", "dxf"], Query()] = "json",
) -> Any:
    """Track 9 Q3: a sheet feature's outline edges (for flanges) and flat pattern, or the blank as
    DXF (CUT / BEND layers) for the 2D quote and the laser."""
    f, doc = _feature(db, user, feature_id)
    if f.feature_type not in SHEET_TYPES:
        raise ApiError(409, "NOT_SHEET", "This feature is not sheet metal")
    spec = _sheet_spec(db, doc, _params(f.feature_type, f.params))
    if format == "dxf":
        name = f"{doc.doc_no}-F{f.feature_id}"
        return Response(
            run_kernel(sheetmetal.flat_dxf, spec, name),  # S1: unfolding builds the body
            media_type="image/vnd.dxf",
            headers={"Content-Disposition": f'attachment; filename="{name}-flat.dxf"'},
        )
    keep = ("thickness", "bend_radius", "k_factor", "bend_source")
    ids = [i if i is not None else f.feature_id for i in spec["flange_ids"]]
    return body(
        {
            "feature_id": f.feature_id,
            **{k: spec[k] for k in keep},
            # each flange with its feature id: the side of its straight parts can take a flange
            "flanges": [
                {**fl, "feature_id": i} for fl, i in zip(spec["flanges"], ids, strict=True)
            ],
            # a body turned into a sheet has no outline to put flanges on
            "edges": sheetmetal.edges(spec["outline"]) if spec["outline"] else [],
            "flat": run_kernel(sheetmetal.sheet_info, spec),
        }
    )


def _drop_import(db: Db, p: ImportParams) -> None:
    """Uploads are content-addressed too: remove the file once no IMPORT feature points at it."""
    users = select(func.count()).where(
        Feature.feature_type == "IMPORT", Feature.params["file_key"].astext == p.file_key
    )
    if db.scalar(users) == 0:
        _import_path(p.file_key, p.format).unlink(missing_ok=True)


@router.post("/api/documents/{document_id}/imports")
# sync def: the kernel call blocks; FastAPI runs it in the threadpool
def import_file(
    document_id: int,
    user: CurrentUser,
    db: Db,
    file: UploadFile = File(...),  # noqa: B008
) -> Any:
    doc = get_document(db, user, document_id, lock=True)
    need(user, "DESIGNER")
    # ends up in exports and messages: no control or text-direction characters
    name = re.sub(
        r"[\x00-\x1f\x7f\u200e\u200f\u202a-\u202e\u2066-\u2069]", "", Path(file.filename or "").name
    )
    fmt = EXTENSIONS.get(Path(name).suffix.lower())
    if fmt is None or not 1 <= len(name) <= 200:
        raise GeomError("STEP_INVALID_FILE", "Only .step/.stp/.iges/.igs files are accepted")
    data = file.file.read(MAX_IMPORT_BYTES + 1)
    if len(data) > MAX_IMPORT_BYTES:
        raise ApiError(413, "FILE_TOO_LARGE", "File exceeds 100 MB")
    check_file(data, fmt)  # cheap magic/external-reference check before the kernel
    p = ImportParams(file_key=hashlib.sha256(data).hexdigest(), format=fmt, filename=name)
    # one import at a time: each holds up to 100 MB and a kernel worker for up to 120 s
    # ponytail: the document row lock + DB connection stay held during the build; move the
    # kernel call out of the transaction if imports of one document start queueing
    if not IMPORT_SLOTS.acquire(timeout=SLOT_WAIT_S):
        raise ApiError(503, "SERVER_BUSY", "Server busy, retry later")
    try:
        _lock_import(db, p)
        path = _import_path(p.file_key, fmt)
        if not path.is_file():  # content-addressed: an existing file has these exact bytes
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)  # atomic: readers never see a partial file
        del data
        try:
            brep, metrics = _build(db, doc, "IMPORT", p, {})
        except BaseException:
            with contextlib.suppress(Exception):  # never mask the original error
                _drop_import(db, p)
            raise
    finally:
        IMPORT_SLOTS.release()
    seq = (
        db.scalar(select(func.max(Feature.seq)).where(Feature.document_id == document_id)) or 0
    ) + 1  # race-free: the document row lock is held
    key = _key(db, p)
    f = Feature(
        document_id=document_id,
        seq=seq,
        feature_type="IMPORT",
        params=p.model_dump(),
        brep_key=key,
        metrics=metrics,
        created_by=user.user_id,
    )
    db.add(f)
    db.flush()
    _store_brep(key, brep)
    _changed(doc)
    db.commit()
    return body(_out(f, _consumed(db, document_id)))


DRAWING_TIMEOUT_S = 120  # hidden line removal of a large part takes a while


Label = Annotated[str, Field(pattern=r"^[A-Z]$")]
MAX_EXTRA_VIEWS = 6
ROUGHNESS = (0.8, 1.6, 3.2, 6.3, 12.5, 25.0)


class SectionSpec(_Strict):
    """Full section on the plane axis = offset (KS B 0001), labelled A-A."""

    label: Label
    axis: Literal["X", "Y", "Z"]
    offset: Coord


class DetailSpec(_Strict):
    """A circle of a view (sheet coordinates of that view) enlarged by factor."""

    label: Label
    view: Literal["FRONT", "TOP", "RIGHT"]
    center: Vec2
    radius: Size
    factor: Annotated[float, Field(gt=1, le=10)]


class DrawingRequest(_Strict):
    """Track 8 P3: a production drawing from the 3D bodies. Left out = the stored setting
    (regenerate) or the default."""

    views: (
        Annotated[list[Literal["FRONT", "TOP", "RIGHT", "ISO"]], Field(min_length=1, max_length=4)]
        | None
    ) = None
    projection: Literal["THIRD", "FIRST"] | None = None
    sheet: Literal["A4", "A3", "A2", "A1", "A0"] | None = None
    portrait: bool | None = None
    scale: Annotated[float, Field(ge=1e-4, le=10)] | None = None  # None = fit the sheet
    sections: Annotated[list[SectionSpec], Field(max_length=MAX_EXTRA_VIEWS)] | None = None
    details: Annotated[list[DetailSpec], Field(max_length=MAX_EXTRA_VIEWS)] | None = None
    dimensions: bool | None = None  # overall sizes and hole callouts
    tolerance: Literal["f", "m", "c", "v"] | None = None  # KS B ISO 2768-1 general tolerance note
    roughness: float | None = None  # Ra of surfaces not marked: one of ROUGHNESS
    material: (
        Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[^\x00-\x1f\x7f]+$")] | None
    ) = None

    @model_validator(mode="after")
    def _unique(self) -> "DrawingRequest":
        if self.views and len(set(self.views)) != len(self.views):
            raise ValueError("each view once")
        if self.roughness is not None and self.roughness not in ROUGHNESS:
            raise ValueError("roughness must be a KS B ISO 1302 preferred Ra value")
        labels = [x.label for x in (self.sections or []) + (self.details or [])]
        if len(set(labels)) != len(labels):
            raise ValueError("each section or detail label once")
        return self


DRAWING_DEFAULT = {
    "views": ["FRONT", "TOP", "RIGHT", "ISO"],
    "projection": "THIRD",
    "sheet": "A3",
    "portrait": False,
    "scale": None,
    "sections": [],
    "details": [],
    "dimensions": True,
    "tolerance": "m",
    "roughness": None,
    "material": None,
}


def _holes_of(db: Db, bodies: list[Feature]) -> list[dict[str, Any]]:
    """Metrics of the HOLE features in the history of the drawn bodies (for the callouts)."""
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    stack = [f.feature_id for f in bodies]
    while stack:
        f = db.get(Feature, stack.pop())
        if f is None or f.feature_id in seen:
            continue
        seen.add(f.feature_id)
        if f.feature_type == "HOLE" and not f.suppressed and f.metrics and f.metrics.get("holes"):
            out.append({**f.metrics["holes"], "feature_id": f.feature_id})
        stack += _inputs(_params(f.feature_type, f.params))
    return out


def _given(req: DrawingRequest) -> dict[str, Any]:
    """The fields set in a request as plain values; a null one is back to its default."""
    out = req.model_dump(include=req.model_fields_set)
    return {k: DRAWING_DEFAULT[k] if v is None else v for k, v in out.items()}


@router.post("/api/documents/{document_id}/drawing")
def make_drawing(document_id: int, req: DrawingRequest, user: CurrentUser, db: Db) -> Any:
    """3D -> 2D: the visible bodies projected into a new DXF revision of the same document.
    A drawing made from 3D is regenerated in place: its AXGEN_* layers are redrawn, what the
    user added stays. A document whose drawing was not made from 3D is refused. A field sent
    (null = back to the default) replaces the stored setting; one left out keeps it."""
    from backend.api import main
    from core.dxf.drawing import drawing_job

    need(user, "DESIGNER")
    doc = get_document(db, user, document_id, lock=True)
    rows = db.scalars(
        select(Feature).where(Feature.document_id == document_id).order_by(Feature.seq)
    ).all()
    consumed = _consumed(db, document_id)
    shown = [f for f in rows if f.feature_id not in consumed and f.status == "OK" and f.metrics]
    if not shown:
        raise ApiError(409, "MODEL_EMPTY", "The document has no 3D bodies to draw")
    current, found = None, None
    stored: dict[str, Any] = {}
    if doc.current_revision_id:
        # the settings as the upload worker read them (no DXF parsing in the API process); they
        # come from the file, so they are checked like a request (BUG-50)
        payload = main._read_json(doc.current_revision_id) or {}
        found = payload.get("drawing")
        if not isinstance(found, dict) and payload.get("entities"):
            raise ApiError(
                409, "DRAWING_NOT_GENERATED", "This drawing was not made from the 3D model"
            )
        if not isinstance(found, dict):
            found = None
        else:
            try:
                kept = DrawingRequest.model_validate(
                    {k: v for k, v in found.items() if k in DRAWING_DEFAULT}
                )
            except ValidationError:
                raise ApiError(
                    409, "DRAWING_CONFIG_INVALID", "The drawing's stored settings are invalid"
                ) from None
            stored = _given(kept)
        current = (main.VAR_DIR / "uploads" / f"{doc.current_revision_id}.dxf").read_bytes()
    asked = _given(req)
    cfg = {k: asked.get(k, stored.get(k, d)) for k, d in DRAWING_DEFAULT.items()}
    if any(d["view"] not in cfg["views"] for d in cfg["details"]):
        raise GeomError("GEOM_INVALID_PARAM", "A detail must enlarge a view that is on the drawing")
    breps = [_feature_brep(db, f, {}) for f in shown]
    holes = _holes_of(db, shown) if cfg["dimensions"] else []
    data = run_kernel(drawing_job, breps, cfg, current, holes, timeout_s=DRAWING_TIMEOUT_S)
    note = "3D에서 도면 다시 생성" if found is not None else "3D에서 도면 생성"
    return main._save_dxf(db, user, doc, data, note, [])


class Pick(_Strict):
    what: Literal["EDGE", "FACE"]
    sig: EdgeRef | FaceRef


class MeasureRequest(_Strict):
    picks: Annotated[list[Pick], Field(max_length=MAX_PICKS)] = []


@router.post("/api/features/{feature_id}/measure")
def measure_feature(feature_id: int, req: MeasureRequest, user: CurrentUser, db: Db) -> Any:
    """FreeCAD Measure (P2-5): lengths, areas, distance and angle of picks; volume, area and centre
    of mass of the body. Mass = volume x a material's density, from GET /api/materials."""
    f, _ = _feature(db, user, feature_id)
    if f.metrics is None or f.brep_key is None:
        raise ApiError(409, "FEATURE_NOT_BUILT", "Feature has no geometry")
    brep = _feature_brep(db, f, {})
    picks = [p.model_dump(exclude_none=True) for p in req.picks]
    return body(run_kernel(measure_job, brep, picks))


@router.get("/api/materials")
def list_materials(user: CurrentUser, db: Db) -> Any:
    """Materials of today's active master data, for the mass in 3D measurement (any member)."""
    from backend.api.routes_master import KST, active_version

    v = active_version(db, datetime.now(KST).date())
    rows = (
        []
        if v is None
        else db.scalars(
            select(Material)
            .where(Material.version_id == v.version_id)
            .order_by(Material.material_code)
        ).all()
    )
    items = [
        {
            "material_code": m.material_code,
            "material_name": m.material_name,
            "density_g_cm3": float(m.density_g_cm3),
        }
        for m in rows
    ]
    return body({"items": items, "total": len(items)})


# ═══════════════════════════════════════════════════════════════════════════
# S8: Surface quality analysis (read-only)
# ═══════════════════════════════════════════════════════════════════════════

Samples = Annotated[int, Field(ge=2, le=200)]


class CurvatureMapRequest(_Strict):
    n_u: Samples = 20
    n_v: Samples = 20


class ContinuityCheckRequest(_Strict):
    n_samples: Samples = 20
    g0_tol: Annotated[float, Field(allow_inf_nan=False, gt=0, le=1.0)] = 1e-3
    g1_tol_deg: Annotated[float, Field(allow_inf_nan=False, gt=0, le=90)] = 1.0
    g2_tol_pct: Annotated[float, Field(allow_inf_nan=False, gt=0, le=100)] = 5.0


class DraftAnalysisRequest(_Strict):
    pull_direction: Vec3
    n_u: Samples = 10
    n_v: Samples = 10


class ZebraStripesRequest(_Strict):
    stripe_dir: Vec3 | None = None
    n_u: Samples = 40
    n_v: Samples = 40


@router.post("/api/features/{feature_id}/curvature-map")
def curvature_map_endpoint(feature_id: int, req: CurvatureMapRequest, user: CurrentUser, db: Db) -> Any:
    f, _ = _feature(db, user, feature_id)
    if f.brep_key is None:
        raise ApiError(409, "FEATURE_NOT_BUILT", "Feature has no geometry")
    brep = _feature_brep(db, f, {})
    return body(run_kernel(curvature_map_job, brep, req.n_u, req.n_v))


@router.post("/api/features/{feature_id}/continuity-check")
def continuity_check_endpoint(
    feature_id: int, req: ContinuityCheckRequest, user: CurrentUser, db: Db
) -> Any:
    f, _ = _feature(db, user, feature_id)
    if f.brep_key is None:
        raise ApiError(409, "FEATURE_NOT_BUILT", "Feature has no geometry")
    brep = _feature_brep(db, f, {})
    return body(run_kernel(continuity_check_job, brep, req.n_samples, req.g0_tol, req.g1_tol_deg, req.g2_tol_pct))


@router.post("/api/features/{feature_id}/draft-analysis")
def draft_analysis_endpoint(
    feature_id: int, req: DraftAnalysisRequest, user: CurrentUser, db: Db
) -> Any:
    f, _ = _feature(db, user, feature_id)
    if f.brep_key is None:
        raise ApiError(409, "FEATURE_NOT_BUILT", "Feature has no geometry")
    brep = _feature_brep(db, f, {})
    return body(run_kernel(draft_analysis_job, brep, list(req.pull_direction), req.n_u, req.n_v))


@router.post("/api/features/{feature_id}/zebra-stripes")
def zebra_stripes_endpoint(
    feature_id: int, req: ZebraStripesRequest, user: CurrentUser, db: Db
) -> Any:
    f, _ = _feature(db, user, feature_id)
    if f.brep_key is None:
        raise ApiError(409, "FEATURE_NOT_BUILT", "Feature has no geometry")
    brep = _feature_brep(db, f, {})
    sd = list(req.stripe_dir) if req.stripe_dir else None
    return body(run_kernel(zebra_stripes_job, brep, sd, req.n_u, req.n_v))


def _export_name(f: Feature) -> str:
    p = _params(f.feature_type, f.params)
    if isinstance(p, ImportParams):
        return Path(p.filename).stem
    kind = (
        p.op
        if isinstance(p, BooleanParams)
        else p.kind
        if isinstance(p, PrimitiveParams)
        else f.feature_type
    )
    return f"{kind.title()}{f.seq:03d}"


@router.get("/api/documents/{document_id}/model")
def export_file(
    document_id: int,
    user: CurrentUser,
    db: Db,
    format: Annotated[Literal["STEP", "IGES", "STL", "BREP"], Query()] = "STEP",
) -> Any:
    doc = get_document(db, user, document_id)  # Track 7: no approval gate, members may export
    rows = db.scalars(
        select(Feature).where(Feature.document_id == document_id).order_by(Feature.seq)
    ).all()
    consumed = _consumed(db, document_id)
    shown = [f for f in rows if f.feature_id not in consumed and f.status == "OK" and f.metrics]
    if not shown:
        raise ApiError(409, "MODEL_EMPTY", "The document has no 3D bodies to export")
    # ponytail: an IMPORT body is written as one product (its sub-assembly is flattened)
    items = [(_export_name(f), _feature_brep(db, f, {})) for f in shown]
    fname = f"{doc.doc_no}.{SUFFIX[format]}"
    db.close()  # give the DB connection back before queueing for the kernel
    if not EXPORT_SLOTS.acquire(timeout=SLOT_WAIT_S):
        raise ApiError(503, "SERVER_BUSY", "Server busy, retry later")
    try:
        data = _exchange(export_job, items, format)
    finally:
        EXPORT_SLOTS.release()
    return Response(
        data,
        media_type=MEDIA[format],
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
