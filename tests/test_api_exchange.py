"""DB-backed API tests for S7: STEP/IGES import (IMPORT feature) and export (FN-12, FN-15)."""

import math

import pytest

from core.geometry.exchange import export_model
from tests.test_exchange import assembly_step, box, cyl

PIN = math.pi * 25 * 10


def post_import(client, w, data, name="asm.step", who="designer"):
    return client.post(
        f"/api/documents/{w.doc}/imports", files={"file": (name, data)}, headers=w.h[who]
    )


def features(client, w):
    r = client.get(f"/api/documents/{w.doc}/features", headers=w.h["designer"])
    return {f["feature_id"]: f for f in r.json()["data"]["items"]}


def test_import_assembly_mesh_and_export_round_trip(client, world):
    r = post_import(client, world, assembly_step(pins=3))
    assert r.status_code == 200, r.text
    f = r.json()["data"]
    assert f["feature_type"] == "IMPORT" and f["visible"] and f["seq"] == 1
    assert f["params"]["format"] == "STEP" and f["params"]["filename"] == "asm.step"
    m = f["metrics"]
    assert m["part_count"] == 2 and m["instance_count"] == 4
    assert m["volume_mm3"] == pytest.approx(6000 + 3 * PIN, rel=1e-6)
    assert m["assembly_tree"][0]["name"] == "ASM"
    mesh = client.get(f"/api/features/{f['feature_id']}/mesh", headers=world.h["viewer"])
    assert mesh.json()["data"]["triangle_count"] > 0

    files = {}
    for fmt, media in (("STEP", "model/step"), ("IGES", "model/iges")):
        r = client.get(
            f"/api/documents/{world.doc}/model", params={"format": fmt}, headers=world.h["designer"]
        )
        assert r.status_code == 200 and r.headers["content-type"].startswith(media)
        assert "attachment" in r.headers["content-disposition"]
        files[fmt] = r.content
    for name, data in (("back.step", files["STEP"]), ("back.igs", files["IGES"])):
        back = post_import(client, world, data, name).json()["data"]["metrics"]
        assert back["volume_mm3"] == pytest.approx(m["volume_mm3"], rel=1e-6)
        assert back["surface_area_mm2"] == pytest.approx(m["surface_area_mm2"], rel=1e-6)


def test_import_validation_and_immutability(client, world):
    d = world.h["designer"]
    step = export_model([("BOX", box())], "STEP")
    for name, data, code in (
        ("a.dxf", step, "STEP_INVALID_FILE"),
        ("a.step", b"not a step file", "STEP_INVALID_FILE"),
        ("a.igs", step, "STEP_INVALID_FILE"),
        ("a.stp", b"ISO-10303-21;\nHEADER;\nbroken", "STEP_READ_FAILED"),
    ):
        r = post_import(client, world, data, name)
        assert r.status_code == 422 and r.json()["error"]["code"] == code, name
    assert features(client, world) == {}
    from backend.api import main

    assert list((main.VAR_DIR / "imports").glob("*")) == []  # failed uploads leave no file

    fid = post_import(client, world, step, "box.STEP").json()["data"]["feature_id"]
    p = features(client, world)[fid]["params"]
    r = client.patch(f"/api/features/{fid}", json={"params": p}, headers=d)
    assert r.status_code == 409 and r.json()["error"]["code"] == "FEATURE_NOT_EDITABLE"
    # IMPORT can only be created from an upload, never from JSON pointing at a stored file
    r = client.post(
        f"/api/documents/{world.doc}/features",
        json={"feature_type": "IMPORT", "params": p},
        headers=d,
    )
    assert r.status_code == 400


def test_import_rbac_and_export_rules(client, world):
    step = export_model([("BOX", box())], "STEP")
    assert post_import(client, world, step, who="viewer").status_code == 403
    url = f"/api/documents/{world.doc}/model"
    r = client.get(url, headers=world.h["designer"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "MODEL_EMPTY"
    post_import(client, world, step)
    r = client.get(url, headers=world.h["viewer"])
    assert r.status_code == 403 and r.json()["error"]["code"] == "EXPORT_NOT_APPROVED"
    assert client.get(url, params={"format": "DWG"}, headers=world.h["designer"]).status_code == 400


def test_imported_bodies_join_booleans_and_files_are_cleaned(client, world):
    from backend.api import main

    d = world.h["designer"]
    a = post_import(client, world, export_model([("BOX", box())], "STEP"), "a.step")
    b = post_import(client, world, export_model([("PIN", cyl())], "IGES"), "b.igs")
    a, b = a.json()["data"]["feature_id"], b.json()["data"]["feature_id"]
    body = {
        "feature_type": "BOOLEAN",
        "params": {"op": "CUT", "target_feature_id": a, "tool_feature_id": b},
    }
    r = client.post(f"/api/documents/{world.doc}/features", json=body, headers=d)
    assert r.status_code == 200, r.text
    c = r.json()["data"]["feature_id"]
    # the r5 pin stands on the box corner at the origin: a quarter of it is inside
    assert r.json()["data"]["metrics"]["volume_mm3"] == pytest.approx(6000 - PIN / 4, rel=1e-6)

    # BREP cache wiped: the BOOLEAN is rebuilt from the stored upload files
    for p in (main.VAR_DIR / "brep").glob("*.brep"):
        p.unlink()
    assert client.get(f"/api/features/{c}/mesh", headers=d).json()["data"]["triangle_count"] > 0

    imports = main.VAR_DIR / "imports"
    assert len(list(imports.glob("*"))) == 2
    assert client.delete(f"/api/features/{c}", headers=d).status_code == 200
    for fid in (a, b):
        assert client.delete(f"/api/features/{fid}", headers=d).status_code == 200
    assert list(imports.glob("*")) == []


def test_shared_upload_survives_delete_in_other_document(client, world):
    from backend.api import main

    d = world.h["designer"]
    r = client.post(
        f"/api/projects/{world.pid}/documents",
        json={"doc_no": "D2", "doc_type": "PART", "title": "T2"},
        headers=d,
    )
    doc2 = r.json()["data"]["document_id"]
    step = export_model([("BOX", box())], "STEP")
    f1 = post_import(client, world, step, "same.step").json()["data"]["feature_id"]
    r = client.post(
        f"/api/documents/{doc2}/imports", files={"file": ("same.step", step)}, headers=d
    )
    f2 = r.json()["data"]["feature_id"]
    assert client.delete(f"/api/features/{f1}", headers=d).status_code == 200
    assert len(list((main.VAR_DIR / "imports").glob("*"))) == 1  # still used by document 2
    for p in (main.VAR_DIR / "brep").glob("*.brep"):
        p.unlink()
    assert client.get(f"/api/features/{f2}/mesh", headers=d).json()["data"]["triangle_count"] > 0


def test_control_characters_stripped_from_filename(client, world):
    step = export_model([("BOX", box())], "STEP")
    f = post_import(client, world, step, "a\x1b[31m\x7fb.step").json()["data"]
    assert f["params"]["filename"] == "a[31mb.step"
