"""Document list/detail, approval workflow (FN-22) and release endpoints."""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import aliased

from backend.api.auth import CurrentUser, Db, get_document, get_project, is_member, need
from backend.api.common import ApiError, body, pick
from backend.db.models import Approval, Document, Revision, User

router = APIRouter()
DocStatus = Literal["DRAFT", "IN_REVIEW", "APPROVED", "REJECTED", "RELEASED"]
DocType = Literal["DRAWING", "PART", "ASSEMBLY"]
DOC_COLS = ("document_id", "project_id", "doc_no", "doc_type", "title", "status")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocumentCreate(_Strict):
    doc_no: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")]
    doc_type: DocType
    title: Annotated[str, Field(min_length=1, max_length=200)]


def revision_out(r: Revision | None) -> dict[str, Any] | None:
    cols = ("revision_id", "revision_no", "parent_revision_id", "checksum", "note")
    return None if r is None else pick(r, *cols, "created_by", "created_at")


def _doc_out(d: Document, rev: Revision | None) -> dict[str, Any]:
    return {
        **pick(d, *DOC_COLS, "current_revision_id", "created_by", "created_at"),
        "current_revision_no": rev.revision_no if rev else None,
    }


@router.get("/api/projects/{project_id}/documents")
def list_documents(
    project_id: int,
    user: CurrentUser,
    db: Db,
    q: Annotated[str | None, Query(max_length=100)] = None,
    status: DocStatus | None = None,
    doc_type: DocType | None = None,
) -> Any:
    get_project(db, user, project_id)
    stmt = (
        select(Document, Revision)
        .outerjoin(Revision, Revision.revision_id == Document.current_revision_id)
        .where(Document.project_id == project_id)
        .order_by(Document.document_id)
    )
    if q:
        stmt = stmt.where(
            Document.doc_no.icontains(q, autoescape=True)
            | Document.title.icontains(q, autoescape=True)
        )
    if status:
        stmt = stmt.where(Document.status == status)
    if doc_type:
        stmt = stmt.where(Document.doc_type == doc_type)
    items = [_doc_out(d, r) for d, r in db.execute(stmt)]
    return body({"items": items, "total": len(items)})


@router.post("/api/projects/{project_id}/documents")
def create_document(project_id: int, req: DocumentCreate, user: CurrentUser, db: Db) -> Any:
    get_project(db, user, project_id)
    need(user, "DESIGNER")
    d = Document(project_id=project_id, created_by=user.user_id, **req.model_dump())
    db.add(d)
    db.commit()  # duplicate (project_id, doc_no) -> IntegrityError -> 409 DUPLICATE_KEY handler
    return body(_doc_out(d, None))


@router.get("/api/documents/{document_id}")
def read_document(document_id: int, user: CurrentUser, db: Db) -> Any:
    d = get_document(db, user, document_id)
    rev = db.get(Revision, d.current_revision_id) if d.current_revision_id else None
    return body({**_doc_out(d, rev), "current_revision": revision_out(rev)})


# --- approvals ---
class ApprovalCreate(_Strict):
    approver_id: int
    comment: Annotated[str, Field(max_length=2000)] | None = None


class Decision(_Strict):
    decision: Literal["APPROVED", "REJECTED"]
    comment: Annotated[str, Field(max_length=2000)] | None = None


def _approval_items(db: Db, *where: Any) -> list[dict[str, Any]]:
    req, app = aliased(User), aliased(User)
    rows = db.execute(
        select(Approval, Document, Revision, req.user_name, app.user_name)
        .join(Document, Document.document_id == Approval.document_id)
        .join(Revision, Revision.revision_id == Approval.revision_id)
        .join(req, req.user_id == Approval.requested_by)
        .join(app, app.user_id == Approval.approver_id)
        .where(*where)
        .order_by(Approval.approval_id.desc())
    )
    return [
        {
            **pick(a, "approval_id", "document_id", "revision_id", "requested_by", "approver_id"),
            **pick(a, "status", "comment", "created_at", "decided_at"),
            "project_id": d.project_id,
            "doc_no": d.doc_no,
            "title": d.title,
            "revision_no": r.revision_no,
            "requested_by_name": rn,
            "approver_name": an,
        }
        for a, d, r, rn, an in rows
    ]


@router.post("/api/documents/{document_id}/approvals")
def request_approval(document_id: int, req: ApprovalCreate, user: CurrentUser, db: Db) -> Any:
    d = get_document(db, user, document_id, lock=True)
    need(user, "DESIGNER")
    if d.status not in ("DRAFT", "REJECTED"):
        raise ApiError(409, "INVALID_STATE", f"Document is {d.status}")
    if d.current_revision_id is None:
        raise ApiError(409, "NO_REVISION", "Document has no revision")
    if req.approver_id == user.user_id:
        raise ApiError(422, "APPROVER_SELF", "Requester cannot approve own request")
    approver = db.get(User, req.approver_id)
    if (
        approver is None
        or not approver.is_active
        or "REVIEWER" not in approver.role_codes
        or not is_member(db, approver, d.project_id)
    ):
        raise ApiError(422, "APPROVER_INVALID", "Approver must be an active REVIEWER member")
    a = Approval(
        document_id=d.document_id,
        revision_id=d.current_revision_id,
        requested_by=user.user_id,
        approver_id=approver.user_id,
        comment=req.comment,
    )
    db.add(a)
    d.status = "IN_REVIEW"
    db.commit()
    return body(_approval_items(db, Approval.approval_id == a.approval_id)[0])


@router.get("/api/approvals")
def list_approvals(
    user: CurrentUser,
    db: Db,
    status: Literal["PENDING", "APPROVED", "REJECTED"] | None = None,
) -> Any:
    where = []
    if "ADMIN" not in user.role_codes:
        where.append(Approval.approver_id == user.user_id)
    if status:
        where.append(Approval.status == status)
    items = _approval_items(db, *where)
    return body({"items": items, "total": len(items)})


@router.post("/api/approvals/{approval_id}/decision")
def decide(approval_id: int, req: Decision, user: CurrentUser, db: Db) -> Any:
    a = db.get(Approval, approval_id)
    if a is None:
        raise ApiError(404, "APPROVAL_NOT_FOUND", "Approval not found")
    if a.approver_id != user.user_id:
        raise ApiError(403, "FORBIDDEN", "Only the assigned approver may decide")
    d = get_document(db, user, a.document_id, lock=True)
    db.refresh(a)
    if a.status != "PENDING" or d.status != "IN_REVIEW":
        raise ApiError(409, "INVALID_STATE", "Approval already decided")
    if req.decision == "REJECTED" and not (req.comment or "").strip():
        raise ApiError(422, "COMMENT_REQUIRED", "A comment is required to reject")
    a.status, a.comment, a.decided_at = req.decision, req.comment, datetime.now(UTC)
    d.status = req.decision
    db.commit()
    return body(_approval_items(db, Approval.approval_id == approval_id)[0])


@router.post("/api/documents/{document_id}/release")
def release(document_id: int, user: CurrentUser, db: Db) -> Any:
    d = get_document(db, user, document_id, lock=True)
    need(user, "REVIEWER")
    if d.status != "APPROVED":
        raise ApiError(409, "INVALID_STATE", "Only APPROVED documents can be released")
    d.status = "RELEASED"
    db.commit()
    rev = db.get(Revision, d.current_revision_id) if d.current_revision_id else None
    return body(_doc_out(d, rev))
