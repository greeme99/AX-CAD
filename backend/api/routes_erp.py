"""FN-24 ERP transfer of an approved BOM: idempotent job per document + revision, retries in the
background, monitoring (SCR-17) and manual retry."""

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks
from sqlalchemy import select

from backend.api.auth import CurrentUser, Db, get_document, need
from backend.api.common import ApiError, body
from backend.api.routes_bom import _bom, _out
from backend.api.routes_quote import _bodies
from backend.db.models import BomHeader, Document, IntegrationJob, Project, ProjectMember, Revision
from backend.services import erp
from core.bom.dxf import source_hash

router = APIRouter()
STALE = timedelta(minutes=10)  # a RUNNING job this old lost its worker (restart): retry allowed
JOB_COLS = (
    "job_id",
    "target_system",
    "job_type",
    "idempotency_key",
    "project_id",
    "bom_id",
    "status",
    "attempt_count",
    "response_payload",
    "last_error",
    "created_by",
    "created_at",
    "updated_at",
)


def _job_out(j: IntegrationJob, user: Any, duplicate: bool = False) -> dict[str, Any]:
    out = {**{c: getattr(j, c) for c in JOB_COLS}, "duplicate": duplicate}
    if j.response_payload and "ADMIN" not in user.role_codes:  # ERP error bodies: admins only
        out["response_payload"] = {"status": j.response_payload.get("status")}
    return out


def _sendable(db: Db, h: BomHeader, doc: Document) -> dict[str, Any]:
    """FN-24 gate, checked on the first send and again on every retry."""
    if doc.status not in ("APPROVED", "RELEASED"):  # FN-22: nothing leaves before approval
        raise ApiError(403, "BOM_NOT_APPROVED", "승인(또는 배포)된 도면의 BOM만 전송할 수 있습니다")
    current = (
        h.revision_id == doc.current_revision_id
        if h.revision_id is not None
        else h.source_hash == source_hash(_bodies(db, doc.document_id))
    )
    if not current:
        raise ApiError(
            409, "BOM_OUTDATED", "현재 승인된 도면·모델에서 다시 생성한 BOM을 전송하세요"
        )
    data = _out(db, h)
    if data["unmapped"]:
        raise ApiError(409, "BOM_UNMAPPED", f"미매핑 품목 {data['unmapped']}건의 품번을 지정하세요")
    if not erp.configured():
        raise ApiError(409, "ERP_NOT_CONFIGURED", "ERP 연동 주소가 설정되지 않았습니다")
    return data


def _payload(db: Db, bom: dict[str, Any], doc: Document, key: str) -> dict[str, Any]:
    project = db.get(Project, doc.project_id)
    rev = db.get(Revision, bom["revision_id"]) if bom["revision_id"] else None
    return {
        "schema": "ax-cad.bom.v1",
        "idempotency_key": key,
        "bom_no": bom["bom_no"],
        "project_code": project.project_code if project else None,
        "document": {
            "doc_no": doc.doc_no,
            "title": doc.title,
            "revision_no": rev.revision_no if rev else None,
            "source_type": bom["source_type"],
        },
        "items": [
            {k: it[k] for k in ("item_no", "part_no", "part_name", "qty", "unit", "level")}
            for it in bom["items"]
        ],
    }


@router.post("/api/boms/{bom_id}/erp")
def send_bom(bom_id: int, user: CurrentUser, db: Db, tasks: BackgroundTasks) -> Any:
    need(user, "MANUFACTURING")
    h = _bom(db, user, bom_id)
    doc = get_document(db, user, h.document_id, lock=True)  # serializes sends per document
    data = _sendable(db, h, doc)
    # document + revision (TC-95); the 3D model has no revision: document + model state
    key = f"BOM:{doc.document_id}:{h.revision_id or 'MODEL:' + h.source_hash[:16]}"
    job = db.scalar(select(IntegrationJob).where(IntegrationJob.idempotency_key == key))
    if job is not None:  # already sent or on its way: never a second transfer
        return body(_job_out(job, user, duplicate=True))
    job = IntegrationJob(
        target_system="ERP",
        job_type="BOM",
        idempotency_key=key,
        project_id=doc.project_id,
        bom_id=bom_id,
        request_payload=_payload(db, data, doc, key),
        created_by=user.user_id,
    )
    db.add(job)
    db.commit()
    tasks.add_task(erp.deliver, job.job_id)
    return body(_job_out(job, user))


@router.get("/api/integration-jobs")
def list_jobs(
    user: CurrentUser,
    db: Db,
    status: Literal["PENDING", "RUNNING", "SUCCESS", "FAILED"] | None = None,
    bom_id: int | None = None,
) -> Any:
    need(user, "MANUFACTURING")
    q = select(IntegrationJob).order_by(IntegrationJob.job_id.desc()).limit(200)
    if "ADMIN" not in user.role_codes:
        q = q.where(
            IntegrationJob.project_id.in_(
                select(ProjectMember.project_id).where(ProjectMember.user_id == user.user_id)
            )
        )
    if status:
        q = q.where(IntegrationJob.status == status)
    if bom_id is not None:
        q = q.where(IntegrationJob.bom_id == bom_id)
    items = [_job_out(j, user) for j in db.scalars(q)]
    return body({"items": items, "total": len(items)})


@router.post("/api/integration-jobs/{job_id}/retry")
def retry_job(job_id: int, user: CurrentUser, db: Db, tasks: BackgroundTasks) -> Any:
    need(user, "MANUFACTURING")
    bom_id = db.scalar(select(IntegrationJob.bom_id).where(IntegrationJob.job_id == job_id))
    try:
        if bom_id is None:
            raise ApiError(404, "JOB_NOT_FOUND", "Job not found")
        h = _bom(db, user, bom_id)  # project membership
    except ApiError:
        raise ApiError(404, "JOB_NOT_FOUND", "Job not found") from None
    doc = get_document(db, user, h.document_id, lock=True)  # same lock order as send_bom
    job = db.get(IntegrationJob, job_id, with_for_update=True)
    assert job is not None
    stale = job.status in ("PENDING", "RUNNING") and datetime.now(UTC) - job.updated_at > STALE
    if job.status != "FAILED" and not stale:
        raise ApiError(409, "INVALID_STATE", f"Job is {job.status}")
    _sendable(db, h, doc)  # a drawing re-opened or revised since stays home
    # run_id = None: a worker still holding the old run can no longer write
    job.status, job.attempt_count, job.last_error, job.run_id = "PENDING", 0, None, None
    db.commit()
    tasks.add_task(erp.deliver, job.job_id)
    return body(_job_out(job, user))
