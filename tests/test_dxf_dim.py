import ezdxf
import pytest

from core.dxf.reader import DxfError, parse_dxf
from core.dxf.writer import apply_edits

DIMS = {
    "DIM_LINEAR": {"p1": [0, 0], "p2": [100, 0], "base": [50, 10], "angle": 0},
    "DIM_ALIGNED": {"p1": [0, 0], "p2": [30, 40], "distance": -5},
    "DIM_ANGULAR": {"center": [0, 0], "p1": [10, 0], "p2": [0, 10], "base": [7, 7]},
    "DIM_RADIUS": {"center": [0, 0], "radius": 12.5, "angle": 45},
    "DIM_DIAMETER": {"center": [5, 5], "radius": 10, "angle": 30},
}
EXPECTED = {"DIM_LINEAR": 100, "DIM_ALIGNED": 50, "DIM_RADIUS": 12.5, "DIM_DIAMETER": 20}
TEXT = {
    "DIM_LINEAR": "100.00",
    "DIM_ALIGNED": "50.00",
    "DIM_ANGULAR": "90°",
    "DIM_RADIUS": "R12.50",
    "DIM_DIAMETER": "Ø20.00",
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
    (rec,) = [e for e in ents if "geom" in e]  # one piece carries the editable definition
    g = rec["geom"]
    if kind == "DIM_ALIGNED":  # ezdxf writes aligned dimensions as rotated linear ones
        assert g["type"] == "DIM_LINEAR" and g["angle"] == pytest.approx(53.130102)
    elif kind == "DIM_LINEAR":  # the base point may slide along the dimension line
        assert {**g, "base": g["base"][1]} == {"type": kind, **DIMS[kind], "base": 10}
    else:
        flat = {
            k: v if isinstance(v, (int, float)) else tuple(v) for k, v in g.items() if k != "type"
        }
        want = {k: v if isinstance(v, (int, float)) else tuple(v) for k, v in DIMS[kind].items()}
        assert g["type"] == kind and flat.keys() == want.keys()
        assert all(flat[k] == pytest.approx(want[k]) for k in want)
    assert [e["text"]["value"] for e in ents if "text" in e] == [TEXT[kind]]
    assert {e["text"]["height"] for e in ents if "text" in e} == {2.5}


def test_delete_and_modify_dimension(tmp_path):
    geom = {"type": "DIM_LINEAR", **DIMS["DIM_LINEAR"]}
    d = ezdxf.new(setup=True)
    dim = d.modelspace().add_linear_dim(
        base=(0, 5), p1=(0, 0), p2=(10, 0), dimstyle="Standard", override={"dimtxt": 3.5}
    )
    dim.render()
    h, block = dim.dimension.dxf.handle, dim.dimension.dxf.geometry
    line = {"type": "LINE", "start": [0, 0], "end": [1, 1]}
    with pytest.raises(DxfError) as exc:
        run(tmp_path, {"modified": [{"handle": h, "layer": "0", "geom": line}]}, d)
    assert exc.value.code == "EDIT_INVALID"
    dst = run(tmp_path, {"modified": [{"handle": h, "layer": "0", "geom": geom}]}, d)
    out = ezdxf.readfile(dst)
    (new,) = out.modelspace().query("DIMENSION")
    assert new.get_measurement() == pytest.approx(100)
    assert new.dxf.dimstyle == "Standard" and new.override()["dimtxt"] == 3.5  # its own look kept
    assert [b.name for b in out.blocks if b.name.startswith("*D")] == [new.dxf.geometry]
    dst = run(tmp_path, {"deleted": [h]}, d)
    out = ezdxf.readfile(dst)
    assert not list(out.modelspace().query("DIMENSION"))
    assert block not in out.blocks  # rendered geometry block removed, not orphaned


def test_text_override_keeps_dimension_read_only(tmp_path):
    d = ezdxf.new(setup=True)
    dim = d.modelspace().add_linear_dim(base=(0, 5), p1=(0, 0), p2=(10, 0), text="SEE NOTE")
    dim.render()
    h = dim.dimension.dxf.handle
    path = run(tmp_path, {"deleted": []}, d)
    assert all("geom" not in e for e in parse_dxf(path)["entities"] if e["handle"] == h)
    geom = {"type": "DIM_LINEAR", **DIMS["DIM_LINEAR"]}
    with pytest.raises(DxfError) as exc:
        run(tmp_path, {"modified": [{"handle": h, "layer": "0", "geom": geom}]}, d)
    assert exc.value.code == "EDIT_HANDLE_NOT_FOUND"
    run(tmp_path, {"deleted": [h]}, d)  # deleting it is still allowed


def test_api_degenerate_dimensions(client, world):
    bad = [
        {"type": "DIM_LINEAR", **DIMS["DIM_LINEAR"], "p2": [0, 0]},
        {"type": "DIM_ALIGNED", **DIMS["DIM_ALIGNED"], "distance": 0},
        {"type": "DIM_ANGULAR", **DIMS["DIM_ANGULAR"], "p2": [10, 0]},
        {"type": "DIM_RADIUS", **DIMS["DIM_RADIUS"], "radius": 0},
        {"type": "DIM_DIAMETER", **DIMS["DIM_DIAMETER"], "radius": -1},
    ]
    for geom in bad:
        r = client.post(
            f"/api/revisions/{'0' * 32}/edits",
            json={"created": [{"layer": "0", "geom": geom}]},
            headers=world.h["designer"],
        )
        assert r.status_code == 422 and r.json()["error"]["code"] == "EDIT_INVALID", geom
