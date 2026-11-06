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
from backend.db.models import AssemblyComponent, AssemblyMate, Document, Feature
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
    source_feature_id: Id
    name: Name | None = None
    placement: Placement = Placement()
    fixed: bool | None = None  # None: the first component is fixed, later ones free


class ComponentPatch(_Strict):
    name: Name | None = None
    placement: Placement | None = None
    fixed: bool | None = None


class FaceSig(_Strict):
    kind: Literal["PLANE", "CYLINDER", "CONE", "SPHERE", "TORUS", "SURFACE"]
    mid: Annotated[list[Coord], Field(min_length=3, max_length=3)]
    area: Annotated[float, Field(allow_inf_nan=False, ge=0, le=1e12)]
    dir: Annotated[list[Coord], Field(min_length=3, max_length=3)] | None = None
    radius: Annotated[float, Field(allow_inf_nan=False, gt=0, le=1e6)] | None = None


class MateSide(_Strict):
    component_id: Id
    face: FaceSig


class MateIn(_Strict):
    mate_type: Literal["COINCIDENT", "DISTANCE", "PARALLEL", "ANGLE", "CONCENTRIC"]
    a: MateSide
    b: MateSide
    value: Annotated[float, Field(allow_inf_nan=False, ge=-1e6, le=1e6)] | None = None
    flip: bool = False


class InterferenceIn(_Strict):
    clearance: Annotated[float, Field(allow_inf_nan=False, ge=0, le=1000)] = 0.0


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
    f = _latest(db, c.source_document_id, c.source_feature_id)
    src = db.get(Document, c.source_document_id)
    return {
        "component_id": c.component_id,
        "name": c.name,
        "source_document_id": c.source_document_id,
        "source_doc_no": src.doc_no if src else None,
        "source_feature_id": c.source_feature_id,
        "body_feature_id": f.feature_id if f else None,
        "status": "OK" if f else "MISSING",
        "bbox": f.metrics["bbox"] if f and f.metrics else None,
        "placement": c.placement,
        "fixed": c.fixed,
        "seq": c.seq,
    }


def _mate_out(m: AssemblyMate) -> dict[str, Any]:
    return {
        "mate_id": m.mate_id,
        "mate_type": m.mate_type,
        "a": m.a,
        "b": m.b,
        "value": m.value,
        "flip": m.flip,
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


@router.post("/api/documents/{document_id}/components")
def add_component(document_id: int, req: ComponentIn, user: CurrentUser, db: Db) -> Any:
    need(user, "DESIGNER")
    doc = _assembly(db, user, document_id, lock=True)
    f = db.get(Feature, req.source_feature_id)
    src = db.get(Document, f.document_id) if f else None
    if f is None or src is None or src.project_id != doc.project_id or src.doc_type == "ASSEMBLY":
        raise ApiError(422, "COMPONENT_SOURCE_INVALID", "Pick a body of a part in this project")
    if _latest(db, src.document_id, f.feature_id) is None:
        raise ApiError(422, "COMPONENT_SOURCE_INVALID", "That feature has no body")
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
    c = AssemblyComponent(
        document_id=document_id,
        source_document_id=src.document_id,
        source_feature_id=f.feature_id,
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
    c, _ = _component(db, user, component_id, lock=True)
    if req.name is not None:
        c.name = req.name
    if req.placement is not None:
        c.placement = req.placement.model_dump()
    if req.fixed is not None:
        c.fixed = req.fixed
    db.commit()
    return body(_comp_out(db, c))


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


def _face_geoms(
    db: Db, comps: list[AssemblyComponent], sides: list[tuple[int, dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Face signatures (component id, sig) -> plane / cylinder geometry in part coordinates."""
    by_id = {c.component_id: c for c in comps}
    if any(cid not in by_id for cid, _ in sides):
        raise ApiError(
            422, "ASSEMBLY_MATE_INVALID", "A mate refers to a component of another assembly"
        )
    parts, key_of = _parts(db, [by_id[cid] for cid in {cid for cid, _ in sides}])
    out: list[dict[str, Any] | None] = [None] * len(sides)
    for key, brep in parts.items():
        idx = [i for i, (cid, _) in enumerate(sides) if key_of[cid] == key]
        geoms = run_kernel(asm.faces_job, brep, [sides[i][1] for i in idx])
        for i, g in zip(idx, geoms, strict=True):
            out[i] = g
    return [g for g in out if g is not None]


def _slot() -> None:
    if not ASM_SLOTS.acquire(timeout=SLOT_WAIT_S):
        raise ApiError(503, "SERVER_BUSY", "Server busy, retry later")


def _snapshot(
    comps: list[AssemblyComponent], mates: list[AssemblyMate]
) -> tuple[dict[int, Any], set[int]]:
    return {c.component_id: (c.placement, c.fixed) for c in comps}, {m.mate_id for m in mates}


def _run_solve(db: Db, user: Any, document_id: int) -> dict[str, Any]:
    """Read the assembly and work out the face geometry without holding the document lock,
    solve in the kernel, then lock and apply — unless someone changed the assembly meanwhile."""
    _assembly(db, user, document_id)
    comps, mates = _rows(db, document_id)
    if not mates:
        return {"dof": None}
    sides = [(m.a["component_id"], m.a["face"]) for m in mates] + [
        (m.b["component_id"], m.b["face"]) for m in mates
    ]
    geoms = _face_geoms(db, comps, sides)
    n = len(mates)
    spec = [
        {
            "type": m.mate_type,
            "a": {**geoms[i], "component": m.a["component_id"]},
            "b": {**geoms[n + i], "component": m.b["component_id"]},
            "value": m.value,
            "flip": m.flip,
        }
        for i, m in enumerate(mates)
    ]
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
    for mid, r in zip(order, res["mates"], strict=True):
        by_mate[mid].status, by_mate[mid].residual = r["status"], r["residual"]
    db.commit()
    return {"dof": res["dof"]}


@router.post("/api/documents/{document_id}/mates")
def add_mate(document_id: int, req: MateIn, user: CurrentUser, db: Db) -> Any:
    need(user, "DESIGNER")
    doc = _assembly(db, user, document_id)
    comps, _ = _rows(db, document_id)
    ids = {c.component_id for c in comps}
    if not {req.a.component_id, req.b.component_id} <= ids:
        raise ApiError(422, "ASSEMBLY_MATE_INVALID", "Pick faces of this assembly's components")
    if req.mate_type in ("DISTANCE", "ANGLE") and req.value is None:
        raise ApiError(422, "ASSEMBLY_MATE_INVALID", f"{req.mate_type} needs a value")
    a, b = req.a.face.model_dump(), req.b.face.model_dump()
    ga, gb = _face_geoms(db, comps, [(req.a.component_id, a), (req.b.component_id, b)])
    # the faces must suit the mate (planes for a distance...): checked before anything is stored
    asm._check(
        {
            "type": req.mate_type,
            "a": {**ga, "component": req.a.component_id},
            "b": {**gb, "component": req.b.component_id},
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
            a={"component_id": req.a.component_id, "face": a},
            b={"component_id": req.b.component_id, "face": b},
            value=req.value,
            flip=req.flip,
            created_by=user.user_id,
        )
    )
    db.commit()
    extra = _run_solve(db, user, document_id)  # an unsatisfiable mate is kept, marked FAILED
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
    comps, _ = _rows(db, doc.document_id)
    if not comps:
        raise ApiError(409, "ASSEMBLY_EMPTY", "The assembly has no components")
    parts, key_of = _parts(db, comps)
    return parts, [
        {"id": c.component_id, "name": c.name, "part": key_of[c.component_id], **c.placement}
        for c in comps
    ]


@router.post("/api/documents/{document_id}/interference")
def interference(document_id: int, req: InterferenceIn, user: CurrentUser, db: Db) -> Any:
    need(user, "DESIGNER", "ESTIMATOR", "REVIEWER")
    doc = _assembly(db, user, document_id)
    parts, comps = _placed(db, doc)
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
            timeout_s=INTERFERENCE_TIMEOUT_S,
        )
    finally:
        ASM_SLOTS.release()
    return body({"document_id": document_id, "clearance": req.clearance, **res})


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
