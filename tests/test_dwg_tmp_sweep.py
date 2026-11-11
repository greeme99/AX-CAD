"""HANDOFF §6 (BUG-48 rest): DWG conversion folders left by a crash are removed at API start."""

import os
import tempfile
import time

from core.dxf import dwg


def test_sweep_removes_only_old_conversion_folders(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    old = time.time() - dwg.STALE_TMP_S - 10
    left = tmp_path / f"{dwg.TMP_PREFIX}crash"
    (left / "in").mkdir(parents=True)
    (left / "in" / "drawing.dwg").write_bytes(b"AC1032")
    os.utime(left, (old, old))
    running = tmp_path / f"{dwg.TMP_PREFIX}now"  # a conversion of another API process
    running.mkdir()
    other = tmp_path / "someone-else"
    other.mkdir()
    os.utime(other, (old, old))
    target = tmp_path / "outside"
    target.mkdir()
    (target / "keep.txt").write_text("x")
    link = tmp_path / f"{dwg.TMP_PREFIX}link"
    link.symlink_to(target, target_is_directory=True)
    os.utime(link, (old, old), follow_symlinks=False)
    plain = tmp_path / f"{dwg.TMP_PREFIX}file"
    plain.write_text("x")
    os.utime(plain, (old, old))

    assert dwg.sweep_leftovers() == 1
    assert not left.exists()
    assert running.is_dir() and other.is_dir() and plain.is_file()
    assert link.is_symlink() and (target / "keep.txt").is_file()  # a link is never followed
