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
from pathlib import Path
from typing import Annotated, Any, Literal

import OCP
from fastapi import APIRouter, File, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import func, select

from backend.api.auth import CurrentUser, Db, get_document, need
from backend.api.common import ApiError, body, pick
from backend.db.models import Document, Feature, Revision, User
from core.geometry import solver
from core.geometry.dressup import MAX_COPIES, MAX_DRAFT_DEG, MAX_HOLES, dressup_job
from core.geometry.errors import GeomError
from core.geometry.exchange import check_file, export_job, import_job
from core.geometry.features import MAX_DISTANCE_MM, boolean_job, revolve_job
from core.geometry.mesh import mesh_job
from core.geometry.pad import MAX_TAPER_DEG, pad_job, pocket_job, primitive_job
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


class FilletParams(_OnBody):
    radius: Size
    edges: EdgeSet


class ChamferParams(_OnBody):
    distance: Size
    edges: EdgeSet


class HoleParams(_OnBody):
    positions: Annotated[
        list[Vec2], Field(min_length=1, max_length=MAX_HOLES)
    ]  # XY, drilled from top
    diameter: Size
    depth: Size | None = None  # None = through all


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


class DraftParams(_OnBody):
    """FreeCAD Draft (Track 8 P2-3): tilt the side faces about the top or bottom plane."""

    angle_deg: Annotated[float, Field(allow_inf_nan=False, ge=-MAX_DRAFT_DEG, le=MAX_DRAFT_DEG)]
    pull: Literal["+Z", "-Z"] = "+Z"
    neutral: Literal["TOP", "BOTTOM"] = "BOTTOM"

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


class ImportParams(_Strict):
    # created only by POST /imports, never from client JSON: file_key points at a stored upload
    file_key: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    format: Literal["STEP", "IGES"]
    filename: Annotated[str, Field(min_length=1, max_length=200)]


Params = (
    SketchParams
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
    **DRESSUP,
}
# feature types that consume earlier features (a sketch-based EXTRUDE/REVOLVE consumes its sketch)
WITH_INPUTS = ("EXTRUDE", "REVOLVE", "POCKET", "SWEEP", "LOFT", "BOOLEAN", *DRESSUP)
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
    ]
    params: dict[str, Any]


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
    if isinstance(p, SweepParams):
        return [p.profile_sketch_id, p.path_sketch_id]
    if isinstance(p, LoftParams):
        return list(p.sketch_ids)
    return [p.sketch_feature_id] if isinstance(p, _Sketch) and p.sketch_feature_id else []


def _inputs(p: Params) -> list[int]:
    if isinstance(p, (SweepParams, LoftParams)):
        return _sketch_inputs(p)
    if isinstance(p, _Sketch):
        ids = [p.sketch_feature_id] if p.sketch_feature_id else []
        return [*ids, p.target_feature_id] if isinstance(p, PocketParams) else ids
    if isinstance(p, BooleanParams):
        return [p.target_feature_id, p.tool_feature_id]
    return [p.target_feature_id] if isinstance(p, _OnBody) else []


def _key(db: Db, p: Params) -> str:
    # chain-aware: a BOOLEAN key includes its inputs' keys, so a changed input changes the output key
    ins = [db.get(Feature, i) for i in _inputs(p)]
    raw = json.dumps(p.model_dump(), sort_keys=True) + OCP.__version__
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
    brep, _ = _build(db, doc, f.feature_type, _params(f.feature_type, f.params), mem, depth + 1)
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
    if isinstance(p, (BooleanParams, _OnBody)):
        bodies = []
        for i in _inputs(p):
            inp = db.get(Feature, i)
            if inp is None or inp.document_id != doc.document_id:
                raise GeomError("GEOM_INVALID_PARAM", "Input feature not found in this document")
            bodies.append(_feature_brep(db, inp, mem, depth))
        if isinstance(p, BooleanParams):
            return run_kernel(boolean_job, p.op, *bodies)
        return run_kernel(dressup_job, ftype, bodies[0], p.model_dump())

    if isinstance(p, PrimitiveParams):
        return run_kernel(primitive_job, p.model_dump())  # type: ignore[no-any-return]
    if isinstance(p, SketchParams):
        return b"", None  # no body: the solved geometry lives in its params
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


def _check_inputs(db: Db, doc: Document, p: Params, seq: int, own: int | None) -> None:
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
        if i in taken:
            raise bad("Input feature is already used by another feature")


def _out(f: Feature, consumed: dict[int, int]) -> dict[str, Any]:
    cols = ("feature_id", "seq", "feature_type", "params", "status", "error_code", "metrics")
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
    seq = (
        db.scalar(select(func.max(Feature.seq)).where(Feature.document_id == document_id)) or 0
    ) + 1  # race-free: the document row lock is held
    if _inputs(p):
        _check_inputs(db, doc, p, seq, None)
        if _chain_depth(db, _inputs(p)) >= MAX_CHAIN_DEPTH:
            raise GeomError("GEOM_INVALID_PARAM", "Feature chain too deep")
    brep, metrics = _build(db, doc, req.feature_type, p, {})
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
    _changed(doc)
    db.commit()
    return body(_out(f, _consumed(db, document_id)))


@router.patch("/api/features/{feature_id}")
def update_feature(feature_id: int, req: FeatureUpdate, user: CurrentUser, db: Db) -> Any:
    f, doc = _feature(db, user, feature_id, lock=True)
    need(user, "DESIGNER")
    if f.feature_type == "IMPORT":
        raise ApiError(409, "FEATURE_NOT_EDITABLE", "Imported bodies have no parameters to edit")
    p = _solved(_params(f.feature_type, req.params))
    if _inputs(p):
        _check_inputs(db, doc, p, f.seq, f.feature_id)
    mem: dict[int, bytes] = {}
    brep, metrics = _build(db, doc, f.feature_type, p, mem)  # failure raises before any row change
    mem[f.feature_id] = brep
    f.params, f.brep_key, f.metrics = p.model_dump(), _key(db, p), metrics
    f.updated_at = func.now()
    # regenerate dependent features in seq order; any failure rolls the whole request back
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
            mem[g.feature_id], g.metrics = _build(db, doc, g.feature_type, gp, mem)
        except GeomError as e:
            db.rollback()
            raise GeomError(
                e.code, e.message, e.http, {**(e.details or {}), "feature_id": g.feature_id}
            ) from None
        g.brep_key = _key(db, gp)
        g.updated_at = func.now()
        changed.add(g.feature_id)
    for fid in changed:
        row = f if fid == f.feature_id else db.get(Feature, fid)
        assert row is not None and row.brep_key is not None
        if mem[fid]:  # a sketch has no body file
            _store_brep(row.brep_key, mem[fid])
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
