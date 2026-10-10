"""FN-23 BOM core: qty = expanded INSERT/MINSERT instances (TC-90), ATTRIB part numbers."""

import ezdxf
import pytest

from core.bom.dxf import bom_from_bodies, bom_from_dxf
from core.dxf.reader import DxfError


def assembly(tmp_path):
    doc = ezdxf.new(setup=True)
    bolt = doc.blocks.new("BOLT")
    bolt.add_circle((0, 0), 3)
    bolt.add_attdef("PART_NO", (0, 0))
    bracket = doc.blocks.new("BRACKET")
    bracket.add_lwpolyline([(0, 0), (40, 0), (40, 20), (0, 20)], close=True)
    for x in (5, 15, 25, 35):
        bracket.add_blockref("BOLT", (x, 10)).add_attrib("PART_NO", "B-M6")
    doc.blocks.new("NUT").add_circle((0, 0), 2)
    msp = doc.modelspace()
    msp.add_blockref("BRACKET", (0, 0)).add_attrib("품번", "BR-100")
    msp.add_blockref("BRACKET", (100, 0))
    grid = msp.add_blockref("BRACKET", (0, 100))  # MINSERT 2 x 3
    grid.grid(size=(2, 3), spacing=(50, 60))
    for x in (300, 320):
        msp.add_blockref("BOLT", (x, 0)).add_attrib("PART_NO", "B-M6")
    msp.add_blockref("NUT", (400, 0))
    msp.add_linear_dim(base=(0, -20), p1=(0, 0), p2=(40, 0)).render()  # anonymous *D block
    path = tmp_path / "asm.dxf"
    doc.saveas(path)
    return str(path)


def test_tc90_qty_is_expanded_instances(tmp_path):
    bom = bom_from_dxf(assembly(tmp_path))
    by = {it["source_name"]: it for it in bom["items"]}
    assert set(by) == {"BRACKET", "BOLT", "NUT"}
    assert by["BRACKET"]["qty"] == 8  # 2 + MINSERT 2x3
    assert by["BOLT"]["qty"] == 2 + 4 * 8  # direct + nested
    assert (by["BRACKET"]["level"], by["BOLT"]["level"]) == (1, 1)
    assert by["BRACKET"]["part_no"] == "BR-100" and by["BRACKET"]["mapping_status"] == "AUTO"
    assert by["BOLT"]["part_no"] == "B-M6"
    assert by["NUT"]["part_no"] is None and by["NUT"]["mapping_status"] == "UNMAPPED"
    assert by["NUT"]["part_name"] == "NUT"
    assert bom["warnings"] == []  # the dimension's *D block is not a part
    assert len(by["BRACKET"]["source_refs"]) == 3  # top-level placements (handles)


def test_nested_only_block_is_level_2(tmp_path):
    doc = ezdxf.new()
    doc.blocks.new("PIN").add_point((0, 0))
    doc.blocks.new("HINGE").add_blockref("PIN", (0, 0))
    doc.modelspace().add_blockref("HINGE", (0, 0))
    doc.saveas(tmp_path / "h.dxf")
    by = {it["source_name"]: it for it in bom_from_dxf(str(tmp_path / "h.dxf"))["items"]}
    assert (by["HINGE"]["level"], by["PIN"]["level"], by["PIN"]["qty"]) == (1, 2, 1)


def test_block_bomb_is_refused_without_expanding(tmp_path):
    doc = ezdxf.new()
    doc.blocks.new("L0").add_point((0, 0))
    for i in range(1, 5):  # 1000^4 instances if expanded
        blk = doc.blocks.new(f"L{i}")
        ins = blk.add_blockref(f"L{i - 1}", (0, 0))
        ins.grid(size=(10, 100), spacing=(1, 1))
    doc.modelspace().add_blockref("L4", (0, 0))
    doc.saveas(tmp_path / "bomb.dxf")
    with pytest.raises(DxfError) as e:
        bom_from_dxf(str(tmp_path / "bomb.dxf"))
    assert e.value.code == "DXF_BLOCK_LIMIT"


def test_bom_from_3d_bodies():
    bom = bom_from_bodies(
        [
            {"feature_id": 1, "name": "Extrude 1", "parts": None},
            {
                "feature_id": 2,
                "name": "asm.step",
                "parts": [
                    {"name": "Bolt", "instance_count": 4},
                    {"name": "Plate", "instance_count": 1},
                ],
            },
            {"feature_id": 3, "name": "b.step", "parts": [{"name": "Bolt", "instance_count": 2}]},
        ]
    )
    by = {it["source_name"]: (it["qty"], it["source_refs"]) for it in bom["items"]}
    assert by == {"Extrude 1": (1, ["1"]), "Bolt": (6, ["2", "3"]), "Plate": (1, ["2"])}
    assert bom["source_type"] == "STEP_ASSEMBLY"
