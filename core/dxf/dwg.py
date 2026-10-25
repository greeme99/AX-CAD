"""Track 8 P1-5: DWG uploads through a converter the user installed (not bundled):
ODA File Converter (free, closed source) or LibreDWG dwg2dxf (GPLv3, run as a separate program).
The resulting DXF then goes through the normal strict DXF checks and parser."""

import os
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path

try:
    import resource
except ImportError:  # Windows
    resource = None  # type: ignore[assignment]

from core.dxf.reader import MAX_FILE_BYTES, DxfError

# every DWG starts with its version string (AC1009 .. AC1032)
DWG_MAGIC = re.compile(rb"^AC10[0-9]{2}")
CONVERT_TIMEOUT_S = 120
# the converter is native code reading an untrusted file (BUG-48): its whole process tree is
# killed on timeout or when its folder grows past the cap, and on POSIX no file may exceed
# FILE_CAP and memory is limited
FILE_CAP = MAX_FILE_BYTES + 1024**2
MEMORY_CAP = 4 * 1024**3
POLL_S = 0.2
# the converter is someone else's program: it gets no database URL, JWT secret or local token
ENV_KEEP = ("PATH", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL", "HOME", "USERPROFILE")


def converter() -> tuple[str, str] | None:
    """(executable, kind) from AXCAD_DWG_CONVERTER, or None when none is configured."""
    path = Path(os.environ.get("AXCAD_DWG_CONVERTER", ""))
    if not path.is_absolute() or not path.is_file():
        return None
    name = path.name.lower()
    if name.startswith("odafileconverter"):
        return str(path), "ODA"
    if name.startswith("dwg2dxf"):
        return str(path), "LibreDWG"
    return None


def _limit(pid: int) -> None:
    """Linux: file-size and memory limits on the started converter (preexec_fn is not safe in a
    threaded server). Children it already forked are still covered by the folder-size watch."""
    if resource is None or not hasattr(resource, "prlimit"):
        return
    try:
        resource.prlimit(pid, resource.RLIMIT_FSIZE, (FILE_CAP, FILE_CAP))
        resource.prlimit(pid, resource.RLIMIT_AS, (MEMORY_CAP, MEMORY_CAP))
    except (ProcessLookupError, PermissionError, ValueError):
        pass


def _kill_tree(p: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        taskkill = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32", "taskkill.exe")
        subprocess.run(
            [str(taskkill), "/T", "/F", "/PID", str(p.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(p.pid, signal.SIGKILL)  # its own session: children included
        except ProcessLookupError:
            pass
    p.wait()


def _size(folder: str) -> int:
    total = 0
    for root, _, files in os.walk(folder):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def dwg_to_dxf(data: bytes) -> tuple[bytes, str, bool]:
    """Converted DXF bytes, the converter kind and whether it reported success (exit code 0)."""
    if not DWG_MAGIC.match(data):
        raise DxfError("DWG_INVALID_FILE", "Not a DWG file", 422)
    conv = converter()
    if conv is None:
        raise DxfError("DWG_CONVERTER_MISSING", "No DWG converter is installed", 422)
    exe, kind = conv
    # a leftover file held by antivirus must not turn a good conversion into an error
    with tempfile.TemporaryDirectory(prefix="axcad-dwg-", ignore_cleanup_errors=True) as tmp:
        src_dir, out_dir = Path(tmp, "in"), Path(tmp, "out")
        src_dir.mkdir()
        out_dir.mkdir()
        src = src_dir / "drawing.dwg"  # fixed names: nothing from the upload reaches the command
        src.write_bytes(data)
        result = out_dir / "drawing.dxf"
        if kind == "ODA":  # input folder, output folder, version, type, recurse, audit, filter
            args = [exe, str(src_dir), str(out_dir), "ACAD2018", "DXF", "0", "1", "drawing.dwg"]
        else:
            args = [exe, "-y", "-o", str(result), str(src)]
        env = {k: v for k, v in os.environ.items() if k.upper() in ENV_KEEP}
        env.update(TEMP=tmp, TMP=tmp, TMPDIR=tmp)
        posix = os.name != "nt"
        try:
            # fixed argument list, no shell; the program is the one the administrator configured
            p = subprocess.Popen(
                args,
                cwd=tmp,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=posix,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),  # no console on Windows
            )
        except OSError:
            raise DxfError(
                "DWG_CONVERT_FAILED", "DWG converter could not be started", 422
            ) from None
        _limit(p.pid)
        deadline = time.monotonic() + CONVERT_TIMEOUT_S
        cap = len(data) + 2 * FILE_CAP  # input + output + the converter's own temp files
        while p.poll() is None:
            if time.monotonic() > deadline:
                _kill_tree(p)
                raise DxfError("DWG_CONVERT_TIMEOUT", "DWG conversion timed out", 422)
            if _size(tmp) > cap:
                _kill_tree(p)
                raise DxfError("FILE_TOO_LARGE", "Converted drawing exceeds 50 MB", 413)
            time.sleep(POLL_S)
        if posix:
            _kill_tree(p)  # nothing it started may keep running (or writing) after it exits
        if not result.is_file() or result.stat().st_size == 0:
            raise DxfError("DWG_CONVERT_FAILED", "DWG conversion produced no drawing", 422)
        if result.stat().st_size > MAX_FILE_BYTES:
            raise DxfError("FILE_TOO_LARGE", "Converted drawing exceeds 50 MB", 413)
        return result.read_bytes(), kind, p.returncode == 0
