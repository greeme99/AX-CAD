"""Track 8 P1-5: DWG uploads through a converter the user installed (not bundled):
ODA File Converter (free, closed source) or LibreDWG dwg2dxf (GPLv3, run as a separate program).
The resulting DXF then goes through the normal strict DXF checks and parser.

Track 10: saving as DWG, or as DXF of another AutoCAD version, goes through ODA File Converter
only (LibreDWG writes DWG R2000 experimentally — not offered)."""

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
# AutoCAD release -> ODA File Converter output version; R12 exists as DXF only
VERSIONS = {
    "2018": "ACAD2018",
    "2013": "ACAD2013",
    "2010": "ACAD2010",
    "2007": "ACAD2007",
    "2004": "ACAD2004",
    "2000": "ACAD2000",
    "R12": "ACAD12",
}
# the version string a DWG starts with -> the AutoCAD release that writes it
DWG_RELEASES = {
    "AC1009": "R11/R12",
    "AC1012": "R13",
    "AC1014": "R14",
    "AC1015": "2000",
    "AC1018": "2004",
    "AC1021": "2007",
    "AC1024": "2010",
    "AC1027": "2013",
    "AC1032": "2018",
}
ODA_DOWNLOAD = "https://www.opendesign.com/guestfiles/oda_file_converter"
WINDOWS = os.name == "nt"


def _installed_oda() -> Path | None:
    """Windows: ODA File Converter in Program Files (admin-only folders, like the launcher's
    Find-DwgConverter), so installing it while AX-CAD runs needs no restart. Never PATH (BUG-48)."""
    if not WINDOWS:
        return None
    found: list[Path] = []
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(var)
        if root and Path(root, "ODA").is_dir():
            found += Path(root, "ODA").glob("*/ODAFileConverter.exe")
    return max(found, key=str) if found else None


def converter() -> tuple[str, str] | None:
    """(executable, kind) from AXCAD_DWG_CONVERTER (else an installed ODA File Converter on
    Windows), or None when none is configured."""
    path = Path(os.environ.get("AXCAD_DWG_CONVERTER", ""))
    if not path.is_absolute() or not path.is_file():
        oda = _installed_oda()
        if oda is None:
            return None
        path = oda
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


def _run(args: list[str], tmp: str, in_size: int) -> int:
    """Run the converter in its temp folder under the time, size and tree limits; exit code."""
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
        raise DxfError("DWG_CONVERT_FAILED", "DWG converter could not be started", 422) from None
    _limit(p.pid)
    deadline = time.monotonic() + CONVERT_TIMEOUT_S
    cap = in_size + 2 * FILE_CAP  # input + output + the converter's own temp files
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
    return int(p.returncode)


def _read_result(result: Path) -> bytes:
    if not result.is_file() or result.stat().st_size == 0:
        raise DxfError("DWG_CONVERT_FAILED", "DWG conversion produced no drawing", 422)
    if result.stat().st_size > MAX_FILE_BYTES:
        raise DxfError("FILE_TOO_LARGE", "Converted drawing exceeds 50 MB", 413)
    return result.read_bytes()


def dwg_release(data: bytes) -> str | None:
    """The AutoCAD release that wrote a DWG ("2018", "2004", ...), from its version string."""
    return DWG_RELEASES.get(data[:6].decode("ascii", "replace"))


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
        code = _run(args, tmp, len(data))
        return _read_result(result), kind, code == 0


def export_drawing(dxf: bytes, fmt: str, version: str) -> bytes:
    """A stored DXF as DWG or DXF of the given AutoCAD release (VERSIONS), via ODA."""
    target = VERSIONS.get(version)
    if fmt not in ("DWG", "DXF") or target is None or (fmt == "DWG" and version == "R12"):
        raise DxfError("EXPORT_VERSION_INVALID", "Unsupported format or version", 422)
    conv = converter()
    if conv is None:
        raise DxfError("DWG_CONVERTER_MISSING", "No DWG converter is installed", 422)
    exe, kind = conv
    if kind != "ODA":
        raise DxfError("DWG_WRITE_UNSUPPORTED", "Saving needs ODA File Converter", 422)
    with tempfile.TemporaryDirectory(prefix="axcad-dwg-", ignore_cleanup_errors=True) as tmp:
        src_dir, out_dir = Path(tmp, "in"), Path(tmp, "out")
        src_dir.mkdir()
        out_dir.mkdir()
        (src_dir / "drawing.dxf").write_bytes(dxf)
        args = [exe, str(src_dir), str(out_dir), target, fmt, "0", "1", "drawing.dxf"]
        _run(args, tmp, len(dxf))
        out = _read_result(out_dir / f"drawing.{fmt.lower()}")
    if fmt == "DWG" and not DWG_MAGIC.match(out):
        raise DxfError("DWG_CONVERT_FAILED", "DWG conversion produced no drawing", 422)
    return out


def info() -> dict[str, object]:
    """What the screens show: which converter, what it can do (no full path of the program)."""
    conv = converter()
    kind = conv[1] if conv else None
    return {
        "available": conv is not None,
        "kind": kind,
        "program": Path(conv[0]).name if conv else None,
        "can_read": conv is not None,
        "can_write": kind == "ODA",
        "versions": list(VERSIONS) if kind == "ODA" else [],
        "download_url": ODA_DOWNLOAD,
    }
