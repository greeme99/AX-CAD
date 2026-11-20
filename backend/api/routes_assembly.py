"""Track 9 Q2: assembly documents — components, mates, solve, interference, export (plan.md §15.3).

An ASSEMBLY document places bodies of other documents of the same project. A component points at
a body feature of its part; the body shown is that feature's latest result in the part (a fillet
added to the part later consumes the old body, and the assembly follows it). Mates store the
picked faces as signatures in part coordinates; the kernel turns them into planes / axes, and the
solver (core/geometry/assembly.py) moves the free components.
"""

import threading
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from backend.api.auth import CurrentUser, Db, get_document, need
from backend.api.common import ApiError, body
from backend.db.models import (
    AssemblyComponent,
    AssemblyExplode,
    AssemblyIgnoredPair,
    AssemblyMate,
    AssemblyPattern,
    Document,
    Feature,
)
from core.geometry import assembly as asm
from core.geometry.worker import run_kernel

router = APIRouter()
Coord = Annotated[float, Field(allow_inf_nan=False, ge=-1e6, le=1e6)]
Angle = Annotated[float, Field(allow_inf_nan=False, ge=-360, le=360)]
Id = Annotated[int, Field(ge=1, le=2**63 - 1)]
Name = Annotated[str, Field(min_length=1, max_length=200)]
SOLVE_TIMEOUT_S = 60  # the solver stops itself at asm.SOLVE_BUDGET_S
INTERFERENCE_TIMEOUT_S = 120  # ... and the interference check at asm.INTERFERENCE_BUDGET_S
SLOT_WAIT_S = 30
MAX_PARTS_BYTES = 256 * 1024**2
ASM_SLOTS = threading.BoundedSemaphore(1)  # one solve / interference run at a time


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Placement(_Strict):
    t: Annotated[list[Coord], Field(min_length=3, max_length=3)] = [0, 0, 0]
    r: Annotated[list[Angle], Field(min_length=3, max_length=3)] = [0, 0, 0]


class ComponentIn(_Strict):
    source_feature_id: Id | None = None
    source_document_id: Id | None = None  # when placing a SUBASSEMBLY (doc_type == 'ASSEMBLY')
    name: Name | None = None
    placement: Placement = Placement()
    fixed: bool | None = None  # None: the first component is fixed, later ones free


class ComponentPatch(_Strict):
    name: Name | None = None
    placement: Placement | None = None
    fixed: bool | None = None
    visible: bool | None = None
    suppressed: bool | None = None


class PatternIn(_Strict):
    source_component_id: Id
    pattern_type: Literal["LINEAR", "CIRCULAR"]
    params: dict[str, Any]


class MirrorIn(_Strict):
    plane_origin: Annotated[list[Coord], Field(min_length=3, max_length=3)]
    plane_normal: Annotated[list[Coord], Field(min_length=3, max_length=3)]


class ReplaceIn(_Strict):
    new_source_document_id: Id
    new_source_feature_id: Id


class FaceSig(_Strict):
    kind: Literal["PLANE", "CYLINDER", "CONE", "SPHERE", "TORUS", "SURFACE"]
    mid: Annotated[list[Coord], Field(min_length=3, max_length=3)]
    area: Annotated[float, Field(allow_inf_nan=False, ge=0, le=1e12)]
    dir: Annotated[list[Coord], Field(min_length=3, max_length=3)] | None = None
    radius: Annotated[float, Field(allow_inf_nan=False, gt=0, le=1e6)] | None = None


class MateSide(_Strict):
    component_id: Id
    face: FaceSig | None = None
    ref_kind: Literal["FACE", "EDGE", "VERTEX"] = "FACE"
    ref: dict[str, Any] | None = None

    def normalized(self) -> dict[str, Any]:
        if self.face is not None:
            return {
                "component_id": self.component_id,
                "ref_kind": "FACE",
                "face": self.face.model_dump(),
            }
        if self.ref is None:
            raise ApiError(422, "ASSEMBLY_MATE_INVALID", "MateSide needs face or ref")
        return {"component_id": self.component_id, "ref_kind": self.ref_kind, "ref": self.ref}


MateTypeLit = Literal[
    "COINCIDENT",
    "DISTANCE",
    "PARALLEL",
    "ANGLE",
    "CONCENTRIC",
    "TANGENT",
    "PERPENDICULAR",
    "LOCK",
    "SYMMETRIC",
    "WIDTH",
    "LIMIT_DISTANCE",
    "LIMIT_ANGLE",
]


class MateIn(_Strict):
    mate_type: MateTypeLit
    a: MateSide
    b: MateSide
    value: Annotated[float, Field(allow_inf_nan=False, ge=-1e6, le=1e6)] | None = None
    value_min: Annotated[float, Field(allow_inf_nan=False, ge=-1e6, le=1e6)] | None = None
    value_max: Annotated[float, Field(allow_inf_nan=False, ge=-1e6, le=1e6)] | None = None
    flip: bool = False
    suppressed: bool = False
    extra: dict[str, Any] | None = None


class MatePatch(_Strict):
    suppressed: bool | None = None
    value: Annotated[float, Field(allow_inf_nan=False, ge=-1e6, le=1e6)] | None = None
    value_min: Annotated[float, Field(allow_inf_nan=False, ge=-1e6, le=1e6)] | None = None
    value_max: Annotated[float, Field(allow_inf_nan=False, ge=-1e6, le=1e6)] | None = None
    flip: bool | None = None


class InterferenceIn(_Strict):
    clearance: Annotated[float, Field(allow_inf_nan=False, ge=0, le=1000)] = 0.0
    include_ignored: bool = False


class IgnoredPairIn(_Strict):
    comp_a_id: Id
    comp_b_id: Id
    reason: Annotated[str, Field(max_length=500)] = ""


class DragIn(_Strict):
    component_id: Id
    target_delta_t: Annotated[list[Coord], Field(min_length=3, max_length=3)]
    target_delta_r: Annotated[list[Angle], Field(min_length=3, max_length=3)] | None = None


class ExplodeStepIn(_Strict):
    step_no: Annotated[int, Field(ge=1, le=1000)]
    component_ids: Annotated[list[Id], Field(min_length=1, max_length=1000)]
    delta: Annotated[list[Coord], Field(min_length=3, max_length=3)]


class ExplodeIn(_Strict):
    name: Name
    steps: Annotated[list[ExplodeStepIn], Field(max_length=100)] = []


class ExplodePatch(_Strict):
    name: Name | None = None
    steps: Annotated[list[ExplodeStepIn], Field(max_length=100)] | None = None


class MotionStudyIn(_Strict):
    drivers: Annotated[list[dict[str, Any]], Field(min_length=1, max_length=64)]
    couplings: Annotated[list[dict[str, Any]], Field(max_length=64)] = []
    duration_s: Annotated[float, Field(allow_inf_nan=False, gt=0, le=3600)] = 2.0
    steps: Annotated[int, Field(ge=1, le=500)] = 20
    check_clashes: bool = True


class AssemblyDrawingIn(_Strict):
    explode_id: Id | None = None
    explode_factor: Annotated[float, Field(allow_inf_nan=False, ge=0.0, le=1.0)] = 1.0
    title: Annotated[str, Field(min_length=1, max_length=120)] = "ASSEMBLY DRAWING"


def _assembly(db: Db, user: Any, document_id: int, lock: bool = False) -> Document:
    doc: Document = get_document(db, user, document_id, lock)
    if doc.doc_type != "ASSEMBLY":
        raise ApiError(409, "NOT_ASSEMBLY", "This is not an assembly document")
    return doc


def _latest(db: Db, doc_id: int, feature_id: int) -> Feature | None:
    """The body that feature_id became in its document: follow BOOLEAN / machining consumers."""
    from backend.api.routes_model import _consumed

    consumed = _consumed(db, doc_id)
    seen = set()
    fid = feature_id
    while fid in consumed and fid not in seen:
        seen.add(fid)
        fid = consumed[fid]
    f = db.get(Feature, fid)
    if f is None or f.document_id != doc_id or f.status != "OK" or not f.metrics:
        return None
    return f


def _component(
    db: Db, user: Any, component_id: int, lock: bool = False
) -> tuple[AssemblyComponent, Document]:
    c = db.get(AssemblyComponent, component_id)
    try:
        if c is None:
            raise ApiError(404, "COMPONENT_NOT_FOUND", "Component not found")
        return c, _assembly(db, user, c.document_id, lock)
    except ApiError as e:
        if e.code == "NOT_ASSEMBLY":
            raise
        raise ApiError(404, "COMPONENT_NOT_FOUND", "Component not found") from None


def _rows(db: Db, doc_id: int) -> tuple[list[AssemblyComponent], list[AssemblyMate]]:
    comps = db.scalars(
        select(AssemblyComponent)
        .where(AssemblyComponent.document_id == doc_id)
        .order_by(AssemblyComponent.seq)
    ).all()
    mates = db.scalars(
        select(AssemblyMate)
        .where(AssemblyMate.document_id == doc_id)
        .order_by(AssemblyMate.mate_id)
    ).all()
    return list(comps), list(mates)


def _comp_out(db: Db, c: AssemblyComponent) -> dict[str, Any]:
    src = db.get(Document, c.source_document_id)
    if c.component_kind == "SUBASSEMBLY":
        return {
            "component_id": c.component_id,
            "name": c.name,
            "component_kind": "SUBASSEMBLY",
            "source_document_id": c.source_document_id,
            "source_doc_no": src.doc_no if src else None,
            "source_feature_id": None,
            "body_feature_id": None,
            "status": "OK" if src and src.doc_type == "ASSEMBLY" else "MISSING",
            "bbox": None,
            "placement": c.placement,
            "fixed": c.fixed,
            "visible": c.visible,
            "suppressed": c.suppressed,
            "pattern_id": c.pattern_id,
            "pattern_index": c.pattern_index,
            "mirrored_from_id": c.mirrored_from_id,
            "mirror_plane": c.mirror_plane,
            "seq": c.seq,
        }
    f = _latest(db, c.source_document_id, c.source_feature_id) if c.source_feature_id else None
    return {
        "component_id": c.component_id,
        "name": c.name,
        "component_kind": "PART",
        "source_document_id": c.source_document_id,
        "source_doc_no": src.doc_no if src else None,
        "source_feature_id": c.source_feature_id,
        "body_feature_id": f.feature_id if f else None,
        "status": "OK" if f else "MISSING",
        "bbox": f.metrics["bbox"] if f and f.metrics else None,
        "placement": c.placement,
        "fixed": c.fixed,
        "visible": c.visible,
        "suppressed": c.suppressed,
        "pattern_id": c.pattern_id,
        "pattern_index": c.pattern_index,
        "mirrored_from_id": c.mirrored_from_id,
        "mirror_plane": c.mirror_plane,
        "seq": c.seq,
    }


def _mate_out(m: AssemblyMate) -> dict[str, Any]:
    return {
        "mate_id": m.mate_id,
        "mate_type": m.mate_type,
        "a": m.a,
        "b": m.b,
        "value": m.value,
        "value_min": m.value_min,
        "value_max": m.value_max,
        "flip": m.flip,
        "suppressed": m.suppressed,
        "extra": m.extra,
        "status": m.status,
        "residual": m.residual,
    }


def _state(db: Db, doc: Document, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    comps, mates = _rows(db, doc.document_id)
    return {
        "document_id": doc.document_id,
        "components": [_comp_out(db, c) for c in comps],
        "mates": [_mate_out(m) for m in mates],
        **(extra or {}),
    }


@router.get("/api/documents/{document_id}/assembly")
def get_assembly(document_id: int, user: CurrentUser, db: Db) -> Any:
    return body(_state(db, _assembly(db, user, document_id)))


@router.get("/api/documents/{document_id}/assembly/candidates")
def candidates(document_id: int, user: CurrentUser, db: Db) -> Any:
    """Bodies of the project's other non-assembly documents that can be placed."""
    from backend.api.routes_quote import _bodies

    doc = _assembly(db, user, document_id)
    docs = db.scalars(
        select(Document)
        .where(
            Document.project_id == doc.project_id,
            Document.doc_type != "ASSEMBLY",
            Document.document_id != doc.document_id,
        )
        .order_by(Document.doc_no)
    ).all()
    out = []
    for d in docs:
        for b in _bodies(db, d.document_id):
            out.append(
                {
                    "document_id": d.document_id,
                    "doc_no": d.doc_no,
                    "title": d.title,
                    "feature_id": b["feature_id"],
                    "name": b["name"],
                    "bbox": b["bbox"],
                    "volume_mm3": b["volume_mm3"],
                }
            )
    return body({"items": out[:1000], "total": len(out)})


def _fetch_children_db(db: Db, doc_id: int) -> list[dict[str, Any]]:
    comps, _ = _rows(db, doc_id)
    return [
        {
            "component_id": c.component_id,
            "name": c.name,
            "component_kind": c.component_kind,
            "source_document_id": c.source_document_id,
            "source_feature_id": c.source_feature_id,
            "placement": c.placement,
            "suppressed": c.suppressed,
            "visible": c.visible,
        }
        for c in comps
    ]


def _sync_patterns(db: Db, doc_id: int) -> None:
    """Recompute derived pattern component placements from their source component."""
    patterns = db.scalars(
        select(AssemblyPattern).where(AssemblyPattern.document_id == doc_id)
    ).all()
    if not patterns:
        return
    comps, _ = _rows(db, doc_id)
    by_id = {c.component_id: c for c in comps}
    for pat in patterns:
        src = by_id.get(pat.source_component_id)
        if src is None:
            continue
        derived_places = asm.pattern_placements(src.placement, pat.pattern_type, pat.params)
        children = sorted(
            [c for c in comps if c.pattern_id == pat.pattern_id],
            key=lambda x: x.pattern_index or 0,
        )
        for child, place in zip(children, derived_places, strict=False):
            child.placement = place
            child.fixed = True


@router.post("/api/documents/{document_id}/components")
def add_component(document_id: int, req: ComponentIn, user: CurrentUser, db: Db) -> Any:
    need(user, "DESIGNER")
    doc = _assembly(db, user, document_id, lock=True)
    count = db.scalar(select(func.count()).where(AssemblyComponent.document_id == document_id)) or 0
    if count >= asm.MAX_COMPONENTS:
        raise ApiError(422, "ASSEMBLY_TOO_LARGE", f"At most {asm.MAX_COMPONENTS} components")
    last = (
        db.scalar(
            select(func.max(AssemblyComponent.seq)).where(
                AssemblyComponent.document_id == document_id
            )
        )
        or 0
    )

    if req.source_document_id is not None and req.source_feature_id is None:
        # SUBASSEMBLY placement (A2)
        src = db.get(Document, req.source_document_id)
        if src is None or src.project_id != doc.project_id or src.doc_type != "ASSEMBLY":
            raise ApiError(
                422, "COMPONENT_SOURCE_INVALID", "Pick an assembly document in this project"
            )
        if src.document_id == document_id:
            raise ApiError(409, "ASSEMBLY_CYCLE", "An assembly cannot contain itself")
        c = AssemblyComponent(
            document_id=document_id,
            source_document_id=src.document_id,
            source_feature_id=None,
            component_kind="SUBASSEMBLY",
            name=req.name or f"{src.doc_no}-{count + 1}",
            placement=req.placement.model_dump(),
            fixed=count == 0 if req.fixed is None else req.fixed,
            seq=last + 1,
            created_by=user.user_id,
        )
        db.add(c)
        db.flush()
        try:
            asm.expand_hierarchy(document_id, lambda did: _fetch_children_db(db, did))
        except asm.GeomError as exc:
            db.rollback()
            status_code = 409 if exc.code == "ASSEMBLY_CYCLE" else 422
            raise ApiError(status_code, exc.code, exc.message) from None
        db.commit()
        return body(_comp_out(db, c))

    if req.source_feature_id is None:
        raise ApiError(
            422, "COMPONENT_SOURCE_INVALID", "Give source_feature_id or source_document_id"
        )
    f = db.get(Feature, req.source_feature_id)
    src = db.get(Document, f.document_id) if f else None
    if f is None or src is None or src.project_id != doc.project_id or src.doc_type == "ASSEMBLY":
        raise ApiError(422, "COMPONENT_SOURCE_INVALID", "Pick a body of a part in this project")
    if _latest(db, src.document_id, f.feature_id) is None:
        raise ApiError(422, "COMPONENT_SOURCE_INVALID", "That feature has no body")
    c = AssemblyComponent(
        document_id=document_id,
        source_document_id=src.document_id,
        source_feature_id=f.feature_id,
        component_kind="PART",
        name=req.name or f"{src.doc_no}-{count + 1}",
        placement=req.placement.model_dump(),
        fixed=count == 0 if req.fixed is None else req.fixed,
        seq=last + 1,
        created_by=user.user_id,
    )
    db.add(c)
    db.commit()
    return body(_comp_out(db, c))


@router.patch("/api/components/{component_id}")
def patch_component(component_id: int, req: ComponentPatch, user: CurrentUser, db: Db) -> Any:
    need(user, "DESIGNER")
    c, doc = _component(db, user, component_id, lock=True)
    if req.name is not None:
        c.name = req.name
    if req.placement is not None:
        c.placement = req.placement.model_dump()
    if req.fixed is not None:
        c.fixed = req.fixed
    if req.visible is not None:
        c.visible = req.visible
    if req.suppressed is not None:
        c.suppressed = req.suppressed
    _sync_patterns(db, doc.document_id)
    db.commit()
    return body(_comp_out(db, c))


@router.post("/api/documents/{document_id}/assembly/patterns")
def create_pattern(document_id: int, req: PatternIn, user: CurrentUser, db: Db) -> Any:
    need(user, "DESIGNER")
    doc = _assembly(db, user, document_id, lock=True)
    src = db.get(AssemblyComponent, req.source_component_id)
    if src is None or src.document_id != document_id:
        raise ApiError(404, "COMPONENT_NOT_FOUND", "Source component not found in this assembly")
    derived = asm.pattern_placements(src.placement, req.pattern_type, req.params)
    count = db.scalar(select(func.count()).where(AssemblyComponent.document_id == document_id)) or 0
    if count + len(derived) > asm.MAX_COMPONENTS:
        raise ApiError(422, "ASSEMBLY_TOO_LARGE", f"At most {asm.MAX_COMPONENTS} components")
    last = (
        db.scalar(
            select(func.max(AssemblyComponent.seq)).where(
                AssemblyComponent.document_id == document_id
            )
        )
        or 0
    )
    pat = AssemblyPattern(
        document_id=document_id,
        source_component_id=src.component_id,
        pattern_type=req.pattern_type,
        params=req.params,
        created_by=user.user_id,
    )
    db.add(pat)
    db.flush()
    for idx, place in enumerate(derived, start=1):
        db.add(
            AssemblyComponent(
                document_id=document_id,
                source_document_id=src.source_document_id,
                source_feature_id=src.source_feature_id,
                component_kind=src.component_kind,
                name=f"{src.name}-P{idx}",
                placement=place,
                fixed=True,
                pattern_id=pat.pattern_id,
                pattern_index=idx,
                seq=last + idx,
                created_by=user.user_id,
            )
        )
    db.commit()
    return body(_state(db, doc, {"pattern_id": pat.pattern_id}))


@router.delete("/api/assembly/patterns/{pattern_id}")
def delete_pattern(pattern_id: int, user: CurrentUser, db: Db) -> Any:
    need(user, "DESIGNER")
    pat = db.get(AssemblyPattern, pattern_id)
    if pat is None:
        raise ApiError(404, "PATTERN_NOT_FOUND", "Pattern not found")
    doc = _assembly(db, user, pat.document_id, lock=True)
    db.delete(pat)
    db.commit()
    return body(_state(db, doc))


@router.post("/api/components/{component_id}/mirror")
def mirror_component(component_id: int, req: MirrorIn, user: CurrentUser, db: Db) -> Any:
    need(user, "DESIGNER")
    src, doc = _component(db, user, component_id, lock=True)
    count = (
        db.scalar(select(func.count()).where(AssemblyComponent.document_id == doc.document_id)) or 0
    )
    if count >= asm.MAX_COMPONENTS:
        raise ApiError(422, "ASSEMBLY_TOO_LARGE", f"At most {asm.MAX_COMPONENTS} components")
    last = (
        db.scalar(
            select(func.max(AssemblyComponent.seq)).where(
                AssemblyComponent.document_id == doc.document_id
            )
        )
        or 0
    )
    mir_place = asm.mirror_placement(src.placement, req.plane_origin, req.plane_normal)
    c = AssemblyComponent(
        document_id=doc.document_id,
        source_document_id=src.source_document_id,
        source_feature_id=src.source_feature_id,
        component_kind=src.component_kind,
        name=f"{src.name}-MIR",
        placement=mir_place,
        fixed=src.fixed,
        mirrored_from_id=src.component_id,
        mirror_plane={"origin": req.plane_origin, "normal": req.plane_normal},
        seq=last + 1,
        created_by=user.user_id,
    )
    db.add(c)
    db.commit()
    return body(_comp_out(db, c))


@router.post("/api/components/{component_id}/replace")
def replace_component(component_id: int, req: ReplaceIn, user: CurrentUser, db: Db) -> Any:
    need(user, "DESIGNER")
    c, doc = _component(db, user, component_id, lock=True)
    if c.component_kind != "PART":
        raise ApiError(422, "COMPONENT_SOURCE_INVALID", "Only PART components can be replaced")
    f = db.get(Feature, req.new_source_feature_id)
    src = db.get(Document, req.new_source_document_id)
    if (
        f is None
        or src is None
        or f.document_id != src.document_id
        or src.project_id != doc.project_id
        or src.doc_type == "ASSEMBLY"
        or _latest(db, src.document_id, f.feature_id) is None
    ):
        raise ApiError(422, "COMPONENT_SOURCE_INVALID", "Pick a valid part body in this project")
    c.source_document_id = src.document_id
    c.source_feature_id = f.feature_id
    db.flush()
    comps, mates = _rows(db, doc.document_id)
    broken = 0
    for m in mates:
        involved = component_id in (m.a.get("component_id"), m.b.get("component_id"))
        if not involved:
            continue
        try:
            _ref_geoms(db, comps, [_side_item(m.a), _side_item(m.b)])
        except (ApiError, asm.GeomError):
            m.status = "BROKEN"
            m.residual = None
            broken += 1
    db.commit()
    return body(_state(db, doc, {"broken_mates": broken}))


@router.delete("/api/components/{component_id}")
def delete_component(component_id: int, user: CurrentUser, db: Db) -> Any:
    need(user, "DESIGNER")
    c, doc = _component(db, user, component_id, lock=True)
    _, mates = _rows(db, doc.document_id)
    for m in mates:  # its mates go with it
        if component_id in (m.a.get("component_id"), m.b.get("component_id")):
            db.delete(m)
    db.delete(c)
    db.commit()
    return body(_state(db, doc))


def _parts(db: Db, comps: list[AssemblyComponent]) -> tuple[dict[str, bytes], dict[int, str]]:
    """Body BREPs by key and each component's key; a component whose body is gone is an error."""
    from backend.api.routes_model import _feature_brep

    parts: dict[str, bytes] = {}
    key_of: dict[int, str] = {}
    total = 0
    for c in comps:
        if c.component_kind != "PART" or c.source_feature_id is None:
            continue
        f = _latest(db, c.source_document_id, c.source_feature_id)
        if f is None:
            raise ApiError(
                409, "COMPONENT_MISSING", f"The body of component '{c.name}' no longer exists"
            )
        key = str(f.feature_id)
        if key not in parts:
            parts[key] = _feature_brep(db, f, {})
            total += len(parts[key])
            if total > MAX_PARTS_BYTES:  # every part goes to the kernel process at once
                raise ApiError(
                    422, "ASSEMBLY_TOO_LARGE", "The parts are too large to process together"
                )
        key_of[c.component_id] = key
    return parts, key_of


def _side_item(side: dict[str, Any]) -> tuple[int, str, dict[str, Any]]:
    rk = side.get("ref_kind", "FACE")
    sig = side.get("face") if side.get("face") is not None else side.get("ref")
    if not isinstance(sig, dict):
        raise ApiError(422, "ASSEMBLY_MATE_INVALID", "Mate side is missing geometry signature")
    return int(side["component_id"]), str(rk), sig


def _ref_geoms(
    db: Db, comps: list[AssemblyComponent], items: list[tuple[int, str, dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Ref signatures (component id, ref_kind, sig) -> geometry in part coordinates."""
    by_id = {c.component_id: c for c in comps}
    if any(cid not in by_id for cid, _, _ in items):
        raise ApiError(
            422, "ASSEMBLY_MATE_INVALID", "A mate refers to a component of another assembly"
        )
    parts, key_of = _parts(db, [by_id[cid] for cid in {cid for cid, _, _ in items}])
    out: list[dict[str, Any] | None] = [None] * len(items)
    for key, brep in parts.items():
        idx = [i for i, (cid, _, _) in enumerate(items) if key_of[cid] == key]
        geoms = run_kernel(asm.refs_job, brep, [(items[i][1], items[i][2]) for i in idx])
        for i, g in zip(idx, geoms, strict=True):
            out[i] = g
    return [g for g in out if g is not None]


def _slot() -> None:
    if not ASM_SLOTS.acquire(timeout=SLOT_WAIT_S):
        raise ApiError(503, "SERVER_BUSY", "Server busy, retry later")


def _snapshot(
    comps: list[AssemblyComponent], mates: list[AssemblyMate]
) -> tuple[dict[int, Any], set[tuple[int, bool]]]:
    return (
        {c.component_id: (c.placement, c.fixed) for c in comps},
        {(m.mate_id, m.suppressed) for m in mates},
    )


def _resolve_mate_specs(
    db: Db, comps: list[AssemblyComponent], mates: list[AssemblyMate]
) -> list[dict[str, Any]]:
    """Build solver mate dicts for all mates, resolving a, b, and optional extra ref sides."""
    items: list[tuple[int, str, dict[str, Any]]] = []
    slots: list[tuple[int, str]] = []
    for i, m in enumerate(mates):
        if m.suppressed:
            continue
        items.append(_side_item(m.a))
        slots.append((i, "a"))
        items.append(_side_item(m.b))
        slots.append((i, "b"))
        extra = m.extra or {}
        for ek in ("plane_side", "a2", "b2"):
            if isinstance(extra.get(ek), dict) and "component_id" in extra[ek]:
                items.append(_side_item(extra[ek]))
                slots.append((i, ek))
    geoms = _ref_geoms(db, comps, items) if items else []
    resolved: dict[tuple[int, str], dict[str, Any]] = {}
    for (i, key), g, (cid, _, _) in zip(slots, geoms, items, strict=True):
        resolved[(i, key)] = {**g, "component": cid}

    specs: list[dict[str, Any]] = []
    for i, m in enumerate(mates):
        if m.suppressed:
            specs.append({"id": m.mate_id, "type": m.mate_type, "suppressed": True})
            continue
        ex = dict(m.extra or {})
        if (i, "plane_side") in resolved:
            ex["plane"] = resolved[(i, "plane_side")]
        if (i, "a2") in resolved:
            ex["a2"] = resolved[(i, "a2")]
        if (i, "b2") in resolved:
            ex["b2"] = resolved[(i, "b2")]
        specs.append(
            {
                "id": m.mate_id,
                "type": m.mate_type,
                "a": resolved[(i, "a")],
                "b": resolved[(i, "b")],
                "value": m.value,
                "value_min": m.value_min,
                "value_max": m.value_max,
                "flip": m.flip,
                "suppressed": False,
                "extra": ex or None,
            }
        )
    return specs


def _run_solve(db: Db, user: Any, document_id: int) -> dict[str, Any]:
    """Read the assembly and work out the ref geometry without holding the document lock,
    solve in the kernel, then lock and apply — unless someone changed the assembly meanwhile."""
    _assembly(db, user, document_id)
    comps, mates = _rows(db, document_id)
    if not mates:
        return {
            "dof": None,
            "dof_by_component": {c.component_id: (0 if c.fixed else 6) for c in comps},
            "conflicting_mate_ids": [],
            "overconstrained": False,
        }
    spec = _resolve_mate_specs(db, comps, mates)
    comps_in = [{"id": c.component_id, **c.placement, "fixed": c.fixed} for c in comps]
    before = _snapshot(comps, mates)
    order = [m.mate_id for m in mates]
    db.commit()  # no lock or open transaction while the kernel works
    _slot()
    try:
        res = run_kernel(asm.solve, comps_in, spec, timeout_s=SOLVE_TIMEOUT_S)
    finally:
        ASM_SLOTS.release()
    _assembly(db, user, document_id, lock=True)
    comps, mates = _rows(db, document_id)
    if _snapshot(comps, mates) != before:
        db.rollback()
        raise ApiError(409, "ASSEMBLY_CHANGED", "The assembly changed while solving; solve again")
    by_mate = {m.mate_id: m for m in mates}
    for c in comps:
        c.placement = res["placements"][c.component_id]
    _sync_patterns(db, document_id)
    for mid, r in zip(order, res["mates"], strict=True):
        by_mate[mid].status, by_mate[mid].residual = r["status"], r["residual"]
    db.commit()
    return {
        "dof": res["dof"],
        "dof_by_component": res["dof_by_component"],
        "conflicting_mate_ids": res["conflicting_mate_ids"],
        "overconstrained": res["overconstrained"],
    }


@router.post("/api/documents/{document_id}/mates")
def add_mate(document_id: int, req: MateIn, user: CurrentUser, db: Db) -> Any:
    import numpy as np

    need(user, "DESIGNER")
    doc = _assembly(db, user, document_id)
    comps, _ = _rows(db, document_id)
    by_id = {c.component_id: c for c in comps}
    if not {req.a.component_id, req.b.component_id} <= set(by_id):
        raise ApiError(422, "ASSEMBLY_MATE_INVALID", "Pick refs of this assembly's components")
    if req.mate_type in ("DISTANCE", "ANGLE") and req.value is None:
        raise ApiError(422, "ASSEMBLY_MATE_INVALID", f"{req.mate_type} needs a value")
    if req.mate_type in ("LIMIT_DISTANCE", "LIMIT_ANGLE") and (
        req.value_min is None or req.value_max is None or req.value_min > req.value_max
    ):
        raise ApiError(
            422, "ASSEMBLY_MATE_INVALID", f"{req.mate_type} needs value_min <= value_max"
        )
    a_norm, b_norm = req.a.normalized(), req.b.normalized()
    extra_store = dict(req.extra or {})
    # LOCK: capture relative transform at mate creation time when not supplied
    if req.mate_type == "LOCK" and "rel_t" not in extra_store:
        ca, cb = by_id[req.a.component_id], by_id[req.b.component_id]
        ra = asm.rot(ca.placement["r"])
        rb = asm.rot(cb.placement["r"])
        ta = np.asarray(ca.placement["t"], dtype=float)
        tb = np.asarray(cb.placement["t"], dtype=float)
        extra_store["rel_t"] = [float(x) for x in (ra.T @ (tb - ta))]
        extra_store["rel_r"] = asm.euler(ra.T @ rb)

    # Validate ref geometries before storing
    items = [_side_item(a_norm), _side_item(b_norm)]
    extra_keys: list[str] = []
    for ek in ("plane_side", "a2", "b2"):
        if isinstance(extra_store.get(ek), dict) and "component_id" in extra_store[ek]:
            items.append(_side_item(extra_store[ek]))
            extra_keys.append(ek)
    geoms = _ref_geoms(db, comps, items)
    ex_check = dict(extra_store)
    for idx_k, ek in enumerate(extra_keys):
        cid = items[2 + idx_k][0]
        ex_check["plane" if ek == "plane_side" else ek] = {**geoms[2 + idx_k], "component": cid}
    asm._check(
        {
            "type": req.mate_type,
            "a": {**geoms[0], "component": req.a.component_id},
            "b": {**geoms[1], "component": req.b.component_id},
            "value": req.value,
            "value_min": req.value_min,
            "value_max": req.value_max,
            "extra": ex_check or None,
        },
        0,
    )
    db.commit()
    doc = _assembly(db, user, document_id, lock=True)
    count = db.scalar(select(func.count()).where(AssemblyMate.document_id == document_id)) or 0
    if count >= asm.MAX_MATES:
        raise ApiError(422, "ASSEMBLY_TOO_LARGE", f"At most {asm.MAX_MATES} mates")
    db.add(
        AssemblyMate(
            document_id=document_id,
            mate_type=req.mate_type,
            a=a_norm,
            b=b_norm,
            value=req.value,
            value_min=req.value_min,
            value_max=req.value_max,
            flip=req.flip,
            suppressed=req.suppressed,
            extra=extra_store or None,
            created_by=user.user_id,
        )
    )
    db.commit()
    extra = _run_solve(db, user, document_id)
    return body(_state(db, doc, extra))


@router.patch("/api/mates/{mate_id}")
def patch_mate(mate_id: int, req: MatePatch, user: CurrentUser, db: Db) -> Any:
    need(user, "DESIGNER")
    m = db.get(AssemblyMate, mate_id)
    if m is None:
        raise ApiError(404, "MATE_NOT_FOUND", "Mate not found")
    try:
        doc = _assembly(db, user, m.document_id, lock=True)
    except ApiError:
        raise ApiError(404, "MATE_NOT_FOUND", "Mate not found") from None
    if req.suppressed is not None:
        m.suppressed = req.suppressed
    if req.value is not None:
        m.value = req.value
    if req.value_min is not None:
        m.value_min = req.value_min
    if req.value_max is not None:
        m.value_max = req.value_max
    if req.flip is not None:
        m.flip = req.flip
    if m.mate_type in ("LIMIT_DISTANCE", "LIMIT_ANGLE") and (
        m.value_min is None or m.value_max is None or m.value_min > m.value_max
    ):
        db.rollback()
        raise ApiError(422, "ASSEMBLY_MATE_INVALID", "value_min must be <= value_max")
    db.commit()
    extra = _run_solve(db, user, doc.document_id)
    return body(_state(db, doc, extra))


@router.delete("/api/mates/{mate_id}")
def delete_mate(mate_id: int, user: CurrentUser, db: Db) -> Any:
    need(user, "DESIGNER")
    m = db.get(AssemblyMate, mate_id)
    if m is None:
        raise ApiError(404, "MATE_NOT_FOUND", "Mate not found")
    try:
        doc = _assembly(db, user, m.document_id, lock=True)
    except ApiError:
        raise ApiError(404, "MATE_NOT_FOUND", "Mate not found") from None
    db.delete(m)
    db.commit()
    return body(_state(db, doc))


@router.post("/api/documents/{document_id}/assembly/solve")
def solve(document_id: int, user: CurrentUser, db: Db) -> Any:
    need(user, "DESIGNER")
    extra = _run_solve(db, user, document_id)
    return body(_state(db, _assembly(db, user, document_id), extra))


def _placed(db: Db, doc: Document) -> tuple[dict[str, bytes], list[dict[str, Any]]]:
    flat = asm.expand_hierarchy(doc.document_id, lambda did: _fetch_children_db(db, did))
    if not flat:
        raise ApiError(409, "ASSEMBLY_EMPTY", "The assembly has no components")
    from backend.api.routes_model import _feature_brep

    parts: dict[str, bytes] = {}
    out: list[dict[str, Any]] = []
    total = 0
    for item in flat:
        f = _latest(db, item["source_document_id"], item["source_feature_id"])
        if f is None:
            raise ApiError(
                409, "COMPONENT_MISSING", f"The body of component '{item['path']}' no longer exists"
            )
        key = str(f.feature_id)
        if key not in parts:
            parts[key] = _feature_brep(db, f, {})
            total += len(parts[key])
            if total > MAX_PARTS_BYTES:
                raise ApiError(
                    422, "ASSEMBLY_TOO_LARGE", "The parts are too large to process together"
                )
        out.append(
            {
                "id": item["component_id"],
                "name": item["path"],
                "part": key,
                **item["world_placement"],
            }
        )
    return parts, out


@router.post("/api/documents/{document_id}/interference")
def interference(document_id: int, req: InterferenceIn, user: CurrentUser, db: Db) -> Any:
    need(user, "DESIGNER", "ESTIMATOR", "REVIEWER")
    doc = _assembly(db, user, document_id)
    parts, comps = _placed(db, doc)
    ignored_rows = (
        []
        if req.include_ignored
        else db.scalars(
            select(AssemblyIgnoredPair).where(AssemblyIgnoredPair.document_id == document_id)
        ).all()
    )
    ignored_pairs = [(r.comp_a_id, r.comp_b_id) for r in ignored_rows]
    db.close()  # the DB connection is not needed while the kernel works
    _slot()
    try:  # pairs and time are capped inside: a partial result, never a killed worker pool
        res = run_kernel(
            asm.interference_job,
            parts,
            comps,
            req.clearance,
            asm.MAX_PAIRS,
            asm.INTERFERENCE_BUDGET_S,
            ignored_pairs,
            timeout_s=INTERFERENCE_TIMEOUT_S,
        )
    finally:
        ASM_SLOTS.release()
    return body({"document_id": document_id, "clearance": req.clearance, **res})


@router.get("/api/documents/{document_id}/assembly/ignored-pairs")
def list_ignored_pairs(document_id: int, user: CurrentUser, db: Db) -> Any:
    _assembly(db, user, document_id)
    rows = db.scalars(
        select(AssemblyIgnoredPair)
        .where(AssemblyIgnoredPair.document_id == document_id)
        .order_by(AssemblyIgnoredPair.ignore_id)
    ).all()
    return body(
        [
            {
                "ignore_id": r.ignore_id,
                "comp_a_id": r.comp_a_id,
                "comp_b_id": r.comp_b_id,
                "reason": r.reason,
            }
            for r in rows
        ]
    )


@router.post("/api/documents/{document_id}/assembly/ignored-pairs")
def add_ignored_pair(document_id: int, req: IgnoredPairIn, user: CurrentUser, db: Db) -> Any:
    need(user, "DESIGNER")
    _assembly(db, user, document_id, lock=True)
    if req.comp_a_id == req.comp_b_id:
        raise ApiError(422, "ASSEMBLY_MATE_INVALID", "Ignored pair needs two different components")
    a_id, b_id = min(req.comp_a_id, req.comp_b_id), max(req.comp_a_id, req.comp_b_id)
    row = AssemblyIgnoredPair(
        document_id=document_id,
        comp_a_id=a_id,
        comp_b_id=b_id,
        reason=req.reason,
        created_by=user.user_id,
    )
    db.add(row)
    db.commit()
    return body(
        {
            "ignore_id": row.ignore_id,
            "comp_a_id": row.comp_a_id,
            "comp_b_id": row.comp_b_id,
            "reason": row.reason,
        }
    )


@router.delete("/api/assembly/ignored-pairs/{ignore_id}")
def delete_ignored_pair(ignore_id: int, user: CurrentUser, db: Db) -> Any:
    need(user, "DESIGNER")
    row = db.get(AssemblyIgnoredPair, ignore_id)
    if row is None:
        raise ApiError(404, "IGNORED_PAIR_NOT_FOUND", "Ignored pair not found")
    _assembly(db, user, row.document_id, lock=True)
    db.delete(row)
    db.commit()
    return body({"deleted": ignore_id})


@router.post("/api/documents/{document_id}/assembly/drag")
def drag_component(document_id: int, req: DragIn, user: CurrentUser, db: Db) -> Any:
    need(user, "DESIGNER")
    _assembly(db, user, document_id)
    comps, mates = _rows(db, document_id)
    spec = _resolve_mate_specs(db, comps, mates) if mates else []
    comps_in = [{"id": c.component_id, **c.placement, "fixed": c.fixed} for c in comps]
    db.commit()
    _slot()
    try:
        res = run_kernel(
            asm.drag_solve,
            comps_in,
            spec,
            req.component_id,
            req.target_delta_t,
            req.target_delta_r,
            timeout_s=SOLVE_TIMEOUT_S,
        )
    finally:
        ASM_SLOTS.release()
    doc = _assembly(db, user, document_id, lock=True)
    comps, _ = _rows(db, document_id)
    for c in comps:
        if c.component_id in res["placements"]:
            c.placement = res["placements"][c.component_id]
    _sync_patterns(db, document_id)
    db.commit()
    return body(_state(db, doc, {"dof": res["dof"], "dof_by_component": res["dof_by_component"]}))


def _explode_out(e: AssemblyExplode) -> dict[str, Any]:
    return {"explode_id": e.explode_id, "name": e.name, "steps": e.steps}


@router.get("/api/documents/{document_id}/assembly/explodes")
def list_explodes(document_id: int, user: CurrentUser, db: Db) -> Any:
    _assembly(db, user, document_id)
    rows = db.scalars(
        select(AssemblyExplode)
        .where(AssemblyExplode.document_id == document_id)
        .order_by(AssemblyExplode.explode_id)
    ).all()
    return body([_explode_out(r) for r in rows])


@router.post("/api/documents/{document_id}/assembly/explodes")
def create_explode(document_id: int, req: ExplodeIn, user: CurrentUser, db: Db) -> Any:
    need(user, "DESIGNER")
    _assembly(db, user, document_id, lock=True)
    row = AssemblyExplode(
        document_id=document_id,
        name=req.name,
        steps=[s.model_dump() for s in req.steps],
        created_by=user.user_id,
    )
    db.add(row)
    db.commit()
    return body(_explode_out(row))


@router.patch("/api/assembly/explodes/{explode_id}")
def patch_explode(explode_id: int, req: ExplodePatch, user: CurrentUser, db: Db) -> Any:
    need(user, "DESIGNER")
    row = db.get(AssemblyExplode, explode_id)
    if row is None:
        raise ApiError(404, "EXPLODE_NOT_FOUND", "Exploded view not found")
    _assembly(db, user, row.document_id, lock=True)
    if req.name is not None:
        row.name = req.name
    if req.steps is not None:
        row.steps = [s.model_dump() for s in req.steps]
    db.commit()
    return body(_explode_out(row))


@router.delete("/api/assembly/explodes/{explode_id}")
def delete_explode(explode_id: int, user: CurrentUser, db: Db) -> Any:
    need(user, "DESIGNER")
    row = db.get(AssemblyExplode, explode_id)
    if row is None:
        raise ApiError(404, "EXPLODE_NOT_FOUND", "Exploded view not found")
    _assembly(db, user, row.document_id, lock=True)
    db.delete(row)
    db.commit()
    return body({"deleted": explode_id})


def assembly_bodies(db: Db, doc_id: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """BOM view of an assembly: each component as one body named after its part document, and
    the part's purchased standard parts once per placed instance."""
    from backend.api.routes_quote import _standard_parts

    comps, _ = _rows(db, doc_id)
    bodies, standard = [], []
    for c in comps:
        src = db.get(Document, c.source_document_id)
        name = f"{src.doc_no} {src.title}" if src else c.name
        bodies.append({"feature_id": f"C{c.component_id}", "name": name, "volume_mm3": None})
        for p in _standard_parts(db, c.source_document_id):
            standard.append({**p, "feature_id": f"C{c.component_id}:{p['feature_id']}"})
    return bodies, standard


def export_assembly(db: Db, doc: Document, fmt: str) -> bytes:
    parts, comps = _placed(db, doc)
    db.close()
    return run_kernel(asm.export_assembly_job, parts, comps, fmt, timeout_s=INTERFERENCE_TIMEOUT_S)  # type: ignore[no-any-return]


@router.get("/api/documents/{document_id}/assembly/mass")
def assembly_mass(document_id: int, user: CurrentUser, db: Db) -> Any:
    from core.geometry import assembly_mass as asm_mass

    doc = _assembly(db, user, document_id)
    parts, comps = _placed(db, doc)
    db.close()
    _slot()
    try:
        res = run_kernel(asm_mass.assembly_mass_job, parts, comps, timeout_s=INTERFERENCE_TIMEOUT_S)
    finally:
        ASM_SLOTS.release()
    return body({"document_id": document_id, **res})


@router.get("/api/documents/{document_id}/assembly/bom")
def assembly_bom(
    document_id: int,
    user: CurrentUser,
    db: Db,
    mode: Annotated[Literal["indented", "rolled_up"], Query()] = "indented",
) -> Any:
    from core.geometry import assembly_mass as asm_mass

    doc = _assembly(db, user, document_id)
    flat = asm.expand_hierarchy(doc.document_id, lambda did: _fetch_children_db(db, did))
    return body(
        {
            "document_id": document_id,
            "mode": mode,
            "items": asm_mass.build_assembly_bom(flat, mode),
            "total_instances": len(flat),
        }
    )


@router.get("/api/documents/{document_id}/assembly/quote-summary")
def assembly_quote_summary(document_id: int, user: CurrentUser, db: Db) -> Any:
    from core.geometry import assembly_mass as asm_mass

    doc = _assembly(db, user, document_id)
    flat = asm.expand_hierarchy(doc.document_id, lambda did: _fetch_children_db(db, did))
    _, mates = _rows(db, document_id)
    active_mates = sum(1 for m in mates if not m.suppressed)
    return body(
        {
            "document_id": document_id,
            "rolled_up_bom": asm_mass.build_assembly_bom(flat, "rolled_up"),
            "assembly_labor": asm_mass.compute_assembly_labor(len(flat), active_mates),
        }
    )


@router.get("/api/documents/{document_id}/assembly/export")
def export(
    document_id: int,
    user: CurrentUser,
    db: Db,
    format: Annotated[Literal["STEP", "IGES", "STL", "BREP"], Query()] = "STEP",
) -> Any:
    from backend.api.routes_model import EXPORT_SLOTS, MEDIA, SLOT_WAIT_S, SUFFIX

    doc = _assembly(db, user, document_id)
    fname = f"{doc.doc_no}.{SUFFIX[format]}"
    if not EXPORT_SLOTS.acquire(timeout=SLOT_WAIT_S):
        raise ApiError(503, "SERVER_BUSY", "Server busy, retry later")
    try:
        data = export_assembly(db, doc, format)
    finally:
        EXPORT_SLOTS.release()
    return Response(
        data,
        media_type=MEDIA[format],
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/api/documents/{document_id}/assembly/motion")
def assembly_motion(document_id: int, req: MotionStudyIn, user: CurrentUser, db: Db) -> Any:
    from core.geometry.assembly_motion import simulate_motion
    from core.geometry.errors import GeomError

    doc = _assembly(db, user, document_id)
    comps, _ = _rows(db, doc.document_id)
    comp_dicts = [_comp_out(db, c) for c in comps if not c.suppressed]
    try:
        res = simulate_motion(
            comp_dicts,
            req.drivers,
            req.couplings,
            duration_s=req.duration_s,
            steps=req.steps,
            check_clashes=req.check_clashes,
        )
    except GeomError as e:
        raise ApiError(422, e.code, e.message) from None
    return body({"document_id": document_id, **res})


@router.post("/api/documents/{document_id}/assembly/drawing")
def assembly_drawing(document_id: int, req: AssemblyDrawingIn, user: CurrentUser, db: Db) -> Any:
    import math

    from core.geometry.assembly_drawing import generate_assembly_drawing_dxf

    doc = _assembly(db, user, document_id)
    comps, _ = _rows(db, doc.document_id)
    comp_dicts = [_comp_out(db, c) for c in comps if not c.suppressed]

    explode_steps: list[dict[str, Any]] = []
    if req.explode_id is not None:
        exp = db.get(AssemblyExplode, req.explode_id)
        if exp is None or exp.document_id != doc.document_id:
            raise ApiError(404, "EXPLODE_NOT_FOUND", "Explode view not found")
        for st in exp.steps or []:
            delta = st.get("delta", [0.0, 0.0, 0.0])
            dist = math.sqrt(sum(float(v) * float(v) for v in delta))
            explode_steps.append(
                {
                    "component_ids": st.get("component_ids", []),
                    "direction": delta,
                    "distance_mm": dist,
                }
            )

    dxf_bytes = generate_assembly_drawing_dxf(
        comp_dicts,
        explode_steps=explode_steps,
        explode_factor=req.explode_factor,
        title=req.title,
    )
    fname = f"{doc.doc_no}_assembly_drawing.dxf"
    return Response(
        dxf_bytes,
        media_type="application/dxf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
