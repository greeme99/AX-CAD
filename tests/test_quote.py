"""S8 FN-14 2D quote metrics on synthetic drawings (TC-61..64). Sample-drawing regression (TC-60)
is added once the G1 expected-metrics sheet arrives."""

import math

import ezdxf
import pytest

from core.quote_engine.metrics2d import Rules, compute_metrics

PI = math.pi


def save(doc, tmp_path, name="d.dxf"):
    path = tmp_path / name
    doc.saveas(path)
    return str(path)


def new():
    doc = ezdxf.new(setup=True)
    doc.units = 4  # ezdxf.new() defaults to metres
    return doc


def plate(w=100, h=50):
    doc = new()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (w, 0), (w, h), (0, h)], close=True)
    return doc, msp


def test_rectangle_holes_bend_and_trace(tmp_path):
    doc, msp = plate()
    doc.layers.add("BEND")
    msp.add_circle((20, 25), 5)
    msp.add_circle((80, 25), 5)
    bend = msp.add_line((50, 0), (50, 50), dxfattribs={"layer": "BEND"})
    m = compute_metrics(save(doc, tmp_path))
    assert m["cutting_length_mm"] == pytest.approx(300 + 2 * 10 * PI, abs=0.01)
    assert m["hole_count"] == 2 and m["holes_by_dia"] == {"10": 2}
    assert m["bend_count"] == 1 and m["bend_length_mm"] == pytest.approx(50)
    assert m["net_area_mm2"] == pytest.approx(5000 - 2 * 25 * PI, abs=0.01)
    assert m["bbox"]["size"] == pytest.approx([100, 50])
    assert m["warnings"] == []
    bends = [i for i in m["items"] if i["role"] == "BEND"]
    assert bends == [
        {"handle": bend.dxf.handle, "layer": "BEND", "role": "BEND", "length_mm": 50.0}
    ]


def test_lines_and_arc_chain_into_one_outline(tmp_path):
    doc = new()
    msp = doc.modelspace()
    msp.add_line((40, 20), (0, 20))  # deliberately unordered and reversed
    msp.add_line((0, 0), (40, 0))
    msp.add_arc((40, 10), 10, -90, 90)
    msp.add_line((0, 0), (0, 20))
    m = compute_metrics(save(doc, tmp_path))
    assert m["cutting_length_mm"] == pytest.approx(100 + 10 * PI, abs=0.01)
    assert m["net_area_mm2"] == pytest.approx(800 + 50 * PI, abs=1e-3)
    assert m["warnings"] == []


def test_bulge_polyline_and_disc_part(tmp_path):
    doc = new()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0, 1), (20, 0, 1)], format="xyb", close=True)  # two half circles: r10
    msp.add_circle((10, 0), 2)
    m = compute_metrics(save(doc, tmp_path))
    assert m["cutting_length_mm"] == pytest.approx(20 * PI + 4 * PI, abs=0.01)
    assert m["net_area_mm2"] == pytest.approx(100 * PI - 4 * PI, abs=1e-3)
    assert m["hole_count"] == 1
    # a disc whose outline is a CIRCLE is not a hole of itself
    doc = new()
    doc.modelspace().add_circle((0, 0), 30)
    m = compute_metrics(save(doc, tmp_path, "disc.dxf"))
    assert m["hole_count"] == 0 and m["net_area_mm2"] == pytest.approx(900 * PI, abs=0.01)


def test_tc61_insert_and_minsert_expansion(tmp_path):
    doc, msp = plate(400, 200)
    blk = doc.blocks.new("HOLES6")
    for i in range(6):
        blk.add_circle((i * 8, 0), 2)
    ins = [msp.add_blockref("HOLES6", (20 + 100 * k, 50)) for k in range(4)]
    m = compute_metrics(save(doc, tmp_path))
    assert m["hole_count"] == 24  # 4 INSERT x 6 holes (Research D4)
    assert {i["handle"] for i in m["items"] if i["role"] == "HOLE"} == {i.dxf.handle for i in ins}
    # MINSERT 2 x 2 array of the same block: 24 holes, BUG-05
    doc, msp = plate(400, 200)
    blk = doc.blocks.new("HOLES6")
    for i in range(6):
        blk.add_circle((i * 8, 0), 2)
    msp.add_blockref("HOLES6", (20, 50)).grid(size=(2, 2), spacing=(60, 150))
    m = compute_metrics(save(doc, tmp_path, "minsert.dxf"))
    assert m["hole_count"] == 24


def test_tc62_duplicate_lines_counted_once(tmp_path):
    doc, msp = plate()
    msp.add_line((100, 0), (0, 0))  # same as the polyline's bottom edge, reversed
    msp.add_circle((50, 25), 5)
    msp.add_circle((50, 25), 5)
    m = compute_metrics(save(doc, tmp_path))
    assert m["cutting_length_mm"] == pytest.approx(300 + 10 * PI, abs=0.01)
    assert m["hole_count"] == 1
    assert "METRIC_DUPLICATE_GEOMETRY:1" in m["warnings"] or any(
        w.startswith("METRIC_DUPLICATE_GEOMETRY") for w in m["warnings"]
    )


def test_tc63_no_outer_contour(tmp_path):
    doc = new()
    doc.modelspace().add_line((0, 0), (10, 0))
    doc.modelspace().add_line((10, 0), (10, 10))
    m = compute_metrics(save(doc, tmp_path))
    assert "METRIC_NO_OUTER_CONTOUR" in m["warnings"] and "METRIC_OPEN_CONTOUR:1" in m["warnings"]
    assert m["net_area_mm2"] is None and m["bbox"] is None
    assert m["cutting_length_mm"] == pytest.approx(20)


def test_ignore_layers_punch_threshold_and_units(tmp_path):
    doc, msp = plate()
    doc.layers.add("DIM")
    msp.add_line((0, -10), (100, -10), dxfattribs={"layer": "DIM"})
    msp.add_text("NOTE", dxfattribs={"layer": "0"})
    msp.add_circle((20, 25), 3)  # dia 6 -> punched
    msp.add_circle((80, 25), 10)  # dia 20 -> laser
    m = compute_metrics(save(doc, tmp_path), Rules(punch_max_dia_mm=6))
    assert m["punch_hole_count"] == 1 and m["hole_count"] == 2
    assert m["cutting_length_mm"] == pytest.approx(300 + 20 * PI, abs=0.01)
    assert {i["role"] for i in m["items"]} == {"CUT", "PUNCH", "HOLE"}
    # inch drawing is converted to mm
    doc = ezdxf.new()
    doc.units = 1
    doc.modelspace().add_lwpolyline([(0, 0), (1, 0), (1, 1), (0, 1)], close=True)
    m = compute_metrics(save(doc, tmp_path, "inch.dxf"))
    assert m["cutting_length_mm"] == pytest.approx(4 * 25.4)


def test_tc64_title_block_from_attribs_and_text(tmp_path):
    doc, msp = plate()
    tb = doc.blocks.new("TITLE")
    for tag in ("PART_NO", "MATERIAL", "THICKNESS"):
        tb.add_attdef(tag, (0, 0))
    ref = msp.add_blockref("TITLE", (200, 0), dxfattribs={"layer": "TITLE"})
    ref.add_auto_attribs({"PART_NO": "AX-1001-01", "MATERIAL": "SS400", "THICKNESS": "3.2"})
    ps = doc.paperspace()
    ps.add_text("품명", height=3, dxfattribs={"insert": (10, 10)})
    ps.add_text("브래킷", height=3, dxfattribs={"insert": (40, 10.5)})
    ps.add_text("수량: 4EA", height=3, dxfattribs={"insert": (10, 0)})
    m = compute_metrics(save(doc, tmp_path))
    t = m["title_block"]
    assert t["part_no"] == "AX-1001-01" and t["material"] == "SS400"
    assert t["thickness_mm"] == 3.2 and t["part_name"] == "브래킷" and t["qty"] == 4
    assert t["missing"] == [] and m["status"] == "OK"
    assert m["cutting_length_mm"] == pytest.approx(300)  # title block geometry is not cut

    doc, _ = plate()
    m = compute_metrics(save(doc, tmp_path, "bare.dxf"))
    assert m["status"] == "INPUT_REQUIRED" and "material" in m["title_block"]["missing"]


def test_rules_from_master_mapping(tmp_path):
    from core.quote_engine.metrics2d import rules_from_mapping

    r = rules_from_mapping(
        [
            {"rule_type": "LAYER", "target": "CUT", "pattern": "OUTLINE"},
            {"rule_type": "LAYER", "target": "BEND", "pattern": "FOLD"},
            {"rule_type": "TITLE_TAG", "target": "material", "pattern": "MAT_CODE"},
        ]
    )
    assert r.cut_layers == ["OUTLINE"] and r.bend_layers == ["FOLD"]
    assert r.ignore_layers == Rules().ignore_layers  # no IGNORE rows: the default stays
    assert (
        r.title_tags["material"] == ["MAT_CODE"] and r.ignore_linetypes == Rules().ignore_linetypes
    )
    doc = new()
    for name in ("OUTLINE", "SKETCH"):
        doc.layers.add(name)
    msp = doc.modelspace()
    msp.add_lwpolyline(
        [(0, 0), (10, 0), (10, 10), (0, 10)], close=True, dxfattribs={"layer": "OUTLINE"}
    )
    msp.add_line((0, 20), (50, 20), dxfattribs={"layer": "SKETCH"})  # not a cut layer: ignored
    m = compute_metrics(save(doc, tmp_path), r)
    assert m["cutting_length_mm"] == pytest.approx(40) and m["warnings"] == []


def test_bomb_and_garbage_inputs_are_bounded(tmp_path):
    from core.dxf.reader import DxfError, parse_dxf

    # empty block in a huge MINSERT grid: rejected before any cell is generated
    doc, msp = plate()
    doc.blocks.new("EMPTY")
    msp.add_blockref("EMPTY", (0, 0)).grid(size=(30000, 30000), spacing=(1, 1))
    path = save(doc, tmp_path, "bomb.dxf")
    for fn in (compute_metrics, parse_dxf):
        with pytest.raises(DxfError) as e:
            fn(path)
        assert e.value.code == "DXF_BLOCK_LIMIT"

    # absurd layer names are refused (every record repeats the name)
    doc, msp = plate()
    doc.layers.add("L" * 300)
    msp.add_line((0, 0), (1, 1), dxfattribs={"layer": "L" * 300})
    path = save(doc, tmp_path, "layer.dxf")
    for fn in (compute_metrics, parse_dxf):
        with pytest.raises(DxfError) as e:
            fn(path)
        assert e.value.code == "DXF_INVALID_FILE"

    # title numbers must be finite and plausible; a huge circle keeps a bounded outline
    doc, msp = plate()
    ps = doc.paperspace()
    ps.add_text("두께: nan", height=3, dxfattribs={"insert": (0, 0)})
    ps.add_text("수량: " + "9" * 30, height=3, dxfattribs={"insert": (0, 10)})
    ps.add_text("품명: " + "x" * 5000, height=3, dxfattribs={"insert": (0, 20)})
    msp.add_circle((0, 0), 1e7)
    m = compute_metrics(save(doc, tmp_path, "title.dxf"))
    t = m["title_block"]
    assert t["thickness_mm"] is None and t["qty"] is None and len(t["part_name"]) == 200
    assert m["net_area_mm2"] == pytest.approx(PI * 1e14 - 5000, rel=1e-9)
