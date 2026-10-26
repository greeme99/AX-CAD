"""Track 8 P2-6 acceptance: five synthetic standard parts built with the P2 features (sketches,
pad, revolve, fillet/chamfer on picked edges and faces, hole wizard, thickness), each against its
analytic volume (1e-6), then all exported to STEP and read back (volume and area 1e-6 per part)."""

import math

import pytest

from tests.test_api_pad import post

PI = math.pi
XZ = {"origin": [0, 0, 0], "normal": [0, -1, 0], "xdir": [1, 0, 0]}  # sketch y = world Z
XY = {"origin": [0, 0, 0], "normal": [0, 0, 1], "xdir": [1, 0, 0]}
REL = 1e-6


def fid(r):
    assert r.status_code == 200, r.text
    return r.json()["data"]


def poly(pts):
    n = len(pts)
    return [
        {"id": i + 1, "type": "LINE", "start": list(pts[i]), "end": list(pts[(i + 1) % n])}
        for i in range(n)
    ]


def sketch(client, w, plane, ents):
    return fid(
        post(client, w, w.doc, "SKETCH", {"plane": plane, "entities": ents, "constraints": []})
    )["feature_id"]


def bracket(client, w):
    """L bracket 60 x 40, 5 thick, 30 wide; R3 fillet on the picked inner corner; 2 M8 bolt holes."""
    sk = sketch(client, w, XZ, poly([(0, 0), (60, 0), (60, 5), (5, 5), (5, 40), (0, 40)]))
    body = fid(
        post(
            client,
            w,
            w.doc,
            "EXTRUDE",
            {"sketch_feature_id": sk, "distance": 30, "direction": "+Z"},
        )
    )["feature_id"]
    corner = {"kind": "LINE", "mid": [5, -15, 5], "length": 30, "dir": [0, 1, 0]}
    fil = fid(
        post(
            client,
            w,
            w.doc,
            "FILLET",
            {"target_feature_id": body, "radius": 3, "edge_refs": [corner]},
        )
    )["feature_id"]
    spec = {"size": "M8", "kind": "CLEARANCE"}  # KS B 1007 medium: 9.0
    f = fid(
        post(
            client,
            w,
            w.doc,
            "HOLE",
            {"target_feature_id": fil, "positions": [[25, -15], [45, -15]], "spec": spec},
        )
    )
    return f, 475 * 30 + 9 * (1 - PI / 4) * 30 - 2 * PI * 4.5**2 * 5


def flange(client, w):
    """Revolved flange (bore 40, disc 100 x 10, hub 60 x 30) with 6 M10 bolt holes on PCD 80."""
    sk = sketch(client, w, XZ, poly([(20, 0), (50, 0), (50, 10), (30, 10), (30, 30), (20, 30)]))
    rev = {"sketch_feature_id": sk, "axis_point": [0, 0], "axis_dir": [0, 1], "angle_deg": 360}
    body = fid(post(client, w, w.doc, "REVOLVE", rev))["feature_id"]
    pcd = [[40 * math.cos(k * PI / 3), 40 * math.sin(k * PI / 3)] for k in range(6)]
    spec = {"size": "M10", "kind": "CLEARANCE"}  # 11.0
    f = fid(
        post(client, w, w.doc, "HOLE", {"target_feature_id": body, "positions": pcd, "spec": spec})
    )
    return f, PI * (500 * 30 + 1600 * 10) - 6 * PI * 5.5**2 * 10  # hub ring r20-30, disc r30-50


def shaft(client, w):
    """Stepped shaft 20/30/20 x 40/50/40 along X, 1 mm chamfers on the end faces (picked faces)."""
    sk = sketch(
        client,
        w,
        XY,
        poly([(0, 0), (0, 10), (40, 10), (40, 15), (90, 15), (90, 10), (130, 10), (130, 0)]),
    )
    rev = {"sketch_feature_id": sk, "axis_point": [0, 0], "axis_dir": [1, 0], "angle_deg": 360}
    body = fid(post(client, w, w.doc, "REVOLVE", rev))["feature_id"]
    ends = [
        {"kind": "PLANE", "mid": [x, 0, 0], "area": PI * 100, "dir": [1, 0, 0]} for x in (0, 130)
    ]
    f = fid(
        post(
            client,
            w,
            w.doc,
            "CHAMFER",
            {"target_feature_id": body, "distance": 1, "face_refs": ends},
        )
    )
    ring = 2 * PI * (10 - 1 / 3) * 0.5  # a 1 x 1 triangle revolved at its centroid radius
    return f, PI * (100 * 40 + 225 * 50 + 100 * 40) - 2 * ring


def housing(client, w):
    """Box 80 x 60 x 40 hollowed 3 mm, open top; four M3 tapped holes 6 deep in the rim corners."""
    box = {"kind": "BOX", "length": 80, "width": 60, "height": 40, "origin": [100, 0, 0]}
    b = fid(post(client, w, w.doc, "PRIMITIVE", box))["feature_id"]
    sh = fid(
        post(
            client, w, w.doc, "SHELL", {"target_feature_id": b, "thickness": 3, "open_face": "TOP"}
        )
    )["feature_id"]
    pts = [[101.5, 1.5], [178.5, 1.5], [101.5, 58.5], [178.5, 58.5]]
    spec = {"size": "M3", "kind": "TAPPED"}  # tap drill 2.5
    f = fid(
        post(
            client,
            w,
            w.doc,
            "HOLE",
            {"target_feature_id": sh, "positions": pts, "depth": 6, "spec": spec},
        )
    )
    assert f["metrics"]["holes"]["thread"]["label"] == "M3"
    return f, 80 * 60 * 40 - 74 * 54 * 37 - 4 * PI * 1.25**2 * 6


def plate(client, w):
    """Sheet-metal style plate 100 x 50 x 2: 5 mm corner chamfers and a row of five 6 mm holes."""
    box = {"kind": "BOX", "length": 100, "width": 50, "height": 2, "origin": [0, 100, 0]}
    b = fid(post(client, w, w.doc, "PRIMITIVE", box))["feature_id"]
    ch = fid(
        post(
            client,
            w,
            w.doc,
            "CHAMFER",
            {"target_feature_id": b, "distance": 5, "edges": "VERTICAL"},
        )
    )["feature_id"]
    pts = [[20 + 15 * i, 125] for i in range(5)]
    f = fid(
        post(client, w, w.doc, "HOLE", {"target_feature_id": ch, "positions": pts, "diameter": 6})
    )
    return f, 100 * 50 * 2 - 4 * 12.5 * 2 - 5 * PI * 9 * 2


@pytest.mark.parametrize("part", [bracket, flange, shaft, housing, plate])
def test_part_volume(client, world, part):
    f, vol = part(client, world)
    assert f["metrics"]["volume_mm3"] == pytest.approx(vol, rel=REL)


def test_parts_survive_a_step_round_trip(client, world):
    built = [part(client, world)[0] for part in (bracket, flange, shaft, housing, plate)]
    r = client.get(
        f"/api/documents/{world.doc}/model", params={"format": "STEP"}, headers=world.h["designer"]
    )
    assert r.status_code == 200
    back = client.post(
        f"/api/documents/{world.doc}/imports",
        files={"file": ("parts.step", r.content)},
        headers=world.h["designer"],
    )
    parts = {p["name"]: p for p in fid(back)["metrics"]["parts"]}
    assert len(parts) == 5
    for f in built:
        p = parts[f"{f['feature_type'].title()}{f['seq']:03d}"]
        assert p["volume_mm3"] == pytest.approx(f["metrics"]["volume_mm3"], rel=REL)
        assert p["surface_area_mm2"] == pytest.approx(f["metrics"]["surface_area_mm2"], rel=REL)
