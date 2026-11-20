"""Tests for the 60 Built-in Standard CAD Blocks (Mech/Arch/Doors&Windows/Furniture/Kitchen/Bath/Veranda/Office/MEP/Electrical)."""

from __future__ import annotations

from core.dxf import param_blocks as pb


def test_all_60_builtin_standard_blocks_validate_and_instantiate() -> None:
    assert len(pb.BUILTIN) == 60
    names: set[str] = set()
    categories: set[str] = set()

    for raw in pb.BUILTIN:
        tpl = pb.validate(raw)
        assert tpl["name"] not in names, f"duplicate block name: {tpl['name']}"
        names.add(tpl["name"])
        assert tpl["layer_policy"] == "0 / ByLayer"
        assert "(0,0)" in tpl["base_point"]
        categories.add(tpl["category"])

        # Instantiate with default parameters across every visibility state
        states = tpl["states"] or [None]
        for st in states:
            placed = pb.instantiate(tpl, {}, st)
            assert placed["name"].startswith(f"PB-{tpl['name']}-")
            assert len(placed["geoms"]) >= 1

    assert categories == {
        "기계",
        "건축",
        "창호·문",
        "가구·퍼니처",
        "주방",
        "욕실",
        "베란다·공용",
        "오피스",
        "배관·공조",
        "전기",
    }
