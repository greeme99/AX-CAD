"""Track 6-2 construction quantity take-off: rooms, walls, counts, demolition/new/keep split."""

import ezdxf
import pytest

from core.quote_engine.qto import DEFAULT_LAYERS, compute_qto, layers_from_mapping


def plan(path, walls, doors=1, extra_room=False):
    """A 5 m x 4 m room (mm units) named 거실, the given wall lines, `doors` door blocks and two
    light fixtures."""
    doc = ezdxf.new()
    doc.units = 4
    msp = doc.modelspace()
    door = doc.blocks.new("DOOR900")
    door.add_line((0, 0), (900, 0))
    msp.add_lwpolyline(
        [(0, 0), (5000, 0), (5000, 4000), (0, 4000)], close=True, dxfattribs={"layer": "ROOM"}
    )
    msp.add_text("거실", dxfattribs={"layer": "TEXT", "insert": (2000, 2000), "height": 200})
    if extra_room:  # a second room chained from loose lines
        for a, b in (
            ((5000, 0), (8000, 0)),
            ((8000, 0), (8000, 4000)),
            ((8000, 4000), (5000, 4000)),
            ((5000, 4000), (5000, 0)),
        ):
            msp.add_line(a, b, dxfattribs={"layer": "실-침실"})
    for a, b in walls:
        msp.add_line(a, b, dxfattribs={"layer": "A-WALL"})
    for i in range(doors):
        msp.add_blockref("DOOR900", (1000 + i * 1500, 0), dxfattribs={"layer": "DOOR"})
    for x in (1000, 3000):
        msp.add_blockref("DOOR900", (x, 2000), dxfattribs={"layer": "E-LITE"})
    doc.saveas(path)
    return str(path)


W1, W2, W3 = ((0, 0), (5000, 0)), ((5000, 0), (5000, 4000)), ((0, 4000), (5000, 4000))


def test_measures_rooms_walls_and_counts(tmp_path):
    q = compute_qto(plan(tmp_path / "a.dxf", [W1, W2], doors=2, extra_room=True))
    m = q["measures"]
    assert m["room_count"] == 2
    assert m["room_area_m2"] == pytest.approx(20 + 12)
    assert m["room_perimeter_m"] == pytest.approx(18 + 14)
    assert m["wall_length_m"] == pytest.approx(9)
    assert m["door_count"] == 2 and m["fixture_count"] == 2
    assert m["new_wall_length_m"] == pytest.approx(9) and m["demo_wall_length_m"] == 0
    names = {r["name"] for r in q["rooms"]}
    assert "거실" in names
    chained = next(r for r in q["rooms"] if r["name"] != "거실")
    assert len(chained["handles"]) == 4  # every edge of a room drawn as loose lines
    assert all(it["handles"] for it in q["items"]) and q["warnings"] == []


def test_base_revision_splits_demolition_new_and_keep(tmp_path):
    base = plan(tmp_path / "base.dxf", [W1, W2])
    cur = plan(tmp_path / "cur.dxf", [W1, W3], doors=2)
    m = compute_qto(cur, base_path=base)["measures"]
    assert m["keep_wall_length_m"] == pytest.approx(5)  # W1 stays
    assert m["new_wall_length_m"] == pytest.approx(5)  # W3 built
    assert m["demo_wall_length_m"] == pytest.approx(4)  # W2 torn down
    assert m["wall_length_m"] == pytest.approx(10)  # the current drawing: new + keep
    assert m["keep_door_count"] == 1 and m["new_door_count"] == 1
    assert m["keep_room_area_m2"] == pytest.approx(20) and m["new_room_area_m2"] == 0


def test_layer_mapping_and_nothing_mapped(tmp_path):
    rows = [{"rule_type": "QTO_LAYER", "target": "WALL", "pattern": "W-*, 벽*"}]
    layers = layers_from_mapping(rows)
    assert layers["WALL"] == ["W-*", "벽*"] and layers["DOOR"] == DEFAULT_LAYERS["DOOR"]
    q = compute_qto(plan(tmp_path / "a.dxf", [W1]), layers)
    assert q["measures"]["wall_length_m"] == 0  # A-WALL is no longer a wall layer
    doc = ezdxf.new()
    doc.modelspace().add_line((0, 0), (1, 0), dxfattribs={"layer": "XYZ"})
    doc.saveas(tmp_path / "x.dxf")
    assert "QTO_NOTHING_MAPPED" in compute_qto(str(tmp_path / "x.dxf"))["warnings"]
