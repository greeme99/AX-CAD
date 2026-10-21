"""Document list and detail (the FN-22 approval workflow was removed in Track 7)."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from backend.api.auth import CurrentUser, Db, get_document, get_project, need
from backend.api.common import ApiError, body, pick
from backend.db.models import Document, Revision

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
    if get_project(db, user, project_id).status == "ARCHIVED":
        raise ApiError(409, "PROJECT_ARCHIVED", "Archived projects are read-only")
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
