"""Track 8 P3-1: projected views (hidden line removal), sheet layout and drawings from 3D."""

import io

import ezdxf
import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

from core.dxf.drawing import drawing_dxf, layout, read_config
from core.geometry import dressup as d
from core.geometry.drawing import project
from tests.test_api_pad import post

ALL = ["FRONT", "TOP", "RIGHT", "ISO"]


def plate():
    """40 x 30 x 10 plate with a 10 mm hole through its middle."""
    return d.holes(BRepPrimAPI_MakeBox(40, 30, 10).Shape(), [(20, 15)], 10, None)[0]


def lines(es):
    return sorted((tuple(e["start"]), tuple(e["end"])) for e in es if e["type"] == "LINE")


def test_three_views_of_a_plate():
    front = project(plate(), "FRONT")
    assert lines(front["visible"]) == lines(
        [
            {"type": "LINE", "start": [0.0, 0.0], "end": [0.0, 10.0]},
            {"type": "LINE", "start": [0.0, 0.0], "end": [40.0, 0.0]},
            {"type": "LINE", "start": [40.0, 0.0], "end": [40.0, 10.0]},
            {"type": "LINE", "start": [0.0, 10.0], "end": [40.0, 10.0]},
        ]
    )
    # the hole: two hidden walls only (edges behind visible lines are not drawn twice)
    assert sorted(lines(front["hidden"])) == [
        ((15.0, 10.0), (15.0, 0.0)),
        ((25.0, 10.0), (25.0, 0.0)),
    ]
    assert lines(front["center"]) == [((20.0, 13.0), (20.0, -3.0))]
    top = project(plate(), "TOP")
    assert {"type": "CIRCLE", "center": [20.0, 15.0], "radius": 5.0} in top["visible"]
    assert len(top["center"]) == 2  # a cross over the hole
    right = project(plate(), "RIGHT")
    assert max(max(e["start"][0], e["end"][0]) for e in right["visible"]) == pytest.approx(30)
    iso = project(plate(), "ISO")
    assert iso["hidden"] == [] and iso["center"] == [] and len(iso["visible"]) >= 9


@pytest.mark.parametrize("projection", ["THIRD", "FIRST"])
def test_layout_places_views_by_projection(projection):
    views = [project(plate(), v) for v in ALL]
    offsets, scale = layout(views, {"sheet": "A3", "projection": projection})
    assert scale == 2  # a 40 mm part on A3 fits at 2:1
    (fx, fy), (tx, ty), (rx, ry) = offsets["FRONT"], offsets["TOP"], offsets["RIGHT"]
    assert tx == pytest.approx(fx)  # the top view lines up over/under the front view
    assert ry == pytest.approx(fy)  # the side view lines up beside it
    if projection == "THIRD":
        assert ty > fy and rx > fx
    else:
        assert ty < fy and rx < fx


def test_regenerate_keeps_user_entities_and_settings():
    views = [project(plate(), v) for v in ALL]
    cfg = {"views": ALL, "projection": "THIRD", "sheet": "A3", "portrait": False, "scale": None}
    first = drawing_dxf(views, cfg, None)
    doc = ezdxf.read(io.StringIO(first.decode()))
    auditor = doc.audit()
    assert not auditor.has_errors
    assert {e.dxf.layer for e in doc.modelspace()} == {
        "AXGEN_OUTLINE",
        "AXGEN_HIDDEN",
        "AXGEN_CENTER",
    }
    assert read_config(first)[0]["used_scale"] == 2
    # the user adds a note; the part changes; regenerate redraws only the generated layers
    doc.modelspace().add_text("NOTE", dxfattribs={"layer": "TEXT"})
    buf = io.StringIO()
    doc.write(buf)
    longer = d.holes(BRepPrimAPI_MakeBox(60, 30, 10).Shape(), [(20, 15)], 10, None)[0]
    again = drawing_dxf([project(longer, v) for v in ALL], cfg, buf.getvalue().encode())
    doc2 = ezdxf.read(io.StringIO(again.decode()))
    assert [e.dxf.text for e in doc2.modelspace().query("TEXT")] == ["NOTE"]
    xs = [
        p
        for e in doc2.modelspace().query("LINE[layer=='AXGEN_OUTLINE']")
        for p in (e.dxf.start.x, e.dxf.end.x)
    ]
    assert max(xs) - min(xs) > 60  # the longer part (front view + side views)
    assert read_config(again)[0]["views"] == ALL


def test_drawing_from_3d_api(client, world):
    dz = world.h["designer"]
    box = {"kind": "BOX", "length": 40, "width": 30, "height": 10}
    bid = post(client, world, world.doc, "PRIMITIVE", box).json()["data"]["feature_id"]
    hole = {"target_feature_id": bid, "positions": [[20, 15]], "diameter": 10}
    hid = post(client, world, world.doc, "HOLE", hole).json()["data"]["feature_id"]
    url = f"/api/documents/{world.doc}/drawing"
    r = client.post(url, json={"projection": "FIRST"}, headers=dz)
    assert r.status_code == 200, r.text
    rid = r.json()["data"]["revision_id"]
    render = client.get(f"/api/revisions/{rid}/render", headers=dz).json()["data"]
    assert {e["layer"] for e in render["entities"]} == {
        "AXGEN_OUTLINE",
        "AXGEN_HIDDEN",
        "AXGEN_CENTER",
    }
    # the user writes on it, the hole grows, the drawing is regenerated with the same settings
    note = {"type": "TEXT", "insert": [5, 5], "value": "NOTE", "height": 3.5, "rotation": 0}
    r = client.post(
        f"/api/revisions/{rid}/edits",
        json={"created": [{"layer": "TEXT", "geom": note}]},
        headers=dz,
    )
    assert r.status_code == 200, r.text
    assert (
        client.patch(
            f"/api/features/{hid}", json={"params": hole | {"diameter": 12}}, headers=dz
        ).status_code
        == 200
    )
    r = client.post(url, json={}, headers=dz)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["revision_no"] == "C"
    render = client.get(
        f"/api/revisions/{r.json()['data']['revision_id']}/render", headers=dz
    ).json()["data"]
    assert any(e["layer"] == "TEXT" for e in render["entities"])
    assert any(
        e.get("geom", {}).get("radius") == 6
        for e in render["entities"]
        if e["layer"] == "AXGEN_OUTLINE"
    )
    revs = client.get(f"/api/documents/{world.doc}/revisions", headers=dz).json()["data"]["items"]
    assert [x["note"] for x in revs[::2]] == ["3D에서 도면 다시 생성", "3D에서 도면 생성"]


def test_drawing_refusals(client, world, dxf):
    dz = world.h["designer"]
    url = f"/api/documents/{world.doc}/drawing"
    r = client.post(url, json={}, headers=dz)
    assert r.status_code == 409 and r.json()["error"]["code"] == "MODEL_EMPTY"
    post(
        client, world, world.doc, "PRIMITIVE", {"kind": "BOX", "length": 4, "width": 3, "height": 1}
    )
    assert client.post(url, json={}, headers=world.h["viewer"]).status_code == 403
    assert client.post(url, json={"views": ["FRONT", "FRONT"]}, headers=dz).status_code == 422
    # a drawing drafted by hand is never overwritten
    up = client.post(
        f"/api/documents/{world.doc}/dxf", files={"file": ("a.dxf", dxf())}, headers=dz
    )
    assert up.status_code == 200, up.text
    r = client.post(url, json={}, headers=dz)
    assert r.status_code == 409 and r.json()["error"]["code"] == "DRAWING_NOT_GENERATED"
