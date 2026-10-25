"""Start drafting in AX-CAD: a blank first revision with standard layers (no outside CAD file)."""

import io

import ezdxf

from core.dxf.template import LAYERS, blank_dxf


def blank(client, w, doc, who="designer", **body):
    return client.post(f"/api/documents/{doc}/blank", json=body, headers=w.h[who])


def test_blank_drawing_then_draft_on_it(client, world):
    d = world.h["designer"]
    r = blank(client, world, world.doc)
    assert r.status_code == 200, r.text
    first = r.json()["data"]
    assert first["revision_no"] == "A" and first["entity_count"] == 0 and first["warnings"] == []
    rid = first["revision_id"]
    render = client.get(f"/api/revisions/{rid}/render", headers=d).json()["data"]
    assert render["extents"] == {"min": [0, 0], "max": [420, 297]}  # the A3 sheet, not a point
    assert render["current_layer"] == "OUTLINE"
    names = [layer["name"] for layer in render["layers"]]
    assert all(n in names for n, *_ in LAYERS)
    hidden = next(layer for layer in render["layers"] if layer["name"] == "HIDDEN")
    assert (
        hidden["linetype"] == "DASHED" and render["linetype_dash"]["DASHED"][0] > 3
    )  # ~3 mm dashes
    revs = client.get(f"/api/documents/{world.doc}/revisions", headers=d).json()["data"]["items"]
    assert revs[0]["note"] == "빈 도면에서 시작"

    # drafting on it saves revision B like any drawing
    line = {"type": "LINE", "start": [10, 10], "end": [110, 10]}
    r = client.post(
        f"/api/revisions/{rid}/edits",
        json={"created": [{"layer": "OUTLINE", "geom": line}]},
        headers=d,
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["revision_no"] == "B" and r.json()["data"]["entity_count"] == 1

    # only a drawing without revisions starts blank (no silent replacement of real work)
    r = blank(client, world, world.doc)
    assert r.status_code == 409 and r.json()["error"]["code"] == "DOCUMENT_HAS_REVISION"


def test_blank_options_and_roles(client, world):
    assert blank(client, world, world.doc, who="viewer").status_code == 403
    r = blank(client, world, world.doc, sheet="A4", portrait=True)
    rid = r.json()["data"]["revision_id"]
    ext = client.get(f"/api/revisions/{rid}/render", headers=world.h["designer"]).json()["data"][
        "extents"
    ]
    assert ext == {"min": [0, 0], "max": [210, 297]}
    for bad in ({"sheet": "B5"}, {"sheet": "A3", "extra": 1}):
        assert blank(client, world, world.doc, **bad).status_code == 422


def test_template_is_a_clean_mm_drawing():
    doc = ezdxf.read(io.StringIO(blank_dxf("A1").decode("utf-8")))
    assert doc.units == 4 and doc.header["$MEASUREMENT"] == 1
    assert tuple(doc.header["$LIMMAX"])[:2] == (841, 594)
    assert doc.layers.get("OUTLINE").dxf.lineweight == 50
    assert not len(doc.modelspace())
    auditor = doc.audit()
    assert not auditor.has_errors
