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
