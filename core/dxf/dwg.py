"""DWG uploads and saves. plan §25: the built-in translator (core/dxf/dwg_builtin.py, ezdwg) needs
nothing installed and is the default — it reads DWG R13..2018 and saves DWG 2000, carrying over
the objects it supports and reporting the rest by type.

Track 8 P1-5 / Track 10: a converter the administrator names in AXCAD_DWG_CONVERTER replaces it:
ODA File Converter (free, closed source; all versions, DXF too), LibreDWG dwg2dxf (GPLv3, read
only) or an acadrust command line. Either way the converter is a separate process under the same
limits, and the resulting DXF goes through the normal strict DXF checks and parser."""

import importlib.metadata
import importlib.util
import io
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
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
WINDOWS = os.name == "nt"
BUILTIN = "builtin"
BUILTIN_SCRIPT = Path(__file__).with_name("dwg_builtin.py")
REPORT_CAP = 64 * 1024
BUILTIN_VERSIONS = ["2000"]  # what the built-in translator saves (DWG only)


def builtin_available() -> bool:
    return importlib.util.find_spec("ezdwg") is not None


def converter() -> tuple[str, str] | None:
    """(executable, kind) of the external converter named in AXCAD_DWG_CONVERTER, or None: then
    the built-in translator is used. Never searched for (PATH, Program Files): which program
    reads a drawing is the administrator's explicit choice (BUG-48)."""
    path = Path(os.environ.get("AXCAD_DWG_CONVERTER", ""))
    if not path.is_absolute() or not path.is_file():
        return None
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


def _plain_file(path: Path) -> int:
    """Size of a regular file the converter wrote, else -1. Never a link: a converter taken over
    by a hostile drawing must not make the API read another file (.env, someone's drawing)."""
    try:
        st = path.lstat()
    except OSError:
        return -1
    return st.st_size if stat.S_ISREG(st.st_mode) and st.st_nlink == 1 else -1


def _read_result(result: Path) -> bytes:
    size = _plain_file(result)
    if size <= 0:
        raise DxfError("DWG_CONVERT_FAILED", "DWG conversion produced no drawing", 422)
    if size > MAX_FILE_BYTES:
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


def _builtin(
    mode: str, data: bytes, src_name: str, out_name: str, *extra: str
) -> tuple[bytes, dict]:
    """The built-in translator in its own process: output bytes and its report."""
    if not builtin_available():
        raise DxfError("DWG_CONVERTER_MISSING", "No DWG converter is installed", 422)
    with tempfile.TemporaryDirectory(prefix=TMP_PREFIX, ignore_cleanup_errors=True) as tmp:
        src, result, report = Path(tmp, src_name), Path(tmp, out_name), Path(tmp, "report.json")
        src.write_bytes(data)
        # -I: no PYTHON* variables, no user site, the temp folder is not on the import path
        args = [sys.executable, "-I", str(BUILTIN_SCRIPT), mode, str(src), str(result), str(report)]
        if _run([*args, *extra], tmp, len(data)) != 0:
            raise DxfError("DWG_CONVERT_FAILED", "The drawing could not be converted", 422)
        out = _read_result(result)
        found: object = {}
        if 0 < _plain_file(report) <= REPORT_CAP:  # written next to an untrusted file's parse
            try:
                found = json.loads(report.read_text(encoding="utf-8"))
            except (OSError, ValueError, RecursionError):
                found = {}
    return out, found if isinstance(found, dict) else {}


def _counts(value: object) -> dict[str, int]:
    """Object counts by type from the translator's report (it read an untrusted file)."""
    if not isinstance(value, dict):
        return {}
    pairs = [(str(k)[:64], v) for k, v in list(value.items())[:200]]
    ok = [(k, v) for k, v in pairs if isinstance(v, int) and not isinstance(v, bool) and v > 0]
    return {k: min(v, 10**9) for k, v in ok if k.isprintable()}


def dwg_import(data: bytes) -> tuple[bytes, str, bool, dict[str, int]]:
    """Converted DXF bytes, the converter kind, whether everything came across, and the objects
    left out by type (known for the built-in translator only)."""
    if not DWG_MAGIC.match(data):
        raise DxfError("DWG_INVALID_FILE", "Not a DWG file", 422)
    if converter() is None:
        dxf, report = _builtin("read", data, "drawing.dwg", "drawing.dxf")
        skipped = _counts(report.get("skipped"))
        return dxf, BUILTIN, not skipped, skipped
    return (*dwg_to_dxf(data), {})


def dwg_to_dxf(data: bytes) -> tuple[bytes, str, bool]:
    """Converted DXF bytes, the converter kind and whether it reported success (exit code 0)."""
    if not DWG_MAGIC.match(data):
        raise DxfError("DWG_INVALID_FILE", "Not a DWG file", 422)
    conv = converter()
    if conv is None:
        return dwg_import(data)[:3]
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
    """A stored DXF as DWG or DXF of the given AutoCAD release (VERSIONS): DWG 2000 by the
    built-in translator, every version by ODA File Converter."""
    target = VERSIONS.get(version)
    if fmt not in ("DWG", "DXF") or target is None or (fmt == "DWG" and version == "R12"):
        raise DxfError("EXPORT_VERSION_INVALID", "Unsupported format or version", 422)
    conv = converter()
    if conv is None:
        if fmt != "DWG" or version not in BUILTIN_VERSIONS:
            raise DxfError(
                "DWG_WRITE_UNSUPPORTED", "The built-in converter saves DWG 2000 only", 422
            )
        out, _ = _builtin("write", dxf, "drawing.dxf", "drawing.dwg", version)
        if not DWG_MAGIC.match(out):
            raise DxfError("DWG_CONVERT_FAILED", "DWG conversion produced no drawing", 422)
        return out
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
            args = [
                exe,
                "convert",
                "--format",
                "dwg",
                "--version",
                version,
                "--output",
                str(result),
                str(src),
            ]
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
    """This PC's converter end to end: a small DXF -> DWG -> DXF, entities compared."""
    import ezdxf

    doc = ezdxf.new("R2018", setup=False)
    msp = doc.modelspace()
    msp.add_line((0, 0), (100, 0))
    msp.add_circle((50, 50), 20)
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 60), (0, 60)], close=True)
    buf = io.StringIO()
    doc.write(buf)
    started = time.monotonic()
    version = "2018" if converter() else BUILTIN_VERSIONS[0]
    dwg = export_drawing(buf.getvalue().encode(), "DWG", version)
    release = dwg_release(dwg)
    dxf, _, clean = dwg_to_dxf(dwg)
    back = ezdxf.read(io.StringIO(dxf.decode("utf-8", "replace")))
    kinds = sorted(e.dxftype() for e in back.modelspace())
    ok = release == version and kinds == ["CIRCLE", "LINE", "LWPOLYLINE"]
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
    if conv is None:
        ok = builtin_available()
        return {
            "available": ok,
            "kind": BUILTIN if ok else None,
            "program": "ezdwg" if ok else None,
            "version": importlib.metadata.version("ezdwg") if ok else None,
            "can_read": ok,
            "can_write": ok,
            "versions": BUILTIN_VERSIONS if ok else [],
            "formats": ["DWG"] if ok else [],
        }
    kind = conv[1]
    writes = kind in ("ODA", "acadrust")
    return {
        "available": True,
        "kind": kind,
        "program": Path(conv[0]).name,
        "version": _version(conv[0]),
        "can_read": True,
        "can_write": writes,
        "versions": list(VERSIONS) if writes else [],
        "formats": ["DWG", "DXF"] if kind == "ODA" else ["DWG"] if writes else [],
    }
