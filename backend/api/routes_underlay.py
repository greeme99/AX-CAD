"""2D underlays U1 (plan.md §21): PDF and DWFx reference files of a project, read-only.

The upload is checked by its own bytes (never the name), parsed in an isolated worker, and stored
once per project by content (var/underlays/<sha256>.<kind>). The page's vector cache and the
images for the screen are made on demand, also in the worker. The server never changes the file."""

import hashlib
import json
import os
import re
import tempfile
import uuid
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from typing import Annotated, Any

from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import select

from backend.api.auth import CurrentUser, Db, get_project, need
from backend.api.common import ApiError, body
from backend.db.models import UnderlayFile
from core.underlay import MAX_RENDER_PX, MAX_UNDERLAY_BYTES, UnderlayError, ops, worker

router = APIRouter()
MAX_PER_PROJECT = 100
NAME_CLEAN = re.compile(r"[^\w가-힣 .()\-]")
CACHE_ITEMS = 64
_cache: "OrderedDict[tuple[Any, ...], bytes]" = OrderedDict()
_cache_lock = Lock()


def _dir() -> Path:
    from backend.api import main  # late: main imports this module

    d = Path(main.VAR_DIR) / "underlays"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(row: UnderlayFile) -> str:
    p = _dir() / f"{row.sha256}.{row.kind}"
    if not p.is_file():
        raise ApiError(404, "UNDERLAY_FILE_MISSING", "참조 파일이 저장소에 없습니다")
    return str(p)


def _err(exc: UnderlayError) -> ApiError:
    return ApiError(exc.http_status, exc.code, exc.message)


def _out(row: UnderlayFile) -> dict[str, Any]:
    return {
        "underlay_id": row.underlay_id,
        "project_id": row.project_id,
        "kind": row.kind,
        "name": row.name,
        "sha256": row.sha256,
        "size_bytes": row.size_bytes,
        "page_count": row.page_count,
        "pages": row.pages,
        "created_at": row.created_at,
    }


def get_underlay(db: Db, user: Any, underlay_id: str) -> UnderlayFile:
    """404 when it does not exist or the project is not the user's (existence does not leak)."""
    row = db.get(UnderlayFile, underlay_id)
    if row is None:
        raise ApiError(404, "UNDERLAY_NOT_FOUND", "참조 파일을 찾을 수 없습니다")
    get_project(db, user, row.project_id)
    return row


@router.post("/api/projects/{project_id}/underlays")
# sync def: FastAPI runs it in a threadpool, so the worker wait never stalls the event loop
def upload(
    project_id: int,
    user: CurrentUser,
    db: Db,
    file: UploadFile = File(...),  # noqa: B008
) -> Any:
    get_project(db, user, project_id)
    need(user, "DESIGNER")
    data = file.file.read(MAX_UNDERLAY_BYTES + 1)
    if len(data) > MAX_UNDERLAY_BYTES:
        raise ApiError(413, "UNDERLAY_TOO_BIG", "참조 파일은 100 MB까지입니다")
    try:
        kind = ops.classify(data)
    except UnderlayError as exc:
        raise _err(exc) from None
    sha = hashlib.sha256(data).hexdigest()
    old = db.scalar(
        select(UnderlayFile).where(
            UnderlayFile.project_id == project_id, UnderlayFile.sha256 == sha
        )
    )
    if old is not None:
        return body(_out(old))  # the same file again: the row that exists
    count = len(
        db.scalars(
            select(UnderlayFile.underlay_id).where(UnderlayFile.project_id == project_id)
        ).all()
    )
    if count >= MAX_PER_PROJECT:
        raise ApiError(
            422, "UNDERLAY_LIMIT", f"프로젝트당 참조 파일은 {MAX_PER_PROJECT}개까지입니다"
        )
    dest = _dir() / f"{sha}.{kind}"
    if not dest.is_file():  # content-addressed: another project may have stored it already
        fd, tmp = tempfile.mkstemp(dir=_dir(), suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            meta = worker.run(ops.info, kind, tmp, timeout_s=60)  # parse before it is kept
            os.replace(tmp, dest)
        except UnderlayError as exc:
            raise _err(exc) from None
        finally:
            Path(tmp).unlink(missing_ok=True)
    else:
        try:
            meta = worker.run(ops.info, kind, str(dest), timeout_s=60)
        except UnderlayError as exc:
            raise _err(exc) from None
    name = NAME_CLEAN.sub(
        "_", (file.filename or f"underlay.{kind}").replace("\\", "/").rsplit("/", 1)[-1]
    )[:120]
    row = UnderlayFile(
        underlay_id=uuid.uuid4().hex,
        project_id=project_id,
        kind=kind,
        name=name or f"underlay.{kind}",
        sha256=sha,
        size_bytes=len(data),
        page_count=meta["page_count"],
        pages=meta["pages"],
        created_by=user.user_id,
    )
    db.add(row)
    db.commit()  # the file is kept and the row is committed together; a rollback leaves only a stored file
    return body(_out(row))


@router.get("/api/projects/{project_id}/underlays")
def list_underlays(project_id: int, user: CurrentUser, db: Db) -> Any:
    get_project(db, user, project_id)
    rows = db.scalars(
        select(UnderlayFile)
        .where(UnderlayFile.project_id == project_id)
        .order_by(UnderlayFile.created_at)
    ).all()
    return body({"items": [_out(r) for r in rows]})


@router.get("/api/underlays/{underlay_id}")
def get_one(underlay_id: str, user: CurrentUser, db: Db) -> Any:
    return body(_out(get_underlay(db, user, underlay_id)))


def _page(row: UnderlayFile, n: int) -> None:
    if not 0 <= n < row.page_count:
        raise ApiError(404, "UNDERLAY_PAGE", "없는 페이지입니다")


@router.get("/api/underlays/{underlay_id}/pages/{page}/vectors")
def vectors(underlay_id: str, page: int, user: CurrentUser, db: Db) -> Any:
    row = get_underlay(db, user, underlay_id)
    _page(row, page)
    cache = _dir() / f"{row.sha256}.p{page}.json"
    try:
        if cache.is_file():
            return body(json.loads(cache.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass  # a damaged cache is rebuilt
    try:
        vec = worker.run(ops.vectors, row.kind, _path(row), page, timeout_s=60)
    except UnderlayError as exc:
        raise _err(exc) from None
    fd, tmp = tempfile.mkstemp(dir=_dir(), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(vec, separators=(",", ":")))
        os.replace(tmp, cache)
    except OSError:
        Path(tmp).unlink(missing_ok=True)  # the cache is only a speed-up
    return body(vec)


def _crop(
    x0: float | None, y0: float | None, x1: float | None, y1: float | None
) -> tuple[float, float, float, float] | None:
    """The window of an image request: all four numbers or none, a real size, sane values."""
    if x0 is None and y0 is None and x1 is None and y1 is None:
        return None
    if x0 is None or y0 is None or x1 is None or y1 is None:
        raise ApiError(422, "UNDERLAY_CROP", "x0,y0,x1,y1를 모두 주어야 합니다")
    if not all(abs(v) < 1e7 for v in (x0, y0, x1, y1)):  # also false for nan
        raise ApiError(422, "UNDERLAY_CROP", "영역 값이 올바르지 않습니다")
    if x1 <= x0 or y1 <= y0:
        raise ApiError(422, "UNDERLAY_CROP", "영역의 크기가 0 이하입니다")
    return (x0, y0, x1, y1)


@router.get("/api/underlays/{underlay_id}/pages/{page}/image")
def image(
    underlay_id: str,
    page: int,
    user: CurrentUser,
    db: Db,
    width: Annotated[int, Query(ge=16, le=MAX_RENDER_PX)] = 1600,
    hide: Annotated[str, Query(max_length=2000)] = "",
    x0: float | None = None,
    y0: float | None = None,
    x1: float | None = None,
    y1: float | None = None,
) -> Response:
    """A PNG of the page, or of the window x0,y0..x1,y1 (page mm), with layers `hide` (names
    separated by |) switched off."""
    row = get_underlay(db, user, underlay_id)
    _page(row, page)
    crop = _crop(x0, y0, x1, y1)
    hidden = sorted({h for h in hide.split("|") if h})[:100]
    key = (row.sha256, page, width, tuple(hidden), crop)
    with _cache_lock:
        png = _cache.get(key)
        if png is not None:
            _cache.move_to_end(key)
    if png is None:
        try:
            png = worker.run(
                ops.render, row.kind, _path(row), page, width, hidden, crop, timeout_s=60
            )
        except UnderlayError as exc:
            raise _err(exc) from None
        with _cache_lock:
            _cache[key] = png
            while len(_cache) > CACHE_ITEMS:
                _cache.popitem(last=False)
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


@router.delete("/api/underlays/{underlay_id}")
def delete(underlay_id: str, user: CurrentUser, db: Db) -> Any:
    row = get_underlay(db, user, underlay_id)
    need(user, "DESIGNER")  # a designer of the project (get_underlay checked the membership)
    sha = row.sha256
    db.delete(row)
    db.commit()
    others = db.scalar(select(UnderlayFile.underlay_id).where(UnderlayFile.sha256 == sha))
    if others is None:  # no project keeps this content any more
        for p in _dir().glob(f"{sha}.*"):
            p.unlink(missing_ok=True)
    return body({"deleted": underlay_id})
