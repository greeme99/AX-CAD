"""Track 8 P3-1: projected views (hidden line removal), sheet layout and drawings from 3D."""

import io
import itertools
import json

import ezdxf
import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

from core.dxf.drawing import drawing_dxf, layout
from core.dxf.reader import _drawing_config
from core.geometry import dressup as d
from core.geometry.drawing import project
from core.geometry.errors import GeomError
from tests.test_api_pad import post

ALL = ["FRONT", "TOP", "RIGHT", "ISO"]


def read_config(data: bytes):
    return _drawing_config(ezdxf.read(io.StringIO(data.decode())))


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
    layers = {e.dxf.layer for e in doc.modelspace()}
    assert layers == {"AXGEN_OUTLINE", "AXGEN_HIDDEN", "AXGEN_CENTER", "AXGEN_DIM"}
    dim_blocks = sorted(b.name for b in doc.blocks if b.name.startswith("*D"))
    assert read_config(first)["used_scale"] == 2
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
    assert read_config(again)["views"] == ALL
    # the old dimensions' pictures are gone, not piled up
    assert len([b for b in doc2.blocks if b.name.startswith("*D")]) == len(dim_blocks)


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
    layers = {e["layer"] for e in render["entities"]}
    assert {"AXGEN_OUTLINE", "AXGEN_HIDDEN", "AXGEN_CENTER", "AXGEN_DIM", "AXGEN_TEXT"} <= layers
    assert all(x.startswith("AXGEN_") or x == "Defpoints" for x in layers)
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


def test_section_and_detail():
    from core.dxf.drawing import detail
    from core.geometry.drawing import section

    sec = section(plate(), "Y", 15)  # through the hole, seen from the front
    assert sec["hidden"] == [] and len(sec["hatch"]) == 2  # material either side of the hole
    areas = sorted(
        abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in itertools.pairwise(lp))) / 2
        for lp in sec["hatch"]
    )
    assert areas == pytest.approx([150, 150])  # 15 x 10 each
    with pytest.raises(GeomError) as e:
        section(plate(), "Z", 50)
    assert e.value.code == "GEOM_EMPTY_RESULT"
    det = detail(
        project(plate(), "TOP"), {"label": "B", "center": [20, 15], "radius": 8, "factor": 2}
    )
    assert {"type": "CIRCLE", "center": [0.0, 0.0], "radius": 10.0} in det[
        "visible"
    ]  # the hole, x2
    assert det["caption"] == "B (2:1)" and det["thin"][0]["radius"] == 16


def test_section_and_detail_on_the_sheet():
    from core.dxf.drawing import drawing_job
    from core.geometry.serialize import to_brep

    cfg = {
        "views": ["FRONT", "TOP", "RIGHT"],
        "projection": "THIRD",
        "sheet": "A3",
        "sections": [{"label": "A", "axis": "Y", "offset": 15}],
        "details": [{"label": "B", "view": "TOP", "center": [20, 15], "radius": 8, "factor": 2}],
    }
    doc = ezdxf.read(io.StringIO(drawing_job([to_brep(plate())], cfg, None).decode()))
    assert not doc.audit().has_errors
    msp = doc.modelspace()
    (hatch,) = msp.query("HATCH")
    assert len(hatch.paths) == 2 and hatch.dxf.pattern_name == "ANSI31"
    texts = sorted(t.dxf.text for t in msp.query("TEXT"))
    assert texts == ["A", "A", "A-A", "B", "B (2:1)"]


def test_sections_through_the_api(client, world):
    dz = world.h["designer"]
    box = {"kind": "BOX", "length": 40, "width": 30, "height": 10}
    post(client, world, world.doc, "PRIMITIVE", box)
    url = f"/api/documents/{world.doc}/drawing"
    bad = {
        "views": ["FRONT"],
        "details": [{"label": "B", "view": "TOP", "center": [0, 0], "radius": 5, "factor": 2}],
    }
    r = client.post(url, json=bad, headers=dz)
    assert r.status_code == 422 and r.json()["error"]["code"] == "GEOM_INVALID_PARAM"
    dup = {
        "sections": [
            {"label": "A", "axis": "Y", "offset": 1},
            {"label": "A", "axis": "X", "offset": 1},
        ]
    }
    assert client.post(url, json=dup, headers=dz).status_code == 422
    r = client.post(url, json={"sections": [{"label": "A", "axis": "X", "offset": 20}]}, headers=dz)
    assert r.status_code == 200, r.text
    render = client.get(
        f"/api/revisions/{r.json()['data']['revision_id']}/render", headers=dz
    ).json()["data"]
    assert any(e["type"] == "HATCH" for e in render["entities"])


def test_hole_callout_text():
    from core.dxf.drawing import hole_text

    tap = {"count": 4, "diameter": 5.0, "depth": 10, "thread": {"label": "M6", "length": 8}}
    assert hole_text(tap) == "4×M6 깊이 8"
    bolt = {
        "count": 2,
        "diameter": 9.0,
        "depth": None,
        "head": {"kind": "COUNTERBORE", "diameter": 14, "depth": 8.6},
    }
    assert hole_text(bolt) == "2×Ø9 관통\\P카운터보어 Ø14 깊이 8.6"
    assert (
        hole_text(
            {
                "count": 1,
                "diameter": 6.6,
                "depth": None,
                "head": {"kind": "COUNTERSINK", "diameter": 13.44},
            }
        )
        == "Ø6.6 관통\\P접시 Ø13.44×90°"
    )


def test_dimensions_callouts_notes_and_title_block(client, world):
    from pypdf import PdfReader

    dz = world.h["designer"]
    box = {"kind": "BOX", "length": 40, "width": 30, "height": 10}
    bid = post(client, world, world.doc, "PRIMITIVE", box).json()["data"]["feature_id"]
    spec = {"size": "M6", "kind": "TAPPED", "thread_depth": 6}
    post(
        client,
        world,
        world.doc,
        "HOLE",
        {"target_feature_id": bid, "positions": [[10, 10], [30, 20]], "depth": 8, "spec": spec},
    )
    body = {"views": ["FRONT", "TOP", "RIGHT"], "roughness": 3.2, "material": "SS400"}
    r = client.post(f"/api/documents/{world.doc}/drawing", json=body, headers=dz)
    assert r.status_code == 200, r.text
    rid = r.json()["data"]["revision_id"]
    src = (main_var(client) / "uploads" / f"{rid}.dxf").read_bytes()
    doc = ezdxf.read(io.StringIO(src.decode()))
    measured = sorted(round(d.get_measurement(), 6) for d in doc.modelspace().query("DIMENSION"))
    # width 40, height 10, depth 30, and the first hole 10 / 10 from the lower left corner
    assert measured == [10, 10, 10, 30, 40]
    (ml,) = doc.modelspace().query("MULTILEADER")
    assert ml.context.mtext.default_content == "2×M6 깊이 6"
    (note,) = doc.modelspace().query("MTEXT")
    assert "KS B ISO 2768-m" in note.text and "Ra 3.2" in note.text
    pdf = client.post(f"/api/revisions/{rid}/pdf", json={"paper": "A3"}, headers=dz)
    assert pdf.status_code == 200, pdf.text
    text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
    assert (
        "SS400" in text and "제3각법" in text and pdf.headers["x-plot-scale"] == "5:1"
    )  # 3 views of a 40 mm part fit A3 at 5:1


def main_var(client):
    from backend.api import main

    return main.VAR_DIR


def test_stored_settings_are_checked_like_a_request(client, world):
    """BUG-50: the settings come back from the file; a tampered drawing cannot steer generation
    or break its PDF, and a field sent as null goes back to the default."""
    from core.dxf.template import CONFIG_KEY

    dz = world.h["designer"]
    url = f"/api/documents/{world.doc}/drawing"
    post(
        client,
        world,
        world.doc,
        "PRIMITIVE",
        {"kind": "BOX", "length": 40, "width": 30, "height": 10},
    )
    r = client.post(url, json={"scale": 0.5}, headers=dz)
    assert r.status_code == 200, r.text
    rid = r.json()["data"]["revision_id"]
    render = lambda rid: client.get(f"/api/revisions/{rid}/render", headers=dz).json()["data"]
    assert render(rid)["drawing"]["used_scale"] == 0.5
    r = client.post(url, json={"scale": None}, headers=dz)  # back to fitting the sheet
    assert render(r.json()["data"]["revision_id"])["drawing"]["used_scale"] == 2
    src = (main_var(client) / "uploads" / f"{r.json()['data']['revision_id']}.dxf").read_text()
    doc = ezdxf.read(io.StringIO(src))
    bad = {"sheet": "A9", "views": ["FRONT"] * 5000, "used_scale": "x", "projection": [1]}
    doc.header.custom_vars.replace(CONFIG_KEY, json.dumps(bad))
    buf = io.StringIO()
    doc.write(buf)
    up = client.post(
        f"/api/documents/{world.doc}/dxf",
        files={"file": ("t.dxf", buf.getvalue().encode())},
        headers=dz,
    )
    assert up.status_code == 200, up.text
    r = client.post(url, json={}, headers=dz)
    assert r.status_code == 409 and r.json()["error"]["code"] == "DRAWING_CONFIG_INVALID"
    pdf = client.post(
        f"/api/revisions/{up.json()['data']['revision_id']}/pdf",
        json={"paper": "A3"},
        headers=world.h["viewer"],
    )
    assert pdf.status_code == 200  # fitted like any drawing, the odd values ignored
    assert client.post(url, json={"scale": 1e-9}, headers=dz).status_code == 422


def test_regenerate_only_drops_dimension_pictures():
    """BUG-50: a generated-layer dimension pointing at a user block does not take it along."""
    views = [project(plate(), v) for v in ALL]
    cfg = {"views": ALL, "projection": "THIRD", "sheet": "A3", "portrait": False, "scale": None}
    doc = ezdxf.read(io.StringIO(drawing_dxf(views, cfg, None).decode()))
    doc.blocks.new("LOGO").add_circle((0, 0), 5)
    doc.modelspace().add_blockref("LOGO", (0, 0), dxfattribs={"layer": "TEXT"})
    doc.modelspace().query("DIMENSION").first.dxf.geometry = "LOGO"
    buf = io.StringIO()
    doc.write(buf)
    again = ezdxf.read(io.StringIO(drawing_dxf(views, cfg, buf.getvalue().encode()).decode()))
    assert "LOGO" in again.blocks and len(again.modelspace().query("INSERT")) == 1
    assert not again.audit().has_errors
