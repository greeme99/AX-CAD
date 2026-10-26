"""Track 8 P5: installed release (R-label, grade, notes) and in-app updates on the per-PC install."""

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api import routes_system
from tests.test_patcher import KEY, PUB, build, patcher, release

INSTALLER = Path(__file__).resolve().parents[1] / "installer"


def test_release_labels():
    assert routes_system.label("1.0.0") == "R1.0"
    assert routes_system.label("1.1.2") == "R1.1 (수정 2)"
    assert routes_system.label("2.0.0") == "R2.0" and routes_system.label("x") is None


@pytest.fixture
def pc(client, world, monkeypatch, tmp_path):
    """A per-PC install: app/ with the real patcher and this test key, data/run/."""
    from backend.api import main

    app = release(
        build(tmp_path / "pc", "1.0.0"),
        grade="UPDATE",
        notes=["첫 릴리스"],
        released_at="2026-11-01",
    )
    shutil.copy(INSTALLER / "patcher.py", app / "scripts" / "patcher.py")
    (app / "scripts" / "patch_pub.hex").write_text(PUB.hex())
    run = tmp_path / "pc" / "run"
    monkeypatch.setenv("AXCAD_APP_DIR", str(app))
    monkeypatch.setenv("AXCAD_RUN_DIR", str(run))
    monkeypatch.setenv("AXCAD_LOCAL_USER", "admin")
    monkeypatch.setenv("AXCAD_LOCAL_TOKEN", "t0ken")
    started = []
    monkeypatch.setattr(routes_system, "launch", lambda *a: started.append(a))
    local = TestClient(
        main.app, base_url="http://localhost:3000", headers={"X-AXCAD-Local": "t0ken"}
    )
    return local, app, run, started


def patch_file(tmp_path, version, grade="UPDATE", **r):
    new = release(build(tmp_path / f"rel-{version}", version, server=version), grade=grade, **r)
    p = tmp_path / f"{version}.zip"
    patcher.make(new, KEY, p, ["server", "web", "scripts"])
    return p


def test_release_and_in_app_update(pc, tmp_path):
    local, app, run, started = pc
    r = local.get("/api/system/release").json()["data"]
    assert r["label"] == "R1.0" and r["notes"] == ["첫 릴리스"] and r["can_update"]
    p = patch_file(tmp_path, "1.2.0", notes=["견적 개선"])
    with p.open("rb") as f:
        r = local.post("/api/system/updates", files={"file": ("p.zip", f)})
    assert r.status_code == 202, r.text
    d = r.json()["data"]
    assert d["label"] == "R1.2" and d["grade"] == "UPDATE" and d["notes"] == ["견적 개선"]
    (args,) = started  # update.ps1 gets the saved patch and the result file
    assert args[0] == app and args[1].parent == run / "patches" and args[1].is_file()
    status = json.loads((run / "update-result.json").read_text())
    assert status["status"] == "RUNNING" and status["to"] == "1.2.0"
    last = local.get("/api/system/release").json()["data"]["last_update"]
    assert last["from"] == "1.0.0"


def test_refused_patches_never_start_an_update(pc, tmp_path):
    local, _, run, started = pc
    bad = [
        (patch_file(tmp_path, "2.1.0"), "업그레이드 패치가 필요"),  # another major
        (patch_file(tmp_path, "0.9.0"), "새 버전이 아닙니다"),
    ]
    junk = tmp_path / "junk.zip"
    junk.write_bytes(b"not a zip")
    bad.append((junk, "패치를 적용할 수 없습니다"))
    for p, msg in bad:
        with p.open("rb") as f:
            r = local.post("/api/system/updates", files={"file": ("p.zip", f)})
        assert r.status_code == 422 and msg in r.json()["error"]["message"], r.text
    assert not started and not list((run / "patches").glob("*.zip"))


def test_updates_only_on_the_pc_install(client, world, pc, monkeypatch, tmp_path):
    local, app, _, started = pc
    p = patch_file(tmp_path, "1.1.0")
    monkeypatch.delenv("AXCAD_APP_DIR")  # a shared server: no in-app update
    with p.open("rb") as f:
        r = local.post("/api/system/updates", files={"file": ("p.zip", f)})
    assert r.status_code == 409
    assert local.get("/api/system/release").json()["data"]["installed"] is False
    monkeypatch.setenv("AXCAD_APP_DIR", str(app))
    monkeypatch.setenv("AXCAD_LOCAL_USER", "designer")  # not an admin
    with p.open("rb") as f:
        assert local.post("/api/system/updates", files={"file": ("p.zip", f)}).status_code == 403
    assert not started
