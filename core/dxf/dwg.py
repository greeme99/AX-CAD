"""Track 8 P1-5: DWG uploads through a converter the user installed (not bundled):
ODA File Converter (free, closed source) or LibreDWG dwg2dxf (GPLv3, run as a separate program).
The resulting DXF then goes through the normal strict DXF checks and parser."""

import os
import re
import subprocess
import tempfile
from pathlib import Path

from core.dxf.reader import MAX_FILE_BYTES, DxfError

# every DWG starts with its version string (AC1009 .. AC1032)
DWG_MAGIC = re.compile(rb"^AC10[0-9]{2}")
CONVERT_TIMEOUT_S = 120
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


def dwg_to_dxf(data: bytes) -> tuple[bytes, str]:
    """Converted DXF bytes and the converter kind."""
    if not DWG_MAGIC.match(data):
        raise DxfError("DWG_INVALID_FILE", "Not a DWG file", 422)
    conv = converter()
    if conv is None:
        raise DxfError("DWG_CONVERTER_MISSING", "No DWG converter is installed", 422)
    exe, kind = conv
    with tempfile.TemporaryDirectory(prefix="axcad-dwg-") as tmp:
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
        try:
            # fixed argument list, no shell; the program is the one the administrator configured
            subprocess.run(
                args,
                cwd=tmp,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=CONVERT_TIMEOUT_S,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),  # no console on Windows
            )
        except subprocess.TimeoutExpired:  # run() has already killed it
            raise DxfError("DWG_CONVERT_TIMEOUT", "DWG conversion timed out", 422) from None
        except OSError:
            raise DxfError(
                "DWG_CONVERT_FAILED", "DWG converter could not be started", 422
            ) from None
        if not result.is_file() or result.stat().st_size == 0:
            raise DxfError("DWG_CONVERT_FAILED", "DWG conversion produced no drawing", 422)
        if result.stat().st_size > MAX_FILE_BYTES:
            raise DxfError("FILE_TOO_LARGE", "Converted drawing exceeds 50 MB", 413)
        return result.read_bytes(), kind
