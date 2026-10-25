"""Track 8 P1-5: DWG uploads through a user-installed converter (a stand-in program here)."""

import os
import stat
import sys
from pathlib import Path

import ezdxf
import pytest

from core.dxf.dwg import dwg_to_dxf
from core.dxf.reader import DxfError

DWG = b"AC1032" + b"\x00" * 64  # a DWG version string; the stand-in never reads the rest


def fake(tmp_path: Path, name: str, body: str) -> Path:
    """A stand-in converter with the real program's name and command line."""
    exe = tmp_path / name
    exe.write_text(f"#!{sys.executable}\nimport os, sys\n{body}\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return exe


DXF_OUT = """
import ezdxf
d = ezdxf.new(); d.units = 4; d.modelspace().add_line((0, 0), (25, 0)); d.saveas(OUT)
open(LOG, "w").write(",".join(sorted(k for k in os.environ if k in ("JWT_SECRET", "DATABASE_URL", "PATH"))))
"""


@pytest.fixture
def libredwg(tmp_path, monkeypatch):
    log = tmp_path / "env.log"
    body = f"LOG = {str(log)!r}\nOUT = sys.argv[sys.argv.index('-o') + 1]\n" + DXF_OUT
    monkeypatch.setenv("AXCAD_DWG_CONVERTER", str(fake(tmp_path, "dwg2dxf", body)))
    monkeypatch.setenv("DATABASE_URL", "postgresql://must-not-leak")
    return log


@pytest.mark.skipif(os.name == "nt", reason="the stand-in converter is a POSIX script")
def test_libredwg_conversion_without_secrets(libredwg):
    dxf, kind, clean = dwg_to_dxf(DWG)
    assert kind == "LibreDWG" and b"LINE" in dxf and clean
    assert libredwg.read_text() == "PATH"  # secrets are not passed to the converter


@pytest.mark.skipif(os.name == "nt", reason="the stand-in converter is a POSIX script")
def test_oda_command_line(tmp_path, monkeypatch):
    log = tmp_path / "env.log"
    # ODA: <in dir> <out dir> <version> <type> <recurse> <audit> <filter>
    body = (
        f"LOG = {str(log)!r}\n"
        "assert sys.argv[3:] == ['ACAD2018', 'DXF', '0', '1', 'drawing.dwg'], sys.argv\n"
        "OUT = os.path.join(sys.argv[2], 'drawing.dxf')\n" + DXF_OUT
    )
    monkeypatch.setenv("AXCAD_DWG_CONVERTER", str(fake(tmp_path, "ODAFileConverter", body)))
    assert dwg_to_dxf(DWG)[1] == "ODA"


@pytest.mark.parametrize(
    ("setup", "data", "code"),
    [
        ("none", DWG, "DWG_CONVERTER_MISSING"),
        ("relative", DWG, "DWG_CONVERTER_MISSING"),
        ("ok", b"0\nSECTION\n", "DWG_INVALID_FILE"),
        ("silent", DWG, "DWG_CONVERT_FAILED"),
    ],
)
@pytest.mark.skipif(os.name == "nt", reason="the stand-in converter is a POSIX script")
def test_conversion_errors(tmp_path, monkeypatch, setup, data, code):
    monkeypatch.delenv("AXCAD_DWG_CONVERTER", raising=False)
    if setup == "relative":
        monkeypatch.setenv("AXCAD_DWG_CONVERTER", "dwg2dxf")
    elif setup in ("ok", "silent"):
        monkeypatch.setenv("AXCAD_DWG_CONVERTER", str(fake(tmp_path, "dwg2dxf", "pass")))
    with pytest.raises(DxfError) as exc:
        dwg_to_dxf(data)
    assert exc.value.code == code


@pytest.mark.skipif(os.name == "nt", reason="the stand-in converter is a POSIX script")
def test_conversion_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr("core.dxf.dwg.CONVERT_TIMEOUT_S", 1)
    monkeypatch.setenv(
        "AXCAD_DWG_CONVERTER", str(fake(tmp_path, "dwg2dxf", "import time; time.sleep(30)"))
    )
    with pytest.raises(DxfError) as exc:
        dwg_to_dxf(DWG)
    assert exc.value.code == "DWG_CONVERT_TIMEOUT"


@pytest.mark.skipif(os.name == "nt", reason="the stand-in converter is a POSIX script")
def test_api_dwg_upload(client, world, libredwg):
    r = client.post(
        f"/api/documents/{world.doc}/dxf",
        files={"file": ("part.DWG", DWG)},
        headers=world.h["designer"],
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["warnings"][0] == "DWG_CONVERTED:LibreDWG"
    rid = data["revision_id"]
    render = client.get(f"/api/revisions/{rid}/render", headers=world.h["viewer"]).json()["data"]
    assert [e["type"] for e in render["entities"]] == ["LINE"]
    # the stored file is the converted DXF: it downloads and edits like any other revision
    dxf = client.get(f"/api/revisions/{rid}/dxf", headers=world.h["viewer"]).content
    assert ezdxf.read(__import__("io").StringIO(dxf.decode())).modelspace()[0].dxftype() == "LINE"


@pytest.mark.skipif(os.name == "nt", reason="the stand-in converter is a POSIX script")
def test_timeout_kills_the_whole_tree(tmp_path, monkeypatch):
    """A converter that started a helper: both are gone after the timeout (BUG-48)."""
    pid_file = tmp_path / "child.pid"
    body = (
        "import subprocess, time\n"
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(c.pid))\n"
        "time.sleep(60)"
    )
    monkeypatch.setattr("core.dxf.dwg.CONVERT_TIMEOUT_S", 1)
    monkeypatch.setenv("AXCAD_DWG_CONVERTER", str(fake(tmp_path, "dwg2dxf", body)))
    with pytest.raises(DxfError) as exc:
        dwg_to_dxf(DWG)
    assert exc.value.code == "DWG_CONVERT_TIMEOUT"
    child = int(pid_file.read_text())
    stat = Path(f"/proc/{child}/stat")
    if stat.exists():  # killed but not yet reaped by init: a zombie is not running
        assert stat.read_text().rsplit(")", 1)[1].split()[0] == "Z"


@pytest.mark.skipif(os.name == "nt", reason="the stand-in converter is a POSIX script")
def test_runaway_output_is_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr("core.dxf.dwg.FILE_CAP", 1024**2)
    monkeypatch.setattr("core.dxf.dwg._limit", lambda pid: None)  # the folder watch alone
    body = (
        "import time\nOUT = sys.argv[sys.argv.index('-o') + 1]\n"
        "f = open(OUT, 'wb')\nwhile True:\n    f.write(b'0' * 65536); f.flush(); time.sleep(0.01)"
    )
    monkeypatch.setenv("AXCAD_DWG_CONVERTER", str(fake(tmp_path, "dwg2dxf", body)))
    with pytest.raises(DxfError) as exc:
        dwg_to_dxf(DWG)
    assert exc.value.code == "FILE_TOO_LARGE"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="prlimit is Linux only")
def test_converter_gets_file_and_memory_limits(tmp_path, monkeypatch):
    log = tmp_path / "limits.log"
    body = (
        "import resource, time\ntime.sleep(0.5)\n"
        f"open({str(log)!r}, 'w').write(str(resource.getrlimit(resource.RLIMIT_FSIZE)[0]))\n"
        "OUT = sys.argv[sys.argv.index('-o') + 1]\nopen(OUT, 'w').write('0')"
    )
    monkeypatch.setenv("AXCAD_DWG_CONVERTER", str(fake(tmp_path, "dwg2dxf", body)))
    dwg_to_dxf(DWG)
    from core.dxf.dwg import FILE_CAP

    assert int(log.read_text()) == FILE_CAP


@pytest.mark.skipif(os.name == "nt", reason="the stand-in converter is a POSIX script")
def test_failing_converter_output_is_flagged(client, world, tmp_path, monkeypatch):
    body = (
        f"LOG = {str(tmp_path / 'x.log')!r}\nOUT = sys.argv[sys.argv.index('-o') + 1]\n"
        + DXF_OUT
        + "\nsys.exit(3)"
    )
    monkeypatch.setenv("AXCAD_DWG_CONVERTER", str(fake(tmp_path, "dwg2dxf", body)))
    r = client.post(
        f"/api/documents/{world.doc}/dxf",
        files={"file": ("part.dwg", DWG)},
        headers=world.h["designer"],
    )
    assert r.json()["data"]["warnings"][:2] == ["DWG_CONVERTED:LibreDWG", "DWG_CONVERT_PARTIAL"]
