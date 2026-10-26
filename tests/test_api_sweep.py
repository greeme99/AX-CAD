"""Track 8 P2-3: Sweep, Loft, Draft and Thickness features through the API."""

import math

import pytest

from tests.test_api_pad import post

XY = {"origin": [0, 0, 0], "normal": [0, 0, 1], "xdir": [1, 0, 0]}
YZ = {"origin": [0, 0, 0], "normal": [1, 0, 0], "xdir": [0, 1, 0]}


def at_z(z):
    return {**XY, "origin": [0, 0, z]}


def sk(client, w, doc, plane, ents):
    r = post(client, w, doc, "SKETCH", {"plane": plane, "entities": ents, "constraints": []})
    assert r.status_code == 200, r.text
    return r.json()["data"]["feature_id"]


def circle(i, r, c=(0, 0)):
    return {"id": i, "type": "CIRCLE", "center": list(c), "radius": r}


def test_sweep_and_regenerate(client, world):
    d = world.h["designer"]
    prof = sk(client, world, world.doc, YZ, [circle(1, 2)])
    path = sk(
        client, world, world.doc, XY, [{"id": 1, "type": "LINE", "start": [0, 0], "end": [30, 0]}]
    )
    r = post(client, world, world.doc, "SWEEP", {"profile_sketch_id": prof, "path_sketch_id": path})
    assert r.status_code == 200, r.text
    f = r.json()["data"]
    assert f["inputs"] == [prof, path]
    assert f["metrics"]["volume_mm3"] == pytest.approx(math.pi * 4 * 30, rel=1e-9)
    # a longer path regenerates the sweep
    items = client.get(f"/api/documents/{world.doc}/features", headers=d).json()["data"]["items"]
    pp = items[1]["params"]
    pp["entities"][0]["end"] = [50, 0]
    assert client.patch(f"/api/features/{path}", json={"params": pp}, headers=d).status_code == 200
    items = client.get(f"/api/documents/{world.doc}/features", headers=d).json()["data"]["items"]
    assert items[2]["metrics"]["volume_mm3"] == pytest.approx(math.pi * 4 * 50, rel=1e-9)
    assert [i["visible"] for i in items] == [False, False, True]


def test_loft_ruled(client, world):
    ids = [
        sk(client, world, world.doc, at_z(z), [circle(1, r)])
        for z, r in ((0, 5), (10, 10), (20, 5))
    ]
    r = post(client, world, world.doc, "LOFT", {"sketch_ids": ids, "ruled": True})
    assert r.status_code == 200, r.text
    frustum = math.pi * 10 / 3 * (25 + 100 + 50)
    assert r.json()["data"]["metrics"]["volume_mm3"] == pytest.approx(2 * frustum, rel=1e-6)
    assert r.json()["data"]["inputs"] == ids


@pytest.mark.parametrize(
    "params",
    [
        {"sketch_ids": [1]},
        {"sketch_ids": [1, 1]},
        {"profile_sketch_id": 1, "path_sketch_id": 1},
    ],
)
def test_sketch_feature_validation(client, world, params):
    ftype = "LOFT" if "sketch_ids" in params else "SWEEP"
    r = post(client, world, world.doc, ftype, params)
    assert r.status_code == 422 and r.json()["error"]["code"] == "GEOM_INVALID_PARAM"


def test_sweep_needs_sketches(client, world):
    box = post(
        client, world, world.doc, "PRIMITIVE", {"kind": "BOX", "length": 4, "width": 4, "height": 4}
    )
    bid = box.json()["data"]["feature_id"]
    prof = sk(client, world, world.doc, YZ, [circle(1, 1)])
    r = post(client, world, world.doc, "SWEEP", {"profile_sketch_id": prof, "path_sketch_id": bid})
    assert r.status_code == 422


def test_draft_and_thickness(client, world):
    box = {"kind": "BOX", "length": 40, "width": 20, "height": 10}
    bid = post(client, world, world.doc, "PRIMITIVE", box).json()["data"]["feature_id"]
    r = post(client, world, world.doc, "DRAFT", {"target_feature_id": bid, "angle_deg": 5})
    assert r.status_code == 200, r.text
    t = math.tan(math.radians(5))
    want = 8000 - 60 * 100 * t + 4 / 3 * 1000 * t * t
    assert r.json()["data"]["metrics"]["volume_mm3"] == pytest.approx(want, rel=1e-9)
    assert (
        post(
            client, world, world.doc, "DRAFT", {"target_feature_id": bid, "angle_deg": 0}
        ).status_code
        == 422
    )

    bid2 = post(client, world, world.doc, "PRIMITIVE", box).json()["data"]["feature_id"]
    shell = {
        "target_feature_id": bid2,
        "thickness": 2,
        "open_face": "NONE",
        "outward": True,
        "join": "INTERSECTION",
    }
    r = post(client, world, world.doc, "SHELL", shell)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["metrics"]["volume_mm3"] == pytest.approx(44 * 24 * 14 - 8000, rel=1e-9)
    # stored shells from before P2-3 (TOP/BOTTOM, no new keys) still validate
    bid3 = post(client, world, world.doc, "PRIMITIVE", box).json()["data"]["feature_id"]
    old = {"target_feature_id": bid3, "thickness": 2, "open_face": "TOP"}
    assert post(client, world, world.doc, "SHELL", old).json()["data"]["metrics"][
        "volume_mm3"
    ] == pytest.approx(8000 - 36 * 16 * 8, rel=1e-9)
