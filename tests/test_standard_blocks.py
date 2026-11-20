"""Tests for the 24 Built-in Standard CAD Blocks (Mechanical, Architectural, Plumbing/HVAC, Electrical)."""

from __future__ import annotations

from core.dxf import param_blocks as pb


def test_all_24_builtin_standard_blocks_validate_and_instantiate() -> None:
    assert len(pb.BUILTIN) == 24
    categories = set()

    for raw in pb.BUILTIN:
        tpl = pb.validate(raw)
        assert tpl["layer_policy"] == "0 / ByLayer"
        assert tpl["base_point"]
        categories.add(tpl["category"])

        # Instantiate with default parameters (and every visibility state if present)
        states = tpl["states"] or [None]
        for st in states:
            placed = pb.instantiate(tpl, {}, st)
            assert placed["name"].startswith(f"PB-{tpl['name']}-")
            assert len(placed["geoms"]) >= 1

    assert categories == {"기계", "건축", "배관·공조", "전기"}
