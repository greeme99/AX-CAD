"""Track 11 L5: the PC's AX-LISP library (plan.md §17) — .lsp files kept in the install folder,
whose c: commands the 2D command line runs by name. The server only stores text: it never runs it
(the screen's interpreter does, with its limits)."""

import json
import os
import re
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter
from fastapi import Path as PathParam
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import CurrentUser, Db, need
from backend.api.common import ApiError, body
from backend.db.models import AuditLog

router = APIRouter()
_lock = threading.Lock()
MAX_FILES = 200
MAX_SOURCE = 64 * 1024
NAME = r"^[\w가-힣][\w가-힣 .\-]{0,59}$"
COMMAND = re.compile(r"\(\s*defun\s+c:([A-Za-z0-9_\-]{1,40})", re.IGNORECASE)


class LispFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(pattern=NAME)]
    source: Annotated[str, Field(max_length=MAX_SOURCE)]
    # W3: when a program last wrote it (vl-file-systime); set by the server, never by the client
    modified: Annotated[str, Field(max_length=40)] | None = None


class LispFileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str, Field(pattern=NAME)]
    source: Annotated[str, Field(max_length=MAX_SOURCE)]


class LibraryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files: Annotated[list[LispFileIn], Field(max_length=MAX_FILES)]


def _path() -> Path:
    from backend.api import main  # late: main imports this module

    return Path(main.VAR_DIR) / "lisp_library.json"


def _out(files: list[dict[str, str]]) -> dict[str, Any]:
    """Each file with the c: commands it defines (read from the text, never run)."""
    items = [
        {**f, "commands": sorted({m.upper() for m in COMMAND.findall(f["source"])})} for f in files
    ]
    return {"items": items}


def _load(strict: bool = False) -> list[dict[str, str]]:
    """The library; damaged reads as empty for the screen, but `strict` (a program writing one
    file) refuses instead of replacing everything with that one file (security review A1)."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise TypeError("not a list")
        return [LispFile(**f).model_dump() for f in data]
    except FileNotFoundError:
        return []
    except (OSError, ValueError, TypeError):
        if strict:
            raise ApiError(
                500, "LISP_LIBRARY_DAMAGED", "LISP 라이브러리 파일이 손상되었습니다"
            ) from None
        return []  # a damaged file reads as an empty library, it is replaced on the next save


def _save(files: list[dict[str, str]]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(files, ensure_ascii=False))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@router.get("/api/lisp")
def list_library(user: CurrentUser) -> Any:
    return body(_out(_load()))


@router.put("/api/lisp")
def put_library(req: LibraryIn, user: CurrentUser) -> Any:
    """Replace the library (the screen sends all files: add, edit, delete, import)."""
    need(user, "DESIGNER")
    now = _now()
    old = {f["name"].lower(): f for f in _load()}
    # W3 review L2: a file whose text did not change keeps its time (vl-file-systime)
    files = [
        {
            **f.model_dump(),
            "modified": (
                o.get("modified")
                if (o := old.get(f.name.lower())) and o["source"] == f.source
                else None
            )
            or now,
        }
        for f in req.files
    ]
    if len({f["name"].lower() for f in files}) != len(files):
        raise ApiError(422, "LISP_NAME_TAKEN", "같은 이름의 파일이 있습니다")
    with _lock:
        _save(files)
    return body(_out(files))


# 2D automation A1: AX-LISP open/read-line/write-line/load_dialog work on one file at a time.
# A program may write data files only (DATA_EXT): it can never replace a LISP program.
DATA_EXT = (".txt", ".csv", ".dat", ".log", ".dcl", ".las")  # .las: layer states (W2)
FileName = Annotated[str, PathParam(pattern=NAME)]


class FileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Annotated[str, Field(max_length=MAX_SOURCE)]


@router.get("/api/lisp/files/{name}")
def read_file(name: FileName, user: CurrentUser) -> Any:
    f = next((f for f in _load() if f["name"].lower() == name.lower()), None)
    if f is None:
        raise ApiError(404, "LISP_FILE_NOT_FOUND", "라이브러리에 없는 파일입니다")
    return body(f)


@router.put("/api/lisp/files/{name}")
def write_file(name: FileName, req: FileIn, user: CurrentUser, db: Db) -> Any:
    need(user, "DESIGNER")
    if not name.lower().endswith(DATA_EXT):
        raise ApiError(
            422,
            "LISP_FILE_KIND",
            "프로그램은 데이터 파일(.txt·.csv·.dat·.log·.dcl)만 쓸 수 있습니다",
        )
    with _lock:  # one read-modify-write at a time: concurrent writes never drop each other's files
        files = _load(strict=True)
        old = next((f for f in files if f["name"].lower() == name.lower()), None)
        if old is None and len(files) >= MAX_FILES:
            raise ApiError(422, "LISP_LIBRARY_FULL", f"라이브러리는 파일 {MAX_FILES}개까지입니다")
        keep = old["name"] if old else name
        entry = {"name": keep, "source": req.source, "modified": _now()}
        files = [f for f in files if f is not old] + [entry]
        _save(files)
    _audit(db, user, "WRITE", keep, {"bytes": len(req.source.encode())})
    return body(entry)


@router.get("/api/lisp/names")
def list_names(user: CurrentUser) -> Any:
    """W3 (vl-directory-files, vl-file-systime): names and times, without the sources."""
    return body({"items": [{"name": f["name"], "modified": f.get("modified")} for f in _load()]})


@router.delete("/api/lisp/files/{name}")
def delete_file(name: FileName, user: CurrentUser, db: Db) -> Any:
    """W3 (vl-file-delete / rename): a program removes data files only, never a LISP program."""
    need(user, "DESIGNER")
    if not name.lower().endswith(DATA_EXT):
        raise ApiError(
            422,
            "LISP_FILE_KIND",
            "프로그램은 데이터 파일(.txt·.csv·.dat·.log·.dcl)만 지울 수 있습니다",
        )
    with _lock:
        files = _load(strict=True)
        kept = [f for f in files if f["name"].lower() != name.lower()]
        if len(kept) == len(files):
            raise ApiError(404, "LISP_FILE_NOT_FOUND", "라이브러리에 없는 파일입니다")
        _save(kept)
    _audit(db, user, "DELETE", name, None)
    return body({"deleted": name})


def _audit(db: Any, user: Any, action: str, name: str, value: dict[str, Any] | None) -> None:
    """W3 review M2: who let a program write or delete which library data file."""
    db.add(
        AuditLog(
            object_type="lisp_files",
            object_id=name,
            action=action,
            old_value=None,
            new_value=value,
            user_id=user.user_id,
        )
    )
    db.commit()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
