"""Track 11 L5: the PC's AX-LISP library (plan.md §17) — .lsp files kept in the install folder,
whose c: commands the 2D command line runs by name. The server only stores text: it never runs it
(the screen's interpreter does, with its limits)."""

import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import CurrentUser, need
from backend.api.common import ApiError, body

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


class LibraryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files: Annotated[list[LispFile], Field(max_length=MAX_FILES)]


def _path() -> Path:
    from backend.api import main  # late: main imports this module

    return Path(main.VAR_DIR) / "lisp_library.json"


def _out(files: list[dict[str, str]]) -> dict[str, Any]:
    """Each file with the c: commands it defines (read from the text, never run)."""
    items = [
        {**f, "commands": sorted({m.upper() for m in COMMAND.findall(f["source"])})} for f in files
    ]
    return {"items": items}


def _load() -> list[dict[str, str]]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return [LispFile(**f).model_dump() for f in data] if isinstance(data, list) else []
    except (OSError, ValueError, TypeError):
        return []  # a damaged file reads as an empty library, it is replaced on the next save


@router.get("/api/lisp")
def list_library(user: CurrentUser) -> Any:
    return body(_out(_load()))


@router.put("/api/lisp")
def put_library(req: LibraryIn, user: CurrentUser) -> Any:
    """Replace the library (the screen sends all files: add, edit, delete, import)."""
    need(user, "DESIGNER")
    files = [f.model_dump() for f in req.files]
    if len({f["name"].lower() for f in files}) != len(files):
        raise ApiError(422, "LISP_NAME_TAKEN", "같은 이름의 파일이 있습니다")
    with _lock:
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
    return body(_out(files))
