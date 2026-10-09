"""DB-backed API tests for S5: features (Extrude), metrics, mesh, RBAC, audit."""

import io

import ezdxf
import pytest

RECT_AREA = 2 * (40 * 30 + 40 * 10 + 30 * 10)


def rect_dxf(w=40, h=30):
    d = ezdxf.new(setup=True)
    d.units = 4  # mm
    d.modelspace().add_lwpolyline([(0, 0), (w, 0), (w, h), (0, h)], close=True)
    buf = io.StringIO()
    d.write(buf)
    return buf.getvalue().encode()


def upload(client, w, doc, who="designer"):
    r = client.post(
        f"/api/documents/{doc}/dxf", files={"file": ("a.dxf", rect_dxf())}, headers=w.h[who]
    )
    rid = r.json()["data"]["revision_id"]
    ent = client.get(f"/api/revisions/{rid}/render", headers=w.h[who]).json()["data"]["entities"]
    return rid, [e["handle"] for e in ent if "geom" in e]


def params(rid, handles, distance=10, direction="+Z"):
    return {
        "feature_type": "EXTRUDE",
        "params": {
            "source_revision_id": rid,
            "handles": handles,
            "distance": distance,
            "direction": direction,
        },
    }


def create(client, w, doc, rid, handles, who="designer", **kw):
    return client.post(
        f"/api/documents/{doc}/features", json=params(rid, handles, **kw), headers=w.h[who]
    )


def test_feature_lifecycle(client, world):
    rid, handles = upload(client, world, world.doc)
    d = world.h["designer"]
    r = create(client, world, world.doc, rid, handles)
    assert r.status_code == 200, r.text
    f = r.json()["data"]
    assert f["seq"] == 1 and f["status"] == "OK" and f["error_code"] is None
    m = f["metrics"]
    assert m["volume_mm3"] == pytest.approx(12000, rel=1e-6)
    assert m["surface_area_mm2"] == pytest.approx(RECT_AREA, rel=1e-6)
    assert m["bbox"]["size"] == pytest.approx([40, 30, 10])

    items = client.get(f"/api/documents/{world.doc}/features", headers=world.h["viewer"]).json()
    assert (
        items["data"]["total"] == 1 and items["data"]["items"][0]["feature_id"] == f["feature_id"]
    )

    fid = f["feature_id"]
    r = client.patch(
        f"/api/features/{fid}", json={"params": params(rid, handles, 20)["params"]}, headers=d
    )
    assert r.json()["data"]["metrics"]["volume_mm3"] == pytest.approx(24000, rel=1e-6)

    # failed regeneration leaves the row unchanged
    bad = params(rid, handles, 20)["params"] | {"handles": ["FFFF"]}
    r = client.patch(f"/api/features/{fid}", json={"params": bad}, headers=d)
    assert r.status_code == 422 and r.json()["error"]["code"] == "GEOM_INVALID_PARAM"
    got = client.get(f"/api/documents/{world.doc}/features", headers=d).json()["data"]["items"][0]
    assert got["params"]["distance"] == 20

    r = client.get(f"/api/features/{fid}/mesh", headers=world.h["viewer"])
    mesh = r.json()["data"]
    assert mesh["triangle_count"] == 12 and len(mesh["faces"]) == 6
    assert mesh["bbox"]["max"] == pytest.approx([40, 30, 20])
    assert set(mesh["faces"][0]) == {"face_index", "positions", "normals", "indices"}

    # cache miss -> rebuilt from params
    from backend.api import main

    for p in (main.VAR_DIR / "brep").glob("*.brep"):
        p.unlink()
    assert client.get(f"/api/features/{fid}/mesh", headers=d).json()["data"]["triangle_count"] == 12

    assert client.delete(f"/api/features/{fid}", headers=d).status_code == 200
    assert (
        client.get(f"/api/documents/{world.doc}/features", headers=d).json()["data"]["total"] == 0
    )


def test_rbac_and_isolation(client, world, make_user, headers):
    rid, handles = upload(client, world, world.doc)
    r = create(client, world, world.doc, rid, handles, who="viewer")
    assert r.status_code == 403
    make_user("outsider", "DESIGNER")
    out = {"Authorization": headers("outsider")["Authorization"]}
    r = client.post(f"/api/documents/{world.doc}/features", json=params(rid, handles), headers=out)
    assert r.status_code == 404
    assert client.get(f"/api/documents/{world.doc}/features", headers=out).status_code == 404
    fid = create(client, world, world.doc, rid, handles).json()["data"]["feature_id"]
    assert client.get(f"/api/features/{fid}/mesh", headers=out).status_code == 404
    assert client.delete(f"/api/features/{fid}", headers=out).status_code == 404
    assert client.delete(f"/api/features/{fid}", headers=world.h["viewer"]).status_code == 403


def test_other_document_revision_rejected(client, world):
    d2 = client.post(
        f"/api/projects/{world.pid}/documents",
        json={"doc_no": "D2", "doc_type": "PART", "title": "T2"},
        headers=world.h["designer"],
    ).json()["data"]["document_id"]
    rid2, handles2 = upload(client, world, d2)
    r = create(client, world, world.doc, rid2, handles2)
    assert r.status_code == 422 and r.json()["error"]["code"] == "GEOM_INVALID_PARAM"


def test_validation_and_geometry_errors(client, world):
    rid, handles = upload(client, world, world.doc)
    for kw in ({"distance": 0}, {"distance": -5}, {"direction": "+X"}, {"distance": 2e6}):
        r = create(client, world, world.doc, rid, handles, **kw)
        assert r.status_code == 422 and r.json()["error"]["code"] == "GEOM_INVALID_PARAM", kw
    extra = params(rid, handles)
    extra["params"]["junk"] = 1
    r = client.post(f"/api/documents/{world.doc}/features", json=extra, headers=world.h["designer"])
    assert r.status_code == 422
    assert create(client, world, world.doc, rid, []).status_code == 422
    assert (
        client.get(f"/api/documents/{world.doc}/features", headers=world.h["designer"]).json()[
            "data"
        ]["total"]
        == 0
    )


def test_open_profile_error_details(client, world):
    d = ezdxf.new(setup=True)
    d.units = 4
    for a, b in (((0, 0), (10, 0)), ((10, 0), (10, 10))):
        d.modelspace().add_line(a, b)
    buf = io.StringIO()
    d.write(buf)
    client.post(
        f"/api/documents/{world.doc}/dxf",
        files={"file": ("a.dxf", buf.getvalue().encode())},
        headers=world.h["designer"],
    )
    cur = client.get(f"/api/documents/{world.doc}", headers=world.h["designer"]).json()["data"]
    rid = cur["current_revision_id"]
    ent = client.get(f"/api/revisions/{rid}/render", headers=world.h["designer"]).json()["data"]
    r = create(client, world, world.doc, rid, [e["handle"] for e in ent["entities"]])
    err = r.json()["error"]
    assert r.status_code == 422 and err["code"] == "GEOM_OPEN_WIRE"
    assert sorted(err["details"]["dangling"]) == [[0, 0], [10, 10]]


def test_feature_audit(client, world):
    rid, handles = upload(client, world, world.doc)
    fid = create(client, world, world.doc, rid, handles).json()["data"]["feature_id"]
    client.delete(f"/api/features/{fid}", headers=world.h["designer"])
    r = client.get(
        f"/api/audit-logs?object_type=features&object_id={fid}", headers=world.h["reviewer"]
    )
    items = r.json()["data"]["items"]
    assert [x["action"] for x in items] == ["DELETE", "INSERT"]
    assert all(x["user_id"] == world.ids["designer"] for x in items)
