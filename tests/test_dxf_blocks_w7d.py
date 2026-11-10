"""W7d (plan §22.8): block redefine / rename / delete, attribute definitions and values, layouts
and groups through the edit API."""

import ezdxf
import pydantic
import pytest

from core.dxf.reader import DxfError, parse_dxf
from core.dxf.writer import apply_edits

LINE = {"type": "LINE", "start": [0, 0], "end": [5, 0]}
CIRCLE = {"type": "CIRCLE", "center": [0, 0], "radius": 3}
ATT = {"tag": "PART", "prompt": "Part no.", "default": "P-000", "insert": [0, -5], "height": 2.5}


def ins(name, x=0, y=0, **extra):
    g = {"type": "INSERT", "name": name, "insert": [x, y], "xscale": 1, "yscale": 1, "rotation": 0}
    return {"layer": "0", "geom": g, **extra}


@pytest.fixture
def src(tmp_path):
    d = ezdxf.new("R2018", setup=True)
    d.units = ezdxf.units.MM
    d.layers.add("LOCK").lock()
    b = d.blocks.new("B")
    b.add_line((0, 0), (1, 0))
    b.add_attdef("OLD", (0, 2), "old-default", height=2)
    b.add_attdef("KEEP", (0, 4), "keep-default", height=2)
    outer = d.blocks.new("OUTER")
    outer.add_blockref("B", (0, 0))
    msp = d.modelspace()
    r = msp.add_blockref("B", (100, 0))
    r.add_auto_attribs({"OLD": "o1", "KEEP": "k1"})
    msp.add_blockref("OUTER", (200, 0))
    msp.add_line((0, 0), (1, 1))
    msp.add_line((0, 0), (1, 1), dxfattribs={"layer": "LOCK"})
    path = str(tmp_path / "s.dxf")
    d.saveas(path)
    return path


def run(src, tmp_path, edits):
    dst = str(tmp_path / "d.dxf")
    apply_edits(src, dst, edits)
    return dst


def refs(doc, name):
    return [e for e in doc.modelspace().query("INSERT") if e.dxf.name == name]


def test_create_with_attdefs_and_values(src, tmp_path):
    dst = run(
        src,
        tmp_path,
        {
            "blocks": [
                {
                    "name": "TAG",
                    "base": [10, 10],
                    "entities": [{"layer": "0", "geom": CIRCLE}],
                    "attdefs": [ATT],
                }
            ],
            "created": [ins("TAG", 50, 50, attributes={"PART": "P-123"})],
        },
    )
    d = ezdxf.readfile(dst)
    a = next(d.blocks.get("TAG").attdefs())
    assert (a.dxf.tag, a.dxf.prompt, a.dxf.text) == ("PART", "Part no.", "P-000")
    assert tuple(a.dxf.insert)[:2] == (-10, -15)  # block coordinates from the base point
    (r,) = refs(d, "TAG")
    assert r.get_attrib_text("PART") == "P-123"
    p = parse_dxf(dst)
    rec = next(e for e in p["entities"] if e["handle"] == r.dxf.handle and "geom" in e)
    assert rec["att"] == {"PART": "P-123"}
    cat = next(c for c in p["blocks"] if c["name"] == "TAG")
    assert cat["attdefs"] == [{"tag": "PART", "prompt": "Part no.", "default": "P-000"}]


def test_modify_attribute_values(src, tmp_path):
    h = refs(ezdxf.readfile(src), "B")[0].dxf.handle
    m = ins("B", 100, 0, attributes={"KEEP": "k2"})
    dst = run(src, tmp_path, {"modified": [{**m, "handle": h}]})
    assert refs(ezdxf.readfile(dst), "B")[0].get_attrib_text("KEEP") == "k2"
    with pytest.raises(DxfError) as ex:
        run(
            src,
            tmp_path,
            {"modified": [{**ins("B", 100, 0, attributes={"NOPE": "x"}), "handle": h}]},
        )
    assert ex.value.code == "EDIT_ATTRIB_NOT_FOUND"


def test_redefine_syncs_references(src, tmp_path):
    new_att = {**ATT, "tag": "NEW", "default": "n-default"}
    keep = {**ATT, "tag": "KEEP", "default": "changed-default"}
    dst = run(
        src,
        tmp_path,
        {
            "blocks": [
                {
                    "op": "redefine",
                    "name": "B",
                    "base": [0, 0],
                    "entities": [{"layer": "0", "geom": CIRCLE}],
                    "attdefs": [keep, new_att],
                }
            ]
        },
    )
    d = ezdxf.readfile(dst)
    assert [e.dxftype() for e in d.blocks.get("B") if e.dxftype() != "ATTDEF"] == ["CIRCLE"]
    (r,) = refs(d, "B")
    tags = {a.dxf.tag: a.dxf.text for a in r.attribs}
    assert tags == {"KEEP": "k1", "NEW": "n-default"}  # value kept, new default added, OLD gone
    assert d.audit().has_errors is False


def test_redefine_refuses_recursion(src, tmp_path):
    for name, inner in (("B", "B"), ("B", "OUTER")):  # direct, and through a block that holds it
        with pytest.raises(DxfError) as ex:
            run(
                src,
                tmp_path,
                {
                    "blocks": [
                        {"op": "redefine", "name": name, "base": [0, 0], "entities": [ins(inner)]}
                    ]
                },
            )
        assert ex.value.code == "EDIT_BLOCK_INVALID"
    with pytest.raises(DxfError) as ex:
        run(
            src,
            tmp_path,
            {
                "blocks": [
                    {
                        "op": "redefine",
                        "name": "NOPE",
                        "base": [0, 0],
                        "entities": [{"layer": "0", "geom": LINE}],
                    }
                ]
            },
        )
    assert ex.value.code == "EDIT_BLOCK_NOT_FOUND"


def test_rename_and_delete(src, tmp_path):
    dst = run(src, tmp_path, {"blocks": [{"op": "rename", "name": "B", "new_name": "B2"}]})
    d = ezdxf.readfile(dst)
    assert "B" not in d.blocks and refs(d, "B2")
    assert d.blocks.get("OUTER").query("INSERT")[0].dxf.name == "B2"  # nested reference too
    for b, code in (
        ({"op": "delete", "name": "B"}, "EDIT_BLOCK_IN_USE"),
        ({"op": "delete", "name": "*Model_Space"}, "EDIT_BLOCK_NOT_FOUND"),
        ({"op": "rename", "name": "B", "new_name": "OUTER"}, "EDIT_BLOCK_EXISTS"),
    ):
        with pytest.raises(DxfError) as ex:
            run(src, tmp_path, {"blocks": [b]})
        assert ex.value.code == code, b
    # deleting the references in the same save frees the block
    d0 = ezdxf.readfile(src)
    gone = [r.dxf.handle for r in d0.modelspace().query("INSERT")]
    dst = run(
        src,
        tmp_path,
        {
            "deleted": gone,
            "blocks": [{"op": "delete", "name": "OUTER"}, {"op": "delete", "name": "B"}],
        },
    )
    d = ezdxf.readfile(dst)
    assert "B" not in d.blocks and "OUTER" not in d.blocks and d.audit().has_errors is False


def test_layouts(src, tmp_path):
    dst = run(
        src,
        tmp_path,
        {
            "layouts": [
                {"op": "create", "name": "Sheet 2"},
                {"op": "rename", "name": "Layout1", "new_name": "Sheet 1"},
            ],
            "created": [{"layer": "0", "geom": LINE, "space": "Sheet 2"}],
        },
    )
    d = ezdxf.readfile(dst)
    assert {"Sheet 1", "Sheet 2"} <= set(d.layouts.names()) and "Layout1" not in d.layouts
    assert len(d.layouts.get("Sheet 2")) == 1
    assert parse_dxf(dst)["tables"]["layouts"] == ["Sheet 1", "Sheet 2"]
    for op, code in (
        ({"op": "delete", "name": "Layout1"}, "EDIT_LAYOUT_PROTECTED"),  # the last one
        ({"op": "create", "name": "Layout1"}, "EDIT_LAYOUT_EXISTS"),
        ({"op": "rename", "name": "Nope", "new_name": "X"}, "EDIT_LAYOUT_NOT_FOUND"),
    ):
        with pytest.raises(DxfError) as ex:
            run(src, tmp_path, {"layouts": [op]})
        assert ex.value.code == code, op


def test_groups(src, tmp_path):
    d0 = ezdxf.readfile(src)
    lines = [e.dxf.handle for e in d0.modelspace().query("LINE")]
    ins_h = refs(d0, "B")[0].dxf.handle
    dst = run(
        src,
        tmp_path,
        {
            "groups": [
                {"op": "create", "name": "G1", "description": "two", "handles": [lines[0], ins_h]}
            ]
        },
    )
    d = ezdxf.readfile(dst)
    assert sorted(d.groups.get("G1").handles()) == sorted([lines[0], ins_h])
    g = parse_dxf(dst)["tables"]["groups"]
    assert g == [
        {
            "name": "G1",
            "description": "two",
            "selectable": True,
            "handles": list(d.groups.get("G1").handles()),
        }
    ]
    dst2 = str(tmp_path / "e.dxf")
    apply_edits(
        dst,
        dst2,
        {
            "groups": [
                {
                    "op": "modify",
                    "name": "G1",
                    "new_name": "G2",
                    "handles": [lines[0]],
                    "selectable": False,
                }
            ]
        },
    )
    d = ezdxf.readfile(dst2)
    assert d.groups.get("G1") is None and list(d.groups.get("G2").handles()) == [lines[0]]
    assert d.groups.get("G2").dxf.selectable == 0
    dst3 = str(tmp_path / "f.dxf")
    apply_edits(dst2, dst3, {"groups": [{"op": "delete", "name": "G2"}]})
    assert ezdxf.readfile(dst3).groups.get("G2") is None
    for op, code in (
        ({"op": "create", "name": "X", "handles": [lines[1]]}, "EDIT_LAYER_LOCKED"),
        ({"op": "create", "name": "X", "handles": ["FFFFF"]}, "EDIT_HANDLE_NOT_FOUND"),
        ({"op": "delete", "name": "NOPE"}, "EDIT_GROUP_NOT_FOUND"),
    ):
        with pytest.raises(DxfError) as ex:
            run(src, tmp_path, {"groups": [op]})
        assert ex.value.code == code, op


def test_request_validation():
    from backend.api.main import EditRequest

    bad = [
        {"blocks": [{"op": "redefine", "name": "B", "base": [0, 0]}]},  # no contents
        {"blocks": [{"op": "rename", "name": "B"}]},
        {"blocks": [{"op": "delete", "name": "B", "base": [0, 0]}]},
        {"blocks": [{"name": "B", "base": [0, 0], "attdefs": [ATT, ATT]}]},  # duplicate tag
        {"blocks": [{"name": "B", "base": [0, 0], "attdefs": [{**ATT, "tag": "A B"}]}]},
        {"blocks": [{"name": "B", "base": [0, 0], "attdefs": [{**ATT, "default": "a\nb"}]}]},
        {"created": [{"layer": "0", "geom": LINE, "attributes": {"A": "x"}}]},
        {"created": [ins("B", attributes={"A": "x\r"})]},
        {"layouts": [{"op": "create", "name": "Model"}]},
        {"layouts": [{"op": "rename", "name": "A"}]},
        {"layouts": [{"op": "create", "name": "A/B"}]},
        {"groups": [{"op": "create", "name": "G"}]},
        {"groups": [{"op": "delete", "name": "G", "handles": ["1A"]}]},
        {"blocks": [{"op": "delete", "name": f"B{i}"} for i in range(21)]},
    ]
    for b in bad:
        with pytest.raises(pydantic.ValidationError):
            EditRequest.model_validate(b)
    EditRequest.model_validate({"blocks": [{"name": "B", "base": [0, 0], "attdefs": [ATT]}]})
    EditRequest.model_validate({"blocks": [{"op": "delete", "name": "B"}]})


def test_w7d_security_review(tmp_path):
    """BUG-68 (W7d review): locked references, loops through missing blocks, arrow blocks,
    overlay XREFs, locked sheets."""
    from backend.api.main import EditRequest

    d = ezdxf.new("R2018", setup=True)
    d.layers.add("LOCK").lock()
    d.blocks.new("LOCKED_B").add_line((0, 0), (1, 0))
    d.blocks.new("WRAP").add_blockref("LOCKED_B", (0, 0))
    d.modelspace().add_blockref("WRAP", (0, 0), dxfattribs={"layer": "LOCK"})
    d.blocks.new("A").add_blockref("X", (0, 0))  # X does not exist (yet)
    d.blocks.new("ARROW").add_line((0, 0), (1, 0))
    d.dimstyles.get("Standard").dxf.dimblk = "ARROW"
    ov = d.blocks.new("OVL")
    ov.block.dxf.flags = 4 | 8  # an overlay XREF
    d.layouts.new("SHEET").add_line((0, 0), (1, 0), dxfattribs={"layer": "LOCK"})
    src = str(tmp_path / "s.dxf")
    d.saveas(src)
    line = [{"layer": "0", "geom": LINE}]
    cases = [
        # shown on a locked layer through WRAP
        (
            {"blocks": [{"op": "redefine", "name": "LOCKED_B", "base": [0, 0], "entities": line}]},
            "EDIT_LAYER_LOCKED",
        ),
        # X -> A -> X
        ({"blocks": [{"name": "X", "base": [0, 0], "entities": [ins("A")]}]}, "EDIT_BLOCK_INVALID"),
        ({"blocks": [{"op": "delete", "name": "ARROW"}]}, "EDIT_BLOCK_IN_USE"),
        ({"blocks": [{"op": "delete", "name": "OVL"}]}, "EDIT_BLOCK_NOT_FOUND"),
        ({"layouts": [{"op": "delete", "name": "SHEET"}]}, "EDIT_LAYER_LOCKED"),
    ]
    for edits, code in cases:
        with pytest.raises(DxfError) as ex:
            run(src, tmp_path, edits)
        assert ex.value.code == code, edits
    for bad in (
        {"groups": [{"op": "modify", "name": "G", "handles": []}]},
        {"blocks": [{"name": "PB-X", "base": [0, 0], "entities": line, "description": "{ }"}]},
    ):
        with pytest.raises(pydantic.ValidationError):
            EditRequest.model_validate(bad)
