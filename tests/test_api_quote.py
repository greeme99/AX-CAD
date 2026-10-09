"""S8 quote metrics API: 2D metrics of a revision with master-data rules, 3D bodies (FN-14/15)."""

import io
import math

import ezdxf
import pytest

from tests.test_api_master import FULL
from tests.test_api_model import boolean, box_and_hole

PI = math.pi


def plate_dxf():
    d = ezdxf.new(setup=True)
    d.units = 4
    msp = d.modelspace()
    for name in ("FOLD", "BEND"):
        d.layers.add(name)
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 50), (0, 50)], close=True)
    msp.add_circle((20, 25), 3)  # dia 6
    msp.add_circle((80, 25), 10)  # dia 20
    msp.add_line((50, 0), (50, 50), dxfattribs={"layer": "FOLD"})
    buf = io.StringIO()
    d.write(buf)
    return buf.getvalue().encode()


@pytest.fixture
def estimator(client, world, make_user, headers):
    uid = make_user("estimator", "ESTIMATOR")
    client.post(
        f"/api/projects/{world.pid}/members", json={"user_id": uid}, headers=world.h["admin"]
    )
    return headers("estimator")


def upload(client, w):
    r = client.post(
        f"/api/documents/{w.doc}/dxf",
        files={"file": ("p.dxf", plate_dxf())},
        headers=w.h["designer"],
    )
    return r.json()["data"]["revision_id"]


def test_metrics_default_then_master_rules(client, world, estimator):
    rid = upload(client, world)
    r = client.get(f"/api/revisions/{rid}/metrics", headers=estimator)
    assert r.status_code == 200, r.text
    m = r.json()["data"]
    assert m["rules_source"] == "DEFAULT" and m["master_version_id"] is None
    # default rules: FOLD is not a bend layer, so the line counts as cut geometry
    assert m["bend_count"] == 0 and m["hole_count"] == 2 and m["punch_hole_count"] == 0
    assert m["cutting_length_mm"] == pytest.approx(300 + 26 * PI + 50, abs=0.01)
    assert m["net_area_mm2"] == pytest.approx(5000 - 9 * PI - 100 * PI, abs=0.01)
    assert m["status"] == "INPUT_REQUIRED"  # no title block in the drawing

    admin = world.h["admin"]
    vid = client.post(
        "/api/master-versions",
        json={"version_code": "V1", "effective_from": "2020-01-01"},
        headers=admin,
    ).json()["data"]["version_id"]
    rules = [
        {"rule_type": "LAYER", "target": "BEND", "pattern": "FOLD*, BEND*"},
        {"rule_type": "PUNCH_MAX_DIA", "target": "HOLE", "pattern": "6"},
    ]
    client.put(f"/api/master-versions/{vid}", json=FULL | {"mapping_rules": rules}, headers=admin)
    assert client.post(f"/api/master-versions/{vid}/activate", headers=admin).status_code == 200

    m = client.get(f"/api/revisions/{rid}/metrics", headers=estimator).json()["data"]
    assert m["rules_source"] == "MASTER" and m["master_version_id"] == vid
    assert m["bend_count"] == 1 and m["bend_length_mm"] == pytest.approx(50)
    assert m["punch_hole_count"] == 1
    assert m["cutting_length_mm"] == pytest.approx(300 + 20 * PI, abs=0.01)  # punched hole excluded
    assert all(i["handle"] for i in m["items"])


def test_metrics_access(client, world, make_user, headers):
    rid = upload(client, world)
    assert client.get(f"/api/revisions/{rid}/metrics", headers=world.h["viewer"]).status_code == 403
    make_user("outsider", "ESTIMATOR")  # not a project member
    r = client.get(f"/api/revisions/{rid}/metrics", headers=headers("outsider"))
    assert r.status_code == 404
    assert client.get("/api/revisions/../metrics", headers=world.h["designer"]).status_code == 404


def test_metrics3d_lists_visible_bodies(client, world, estimator):
    _, a, b = box_and_hole(client, world)
    c = boolean(client, world, "CUT", a, b).json()["data"]["feature_id"]
    r = client.get(f"/api/documents/{world.doc}/metrics3d", headers=estimator)
    assert r.status_code == 200, r.text
    bodies = r.json()["data"]["bodies"]
    assert [x["feature_id"] for x in bodies] == [c]
    assert bodies[0]["name"] == "Cut003"
    assert bodies[0]["volume_mm3"] == pytest.approx(12000 - PI * 25 * 10, rel=1e-6)
    assert (
        client.get(f"/api/documents/{world.doc}/metrics3d", headers=world.h["viewer"]).status_code
        == 403
    )
