"""Track 9 Q4: parametric block templates (plan.md §15.5): the built-in ones plus the company's
own (JSON, saved per install by an administrator), and the block a set of values makes."""

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import CurrentUser, need
from backend.api.common import ApiError, body
from core.dxf import param_blocks as pb

router = APIRouter()
_lock = threading.Lock()
Num = Annotated[float, Field(allow_inf_nan=False, ge=-1e7, le=1e7)]


class InstantiateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: Annotated[
        dict[Annotated[str, Field(max_length=16)], Num], Field(max_length=pb.MAX_PARAMS)
    ] = {}
    state: Annotated[str, Field(max_length=20)] | None = None


class TemplatesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    templates: Annotated[list[dict[str, Any]], Field(max_length=pb.MAX_TEMPLATES)]


def _path() -> Path:
    from backend.api import main  # late: main imports this module

    return Path(main.VAR_DIR) / "param_blocks.json"


_cache: tuple[tuple[str, int, int], list[dict[str, Any]]] | None = None


def _user() -> list[dict[str, Any]]:
    """The company templates, validated once per file change (not on every request)."""
    global _cache
    p = _path()
    try:
        st = p.stat()
    except OSError:
        return []
    key = (str(p), st.st_mtime_ns, st.st_size)
    if _cache and _cache[0] == key:
        return _cache[1]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for t in data if isinstance(data, list) else []:
        try:
            out.append(pb.validate(t))
        except pb.ParamBlockError:
            continue  # an edited file with a bad entry loses that entry, not the list
    _cache = (key, out)
    return out


def templates() -> dict[str, dict[str, Any]]:
    return {t["name"]: {**pb.validate(t), "builtin": True} for t in pb.BUILTIN} | {
        t["name"]: {**t, "builtin": False} for t in _user()
    }


@router.get("/api/param-blocks")
def list_templates(user: CurrentUser) -> Any:
    return body({"items": list(templates().values())})


@router.post("/api/param-blocks/{name}/instantiate")
def instantiate(name: str, req: InstantiateIn, user: CurrentUser) -> Any:
    t = templates().get(name)
    if t is None:
        raise ApiError(404, "PARAM_BLOCK_NOT_FOUND", "No such parametric block")
    try:
        return body(pb.instantiate(t, req.values, req.state))
    except pb.ParamBlockError as e:
        raise ApiError(422, "PARAM_BLOCK_INVALID", str(e)) from None


@router.put("/api/param-blocks")
def put_templates(req: TemplatesIn, user: CurrentUser) -> Any:
    """Replace the company templates (built-in names cannot be reused)."""
    need(user, "ADMIN")
    builtin = {t["name"] for t in pb.BUILTIN}
    clean = []
    for i, t in enumerate(req.templates):
        try:
            v = pb.validate(t)
        except pb.ParamBlockError as e:
            raise ApiError(422, "PARAM_BLOCK_INVALID", f"{i + 1}번째 템플릿: {e}") from None
        if v["name"] in builtin:
            raise ApiError(422, "PARAM_BLOCK_INVALID", f"{v['name']}은(는) 내장 템플릿 이름입니다")
        clean.append(v)
    if len({t["name"] for t in clean}) != len(clean):
        raise ApiError(422, "PARAM_BLOCK_INVALID", "템플릿 이름이 겹칩니다")
    with _lock:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")  # unique: several workers
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(clean, ensure_ascii=False))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    return body({"items": list(templates().values())})
