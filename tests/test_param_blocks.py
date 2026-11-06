"""Track 9 Q4: parametric blocks (plan.md §15.5, TC-177)."""

import io
import json
import math

import ezdxf
import pytest

from core.dxf import param_blocks as pb


def by(name):
    return next(t for t in pb.BUILTIN if t["name"] == name)


@pytest.mark.parametrize("t", pb.BUILTIN, ids=lambda t: t["name"])
def test_builtins_validate_and_place_with_defaults(t):
    assert pb.validate(t)["name"] == t["name"]
    out = pb.instantiate(t, {})
    assert out["geoms"] and out["name"].startswith(f"PB-{t['name']}-")
    assert pb.parse_description(out["description"]) == {
        "template": t["name"],
        "values": out["values"],
        "state": out["state"],
    }


def test_hole_plate_follows_values():
    out = pb.instantiate(by("HOLE_PLATE"), {"W": 200, "H": 80, "D": 11, "E": 15})
    rect = out["geoms"][0]
    assert rect["points"][2][:2] == [200, 80]
    centres = sorted(tuple(g["center"]) for g in out["geoms"][1:])
    assert centres == [(15, 15), (15, 65), (185, 15), (185, 65)]
    assert all(g["radius"] == 5.5 for g in out["geoms"][1:])
    # the same values give the same block (reused), others a new one
    assert (
        pb.instantiate(by("HOLE_PLATE"), {"W": 200, "H": 80, "D": 11, "E": 15})["name"]
        == out["name"]
    )
    assert (
        pb.instantiate(by("HOLE_PLATE"), {"W": 201, "H": 80, "D": 11, "E": 15})["name"]
        != out["name"]
    )


def test_bolt_circle_repeat_and_states():
    out = pb.instantiate(by("BOLT_CIRCLE"), {"N": 6, "PCD": 100}, "관통")
    holes = [g for g in out["geoms"] if g["radius"] == 7]
    assert len(holes) == 6
    assert all(math.hypot(*g["center"]) == pytest.approx(50) for g in holes)
    assert holes[1]["center"] == pytest.approx([25, 50 * math.sin(math.pi / 3)])
    blind = pb.instantiate(by("BOLT_CIRCLE"), {"N": 6, "PCD": 100}, "막힘")
    assert len(blind["geoms"]) == len(out["geoms"]) - 1  # no bore
    assert blind["name"] != out["name"]
    door_l = pb.instantiate(by("DOOR"), {"W": 900}, "왼쪽")
    door_r = pb.instantiate(by("DOOR"), {"W": 900}, "오른쪽")
    assert door_l["geoms"][1]["center"] == [0, 0] and door_r["geoms"][1]["center"] == [900, 0]


def test_text_placeholders_and_zero_size_skipped():
    out = pb.instantiate(by("WINDOW"), {"W": 1500})
    assert out["geoms"][-1]["value"] == "W1500"
    no_bore = pb.instantiate(by("BOLT_CIRCLE"), {"ID": 0})
    assert all(g.get("radius") != 0 for g in no_bore["geoms"])


@pytest.mark.parametrize(
    ("values", "state", "msg"),
    [
        ({"W": 5}, None, "10~5000"),
        ({"W": float("nan")}, None, "10~5000"),
        ({"Q": 1}, None, "모르는"),
        ({}, "위쪽", "상태"),
    ],
)
def test_bad_values(values, state, msg):
    t = by("DOOR") if state else by("HOLE_PLATE")
    with pytest.raises(pb.ParamBlockError, match=msg):
        pb.instantiate(t, values, state)


@pytest.mark.parametrize(
    "bad",
    [
        {
            "name": "lower",
            "params": [pb._p("W", "w", 1, 0, 2)],
            "entities": [{"type": "CIRCLE", "cx": "0", "cy": "0", "r": "W"}],
        },
        {
            "name": "X",
            "params": [pb._p("W", "w", 5, 0, 2)],
            "entities": [{"type": "CIRCLE", "cx": "0", "cy": "0", "r": "W"}],
        },
        {
            "name": "X",
            "params": [pb._p("W", "w", 1, 0, 2)],
            "entities": [{"type": "CIRCLE", "cx": "__import__('os')", "cy": "0", "r": "W"}],
        },
        {
            "name": "X",
            "params": [pb._p("W", "w", 1, 0, 2)],
            "entities": [{"type": "CIRCLE", "cx": "W**99", "cy": "0", "r": "W"}],
        },
        {
            "name": "X",
            "params": [pb._p("W", "w", 1, 0, 2)],
            "entities": [{"type": "CIRCLE", "cx": "Z", "cy": "0", "r": "W"}],
        },
        {"name": "X", "params": [pb._p("W", "w", 1, 0, 2)], "entities": [{"type": "SPLINE"}]},
        {
            "name": "X",
            "params": [pb._p("W", "w", 1, 0, 2)],
            "entities": [{"type": "CIRCLE", "cx": 0, "cy": "0", "r": "W"}],
        },
        {
            "name": "X",
            "params": [pb._p("px", "w", 1, 0, 2)],
            "entities": [{"type": "CIRCLE", "cx": "0", "cy": "0", "r": "px"}],
        },
        {
            "name": "X",
            "params": [pb._p("W", "w", 1, 0, 2)],
            "entities": [{"type": "CIRCLE", "cx": "0", "cy": "0", "r": "W", "when": ["A"]}],
        },
    ],
)
def test_bad_templates(bad):
    with pytest.raises(pb.ParamBlockError):
        pb.validate(bad)


def test_runtime_limits():
    t = pb.validate(
        {
            "name": "MANY",
            "params": [pb._p("N", "n", 1000, 1, 5000)],
            "entities": [
                {
                    "type": "CIRCLE",
                    "cx": "px",
                    "cy": "py",
                    "r": "1",
                    "repeat": {
                        "count": "N",
                        "polar": {"cx": "0", "cy": "0", "r": "10", "start": "0"},
                    },
                }
            ],
        }
    )
    with pytest.raises(pb.ParamBlockError, match="반복"):
        pb.instantiate(t, {})
    zero = pb.validate(
        {
            "name": "ZERO",
            "params": [pb._p("W", "w", 0, 0, 1)],
            "entities": [{"type": "CIRCLE", "cx": "1/W", "cy": "0", "r": "1"}],
        }
    )
    with pytest.raises(pb.ParamBlockError, match="계산"):
        pb.instantiate(zero, {})


def test_description_survives_a_dxf_round_trip():
    out = pb.instantiate(by("SLOT"), {"L": 40, "W": 12})
    doc = ezdxf.new()
    blk = doc.blocks.new(out["name"])
    blk.block.dxf.description = out["description"]
    blk.add_circle((0, 0), 1)
    buf = io.StringIO()
    doc.write(buf)
    back = ezdxf.read(io.StringIO(buf.getvalue()))
    got = pb.parse_description(back.blocks.get(out["name"]).block.dxf.description)
    assert got == {"template": "SLOT", "values": {"L": "40", "W": "12"}, "state": None}
    assert (
        pb.parse_description("{}") is None
        and pb.parse_description(json.dumps({"axcad_pb": 1, "template": "x", "values": {}})) is None
    )


def test_placed_block_round_trips_through_the_editor(tmp_path):
    """The editor's save (apply_edits) writes the description; reading lists it as `param`."""
    from core.dxf.reader import parse_dxf
    from core.dxf.writer import apply_edits

    out = pb.instantiate(by("HOLE_PLATE"), {"W": 120})
    src, dst = tmp_path / "s.dxf", tmp_path / "d.dxf"
    d = ezdxf.new(setup=True)
    d.units = 4
    d.saveas(src)
    edits = {
        "blocks": [
            {
                "name": out["name"],
                "base": [0, 0],
                "entities": [{"layer": "0", "geom": g} for g in out["geoms"]],
                "description": out["description"],
            }
        ],
        "created": [
            {
                "layer": "0",
                "geom": {
                    "type": "INSERT",
                    "name": out["name"],
                    "insert": [10, 20],
                    "xscale": 1,
                    "yscale": 1,
                    "rotation": 0,
                },
            }
        ],
    }
    apply_edits(str(src), str(dst), edits)
    blocks = {b["name"]: b for b in parse_dxf(str(dst))["blocks"]}
    assert blocks[out["name"]]["param"] == {
        "template": "HOLE_PLATE",
        "values": out["values"],
        "state": None,
    }


def test_api(client, world):
    h = world.h["designer"]
    items = client.get("/api/param-blocks", headers=h).json()["data"]["items"]
    assert {t["name"] for t in items} >= {
        "HOLE_PLATE",
        "SLOT",
        "BOLT_CIRCLE",
        "H_BEAM",
        "DOOR",
        "WINDOW",
    }
    assert all(isinstance(t["states"], list) for t in items)  # the dialog reads it on every one
    r = client.post("/api/param-blocks/SLOT/instantiate", json={"values": {"L": 50}}, headers=h)
    assert r.status_code == 200 and r.json()["data"]["values"]["L"] == "50"
    r = client.post("/api/param-blocks/SLOT/instantiate", json={"values": {"L": 1e9}}, headers=h)
    assert r.status_code == 422
    assert client.post("/api/param-blocks/NOPE/instantiate", json={}, headers=h).status_code == 404
    mine = {
        "name": "BRACKET_PLATE",
        "label": "회사 브래킷",
        "params": [pb._p("W", "폭", 80, 10, 500)],
        "entities": [{"type": "RECT", "x": "0", "y": "0", "w": "W", "h": "W/2"}],
    }
    assert client.put("/api/param-blocks", json={"templates": [mine]}, headers=h).status_code == 403
    admin = world.h["admin"]
    r = client.put("/api/param-blocks", json={"templates": [mine]}, headers=admin)
    assert r.status_code == 200
    r = client.post(
        "/api/param-blocks/BRACKET_PLATE/instantiate", json={"values": {"W": 100}}, headers=h
    )
    assert r.json()["data"]["geoms"][0]["points"][2][:2] == [100, 50]
    for bad in (
        {**mine, "name": "SLOT"},
        {**mine, "entities": [{"type": "RECT", "x": "open('x')", "y": "0", "w": "1", "h": "1"}]},
    ):
        r = client.put("/api/param-blocks", json={"templates": [bad]}, headers=admin)
        assert r.status_code == 422
