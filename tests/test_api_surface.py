"""plan §23 S6~S8: surface features and the quality analysis through the API (TC-210)."""

import math

import pytest

from tests.test_api_sketch import XY, XZ, line


def post(client, w, ftype, params):
    return client.post(
        f"/api/documents/{w.doc}/features",
        json={"feature_type": ftype, "params": params},
        headers=w.h["designer"],
    )


def make(client, w, ftype, params):
    r = post(client, w, ftype, params)
    assert r.status_code == 200, r.text
    f = r.json()["data"]
    assert f["status"] == "OK", f
    return f


def sketch(client, w, ents, plane=XY):
    return make(client, w, "SKETCH", {"plane": plane, "entities": ents, "constraints": []})[
        "feature_id"
    ]


def at_z(z):
    return {"origin": [0, 0, z], "normal": [0, 0, 1], "xdir": [1, 0, 0]}


def open_l(client, w, plane=XY):
    """An open L: (0,0) → (40,0) → (40,30)."""
    return sketch(client, w, [line(1, (0, 0), (40, 0)), line(2, (40, 0), (40, 30))], plane)


def flat(client, w, size=40.0, z=0.0):
    """A flat size × size sheet at height z (a line extruded sideways)."""
    plane = {"origin": [0, 0, z], "normal": [0, -1, 0], "xdir": [1, 0, 0]}
    sk = sketch(client, w, [line(1, (0, 0), (size, 0))], plane)
    return make(client, w, "SURF_EXTRUDE", {"sketch_feature_id": sk, "distance": size})


def analysis(client, w, fid, what, json):
    r = client.post(f"/api/features/{fid}/{what}", json=json, headers=w.h["designer"])
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_extrude_revolve_loft_sweep_make_sheet_bodies(client, world):
    ext = make(
        client, world, "SURF_EXTRUDE", {"sketch_feature_id": open_l(client, world), "distance": 20}
    )
    m = ext["metrics"]
    assert m["kind"] == "sheet" and m["surface_area_mm2"] == pytest.approx(70 * 20, rel=1e-9)

    # a half cylinder R = 10, height 30: a line parallel to the axis turned 180 degrees
    sk = sketch(client, world, [line(1, (10, 0), (10, 30))], XZ)
    rev = make(
        client,
        world,
        "SURF_REVOLVE",
        {"sketch_feature_id": sk, "axis_point": [0, 0], "axis_dir": [0, 1], "angle_deg": 180},
    )
    assert rev["metrics"]["surface_area_mm2"] == pytest.approx(math.pi * 10 * 30, rel=1e-6)

    a = sketch(client, world, [line(1, (0, 0), (40, 0))], at_z(0))
    b = sketch(client, world, [line(1, (0, 10), (40, 10))], at_z(30))
    loft = make(client, world, "SURF_LOFT", {"sketch_ids": [a, b], "ruled": True})
    assert loft["metrics"]["kind"] == "sheet"
    assert loft["metrics"]["surface_area_mm2"] == pytest.approx(40 * math.hypot(10, 30), rel=1e-6)

    prof = sketch(client, world, [line(1, (0, 0), (10, 0))], XZ)
    path = sketch(client, world, [line(1, (0, 0), (0, 50))], XY)
    sweep = make(client, world, "SURF_SWEEP", {"profile_sketch_id": prof, "path_sketch_id": path})
    assert sweep["metrics"]["surface_area_mm2"] == pytest.approx(500, rel=1e-6)


def test_boundary_and_fill_from_edge_points(client, world):
    square = [
        [[0, 0, 0], [20, 0, 0], [40, 0, 0]],
        [[40, 0, 0], [40, 20, 0], [40, 40, 0]],
        [[40, 40, 0], [20, 40, 0], [0, 40, 0]],
        [[0, 40, 0], [0, 20, 0], [0, 0, 0]],
    ]
    b = make(client, world, "SURF_BOUNDARY", {"edge_point_sets": square})
    assert b["metrics"]["surface_area_mm2"] == pytest.approx(1600, rel=1e-4)

    ring = [
        [30 * math.cos(2 * math.pi * k / 5), 30 * math.sin(2 * math.pi * k / 5), 0]
        for k in range(5)
    ]
    sides = [
        [p, [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2, 0], q]
        for p, q in zip(ring, [*ring[1:], ring[0]], strict=True)
    ]
    f = make(client, world, "SURF_FILL", {"edge_point_sets": sides})
    assert f["metrics"]["surface_area_mm2"] == pytest.approx(
        5 / 2 * 30**2 * math.sin(2 * math.pi / 5), rel=1e-3
    )


def test_edit_features_consume_their_body(client, world):
    h = world.h["designer"]
    base = flat(client, world)
    ext = make(
        client, world, "SURF_EXTEND", {"target_feature_id": base["feature_id"], "distance": 10}
    )
    assert ext["metrics"]["kind"] == "sheet"  # 40 x 40 grown 10 mm past every side
    assert ext["metrics"]["surface_area_mm2"] == pytest.approx(3600, rel=1e-9)
    trim = make(
        client,
        world,
        "SURF_TRIM",
        {
            "target_feature_id": ext["feature_id"],
            "plane_origin": [20, 0, 0],
            "plane_normal": [1, 0, 0],
        },
    )
    assert trim["metrics"]["surface_area_mm2"] == pytest.approx(1800, rel=1e-9)
    thick = make(
        client, world, "SURF_THICKEN", {"target_feature_id": trim["feature_id"], "thickness": 2}
    )
    assert thick["metrics"]["kind"] == "solid"
    assert thick["metrics"]["volume_mm3"] == pytest.approx(
        trim["metrics"]["surface_area_mm2"] * 2, rel=1e-6
    )
    # each step consumed the one before: only the last body is shown
    rows = client.get(f"/api/documents/{world.doc}/features", headers=h).json()["data"]["items"]
    shown = [f["feature_id"] for f in rows if f["visible"] and f["feature_type"] != "SKETCH"]
    assert shown == [thick["feature_id"]]

    off = make(
        client,
        world,
        "SURF_OFFSET",
        {"target_feature_id": flat(client, world, z=50)["feature_id"], "offset": 5},
    )
    assert off["metrics"]["surface_area_mm2"] == pytest.approx(1600, rel=1e-6)


def test_knit_two_sheets_and_consume_them(client, world):
    h = world.h["designer"]
    a = make(
        client,
        world,
        "SURF_EXTRUDE",
        {"sketch_feature_id": sketch(client, world, [line(1, (0, 0), (40, 0))]), "distance": 20},
    )
    b = make(
        client,
        world,
        "SURF_EXTRUDE",
        {"sketch_feature_id": sketch(client, world, [line(1, (40, 0), (40, 30))]), "distance": 20},
    )
    knit = make(
        client, world, "SURF_KNIT", {"face_feature_ids": [a["feature_id"], b["feature_id"]]}
    )
    assert knit["metrics"]["faces"] == 2
    assert knit["metrics"]["surface_area_mm2"] == pytest.approx(1400, rel=1e-9)
    rows = client.get(f"/api/documents/{world.doc}/features", headers=h).json()["data"]["items"]
    by_id = {f["feature_id"]: f for f in rows}
    assert not by_id[a["feature_id"]]["visible"] and not by_id[b["feature_id"]]["visible"]
    assert sorted(by_id[knit["feature_id"]]["inputs"]) == [a["feature_id"], b["feature_id"]]

    # the L is sharp along the shared edge: G0 holds, G1 does not
    c = analysis(client, world, knit["feature_id"], "continuity-check", {})
    assert c["summary"]["total_edges"] == 1
    e = c["edges"][0]
    assert e["pass_g0"] and not e["pass_g1"] and e["g1_max_deg"] == pytest.approx(90, abs=1e-6)


def test_analysis_of_a_half_cylinder(client, world):
    sk = sketch(client, world, [line(1, (10, 0), (10, 30))], XZ)
    rev = make(
        client,
        world,
        "SURF_REVOLVE",
        {"sketch_feature_id": sk, "axis_point": [0, 0], "axis_dir": [0, 1], "angle_deg": 180},
    )
    fid = rev["feature_id"]
    cm = analysis(client, world, fid, "curvature-map", {"n_u": 5, "n_v": 5})
    assert len(cm["samples"]) == 25
    assert all(abs(s["gauss"]) < 1e-9 for s in cm["samples"])
    assert all(abs(s["mean"]) == pytest.approx(1 / 20, rel=1e-4) for s in cm["samples"])
    d = analysis(client, world, fid, "draft-analysis", {"pull_direction": [0, 0, 1]})
    assert d["faces"][0]["draft_max_deg"] == pytest.approx(0, abs=1e-6)  # walls along the pull
    z = analysis(client, world, fid, "zebra-stripes", {"n_u": 4, "n_v": 4})
    assert len(z["samples"]) == 16


def test_surface_params_are_validated(client, world):
    r = post(client, world, "SURF_EXTRUDE", {"sketch_feature_id": 999999, "distance": 10})
    assert r.status_code in (404, 409, 422), r.text
    r = post(client, world, "SURF_BOUNDARY", {"edge_point_sets": [[[0, 0, 0], [1, 0, 0]]] * 2})
    assert r.status_code == 422
    r = post(client, world, "SURF_KNIT", {"face_feature_ids": [], "tolerance": 0.001})
    assert r.status_code == 422
