"""DB-backed API tests for S5: features (Extrude), metrics, mesh, RBAC, audit."""

import io
import math

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


def two_shape_dxf():
    d = ezdxf.new(setup=True)
    d.units = 4
    d.modelspace().add_lwpolyline([(0, 0), (40, 0), (40, 30), (0, 30)], close=True)
    d.modelspace().add_circle((20, 15), 5)
    buf = io.StringIO()
    d.write(buf)
    return buf.getvalue().encode()


def box_and_hole(client, w):
    """EXTRUDE A (40x30x10 box) and B (circle r5, 20 tall) from one revision."""
    h = w.h["designer"]
    r = client.post(
        f"/api/documents/{w.doc}/dxf", files={"file": ("a.dxf", two_shape_dxf())}, headers=h
    )
    rid = r.json()["data"]["revision_id"]
    ents = client.get(f"/api/revisions/{rid}/render", headers=h).json()["data"]["entities"]
    by = {e["geom"]["type"]: e["handle"] for e in ents if "geom" in e}
    a = create(client, w, w.doc, rid, [by["LWPOLYLINE"]], distance=10).json()["data"]
    b = create(client, w, w.doc, rid, [by["CIRCLE"]], distance=20).json()["data"]
    return rid, a["feature_id"], b["feature_id"]


def boolean(client, w, op, a, b):
    body = {
        "feature_type": "BOOLEAN",
        "params": {"op": op, "target_feature_id": a, "tool_feature_id": b},
    }
    return client.post(f"/api/documents/{w.doc}/features", json=body, headers=w.h["designer"])


def listing(client, w):
    r = client.get(f"/api/documents/{w.doc}/features", headers=w.h["designer"])
    return {f["feature_id"]: f for f in r.json()["data"]["items"]}


def test_boolean_cut_regenerate_and_rollback(client, world):
    d = world.h["designer"]
    _, a, b = box_and_hole(client, world)
    r = boolean(client, world, "CUT", a, b)
    assert r.status_code == 200, r.text
    c = r.json()["data"]["feature_id"]
    cut = 12000 - math.pi * 25 * 10
    items = listing(client, world)
    assert [items[i]["visible"] for i in (a, b, c)] == [False, False, True]
    assert items[c]["inputs"] == [a, b] and items[a]["inputs"] == []
    assert items[c]["metrics"]["volume_mm3"] == pytest.approx(cut, rel=1e-6)

    # consumed bodies cannot be used again; bad references
    assert boolean(client, world, "FUSE", a, c).status_code == 422
    assert boolean(client, world, "FUSE", c, c).status_code == 422
    assert boolean(client, world, "FUSE", c, 99999).status_code == 422

    # PATCH B: C regenerated within the same request
    pb = items[b]["params"] | {"distance": 5}
    r = client.patch(f"/api/features/{b}", json={"params": pb}, headers=d)
    assert r.status_code == 200, r.text
    got = listing(client, world)
    assert got[c]["metrics"]["volume_mm3"] == pytest.approx(12000 - math.pi * 25 * 5, rel=1e-6)

    # cache wiped: mesh of C is rebuilt recursively from params
    from backend.api import main

    for p in (main.VAR_DIR / "brep").glob("*.brep"):
        p.unlink()
    assert client.get(f"/api/features/{c}/mesh", headers=d).json()["data"]["triangle_count"] > 0

    # DELETE of an input is refused; deleting the BOOLEAN frees them
    for i in (a, b):
        r = client.delete(f"/api/features/{i}", headers=d)
        assert r.status_code == 409 and r.json()["error"]["code"] == "FEATURE_IN_USE"
    assert client.delete(f"/api/features/{c}", headers=d).status_code == 200
    assert all(f["visible"] for f in listing(client, world).values())


def test_dependent_failure_rolls_back(client, world):
    d = world.h["designer"]
    _, a, b = box_and_hole(client, world)
    c = boolean(client, world, "COMMON", a, b).json()["data"]["feature_id"]
    before = listing(client, world)
    assert before[c]["metrics"]["volume_mm3"] == pytest.approx(math.pi * 25 * 10, rel=1e-6)
    # -Z puts B below A: they only touch -> COMMON is empty
    r = client.patch(
        f"/api/features/{b}", json={"params": before[b]["params"] | {"direction": "-Z"}}, headers=d
    )
    err = r.json()["error"]
    assert r.status_code == 422 and err["code"] == "GEOM_EMPTY_RESULT"
    assert err["details"]["feature_id"] == c
    after = listing(client, world)
    for i in (a, b, c):
        assert (
            after[i]["metrics"] == before[i]["metrics"]
            and after[i]["params"] == before[i]["params"]
        )


def test_revolve_feature(client, world):
    rid, handles = upload(client, world, world.doc)  # 40x30 rectangle at the origin
    body = {
        "feature_type": "REVOLVE",
        "params": {
            "source_revision_id": rid,
            "handles": handles,
            "axis_point": [0, 0],
            "axis_dir": [0, 1],
            "angle_deg": 360,
        },
    }
    r = client.post(f"/api/documents/{world.doc}/features", json=body, headers=world.h["designer"])
    assert r.status_code == 200, r.text
    f = r.json()["data"]
    assert f["metrics"]["volume_mm3"] == pytest.approx(math.pi * 40**2 * 30, rel=1e-6)
    assert f["visible"] is True and f["inputs"] == []
    for bad in (
        {"axis_dir": [0, 0]},
        {"angle_deg": 361},
        {"axis_point": [0]},
        {"axis_point": [20, 0]},
    ):
        b2 = {**body, "params": body["params"] | bad}
        r = client.post(
            f"/api/documents/{world.doc}/features", json=b2, headers=world.h["designer"]
        )
        assert r.status_code == 422 and r.json()["error"]["code"] == "GEOM_INVALID_PARAM", bad


def test_project_member_audit(client, world):
    r = client.get(
        f"/api/audit-logs?object_type=project_members&object_id={world.pid}",
        headers=world.h["reviewer"],
    )
    items = r.json()["data"]["items"]
    assert len(items) == 4 and {x["action"] for x in items} == {"INSERT"}
    assert {world.ids[n] for n in ("designer", "reviewer", "viewer")} <= {
        x["new_value"]["user_id"] for x in items
    }
