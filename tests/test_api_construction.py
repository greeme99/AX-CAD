"""Track 6 construction quotes over the API: QTO preview with a base revision (demolition/new/keep),
construction quote lines and traces, adjust factors, validity warnings, scenarios and sensitivity."""

import io

import pytest

from tests.test_api_quote import estimator  # noqa: F401 - fixture
from tests.test_qto import W1, W2, W3, plan


def dxf(tmp_path, name, walls, doors=1):
    return io.BytesIO(open(plan(tmp_path / name, walls, doors), "rb").read())


def upload(client, w, data):
    r = client.post(
        f"/api/documents/{w.doc}/dxf", files={"file": ("p.dxf", data)}, headers=w.h["designer"]
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["revision_id"]


@pytest.fixture
def revs(client, world, tmp_path):
    """(base, current): the existing building (W1, W2, one door) and the renovation (W1, W3,
    two doors) as two revisions of the same drawing."""
    base = upload(client, world, dxf(tmp_path, "base.dxf", [W1, W2]))
    cur = upload(client, world, dxf(tmp_path, "cur.dxf", [W1, W3], doors=2))
    return base, cur


def test_qto_preview_splits_against_the_base(client, world, estimator, revs):  # noqa: F811
    base, cur = revs
    r = client.get(f"/api/revisions/{cur}/qto?base_revision_id={base}", headers=estimator)
    assert r.status_code == 200, r.text
    q = r.json()["data"]
    m = q["measures"]
    assert m["demo_wall_length_m"] == 4 and m["new_wall_length_m"] == 5
    assert m["keep_wall_length_m"] == 5 and m["new_door_count"] == 1
    assert q["base_revision_id"] == base and q["rooms"][0]["name"] == "거실"
    assert q["rules_source"] == "DEFAULT"
    # no base: everything is new work
    m = client.get(f"/api/revisions/{cur}/qto", headers=estimator).json()["data"]["measures"]
    assert m["new_wall_length_m"] == 10 and m["demo_wall_length_m"] == 0
    r = client.get(f"/api/revisions/{cur}/qto?base_revision_id={cur}", headers=estimator)
    assert r.status_code == 422 and r.json()["error"]["code"] == "QTO_BASE_INVALID"
    r = client.get(f"/api/revisions/{cur}/qto?base_revision_id=x", headers=estimator)
    assert r.status_code == 422  # not a revision id


def test_qto_base_from_another_project_is_refused(client, world, estimator, revs, tmp_path):  # noqa: F811
    _, cur = revs
    admin = world.h["admin"]
    pid = client.post(
        "/api/projects", json={"project_code": "P2", "project_name": "o"}, headers=admin
    ).json()["data"]["project_id"]
    doc = client.post(
        f"/api/projects/{pid}/documents",
        json={"doc_no": "D2", "doc_type": "DRAWING", "title": "T"},
        headers=admin,
    ).json()["data"]["document_id"]
    r = client.post(
        f"/api/documents/{doc}/dxf",
        files={"file": ("p.dxf", dxf(tmp_path, "o.dxf", [W1]))},
        headers=admin,
    )
    other = r.json()["data"]["revision_id"]
    url = f"/api/revisions/{cur}/qto?base_revision_id={other}"
    assert client.get(url, headers=estimator).status_code == 404  # not a member there
    r = client.get(url, headers=admin)
    assert r.status_code == 422 and r.json()["error"]["code"] == "QTO_BASE_INVALID"
