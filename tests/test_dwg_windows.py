"""BUG-59 (3): on Windows the DWG converter runs in a Job Object — memory capped per process, the
whole tree ended on timeout and when the job closes. Runs in the CI windows job only."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from core.dxf import dwg

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Job Object")

# waits until AX-CAD has put it in the job, then starts a helper that would outlive it
PARENT = (
    "import subprocess, sys, time\n"
    "time.sleep(1.5)\n"
    "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
    "open(sys.argv[1], 'w').write(str(c.pid))\n"
    "time.sleep(120)\n"
)


def alive(pid: int) -> bool:
    out = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, check=False
    ).stdout
    return str(pid) in out


def started(tmp_path: Path) -> tuple[subprocess.Popen[bytes], "dwg._Job", Path]:
    pid_file = tmp_path / "child.pid"
    p = subprocess.Popen([sys.executable, "-c", PARENT, str(pid_file)])
    job = dwg._Job(p.pid)
    for _ in range(100):
        if pid_file.exists() and pid_file.read_text():
            break
        time.sleep(0.1)
    return p, job, pid_file


def test_terminate_ends_the_whole_tree(tmp_path):
    p, job, pid_file = started(tmp_path)
    child = int(pid_file.read_text())
    assert alive(child)
    job.terminate()
    p.wait(timeout=10)
    time.sleep(0.5)
    assert not alive(child)
    job.close()


def test_closing_the_job_ends_what_is_left(tmp_path):
    p, job, pid_file = started(tmp_path)
    child = int(pid_file.read_text())
    p.kill()  # the converter itself exited; its helper is still running
    p.wait(timeout=10)
    assert alive(child)
    job.close()
    time.sleep(0.5)
    assert not alive(child)


@pytest.mark.parametrize(("mb", "fits"), [(50, True), (600, False)])
def test_memory_is_capped(monkeypatch, mb, fits):
    monkeypatch.setattr(dwg, "MEMORY_CAP", 256 * 1024**2)
    code = f"import time; time.sleep(1.5); b = bytearray({mb} * 1024 * 1024)"
    p = subprocess.Popen([sys.executable, "-c", code], stderr=subprocess.DEVNULL)
    job = dwg._Job(p.pid)
    try:
        assert (p.wait(timeout=30) == 0) is fits
    finally:
        job.close()
