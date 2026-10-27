"""Track 8 P5: the installed release and in-app updates (per-PC install, plan.md §14.4).

The launcher sets AXCAD_APP_DIR (the program folder, replaced by patches) and AXCAD_RUN_DIR (this
PC's run folder). A patch chosen in the app is checked with the install's own patcher (signature,
version order, grade and skip rules), saved under the run folder, then update.ps1 applies it:
stop -> back up -> swap -> migrate -> start, rolling back on failure, and writes the outcome to
update-result.json for this screen. Without those variables (a shared server) updating is off."""

import base64
import importlib.util
import json
import os
import re
import shutil
import subprocess
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, UploadFile

from backend.api.auth import CurrentUser, local_login, need
from backend.api.common import ApiError, body

router = APIRouter()
MAX_PATCH_BYTES = 4 * 1024**3  # a full upgrade carries the runtimes (~2 GB unpacked)
VERSION_RE = re.compile(r"^(\d{1,4})\.(\d{1,4})\.(\d{1,6})$")
STALE = timedelta(hours=2)  # an update still "running" after this died without saying so
QUOTES = "'\u2018\u2019\u201a\u201b"  # PowerShell takes all of these as a single quote


def label(version: str | None) -> str | None:
    """1.1.0 -> R1.1, 1.1.2 -> R1.1 (수정 2): what users see (plan.md §14.4)."""
    m = VERSION_RE.fullmatch(version or "")
    if not m:
        return None
    major, minor, fix = (int(x) for x in m.groups())
    return f"R{major}.{minor}" + (f" (수정 {fix})" if fix else "")


def _dirs() -> tuple[Path, Path] | None:
    app, run = os.environ.get("AXCAD_APP_DIR"), os.environ.get("AXCAD_RUN_DIR")
    return (Path(app), Path(run)) if app and run else None


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _running(result: Path) -> dict[str, Any] | None:
    """The update in progress, if any (an old RUNNING record means update.ps1 died)."""
    r = _json(result)
    if not isinstance(r, dict) or r.get("status") != "RUNNING":
        return None
    try:
        started = datetime.fromisoformat(r["started_at"])
    except (KeyError, TypeError, ValueError):
        return None
    return r if datetime.now(UTC) - started < STALE else None


@router.get("/api/system/release")
def release(user: CurrentUser) -> Any:
    """Installed version (R-label), its grade and change notes, and the last update's outcome."""
    dirs = _dirs()
    if dirs is None:
        return body({"installed": False, "version": None, "label": None})
    app, run = dirs
    try:
        version = (app / "VERSION").read_text(encoding="ascii").strip()
    except OSError:
        version = None
    rel = _json(app / "RELEASE.json") or {}
    return body(
        {
            "installed": True,
            "version": version,
            "label": label(version),
            "grade": rel.get("grade"),
            "notes": rel.get("notes") or [],
            "released_at": rel.get("released_at"),
            "last_update": _last(run / "update-result.json"),
            "can_update": local_login() is not None,
        }
    )


def _last(result: Path) -> Any:
    r = _json(result)
    if isinstance(r, dict) and r.get("status") == "RUNNING" and _running(result) is None:
        msg = "업데이트 결과를 확인할 수 없습니다. data\\logs와 시작 메뉴 AX-CAD 업데이트로 확인하세요"
        return {**r, "status": "UNKNOWN", "message": msg}
    return r


def _patcher(app: Path) -> Any:
    """The install's own patcher (standard library only), so the rules match update.ps1."""
    spec = importlib.util.spec_from_file_location("axcad_patcher", app / "scripts" / "patcher.py")
    if spec is None or spec.loader is None:
        raise ApiError(409, "UPDATE_UNAVAILABLE", "업데이트 도구가 없습니다")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def launch(app: Path, patch: Path, result: Path) -> None:
    """Start update.ps1 outside this process tree: it stops this API (taskkill /T) while running.
    A WMI-created process has no parent here, so it survives that."""

    def lit(p: Path) -> str:  # a PowerShell single-quoted literal, whatever the folder names
        text = str(p)
        for q in QUOTES:
            text = text.replace(q, q * 2)
        return f"'{text}'"

    inner = f"& {lit(app / 'scripts' / 'update.ps1')} -Patch {lit(patch)} -Result {lit(result)} -NoPause"
    encoded = base64.b64encode(inner.encode("utf-16-le")).decode("ascii")  # no quoting left
    cmd = (
        "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden "
        f"-EncodedCommand {encoded}"
    )
    create = (
        "Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
        f"-Arguments @{{CommandLine='{cmd}'}} | Out-Null"
    )
    subprocess.run(  # fixed program; the paths are ours (install folder, uuid file name)
        ["powershell.exe", "-NoProfile", "-Command", create], check=True, timeout=60
    )


@router.post("/api/system/updates", status_code=202)
def start_update(
    user: CurrentUser, file: Annotated[UploadFile, File(description="signed patch .zip")]
) -> Any:
    """ADMIN, this PC's install only: check the patch, then hand it to update.ps1 (the app
    restarts; poll GET /api/system/release for the outcome)."""
    need(user)
    dirs = _dirs()
    if dirs is None or local_login() is None:
        raise ApiError(409, "UPDATE_UNAVAILABLE", "PC 설치본에서만 업데이트할 수 있습니다")
    app, run = dirs
    result = run / "update-result.json"
    if (app / "rollback.json").exists() or _running(result):
        raise ApiError(409, "UPDATE_IN_PROGRESS", "이전 업데이트가 아직 끝나지 않았습니다")
    folder = run / "patches"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.zip"):  # one patch at a time: earlier uploads are spent
        old.unlink(missing_ok=True)
    dest = folder / f"{uuid.uuid4().hex}.zip"
    size = 0
    with dest.open("wb") as out:
        while chunk := file.file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_PATCH_BYTES:
                out.close()
                dest.unlink(missing_ok=True)
                raise ApiError(413, "FILE_TOO_LARGE", "Patch too large")
            out.write(chunk)
    if shutil.disk_usage(run).free < 3 * size:  # unpacked copy + backup still to come
        dest.unlink(missing_ok=True)
        raise ApiError(409, "DISK_FULL", "디스크 공간이 부족합니다 (패치 크기의 3배 이상 필요)")
    patcher = _patcher(app)
    try:
        pub = patcher._read_hex(app / "scripts" / "patch_pub.hex", 32)
        m = patcher.check(dest, app, pub)
    except (patcher.PatchError, OSError, ValueError, KeyError, zipfile.BadZipFile) as e:
        dest.unlink(missing_ok=True)
        raise ApiError(422, "PATCH_REFUSED", f"패치를 적용할 수 없습니다: {e}") from None
    started = {
        "status": "RUNNING",
        "from": (app / "VERSION").read_text(encoding="ascii").strip(),
        "to": m["version"],
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    tmp = result.with_suffix(".tmp")
    tmp.write_text(json.dumps(started), encoding="utf-8")
    os.replace(tmp, result)
    try:
        launch(app, dest, result)
    except (OSError, subprocess.SubprocessError) as e:
        result.unlink(missing_ok=True)
        raise ApiError(500, "UPDATE_FAILED", f"업데이트를 시작하지 못했습니다: {e}") from None
    rel = m.get("release") or {"grade": "UPDATE", "notes": []}
    return body(
        {
            "version": m["version"],
            "label": label(m["version"]),
            "grade": rel["grade"],
            "notes": rel.get("notes", []),
            "status": "RUNNING",
        }
    )
