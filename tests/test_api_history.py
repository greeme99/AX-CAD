"""Track 8 P2-5: suppress, reorder and insert at the rollback bar (FreeCAD feature history)."""

import math

import pytest

from tests.test_api_pad import post

BOX = {"kind": "BOX", "length": 40, "width": 30, "height": 10}
CORNER = (4 - math.pi) * 10  # a R2 fillet on one 10 mm vertical corner: r^2 (1 - pi/4) * 10


def items(client, w):
    r = client.get(f"/api/documents/{w.doc}/features", headers=w.h["designer"])
    return r.json()["data"]["items"]


def fid(r):
    assert r.status_code == 200, r.text
    return r.json()["data"]["feature_id"]


def act(client, w, f, what, payload):
    return client.post(f"/api/features/{f}/{what}", json=payload, headers=w.h["designer"])


def plate_with_fillet(client, w):
    box = fid(post(client, w, w.doc, "PRIMITIVE", BOX))
    fil = fid(
        post(
            client, w, w.doc, "FILLET", {"target_feature_id": box, "radius": 2, "edges": "VERTICAL"}
        )
    )
    return box, fil


def test_suppress_passes_the_body_on(client, world):
    box, fil = plate_with_fillet(client, world)
    hole = fid(
        post(
            client,
            world,
            world.doc,
            "HOLE",
            {"target_feature_id": fil, "positions": [[20, 15]], "diameter": 10},
        )
    )
    full = 12000 - 4 * CORNER - math.pi * 25 * 10
    assert items(client, world)[2]["metrics"]["volume_mm3"] == pytest.approx(full, rel=1e-6)
    r = act(client, world, fil, "suppress", {"suppressed": True})
    assert r.status_code == 200 and r.json()["data"]["suppressed"] is True
    rows = items(client, world)
    assert rows[1]["metrics"]["volume_mm3"] == pytest.approx(12000)  # the plain box
    assert rows[2]["metrics"]["volume_mm3"] == pytest.approx(12000 - math.pi * 250, rel=1e-6)
    assert act(client, world, fil, "suppress", {"suppressed": False}).status_code == 200
    assert items(client, world)[2]["metrics"]["volume_mm3"] == pytest.approx(full, rel=1e-6)
    # a body-maker has nothing to pass on: refused while the fillet uses it, fine on its own
    r = act(client, world, box, "suppress", {"suppressed": True})
    assert r.status_code == 409 and r.json()["error"]["code"] == "FEATURE_IN_USE"
    assert act(client, world, hole, "suppress", {"suppressed": True}).status_code == 200
    lone = fid(post(client, world, world.doc, "PRIMITIVE", BOX))
    r = act(client, world, lone, "suppress", {"suppressed": True})
    assert r.status_code == 200 and r.json()["data"]["metrics"] is None


def test_move_keeps_inputs_first(client, world):
    box, fil = plate_with_fillet(client, world)
    other = fid(post(client, world, world.doc, "PRIMITIVE", BOX | {"origin": [0, 50, 0]}))
    r = act(client, world, fil, "move", {"direction": "UP"})  # above its own box: refused
    assert r.status_code == 409 and r.json()["error"]["code"] == "FEATURE_NOT_MOVABLE"
    assert act(client, world, other, "move", {"direction": "UP"}).status_code == 200
    assert act(client, world, other, "move", {"direction": "UP"}).status_code == 200
    assert [i["feature_id"] for i in items(client, world)] == [other, box, fil]
    r = act(client, world, other, "move", {"direction": "UP"})
    assert r.status_code == 409


def test_insert_at_the_rollback_bar_relinks_the_next_feature(client, world):
    box, fil = plate_with_fillet(client, world)
    # rolled back to the box: a hole goes between the box and the fillet, which now uses the hole
    hole = {"target_feature_id": box, "positions": [[20, 15]], "diameter": 10}
    r = client.post(
        f"/api/documents/{world.doc}/features",
        json={"feature_type": "HOLE", "params": hole, "insert_after": 1},
        headers=world.h["designer"],
    )
    h = fid(r)
    rows = items(client, world)
    assert [i["feature_id"] for i in rows] == [box, h, fil] and [i["seq"] for i in rows] == [
        1,
        2,
        3,
    ]
    assert rows[2]["params"]["target_feature_id"] == h and rows[2]["inputs"] == [h]
    assert [i["visible"] for i in rows] == [False, False, True]
    want = 12000 - 4 * CORNER - math.pi * 250
    assert rows[2]["metrics"]["volume_mm3"] == pytest.approx(want, rel=1e-6)
    # the box is used by the hole now: another insert on it is refused
    r = client.post(
        f"/api/documents/{world.doc}/features",
        json={
            "feature_type": "CHAMFER",
            "params": {"target_feature_id": box, "distance": 1, "edges": "TOP"},
            "insert_after": 2,
        },
        headers=world.h["designer"],
    )
    assert r.status_code == 422


def test_measure_picks_and_body(client, world):
    box = fid(post(client, world, world.doc, "PRIMITIVE", BOX))
    top = {"kind": "PLANE", "mid": [20, 15, 10], "area": 1200, "dir": [0, 0, 1]}
    bottom = top | {"mid": [20, 15, 0]}
    side = {"kind": "PLANE", "mid": [40, 15, 5], "area": 300, "dir": [1, 0, 0]}
    edge = {"kind": "LINE", "mid": [40, 30, 5], "length": 10, "dir": [0, 0, 1]}
    m = lambda picks: client.post(  # noqa: E731
        f"/api/features/{box}/measure", json={"picks": picks}, headers=world.h["viewer"]
    )
    r = m([{"what": "FACE", "sig": top}, {"what": "FACE", "sig": bottom}])
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["distance"] == pytest.approx(10) and d["angle_deg"] == pytest.approx(0)
    assert d["items"][0]["area"] == 1200 and d["body"]["volume_mm3"] == pytest.approx(12000)
    assert d["body"]["center"] == pytest.approx([20, 15, 5])
    d = m([{"what": "FACE", "sig": top}, {"what": "EDGE", "sig": edge}]).json()["data"]
    assert d["distance"] == pytest.approx(0) and d["angle_deg"] == pytest.approx(0)
    d = m([{"what": "FACE", "sig": top}, {"what": "FACE", "sig": side}]).json()["data"]
    assert d["angle_deg"] == pytest.approx(90)
    r = m([{"what": "EDGE", "sig": edge | {"mid": [400, 300, 5]}}])
    assert r.status_code == 422 and r.json()["error"]["code"] == "GEOM_REF_LOST"


def test_materials_for_mass(client, world):
    admin = world.h["admin"]
    assert client.get("/api/materials", headers=world.h["viewer"]).json()["data"]["items"] == []
    from tests.test_api_master import FULL

    r = client.post(
        "/api/master-versions",
        json={"version_code": "V1", "effective_from": "2020-01-01"},
        headers=admin,
    )
    vid = r.json()["data"]["version_id"]
    assert client.put(f"/api/master-versions/{vid}", json=FULL, headers=admin).status_code == 200
    assert client.post(f"/api/master-versions/{vid}/activate", headers=admin).status_code == 200
    items = client.get("/api/materials", headers=world.h["viewer"]).json()["data"]["items"]
    assert items == [{"material_code": "SS400", "material_name": None, "density_g_cm3": 7.85}]
