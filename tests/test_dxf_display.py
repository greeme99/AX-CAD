"""Track 8 P1-1: 2D reading/display of hatches, block attributes, leaders, points, linetypes,
lineweights and paper-space sheets (synthetic samples in tests/samples/drawings.py)."""

import json

import ezdxf
import pytest
from ezdxf import units

from core.dxf.reader import RENDER_FORMAT, parse_dxf
from tests.samples.drawings import SAMPLES, write_all


@pytest.fixture(scope="module")
def parsed(tmp_path_factory):
    folder = tmp_path_factory.mktemp("samples")
    return {name: parse_dxf(str(path)) for name, path in write_all(folder).items()}


def by_type(p, kind):
    return [e for e in p["entities"] if e["type"] == kind]


def test_every_sample_reads_without_unsupported_warnings(parsed):
    assert set(parsed) == set(SAMPLES)
    for name, p in parsed.items():
        assert p["format"] == RENDER_FORMAT
        assert not [w for w in p["warnings"] if w.startswith("UNSUPPORTED_ENTITY")], name


def test_hatch_pattern_lines_and_solid_fill(parsed):
    pattern, solid = sorted(by_type(parsed["hatch"], "HATCH"), key=lambda e: bool(e.get("fill")))
    assert pattern["paths"][0][0] == pattern["paths"][0][-1]  # boundary drawn closed
    assert len(pattern["paths"]) > 5 and "fill" not in pattern  # ANSI31 lines inside it
    for a, b in pattern["paths"][1:]:  # every pattern line stays within the 40 x 20 boundary
        assert all(-1e-6 <= p[0] <= 40 + 1e-6 and -1e-6 <= p[1] <= 20 + 1e-6 for p in (a, b))
    assert solid["fill"] is True and solid["paths"][0][0] == [50.0, 0.0]


def test_dense_hatch_falls_back_to_its_boundary(tmp_path):
    doc = ezdxf.new("R2018")
    doc.units = units.MM
    h = doc.modelspace().add_hatch()
    h.paths.add_polyline_path([(0, 0), (1000, 0), (1000, 1000), (0, 1000)], is_closed=True)
    h.set_pattern_fill("ANSI31", scale=0.001)
    doc.saveas(tmp_path / "dense.dxf")
    p = parse_dxf(str(tmp_path / "dense.dxf"))
    assert "HATCH_PATTERN_SIMPLIFIED:1" in p["warnings"]
    assert len(by_type(p, "HATCH")[0]["paths"]) == 1


def test_block_attributes_are_drawn_with_their_insert(parsed):
    p = parsed["blocks"]
    attribs = by_type(p, "ATTRIB")
    assert sorted(a["text"]["value"] for a in attribs) == ["P-1", "P-2", "P-3"]
    circles = {e["handle"] for e in by_type(p, "CIRCLE")}
    assert {a["handle"] for a in attribs} == circles  # picked together with their block
    # each reference is editable as a whole (one piece carries it); its content is not
    assert sorted(e["geom"]["type"] for e in p["entities"] if "geom" in e) == ["INSERT"] * 3


def test_leader_point_and_dimension(parsed):
    p = parsed["annotations"]
    assert any(e.get("fill") for e in by_type(p, "SOLID"))  # leader arrowhead
    assert [30.0, 30.0] in [e["point"] for e in by_type(p, "POINT")]
    assert any("NOTE" in e["text"]["value"] for e in by_type(p, "MTEXT"))


def test_linetypes_and_lineweights(parsed):
    center, dashdot, dashed = sorted(
        by_type(parsed["linetypes"], "LINE"), key=lambda e: e["paths"][0][0][1]
    )
    assert center["dash"] == [3.175, 0.635, 0.635, 0.635] and center["lw"] == 0.25  # BYLAYER
    assert dashdot["dash"][2] == 0.0 and dashdot["lw"] == 0.5  # a dot is a 0-length dash
    assert dashed["dash"] == [2.54, 0.508] and "lw" not in dashed  # ltscale 2 x DASHED


def test_paper_space_sheet_with_viewport(parsed):
    p = parsed["sheet"]
    assert len(p["entities"]) == 1 and "geom" in p["entities"][0]  # model space stays editable
    (sheet,) = p["sheets"]
    assert sheet["name"] == "Layout1"
    assert sheet["paper"]["max"][0] - sheet["paper"]["min"][0] == 420
    assert {e["type"] for e in sheet["entities"]} == {"LWPOLYLINE", "TEXT"}
    assert all("geom" not in e for e in sheet["entities"])  # sheets are view-only
    (vp,) = sheet["viewports"]
    assert vp == {"frame": [[50.0, 50.0], [250.0, 250.0]], "view_center": [50.0, 50.0], "zoom": 2.0}


def test_unused_default_layout_is_not_a_sheet(parsed):
    assert parsed["hatch"]["sheets"] == []


def test_old_render_payload_is_upgraded_on_view(client, world, tmp_path):
    from backend.api import main

    path = write_all(tmp_path)["sheet"]
    r = client.post(
        f"/api/documents/{world.doc}/dxf",
        files={"file": ("s.dxf", path.read_bytes())},
        headers=world.h["designer"],
    )
    rid = r.json()["data"]["revision_id"]
    stored = main.VAR_DIR / "revisions" / f"{rid}.json"
    old = json.loads(stored.read_text())
    for key in ("format", "sheets"):  # as written before Track 8
        old.pop(key)
    stored.write_text(json.dumps(old))
    data = client.get(f"/api/revisions/{rid}/render", headers=world.h["viewer"]).json()["data"]
    assert data["format"] == RENDER_FORMAT and data["sheets"][0]["name"] == "Layout1"
    assert json.loads(stored.read_text())["format"] == RENDER_FORMAT  # upgraded once, on disk
