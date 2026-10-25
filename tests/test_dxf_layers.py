"""Track 8 P1-3b: layer create/rename/colour/linetype/lock and entity colour/linetype edits."""

import ezdxf
import pytest

from core.dxf.reader import DxfError, parse_dxf
from core.dxf.writer import apply_edits

LINE = {"type": "LINE", "start": [0, 0], "end": [10, 0]}


def run(tmp_path, edits, doc):
    src, dst = str(tmp_path / "s.dxf"), str(tmp_path / "d.dxf")
    doc.saveas(src)
    apply_edits(src, dst, edits)
    return dst


@pytest.fixture
def doc():
    d = ezdxf.new(setup=True)
    d.units = ezdxf.units.MM
    d.layers.add("WALL", color=1)
    d.modelspace().add_line((0, 0), (5, 0), dxfattribs={"layer": "WALL"})
    return d


def test_layer_ops_apply_in_order(tmp_path, doc):
    edits = {
        "created": [{"layer": "WALL", "geom": LINE}],  # entity edits come first, on the old name
        "layers": [
            {"name": "NOTES", "create": True, "color": 3, "linetype": "DASHED"},
            {"name": "WALL", "rename": "WALL-EX", "color": 5, "locked": True},
        ],
    }
    p = parse_dxf(run(tmp_path, edits, doc))
    layers = {ly["name"]: ly for ly in p["layers"]}
    assert "WALL" not in layers
    assert layers["WALL-EX"]["aci"] == 5 and layers["WALL-EX"]["locked"] is True
    assert layers["NOTES"]["aci"] == 3 and layers["NOTES"]["linetype"] == "DASHED"
    assert {e["layer"] for e in p["entities"]} == {"WALL-EX"}  # entities follow the rename
    assert "DASHED" in p["linetypes"]


@pytest.mark.parametrize(
    ("op", "code"),
    [
        ({"name": "WALL", "create": True}, "EDIT_LAYER_EXISTS"),
        ({"name": "BAD<NAME", "create": True}, "EDIT_LAYER_INVALID"),
        ({"name": "NOPE", "color": 2}, "EDIT_LAYER_NOT_FOUND"),
        ({"name": "0", "rename": "ZERO"}, "EDIT_LAYER_INVALID"),
        ({"name": "WALL", "rename": "0"}, "EDIT_LAYER_EXISTS"),
        ({"name": "WALL", "linetype": "NO-SUCH"}, "EDIT_LINETYPE_NOT_FOUND"),
    ],
)
def test_layer_op_errors(tmp_path, doc, op, code):
    with pytest.raises(DxfError) as exc:
        run(tmp_path, {"layers": [op]}, doc)
    assert exc.value.code == code


def test_entity_colour_and_linetype(tmp_path, doc):
    line = doc.modelspace()[0]
    line.dxf.true_color = 0x123456
    h = line.dxf.handle
    edits = {
        "modified": [
            {"handle": h, "layer": "WALL", "geom": LINE, "color": 2, "linetype": "CENTER"}
        ],
        "created": [{"layer": "0", "geom": LINE}],
    }
    p = parse_dxf(run(tmp_path, edits, doc))
    mod = next(e for e in p["entities"] if e["handle"] == h)
    new = next(e for e in p["entities"] if e["handle"] != h)
    assert (mod["aci"], mod["linetype"], mod["color"]) == (2, "CENTER", "#ffff00")
    assert (new["aci"], new["linetype"]) == (256, "BYLAYER")  # created entities follow their layer
    with pytest.raises(DxfError) as exc:
        run(tmp_path, {"modified": [{**edits["modified"][0], "linetype": "NO-SUCH"}]}, doc)
    assert exc.value.code == "EDIT_LINETYPE_NOT_FOUND"


def test_edited_dimension_keeps_its_colour(tmp_path, doc):
    dim = doc.modelspace().add_linear_dim(
        base=(0, 5), p1=(0, 0), p2=(10, 0), dxfattribs={"color": 4}
    )
    dim.render()
    geom = {"type": "DIM_LINEAR", "p1": [0, 0], "p2": [20, 0], "base": [0, 5], "angle": 0}
    h = dim.dimension.dxf.handle
    out = ezdxf.readfile(
        run(tmp_path, {"modified": [{"handle": h, "layer": "0", "geom": geom}]}, doc)
    )
    (d,) = out.modelspace().query("DIMENSION")
    assert d.dxf.color == 4 and d.get_measurement() == pytest.approx(20)


def test_api_layer_ops_schema(client, world):
    for body in (
        {"layers": [{"name": "A", "color": 256}]},  # layers have no BYLAYER colour
        {"layers": [{"name": "A", "frozen": True}]},  # unknown field
        {"created": [{"layer": "0", "geom": LINE, "color": 300}]},
        {"layers": []},  # nothing to do
    ):
        r = client.post(f"/api/revisions/{'0' * 32}/edits", json=body, headers=world.h["designer"])
        assert r.status_code == 422 and r.json()["error"]["code"] == "EDIT_INVALID", body


SQUARE = [[0, 0, 0], [20, 0, 0], [20, 10, 1], [0, 10, 0]]  # one bulged (half-circle) side


@pytest.mark.parametrize("pattern", ["ANSI31", "SOLID"])
def test_hatch_create_read_and_modify(tmp_path, doc, pattern):
    geom = {"type": "HATCH", "boundary": SQUARE, "pattern": pattern, "scale": 2, "angle": 15}
    p = parse_dxf(run(tmp_path, {"created": [{"layer": "WALL", "geom": geom}]}, doc))
    (h,) = [e for e in p["entities"] if e["type"] == "HATCH"]
    want = {**geom} if pattern != "SOLID" else {**geom, "scale": 1, "angle": 0}
    assert h["geom"] == want and h["paths"]  # editable, and drawn by the server
    assert h.get("fill", False) == (pattern == "SOLID")
    moved = {**geom, "boundary": [[x + 5, y, b] for x, y, b in SQUARE], "pattern": "ANSI37"}
    doc2 = ezdxf.readfile(tmp_path / "d.dxf")
    p2 = parse_dxf(
        run(tmp_path, {"modified": [{"handle": h["handle"], "layer": "WALL", "geom": moved}]}, doc2)
    )
    (h2,) = [e for e in p2["entities"] if e["type"] == "HATCH"]
    assert h2["handle"] == h["handle"] and h2["geom"] == moved


def test_hatch_rules(tmp_path, doc):
    bad = {"type": "HATCH", "boundary": SQUARE, "pattern": "NO-SUCH", "scale": 1, "angle": 0}
    with pytest.raises(DxfError) as exc:
        run(tmp_path, {"created": [{"layer": "0", "geom": bad}]}, doc)
    assert exc.value.code == "EDIT_PATTERN_NOT_FOUND"
    two = doc.modelspace().add_hatch()  # two boundary loops (an island): shown, read-only
    two.paths.add_polyline_path([(0, 0), (9, 0), (9, 9)], is_closed=True)
    two.paths.add_polyline_path([(1, 1), (2, 1), (2, 2)], is_closed=True)
    p = parse_dxf(run(tmp_path, {"layers": [{"name": "WALL", "color": 2}]}, doc))
    (h,) = [e for e in p["entities"] if e["type"] == "HATCH"]
    assert "geom" not in h and h["paths"]
    with pytest.raises(DxfError) as exc:
        run(tmp_path, {"deleted": [two.dxf.handle]}, doc)
    assert exc.value.code == "EDIT_HANDLE_NOT_FOUND"


def test_block_create_insert_and_move_with_attributes(tmp_path, doc):
    circle = {"type": "CIRCLE", "center": [10, 10], "radius": 2}
    ins = {
        "type": "INSERT",
        "name": "BOLT",
        "insert": [10, 10],
        "xscale": 1,
        "yscale": 1,
        "rotation": 0,
    }
    edits = {
        "blocks": [
            {"name": "BOLT", "base": [10, 10], "entities": [{"layer": "WALL", "geom": circle}]}
        ],
        "created": [{"layer": "WALL", "geom": ins}],
    }
    p = parse_dxf(run(tmp_path, edits, doc))
    (rec,) = [e for e in p["entities"] if e.get("geom", {}).get("type") == "INSERT"]
    assert rec["geom"] == ins  # the new reference draws the circle where it was
    assert rec["paths"][0][0] == pytest.approx([12, 10])
    bolt = next(b for b in p["blocks"] if b["name"] == "BOLT")
    assert bolt["paths"][0][0] == pytest.approx([2, 0])  # catalog: block coordinates

    doc2 = ezdxf.readfile(tmp_path / "d.dxf")
    doc2.blocks.get("BOLT").add_attdef("NO", (0, 3), dxfattribs={"height": 1})
    tagged = doc2.modelspace().add_blockref("BOLT", (0, 0))
    tagged.add_auto_attribs({"NO": "7"})
    moved = {**ins, "insert": [50, 20], "xscale": 2, "yscale": -2, "rotation": 90}
    h = tagged.dxf.handle
    out = run(tmp_path, {"modified": [{"handle": h, "layer": "0", "geom": moved}]}, doc2)
    p2 = parse_dxf(out)
    g = next(e["geom"] for e in p2["entities"] if e["handle"] == h and "geom" in e)
    assert g["name"] == "BOLT" and g["insert"] == pytest.approx([50, 20])
    assert (g["xscale"], g["yscale"], g["rotation"]) == pytest.approx((2, -2, 90))
    (att,) = ezdxf.readfile(out).entitydb.get(h).attribs
    assert att.dxf.insert.isclose((36, 0, 0), abs_tol=1e-6)  # (0, 3) - base, x2/-2, +90 deg


@pytest.mark.parametrize(
    ("edits", "code"),
    [
        (
            {
                "blocks": [
                    {"name": "WALL*", "base": [0, 0], "entities": [{"layer": "0", "geom": LINE}]}
                ]
            },
            "EDIT_BLOCK_INVALID",
        ),
        (
            {
                "created": [
                    {
                        "layer": "0",
                        "geom": {
                            "type": "INSERT",
                            "name": "NONE",
                            "insert": [0, 0],
                            "xscale": 1,
                            "yscale": 1,
                            "rotation": 0,
                        },
                    }
                ]
            },
            "EDIT_BLOCK_NOT_FOUND",
        ),
        (
            {
                "created": [
                    {
                        "layer": "0",
                        "geom": {
                            "type": "INSERT",
                            "name": "*Model_Space",
                            "insert": [0, 0],
                            "xscale": 1,
                            "yscale": 1,
                            "rotation": 0,
                        },
                    }
                ]
            },
            "EDIT_BLOCK_NOT_FOUND",
        ),
    ],
)
def test_block_rules(tmp_path, doc, edits, code):
    with pytest.raises(DxfError) as exc:
        run(tmp_path, edits, doc)
    assert exc.value.code == code
