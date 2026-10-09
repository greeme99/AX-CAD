import ezdxf
import pytest

from core.dxf.reader import DxfError, parse_dxf
from core.dxf.writer import apply_edits

DIMS = {
    "DIM_LINEAR": {"p1": [0, 0], "p2": [100, 0], "base": [50, 10], "angle": 0},
    "DIM_ALIGNED": {"p1": [0, 0], "p2": [30, 40], "distance": -5},
    "DIM_ANGULAR": {"center": [0, 0], "p1": [10, 0], "p2": [0, 10], "base": [7, 7]},
    "DIM_RADIUS": {"center": [0, 0], "radius": 12.5, "angle": 45},
}
EXPECTED = {"DIM_LINEAR": 100, "DIM_ALIGNED": 50, "DIM_RADIUS": 12.5}
TEXT = {
    "DIM_LINEAR": "100.00",
    "DIM_ALIGNED": "50.00",
    "DIM_ANGULAR": "90°",
    "DIM_RADIUS": "R12.50",
}


def run(tmp_path, edits, doc=None):
    doc = doc or ezdxf.new(setup=True)
    doc.units = ezdxf.units.MM
    src, dst = str(tmp_path / "s.dxf"), str(tmp_path / "d.dxf")
    doc.saveas(src)
    apply_edits(src, dst, edits)
    return dst


@pytest.mark.parametrize("kind", DIMS)
def test_create_dimension(tmp_path, kind):
    dst = run(tmp_path, {"created": [{"layer": "0", "geom": {"type": kind, **DIMS[kind]}}]})
    dims = list(ezdxf.readfile(dst).modelspace().query("DIMENSION"))
    assert len(dims) == 1
    if kind in EXPECTED:
        assert dims[0].get_measurement() == pytest.approx(EXPECTED[kind], abs=1e-6)
    ents = [e for e in parse_dxf(dst)["entities"] if e["handle"] == dims[0].dxf.handle]
    assert ents and all("geom" not in e for e in ents)
    assert [e["text"]["value"] for e in ents if "text" in e] == [TEXT[kind]]
    assert {e["text"]["height"] for e in ents if "text" in e} == {2.5}


def test_delete_and_modify_dimension(tmp_path):
    geom = {"type": "DIM_LINEAR", **DIMS["DIM_LINEAR"]}
    d = ezdxf.new(setup=True)
    dim = d.modelspace().add_linear_dim(base=(0, 5), p1=(0, 0), p2=(10, 0))
    dim.render()
    h, block = dim.dimension.dxf.handle, dim.dimension.dxf.geometry
    line = {"type": "LINE", "start": [0, 0], "end": [1, 1]}
    with pytest.raises(DxfError) as exc:
        run(tmp_path, {"modified": [{"handle": h, "layer": "0", "geom": line}]}, d)
    assert exc.value.code == "EDIT_HANDLE_NOT_FOUND"
    with pytest.raises(DxfError) as exc:
        run(tmp_path, {"modified": [{"handle": h, "layer": "0", "geom": geom}]}, d)
    assert exc.value.code == "EDIT_HANDLE_NOT_FOUND"
    dst = run(tmp_path, {"deleted": [h]}, d)
    out = ezdxf.readfile(dst)
    assert not list(out.modelspace().query("DIMENSION"))
    assert block not in out.blocks  # rendered geometry block removed, not orphaned


def test_api_degenerate_dimensions(client, world):
    bad = [
        {"type": "DIM_LINEAR", **DIMS["DIM_LINEAR"], "p2": [0, 0]},
        {"type": "DIM_ALIGNED", **DIMS["DIM_ALIGNED"], "distance": 0},
        {"type": "DIM_ANGULAR", **DIMS["DIM_ANGULAR"], "p2": [10, 0]},
        {"type": "DIM_RADIUS", **DIMS["DIM_RADIUS"], "radius": 0},
    ]
    for geom in bad:
        r = client.post(
            f"/api/revisions/{'0' * 32}/edits",
            json={"created": [{"layer": "0", "geom": geom}]},
            headers=world.h["designer"],
        )
        assert r.status_code == 422 and r.json()["error"]["code"] == "EDIT_INVALID", geom
