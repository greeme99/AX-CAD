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
