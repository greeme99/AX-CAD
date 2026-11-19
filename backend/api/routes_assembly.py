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
