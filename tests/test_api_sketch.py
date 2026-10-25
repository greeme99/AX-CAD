"""Track 8 P2-1: SKETCH features, interactive solve, sketch-based Extrude/Revolve."""

import math

import pytest

XY = {"origin": [0, 0, 0], "normal": [0, 0, 1], "xdir": [1, 0, 0]}
XZ = {"origin": [0, 0, 0], "normal": [0, -1, 0], "xdir": [1, 0, 0]}  # FreeCAD XZ plane


def line(i, a, b):
    return {"id": i, "type": "LINE", "start": list(a), "end": list(b)}


def ref(e, p="edge"):
    return {"e": e, "p": p}


def plate(w=40, h=25, hole=3):
    """Rough rectangle (dimensions by constraints) with a hole circle in it."""
    ents = [
        line(1, (0.2, 0), (38, 0.5)),
        line(2, (38, 0.5), (39, 26)),
        line(3, (39, 26), (0, 24)),
        line(4, (0, 24), (0.2, 0)),
        {"id": 5, "type": "CIRCLE", "center": [12, 11], "radius": 2},
    ]
    cons = [
        *(
            {"type": "COINCIDENT", "refs": [ref(a, "end"), ref(b, "start")]}
            for a, b in ((1, 2), (2, 3), (3, 4), (4, 1))
        ),
        {"type": "HORIZONTAL", "refs": [ref(1)]},
        {"type": "HORIZONTAL", "refs": [ref(3)]},
        {"type": "VERTICAL", "refs": [ref(2)]},
        {"type": "VERTICAL", "refs": [ref(4)]},
        {"type": "COINCIDENT", "refs": [ref(1, "start"), ref(-1, "start")]},
        {"type": "DISTANCE", "refs": [ref(1)], "value": w},
        {"type": "DISTANCE", "refs": [ref(2)], "value": h},
        {"type": "RADIUS", "refs": [ref(5)], "value": hole},
    ]
    return ents, cons


def sketch(client, w, doc, plane=XY, **kw):
    ents, cons = plate(**kw)
    body = {
        "feature_type": "SKETCH",
        "params": {"plane": plane, "entities": ents, "constraints": cons},
    }
    return client.post(f"/api/documents/{doc}/features", json=body, headers=w.h["designer"])


def pad(client, w, doc, sid, distance=5):
    body = {
        "feature_type": "EXTRUDE",
        "params": {"sketch_feature_id": sid, "distance": distance, "direction": "+Z"},
    }
    return client.post(f"/api/documents/{doc}/features", json=body, headers=w.h["designer"])


def test_solve_endpoint(client, world):
    ents, cons = plate()
    r = client.post(
        "/api/sketches/solve",
        json={"entities": ents, "constraints": cons},
        headers=world.h["designer"],
    )
    assert r.status_code == 200, r.text
    res = r.json()["data"]
    assert res["status"] == "OK" and res["dof"] == 2  # the hole centre is still free
    assert res["entities"][1]["end"] == pytest.approx([40, 25])
    bad = [*cons, {"type": "DISTANCE", "refs": [ref(3)], "value": 41}]
    res = client.post(
        "/api/sketches/solve",
        json={"entities": ents, "constraints": bad},
        headers=world.h["designer"],
    ).json()["data"]
    assert res["status"] == "CONFLICT" and res["conflicting"] == [12]
    r = client.post(
        "/api/sketches/solve",
        json={"entities": ents, "constraints": cons},
        headers=world.h["viewer"],
    )
    assert r.status_code == 403


def test_sketch_pad_and_regenerate(client, world):
    d = world.h["designer"]
    r = sketch(client, world, world.doc)
    assert r.status_code == 200, r.text
    sk = r.json()["data"]
    assert sk["metrics"] is None and sk["params"]["entities"][2]["end"] == pytest.approx([0, 25])
    r = pad(client, world, world.doc, sk["feature_id"])
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["inputs"] == [sk["feature_id"]]
    assert body["metrics"]["volume_mm3"] == pytest.approx((40 * 25 - math.pi * 9) * 5, rel=1e-9)
    items = client.get(f"/api/documents/{world.doc}/features", headers=d).json()["data"]["items"]
    assert [i["visible"] for i in items] == [False, True]  # the used sketch is hidden

    # changing a dimension regenerates the pad
    p = sk["params"]
    p["constraints"][9]["value"] = 50
    r = client.patch(f"/api/features/{sk['feature_id']}", json={"params": p}, headers=d)
    assert r.status_code == 200, r.text
    items = client.get(f"/api/documents/{world.doc}/features", headers=d).json()["data"]["items"]
    assert items[1]["metrics"]["volume_mm3"] == pytest.approx((50 * 25 - math.pi * 9) * 5, rel=1e-9)

    # a conflicting edit is refused and nothing changes
    p["constraints"].append({"type": "DISTANCE", "refs": [ref(3)], "value": 10})
    r = client.patch(f"/api/features/{sk['feature_id']}", json={"params": p}, headers=d)
    assert r.status_code == 422 and r.json()["error"]["code"] == "SKETCH_CONFLICT"
    assert r.json()["error"]["details"]["constraints"] == [12]

    # in use: cannot delete; no body: no mesh, not exported or quoted
    r = client.delete(f"/api/features/{sk['feature_id']}", headers=d)
    assert r.status_code == 409 and r.json()["error"]["code"] == "FEATURE_IN_USE"
    assert client.get(f"/api/features/{sk['feature_id']}/mesh", headers=d).status_code == 409
    bodies = client.get(f"/api/documents/{world.doc}/metrics3d", headers=d).json()["data"]["bodies"]
    assert [b["feature_type"] for b in bodies] == ["EXTRUDE"]


def test_sketch_on_xz_plane_and_revolve(client, world):
    sid = sketch(client, world, world.doc, plane=XZ).json()["data"]["feature_id"]
    r = pad(client, world, world.doc, sid, distance=8)
    assert r.json()["data"]["metrics"]["bbox"]["min"] == pytest.approx([0, -8, 0], abs=1e-6)
    assert r.json()["data"]["metrics"]["bbox"]["max"] == pytest.approx([40, 0, 25], abs=1e-6)

    sid = sketch(client, world, world.doc).json()["data"]["feature_id"]
    body = {
        "feature_type": "REVOLVE",
        "params": {
            "sketch_feature_id": sid,
            "axis_point": [0, 0],
            "axis_dir": [0, 1],
            "angle_deg": 360,
        },
    }
    r = client.post(f"/api/documents/{world.doc}/features", json=body, headers=world.h["designer"])
    assert r.status_code == 200, r.text
    # Pappus: area x path of its centroid (plate minus hole about the y axis)
    a_plate, a_hole = 40 * 25, math.pi * 9
    x_c = (a_plate * 20 - a_hole * 12) / (a_plate - a_hole)
    want = (a_plate - a_hole) * 2 * math.pi * x_c
    assert r.json()["data"]["metrics"]["volume_mm3"] == pytest.approx(want, rel=1e-6)


@pytest.mark.parametrize(
    ("params", "code"),
    [
        (
            {"sketch_feature_id": 1, "source_revision_id": "0" * 32, "handles": ["A"]},
            "GEOM_INVALID_PARAM",
        ),
        ({}, "GEOM_INVALID_PARAM"),
    ],
)
def test_extrude_needs_one_source(client, world, params, code):
    body = {"feature_type": "EXTRUDE", "params": {**params, "distance": 5, "direction": "+Z"}}
    r = client.post(f"/api/documents/{world.doc}/features", json=body, headers=world.h["designer"])
    assert r.status_code == 422 and r.json()["error"]["code"] == code


def test_inputs_must_be_the_right_kind(client, world):
    d = world.h["designer"]
    sid = sketch(client, world, world.doc).json()["data"]["feature_id"]
    fillet = {
        "feature_type": "FILLET",
        "params": {"target_feature_id": sid, "radius": 1, "edges": "ALL"},
    }
    r = client.post(f"/api/documents/{world.doc}/features", json=fillet, headers=d)
    assert r.status_code == 422 and r.json()["error"]["code"] == "GEOM_INVALID_PARAM"
    body_id = pad(client, world, world.doc, sid).json()["data"]["feature_id"]
    r = pad(client, world, world.doc, body_id)  # a body is not a sketch
    assert r.status_code == 422
    r = pad(client, world, world.doc, sid)  # one sketch, one feature
    assert r.status_code == 422


def test_sketch_validation(client, world):
    d = world.h["designer"]
    ents, cons = plate()
    for plane in (
        {"origin": [0, 0, 0], "normal": [0, 0, 1], "xdir": [0, 0, 3]},
        {"origin": [0, 0, 0], "normal": [0, 0, 0], "xdir": [1, 0, 0]},
    ):
        body = {
            "feature_type": "SKETCH",
            "params": {"plane": plane, "entities": ents, "constraints": cons},
        }
        r = client.post(f"/api/documents/{world.doc}/features", json=body, headers=d)
        assert r.status_code == 422 and r.json()["error"]["code"] == "GEOM_INVALID_PARAM"
    bad = [*cons, {"type": "TANGENT", "refs": [ref(5, "center"), ref(1, "end")]}]
    r = client.post("/api/sketches/solve", json={"entities": ents, "constraints": bad}, headers=d)
    assert r.status_code == 422 and r.json()["error"]["code"] == "SKETCH_INVALID_CONSTRAINT"
    r = client.post(
        "/api/sketches/solve",
        json={"entities": ents, "constraints": [{"type": "MAGIC", "refs": [ref(1)]}]},
        headers=d,
    )
    assert r.status_code == 422
