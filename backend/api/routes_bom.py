"""FN-23 BOM: generate from the current DXF revision (blocks) or the 3D model (parts), map part
numbers by hand where the drawing has none, export CSV/JSON."""

import csv
import io
import json
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from backend.api.auth import CurrentUser, Db, get_document, need
from backend.api.common import ApiError, body
from backend.api.routes_master import KST
from backend.api.routes_quote import METRIC_SLOTS, SLOT_WAIT_S, _bodies
from backend.db.models import BomHeader, BomItem, Document
from core.bom.dxf import bom_from_bodies, bom_job
from core.dxf.reader import run_isolated

router = APIRouter()
ROLES = ("DESIGNER", "MANUFACTURING")
PART_NO = r"^[\w.\-/ ]{1,64}$"  # unicode letters allowed (\w), no control or quote characters
FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")
EXPORT_COLS = (
    "item_no",
    "part_no",
    "part_name",
    "qty",
    "unit",
    "level",
    "mapping_status",
    "source_name",
)


class BomCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["REVISION", "MODEL"]


class ItemPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    part_no: Annotated[str, Field(pattern=PART_NO)]
    part_name: Annotated[str, Field(min_length=1, max_length=200)] | None = None


def _out(db: Db, h: BomHeader) -> dict[str, Any]:
    items = db.scalars(
        select(BomItem).where(BomItem.bom_id == h.bom_id).order_by(BomItem.item_no)
    ).all()
    cols = lambda row: {c.key: getattr(row, c.key) for c in row.__table__.columns}
    return {
        **cols(h),
        "items": [cols(i) for i in items],
        "unmapped": sum(i.mapping_status == "UNMAPPED" for i in items),
    }


def _bom(db: Db, user: Any, bom_id: int) -> BomHeader:
    h = db.get(BomHeader, bom_id)
    try:
        if h is None:
            raise ApiError(404, "BOM_NOT_FOUND", "BOM not found")
        get_document(db, user, h.document_id)  # project membership
    except ApiError:
        raise ApiError(404, "BOM_NOT_FOUND", "BOM not found") from None
    return h


@router.post("/api/documents/{document_id}/boms")
# sync def: the DXF is parsed in an isolated worker
def create_bom(document_id: int, req: BomCreate, user: CurrentUser, db: Db) -> Any:
    from backend.api import main  # late: main imports the routers

    need(user, *ROLES)
    doc: Document = get_document(db, user, document_id)
    revision_id = None
    if req.source == "REVISION":
        if doc.current_revision_id is None:
            raise ApiError(409, "NO_REVISION", "Document has no revision")
        revision_id = doc.current_revision_id
        src = main.VAR_DIR / "uploads" / f"{revision_id}.dxf"
        if not src.is_file():
            raise ApiError(404, "REVISION_NOT_FOUND", "Revision not found")
        if not METRIC_SLOTS.acquire(timeout=SLOT_WAIT_S):
            raise ApiError(503, "SERVER_BUSY", "Server busy, retry later")
        try:
            result = json.loads(
                run_isolated(bom_job, str(src), fail=("BOM_FAILED", "BOM 생성에 실패했습니다"))
            )
        finally:
            METRIC_SLOTS.release()
    else:
        result = bom_from_bodies(_bodies(db, document_id))
    if not result["items"]:
        raise ApiError(422, "BOM_EMPTY", "도면에 BOM으로 만들 블록/부품이 없습니다")
    bid = db.scalar(select(func.nextval("bom_headers_bom_id_seq")))
    h = BomHeader(
        bom_id=bid,
        bom_no=f"B-{datetime.now(KST):%Y%m%d}-{bid:06d}",
        project_id=doc.project_id,
        document_id=document_id,
        revision_id=revision_id,
        source_type=result["source_type"],
        warnings=result["warnings"],
        created_by=user.user_id,
    )
    db.add(h)
    db.flush()
    db.add_all(
        BomItem(bom_id=bid, item_no=n, **it) for n, it in enumerate(result["items"], start=1)
    )
    db.commit()
    return body(_out(db, h))


@router.get("/api/documents/{document_id}/boms")
def list_boms(document_id: int, user: CurrentUser, db: Db) -> Any:
    need(user, *ROLES)
    get_document(db, user, document_id)
    rows = db.scalars(
        select(BomHeader)
        .where(BomHeader.document_id == document_id)
        .order_by(BomHeader.bom_id.desc())
        .limit(100)  # ponytail: newest 100, add paging when a document collects more
    ).all()
    keys = ("bom_id", "bom_no", "revision_id", "source_type", "created_at")
    items = [{k: getattr(h, k) for k in keys} for h in rows]
    return body({"items": items, "total": len(items)})


@router.get("/api/boms/{bom_id}")
def get_bom(bom_id: int, user: CurrentUser, db: Db) -> Any:
    need(user, *ROLES)
    return body(_out(db, _bom(db, user, bom_id)))


@router.patch("/api/bom-items/{item_id}")
def map_item(item_id: int, req: ItemPatch, user: CurrentUser, db: Db) -> Any:
    """TC-91: a part number typed in by hand for a block the drawing did not label."""
    need(user, *ROLES)
    bom_id = db.scalar(select(BomItem.bom_id).where(BomItem.bom_item_id == item_id))
    if bom_id is None:
        raise ApiError(404, "BOM_NOT_FOUND", "BOM not found")
    h = _bom(db, user, bom_id)
    it = db.get(BomItem, item_id, with_for_update=True)
    assert it is not None
    it.part_no = req.part_no.strip()
    if req.part_name:
        it.part_name = req.part_name.strip()
    it.mapping_status, it.mapped_by, it.mapped_at = "MANUAL", user.user_id, func.now()
    db.commit()  # the audit trigger keeps the old mapping
    return body(_out(db, h))


def _cell(v: Any) -> Any:
    # CSV opened in Excel: a leading = + - @ would run as a formula
    return "'" + v if isinstance(v, str) and v.startswith(FORMULA_LEAD) else v


@router.get("/api/boms/{bom_id}/export")
def export_bom(
    bom_id: int, user: CurrentUser, db: Db, format: Literal["csv", "json"] = "csv"
) -> Response:
    need(user, *ROLES)
    h = _bom(db, user, bom_id)
    data = _out(db, h)
    rows = [{k: it[k] for k in EXPORT_COLS} for it in data["items"]]
    if format == "json":
        payload = {
            "bom_no": h.bom_no,
            "document_id": h.document_id,
            "revision_id": h.revision_id,
            "source_type": h.source_type,
            "items": rows,
        }
        content = json.dumps(payload, ensure_ascii=False, indent=2).encode()
        media = "application/json"
    else:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(EXPORT_COLS)
        w.writerows([_cell(r[k]) for k in EXPORT_COLS] for r in rows)
        content = ("﻿" + buf.getvalue()).encode()  # BOM mark: Excel opens UTF-8 Korean
        media = "text/csv; charset=utf-8"
    return Response(
        content,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{h.bom_no}.{format}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
