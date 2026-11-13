"""Track 8 P1-5: DWG uploads through a converter the user installed (not bundled):
ODA File Converter (free, closed source) or LibreDWG dwg2dxf (GPLv3, run as a separate program).
The resulting DXF then goes through the normal strict DXF checks and parser.

Track 10: saving as DWG, or as DXF of another AutoCAD version, goes through ODA File Converter
only (LibreDWG writes DWG R2000 experimentally — not offered)."""

import io
import os
import re
import shutil
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
TMP_PREFIX = "axcad-dwg-"
# a folder older than this belongs to no running conversion (the converter is killed at the timeout)
STALE_TMP_S = 2 * CONVERT_TIMEOUT_S + 60
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

    # "ODAFileConverter 26.4.0": the highest version by number, not by text ("9" > "26")
    def version(p: Path) -> tuple[int, ...]:
        return tuple(int(x) for x in re.findall(r"\d+", p.parent.name))

    return max(found, key=version) if found else None


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
    if name.startswith(("acadrust", "axcad-dwg")):
        return str(path), "acadrust"
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


class _Job:
    """Windows: a Job Object holding the converter and everything it starts — memory capped
    (MEMORY_CAP per process), the whole tree ended by terminate() or when the handle closes
    (BUG-59 (3)). A process it started before assign() returns is not covered (taskkill /T is)."""

    def __init__(self, pid: int) -> None:
        import ctypes
        from ctypes import wintypes as w

        class Basic(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", w.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", w.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", w.DWORD),
                ("SchedulingClass", w.DWORD),
            ]

        class Io(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", Basic),
                ("IoInfo", Io),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        k = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        k.CreateJobObjectW.restype = w.HANDLE
        k.OpenProcess.restype = w.HANDLE
        self._k, self.handle = k, k.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError("CreateJobObject failed")
        info = Extended()
        # KILL_ON_JOB_CLOSE 0x2000 | PROCESS_MEMORY 0x100
        info.BasicLimitInformation.LimitFlags = 0x2000 | 0x100
        info.ProcessMemoryLimit = MEMORY_CAP
        # JobObjectExtendedLimitInformation = 9
        if not k.SetInformationJobObject(
            w.HANDLE(self.handle), 9, ctypes.byref(info), ctypes.sizeof(info)
        ):
            self.close()
            raise OSError("SetInformationJobObject failed")
        proc = k.OpenProcess(0x0001 | 0x0100, False, pid)  # PROCESS_TERMINATE | SET_QUOTA
        ok = bool(proc) and k.AssignProcessToJobObject(w.HANDLE(self.handle), w.HANDLE(proc))
        if proc:
            k.CloseHandle(w.HANDLE(proc))
        if not ok:
            self.close()
            raise OSError("AssignProcessToJobObject failed")

    def terminate(self) -> None:
        from ctypes import wintypes as w

        if self.handle:
            self._k.TerminateJobObject(w.HANDLE(self.handle), 1)

    def close(self) -> None:
        from ctypes import wintypes as w

        if self.handle:
            self._k.CloseHandle(w.HANDLE(self.handle))  # KILL_ON_JOB_CLOSE: what is left ends
            self.handle = None


def _job(pid: int) -> "_Job | None":
    if not WINDOWS:
        return None
    try:
        return _Job(pid)
    except OSError:  # e.g. AX-CAD itself runs in a job that forbids nesting: taskkill still works
        return None


def _kill_tree(p: subprocess.Popen[bytes], job: "_Job | None" = None) -> None:
    if job is not None:
        job.terminate()
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
    job = _job(p.pid)
    try:
        deadline = time.monotonic() + CONVERT_TIMEOUT_S
        cap = in_size + 2 * FILE_CAP  # input + output + the converter's own temp files
        while p.poll() is None:
            if time.monotonic() > deadline:
                _kill_tree(p, job)
                raise DxfError("DWG_CONVERT_TIMEOUT", "DWG conversion timed out", 422)
            if _size(tmp) > cap:
                _kill_tree(p, job)
                raise DxfError("FILE_TOO_LARGE", "Converted drawing exceeds 50 MB", 413)
            time.sleep(POLL_S)
        if posix:
            _kill_tree(p)  # nothing it started may keep running (or writing) after it exits
        return int(p.returncode)
    finally:
        if job is not None:
            job.close()  # same on Windows: the job's remaining processes end with it


def _read_result(result: Path) -> bytes:
    if not result.is_file() or result.stat().st_size == 0:
        raise DxfError("DWG_CONVERT_FAILED", "DWG conversion produced no drawing", 422)
    if result.stat().st_size > MAX_FILE_BYTES:
        raise DxfError("FILE_TOO_LARGE", "Converted drawing exceeds 50 MB", 413)
    return result.read_bytes()


def dwg_release(data: bytes) -> str | None:
    """The AutoCAD release that wrote a DWG ("2018", "2004", ...), from its version string."""
    return DWG_RELEASES.get(data[:6].decode("ascii", "replace"))


def sweep_leftovers(now: float | None = None) -> int:
    """At API start: conversion folders a crash, kill or power cut left in the temp folder (the
    drawing copies in them would otherwise stay). Only this module's prefix, only real directories
    (no links) owned by this account, and only ones older than any conversion can run, so another
    API process's running conversion is never touched. Returns how many were removed."""
    now = time.time() if now is None else now
    removed = 0
    try:
        found = list(Path(tempfile.gettempdir()).glob(TMP_PREFIX + "*"))
    except OSError:
        return 0  # best effort: an unreadable temp folder must not stop the API
    for d in found:
        try:
            st = d.lstat()
            if d.is_symlink() or getattr(st, "st_file_attributes", 0) & 0x400:
                continue  # a link or a Windows junction (reparse point): never followed
            if not d.is_dir() or now - st.st_mtime < STALE_TMP_S:
                continue
            if hasattr(os, "getuid") and st.st_uid != os.getuid():
                continue
            shutil.rmtree(d, ignore_errors=True)
            removed += not d.exists()
        except OSError:
            continue
    return removed


def dwg_to_dxf(data: bytes) -> tuple[bytes, str, bool]:
    """Converted DXF bytes, the converter kind and whether it reported success (exit code 0)."""
    if not DWG_MAGIC.match(data):
        raise DxfError("DWG_INVALID_FILE", "Not a DWG file", 422)
    conv = converter()
    if conv is None:
        raise DxfError("DWG_CONVERTER_MISSING", "No DWG converter is installed", 422)
    exe, kind = conv
    # a leftover file held by antivirus must not turn a good conversion into an error
    with tempfile.TemporaryDirectory(prefix=TMP_PREFIX, ignore_cleanup_errors=True) as tmp:
        src_dir, out_dir = Path(tmp, "in"), Path(tmp, "out")
        src_dir.mkdir()
        out_dir.mkdir()
        src = src_dir / "drawing.dwg"  # fixed names: nothing from the upload reaches the command
        src.write_bytes(data)
        result = out_dir / "drawing.dxf"
        if kind == "ODA":  # input folder, output folder, version, type, recurse, audit, filter
            args = [exe, str(src_dir), str(out_dir), "ACAD2018", "DXF", "0", "1", "drawing.dwg"]
        elif kind == "acadrust":
            args = [exe, "convert", "--format", "dxf", "--output", str(result), str(src)]
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
    if kind == "acadrust":
        if fmt != "DWG":
            raise DxfError("DWG_WRITE_UNSUPPORTED", "acadrust can only write DWG", 422)
        with tempfile.TemporaryDirectory(prefix=TMP_PREFIX, ignore_cleanup_errors=True) as tmp:
            src_dir, out_dir = Path(tmp, "in"), Path(tmp, "out")
            src_dir.mkdir()
            out_dir.mkdir()
            src = src_dir / "drawing.dxf"
            src.write_bytes(dxf)
            result = out_dir / "drawing.dwg"
            args = [exe, "convert", "--format", "dwg", "--version", version, "--output", str(result), str(src)]
            _run(args, tmp, len(dxf))
            out = _read_result(result)
        if not DWG_MAGIC.match(out):
            raise DxfError("DWG_CONVERT_FAILED", "DWG conversion produced no drawing", 422)
        return out
    if kind != "ODA":
        raise DxfError("DWG_WRITE_UNSUPPORTED", "Saving needs ODA File Converter", 422)
    with tempfile.TemporaryDirectory(prefix=TMP_PREFIX, ignore_cleanup_errors=True) as tmp:
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


def selftest() -> dict[str, object]:
    """This PC's converter end to end: a small DXF -> DWG 2018 -> DXF, entities compared."""
    import ezdxf

    doc = ezdxf.new("R2018", setup=False)
    msp = doc.modelspace()
    msp.add_line((0, 0), (100, 0))
    msp.add_circle((50, 50), 20)
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 60), (0, 60)], close=True)
    buf = io.StringIO()
    doc.write(buf)
    started = time.monotonic()
    dwg = export_drawing(buf.getvalue().encode(), "DWG", "2018")
    release = dwg_release(dwg)
    dxf, _, clean = dwg_to_dxf(dwg)
    back = ezdxf.read(io.StringIO(dxf.decode("utf-8", "replace")))
    kinds = sorted(e.dxftype() for e in back.modelspace())
    ok = release == "2018" and kinds == ["CIRCLE", "LINE", "LWPOLYLINE"]
    return {
        "ok": ok,
        "dwg_release": release,
        "dwg_bytes": len(dwg),
        "entities": kinds,
        "exit_ok": clean,
        "seconds": round(time.monotonic() - started, 2),
    }


def _version(exe: str) -> str | None:
    """'ODAFileConverter 26.4.0' -> '26.4.0' (the install folder names the version)."""
    m = re.search(r"\d+(?:\.\d+)+", Path(exe).parent.name)
    return m.group(0) if m else None


def info() -> dict[str, object]:
    """What the screens show: which converter, what it can do (no full path of the program)."""
    conv = converter()
    kind = conv[1] if conv else None
    return {
        "available": conv is not None,
        "kind": kind,
        "program": Path(conv[0]).name if conv else None,
        "version": _version(conv[0]) if conv else None,
        "can_read": conv is not None,
        "can_write": kind in ("ODA", "acadrust"),
        "versions": list(VERSIONS) if kind in ("ODA", "acadrust") else [],
        "download_url": ODA_DOWNLOAD,
    }
