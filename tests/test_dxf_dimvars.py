"""W4 (plan §22.4): a dimension's own variables (ActiveX Dim* properties) and text override round-trip."""

import ezdxf
import pydantic
import pytest

from core.dxf.reader import DxfError, parse_dxf
from core.dxf.writer import apply_edits

DIM = {"type": "DIM_LINEAR", "p1": [0, 0], "p2": [50, 0], "base": [0, 10], "angle": 0}


@pytest.fixture
def src(tmp_path):
    d = ezdxf.new("R2018", setup=True)
    d.units = ezdxf.units.MM
    d.blocks.new("TICK").add_line((0, 0), (1, 1))
    p = str(tmp_path / "s.dxf")
    d.saveas(p)
    return p


def dims(path):
    return [
        e
        for e in parse_dxf(path)["entities"]
        if e.get("geom", {}).get("type", "").startswith("DIM_")
    ]


def test_overrides_and_text_round_trip(src, tmp_path):
    over = {
        "dimasz": 4.0,
        "dimtxt": 3.5,
        "dimclrd": 1,
        "dimblk": "TICK",
        "dimpost": "<> mm",
        "dimdec": 1,
    }
    dst = str(tmp_path / "d.dxf")
    apply_edits(
        src,
        dst,
        {"created": [{"layer": "0", "geom": {**DIM, "overrides": over, "text": "<> TYP"}}]},
    )
    (d,) = dims(dst)
    assert d["geom"]["text"] == "<> TYP"
    for k, v in over.items():
        assert d["geom"]["overrides"][k] == v, k
    # a later change keeps the others and sets the new one
    dst2 = str(tmp_path / "e.dxf")
    g = {**d["geom"], "overrides": {"dimtxt": 5.0}}
    apply_edits(dst, dst2, {"modified": [{"handle": d["handle"], "layer": "0", "geom": g}]})
    (d2,) = dims(dst2)
    assert d2["geom"]["overrides"]["dimtxt"] == 5.0 and d2["geom"]["overrides"]["dimasz"] == 4.0
    assert d2["geom"]["text"] == "<> TYP"


def test_refusals(src, tmp_path):
    for over, code in (
        ({"dimblk": "NOPE"}, "EDIT_BLOCK_NOT_FOUND"),
        ({"dimltype": "NOPE"}, "EDIT_LINETYPE_NOT_FOUND"),
        ({"dimtxsty": "NOPE"}, "EDIT_TABLE_NOT_FOUND"),
    ):
        with pytest.raises(DxfError) as ex:
            apply_edits(
                src,
                str(tmp_path / "d.dxf"),
                {"created": [{"layer": "0", "geom": {**DIM, "overrides": over}}]},
            )
        assert ex.value.code == code


def test_request_validation():
    from backend.api.main import EditRequest

    for over in (
        {"dimx": 1},
        {"dimasz": -1},
        {"dimtad": 9},
        {"dimpost": "a\nb"},
        {"dimlwd": 7},
        {"dimtol": True},
    ):
        with pytest.raises(pydantic.ValidationError):
            EditRequest.model_validate(
                {"created": [{"layer": "0", "geom": {**DIM, "overrides": over}}]}
            )
    with pytest.raises(pydantic.ValidationError):
        EditRequest.model_validate({"created": [{"layer": "0", "geom": {**DIM, "text": "x\r"}}]})
    EditRequest.model_validate(
        {
            "created": [
                {"layer": "0", "geom": {**DIM, "overrides": {"dimasz": 2, "dimblk": "OPEN30"}}}
            ]
        }
    )
