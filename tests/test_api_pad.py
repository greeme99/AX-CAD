"""Track 8 P2-2: Pad options, Pocket and Primitive features through the API."""

import math

import pytest

from tests.test_api_sketch import XY, sketch

TOP = {"origin": [0, 0, 5], "normal": [0, 0, 1], "xdir": [1, 0, 0]}


def post(client, w, doc, ftype, params):
    return client.post(
        f"/api/documents/{doc}/features",
        json={"feature_type": ftype, "params": params},
        headers=w.h["designer"],
    )


def hole_sketch(client, w, doc, r=3):
    ents = [{"id": 1, "type": "CIRCLE", "center": [20, 12.5], "radius": r}]
    body = {"plane": TOP, "entities": ents, "constraints": []}
    return post(client, w, doc, "SKETCH", body).json()["data"]["feature_id"]


def test_pad_modes_and_taper(client, world):
    sid = sketch(client, world, world.doc, plane=XY).json()["data"]["feature_id"]
    hole = math.pi * 9  # plate() has an r = 3 hole
    r = post(
        client,
        world,
        world.doc,
        "EXTRUDE",
        {"sketch_feature_id": sid, "mode": "SYMMETRIC", "distance": 10, "direction": "+Z"},
    )
    assert r.status_code == 200, r.text
    m = r.json()["data"]["metrics"]
    assert m["volume_mm3"] == pytest.approx((1000 - hole) * 10, rel=1e-9)
    assert m["bbox"]["min"][2] == pytest.approx(-5) and m["bbox"]["max"][2] == pytest.approx(5)
    # edit the same pad: two lengths, then a taper (the old params stay valid on the way)
    fid = r.json()["data"]["feature_id"]
    p = {
        "sketch_feature_id": sid,
        "mode": "TWO_SIDED",
        "distance": 6,
        "distance2": 2,
        "direction": "+Z",
    }
    r = client.patch(f"/api/features/{fid}", json={"params": p}, headers=world.h["designer"])
    assert r.json()["data"]["metrics"]["volume_mm3"] == pytest.approx((1000 - hole) * 8, rel=1e-9)
    p = {**p, "mode": "LENGTH", "taper_deg": 3}
    r = client.patch(f"/api/features/{fid}", json={"params": p}, headers=world.h["designer"])
    assert r.status_code == 200, r.text
    assert r.json()["data"]["metrics"]["volume_mm3"] < (1000 - hole) * 6


def test_pad_up_to_face_and_validation(client, world):
    sid = sketch(client, world, world.doc).json()["data"]["feature_id"]
    up = {"origin": [0, 0, 12], "normal": [0, 0, -1]}  # a face looking down at the sketch
    ok = {"sketch_feature_id": sid, "mode": "UP_TO_FACE", "up_to": up, "direction": "+Z"}
    r = post(client, world, world.doc, "EXTRUDE", ok)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["metrics"]["bbox"]["max"][2] == pytest.approx(12)
    for bad in (
        {**ok, "up_to": None},  # mode needs a face
        {**ok, "mode": "THROUGH_ALL"},  # pockets only
        {**ok, "mode": "LENGTH", "distance": 5, "taper_deg": 80},
    ):
        r = post(client, world, world.doc, "EXTRUDE", bad)
        assert r.status_code == 422 and r.json()["error"]["code"] == "GEOM_INVALID_PARAM", bad


def test_pocket_through_all_consumes_body_and_sketch(client, world):
    d = world.h["designer"]
    box = post(
        client,
        world,
        world.doc,
        "PRIMITIVE",
        {"kind": "BOX", "length": 40, "width": 25, "height": 5},
    )
    assert box.status_code == 200, box.text
    assert box.json()["data"]["metrics"]["volume_mm3"] == pytest.approx(5000)
    bid = box.json()["data"]["feature_id"]
    sid = hole_sketch(client, world, world.doc)
    p = {
        "sketch_feature_id": sid,
        "target_feature_id": bid,
        "mode": "THROUGH_ALL",
        "direction": "-Z",
    }
    r = post(client, world, world.doc, "POCKET", p)
    assert r.status_code == 200, r.text
    pocket = r.json()["data"]
    assert pocket["inputs"] == [sid, bid]
    assert pocket["metrics"]["volume_mm3"] == pytest.approx(5000 - math.pi * 9 * 5, rel=1e-9)
    items = client.get(f"/api/documents/{world.doc}/features", headers=d).json()["data"]["items"]
    assert [i["visible"] for i in items] == [False, False, True]
    # a bigger hole regenerates the pocket through the sketch
    sk = items[1]
    sk["params"]["entities"][0]["radius"] = 4
    r = client.patch(f"/api/features/{sid}", json={"params": sk["params"]}, headers=d)
    assert r.status_code == 200, r.text
    items = client.get(f"/api/documents/{world.doc}/features", headers=d).json()["data"]["items"]
    assert items[2]["metrics"]["volume_mm3"] == pytest.approx(5000 - math.pi * 16 * 5, rel=1e-9)
    # the names: Box001, Sketch002, Pocket003 in exports and quotes
    bodies = client.get(f"/api/documents/{world.doc}/metrics3d", headers=d).json()["data"]["bodies"]
    assert [b["name"] for b in bodies] == ["Pocket003"]


def test_pocket_inputs_and_direction(client, world):
    bid = post(
        client, world, world.doc, "PRIMITIVE", {"kind": "CYLINDER", "radius": 20, "height": 5}
    ).json()["data"]["feature_id"]
    sid = hole_sketch(client, world, world.doc)
    away = {"sketch_feature_id": sid, "target_feature_id": bid, "distance": 2, "direction": "+Z"}
    r = post(client, world, world.doc, "POCKET", away)
    assert r.status_code == 422 and r.json()["error"]["code"] == "GEOM_EMPTY_RESULT"
    same = {**away, "target_feature_id": sid}
    assert post(client, world, world.doc, "POCKET", same).status_code == 422
    swapped = {**away, "sketch_feature_id": bid, "target_feature_id": sid}  # kinds checked
    assert post(client, world, world.doc, "POCKET", swapped).status_code == 422


@pytest.mark.parametrize(
    ("params", "vol"),
    [
        ({"kind": "CYLINDER", "radius": 5, "height": 10, "axis": "X"}, math.pi * 250),
        ({"kind": "CONE", "radius": 5, "radius2": 0, "height": 9}, math.pi * 75),
        ({"kind": "SPHERE", "radius": 5, "origin": [10, 0, 0]}, 4 / 3 * math.pi * 125),
        ({"kind": "TORUS", "radius": 10, "radius2": 2}, 2 * math.pi**2 * 40),
    ],
)
def test_primitives(client, world, params, vol):
    r = post(client, world, world.doc, "PRIMITIVE", params)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["metrics"]["volume_mm3"] == pytest.approx(vol, rel=1e-9)


@pytest.mark.parametrize(
    "params",
    [
        {"kind": "BOX", "length": 1, "width": 1},
        {"kind": "CONE", "radius": 5, "radius2": 5, "height": 3},
        {"kind": "TORUS", "radius": 2, "radius2": 3},
        {"kind": "SPHERE", "radius": 0},
    ],
)
def test_primitive_validation(client, world, params):
    r = post(client, world, world.doc, "PRIMITIVE", params)
    assert r.status_code == 422 and r.json()["error"]["code"] == "GEOM_INVALID_PARAM"
