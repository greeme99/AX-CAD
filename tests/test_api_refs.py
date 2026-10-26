"""Track 8 P2-4: fillet picked edges through the API, regenerate, lose the pick."""

import math

import pytest

from tests.test_api_pad import post

CORNER = {"kind": "LINE", "mid": [40, 25, 2.5], "length": 5, "dir": [0, 0, 1]}


def items(client, w):
    r = client.get(f"/api/documents/{w.doc}/features", headers=w.h["designer"])
    return r.json()["data"]["items"]


def test_picked_edge_fillet_follows_and_loses_the_corner(client, world):
    box = {"kind": "BOX", "length": 40, "width": 25, "height": 5}
    bid = post(client, world, world.doc, "PRIMITIVE", box).json()["data"]["feature_id"]
    mesh = client.get(f"/api/features/{bid}/mesh", headers=world.h["designer"]).json()["data"]
    assert CORNER | {"mid": [40.0, 25.0, 2.5]} in [e["sig"] for e in mesh["edges"]]
    r = post(
        client,
        world,
        world.doc,
        "FILLET",
        {"target_feature_id": bid, "radius": 2, "edge_refs": [CORNER]},
    )
    assert r.status_code == 200, r.text
    corner = (4 - math.pi) * 5
    assert r.json()["data"]["metrics"]["volume_mm3"] == pytest.approx(5000 - corner, rel=1e-6)
    d = world.h["designer"]
    # the box grows 2 mm longer: the same corner is found again
    assert (
        client.patch(
            f"/api/features/{bid}", json={"params": box | {"length": 42}}, headers=d
        ).status_code
        == 200
    )
    assert items(client, world)[1]["metrics"]["volume_mm3"] == pytest.approx(
        5250 - corner, rel=1e-6
    )
    # far bigger: the corner is gone
    r = client.patch(
        f"/api/features/{bid}",
        json={"params": box | {"length": 400, "width": 300, "height": 50}},
        headers=d,
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "GEOM_REF_LOST"


@pytest.mark.parametrize("extra", [{}, {"edges": "ALL", "edge_refs": [CORNER]}])
def test_exactly_one_edge_source(client, world, extra):
    box = {"kind": "BOX", "length": 40, "width": 25, "height": 5}
    bid = post(client, world, world.doc, "PRIMITIVE", box).json()["data"]["feature_id"]
    r = post(client, world, world.doc, "FILLET", {"target_feature_id": bid, "radius": 1, **extra})
    assert r.status_code == 422


def test_hole_wizard_from_sketch_points(client, world):
    box = {"kind": "BOX", "length": 40, "width": 25, "height": 5}
    bid = post(client, world, world.doc, "PRIMITIVE", box).json()["data"]["feature_id"]
    top = {"origin": [0, 0, 5], "normal": [0, 0, 1], "xdir": [1, 0, 0]}
    ents = [
        {"id": 1, "type": "POINT", "point": [10, 10]},
        {"id": 2, "type": "CIRCLE", "center": [30, 15], "radius": 1, "construction": True},
    ]
    sk = post(
        client, world, world.doc, "SKETCH", {"plane": top, "entities": ents, "constraints": []}
    )
    sid = sk.json()["data"]["feature_id"]
    spec = {"size": "M5", "kind": "CLEARANCE", "head": "COUNTERBORE"}
    r = post(
        client,
        world,
        world.doc,
        "HOLE",
        {"target_feature_id": bid, "sketch_feature_id": sid, "spec": spec},
    )
    assert r.status_code == 200, r.text
    f = r.json()["data"]
    assert f["inputs"] == [bid, sid]
    holes = f["metrics"]["holes"]
    assert holes["count"] == 2 and holes["diameter"] == 5.5 and holes["head"]["diameter"] == 9.5
    assert holes["centers"] == [[10.0, 10.0, 5.0], [30.0, 15.0, 5.0]]
    cut = 2 * math.pi * (9.5 / 2) ** 2 * 5  # the 5.4 deep counterbore goes through the 5 mm plate
    assert f["metrics"]["volume_mm3"] == pytest.approx(5000 - cut, rel=1e-6)
    assert [i["visible"] for i in items(client, world)] == [False, False, True]


@pytest.mark.parametrize(
    "bad",
    [
        {"positions": [[1, 1]], "spec": {"size": "M7", "kind": "TAPPED"}},
        {"positions": [[1, 1]], "diameter": 3, "spec": {"size": "M3", "kind": "TAPPED"}},
        {"diameter": 3},
        {
            "positions": [[1, 1]],
            "depth": 2,
            "spec": {"size": "M3", "kind": "TAPPED", "thread_depth": 3},
        },
    ],
)
def test_hole_wizard_rejects(client, world, bad):
    box = {"kind": "BOX", "length": 40, "width": 25, "height": 5}
    bid = post(client, world, world.doc, "PRIMITIVE", box).json()["data"]["feature_id"]
    r = post(client, world, world.doc, "HOLE", {"target_feature_id": bid, **bad})
    assert r.status_code == 422 and r.json()["error"]["code"] == "GEOM_INVALID_PARAM"
