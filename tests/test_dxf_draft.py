"""Track 8 P1-3e: points, ellipses, fit-point splines and leaders round-trip through edits;
P3-4 (C-1): a multileader keeps its leader and text as one object."""

import math

import ezdxf
import pytest
from ezdxf.math import Vec2
from ezdxf.render import mleader

from core.dxf.reader import DxfError, parse_dxf
from core.dxf.writer import apply_edits

GEOMS = [
    {"type": "POINT", "point": [3, 4]},
    {
        "type": "ELLIPSE",
        "center": [10, 0],
        "major": [8, 6],
        "ratio": 0.5,
        "start_param": 0,
        "end_param": round(math.tau, 6),
    },
    {"type": "SPLINE", "fit": [[0, 0], [10, 5], [20, 0], [30, 8]]},
    {"type": "LEADER", "vertices": [[0, 0], [10, 10], [20, 10]]},
    {
        "type": "MULTILEADER",
        "vertices": [[0, 0], [10, 10], [20, 10]],
        "value": "4×M6",
        "height": 2.5,
    },
]


def run(tmp_path, edits, doc=None):
    doc = doc or ezdxf.new(setup=True)
    doc.units = ezdxf.units.MM
    src, dst = str(tmp_path / "s.dxf"), str(tmp_path / "d.dxf")
    doc.saveas(src)
    apply_edits(src, dst, edits)
    return dst


def editable(p, kind):
    return [e for e in p["entities"] if e.get("geom", {}).get("type") == kind]


@pytest.mark.parametrize("geom", GEOMS, ids=[g["type"] for g in GEOMS])
def test_create_read_modify(tmp_path, geom):
    p = parse_dxf(run(tmp_path, {"created": [{"layer": "0", "geom": geom}]}))
    (rec,) = editable(p, geom["type"])
    assert rec["geom"] == geom
    assert rec.get("paths") or rec.get("point") or rec.get("text")  # drawn by the server
    moved = {
        k: ([[x + 5, y] for x, y in v] if k in ("fit", "vertices") else v) for k, v in geom.items()
    }
    for k in ("point", "center"):
        if k in moved:
            moved[k] = [moved[k][0] + 5, moved[k][1]]
    doc = ezdxf.readfile(tmp_path / "d.dxf")
    p2 = parse_dxf(
        run(tmp_path, {"modified": [{"handle": rec["handle"], "layer": "0", "geom": moved}]}, doc)
    )
    (rec2,) = editable(p2, geom["type"])
    assert rec2["handle"] == rec["handle"] and rec2["geom"] == moved


def test_spline_passes_through_its_fit_points(tmp_path):
    fit = GEOMS[2]["fit"]
    p = parse_dxf(run(tmp_path, {"created": [{"layer": "0", "geom": GEOMS[2]}]}))
    (line,) = editable(p, "SPLINE")[0]["paths"]
    for q in fit:  # the drawn curve meets every fit point
        assert min(math.dist(q, v) for v in line) < 0.05


def test_multileader_text_follows_its_last_leg(tmp_path):
    left = {"type": "MULTILEADER", "vertices": [[0, 0], [-10, 10]], "value": "C1", "height": 5}
    p = parse_dxf(
        run(tmp_path, {"created": [{"layer": "0", "geom": GEOMS[4]}, {"layer": "0", "geom": left}]})
    )
    texts = [e for e in p["entities"] if e["type"] == "MTEXT"]
    assert len(texts) == 2 and len({e["handle"] for e in p["entities"]}) == 2  # one object each
    right, left = sorted((e["text"] for e in texts), key=lambda t: t["value"] != "4×M6")
    assert right["insert"][0] > 20 and left["insert"][0] < -10  # beside the landing


def test_multileader_landing_and_old_versions(tmp_path):
    doc = ezdxf.new(setup=True)
    b = doc.modelspace().add_multileader_mtext("Standard")  # the drawing generator's callouts
    b.set_content("2×Ø9", char_height=3.5)
    b.add_leader_line(mleader.ConnectionSide.left, [Vec2(0, 0)])
    b.build(insert=Vec2(20, 10))
    (rec,) = editable(parse_dxf(run(tmp_path, {"layers": [{"name": "0"}]}, doc)), "MULTILEADER")
    v = rec["geom"]["vertices"]
    assert len(v) == 3 and v[1][1] == v[2][1] and v[2][0] - v[1][0] == pytest.approx(8)  # dogleg
    with pytest.raises(DxfError):
        run(tmp_path, {"created": [{"layer": "0", "geom": GEOMS[4]}]}, ezdxf.new("R12"))


def test_api_rejects_degenerate_shapes(client, world):
    bad = [
        {**GEOMS[1], "ratio": 1.5},
        {**GEOMS[1], "major": [0, 0]},
        {"type": "SPLINE", "fit": [[0, 0], [0, 0], [1, 1]]},
        {"type": "LEADER", "vertices": [[0, 0]]},
        {**GEOMS[4], "value": ""},
    ]
    for geom in bad:
        r = client.post(
            f"/api/revisions/{'0' * 32}/edits",
            json={"created": [{"layer": "0", "geom": geom}]},
            headers=world.h["designer"],
        )
        assert r.status_code == 422 and r.json()["error"]["code"] == "EDIT_INVALID", geom
