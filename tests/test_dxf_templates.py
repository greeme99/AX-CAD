"""Tests for CAD Template Samples (BLANK_KS, MECHANICAL_KS, ARCHITECTURAL_KS, INTERIOR_RENO, MEP_FACILITY)."""

from __future__ import annotations

import io

import ezdxf

from core.dxf.template import TemplateCode, blank_dxf, list_template_samples


def test_all_5_templates_generate_valid_r2018_dxf_with_layers_border_and_blocks() -> None:
    catalog = list_template_samples()
    assert len(catalog) == 6

    for item in catalog:
        code: TemplateCode = item["code"]
        sheet = item["default_sheet"]
        raw = blank_dxf(
            sheet=sheet, portrait=False, template=code, title="샘플 도면", doc_no="AX-T01"
        )
        doc = ezdxf.read(io.StringIO(raw.decode("utf-8")))
        assert doc.header["$MEASUREMENT"] == 1
        assert doc.header["$CLAYER"] == "OUTLINE"
        assert "OUTLINE" in doc.layers and "DIM" in doc.layers

        msp = doc.modelspace()
        if code == "BLANK_KS":
            assert len(list(msp)) == 0
        else:
            assert "BORDER" in doc.layers and "TITLEBLOCK" in doc.layers
            assert len(list(msp)) >= 15
            texts = [e.dxf.text for e in msp.query("TEXT")]
            assert any("AX-T01" in t for t in texts)
            assert any("도면명" in t for t in texts)

    # Verify domain-specific reusable symbol blocks exist
    comp = ezdxf.read(io.StringIO(blank_dxf("A3", template="COMPANY_INTERNAL").decode("utf-8")))
    assert "SYM_INTERNAL_STAMP" in comp.blocks
    assert "MEMO" in comp.layers and "QUOTE_REF" in comp.layers

    mech = ezdxf.read(io.StringIO(blank_dxf("A3", template="MECHANICAL_KS").decode("utf-8")))
    assert "SYM_ROUGHNESS" in mech.blocks
    assert "SYM_GDT_PERP" in mech.blocks
    assert "SYM_THIRD_ANGLE" in mech.blocks

    arch = ezdxf.read(io.StringIO(blank_dxf("A1", template="ARCHITECTURAL_KS").decode("utf-8")))
    assert "SYM_NORTH" in arch.blocks
    assert "SYM_SECTION" in arch.blocks
    assert "BLK_DOOR_900" in arch.blocks
    assert "BLK_WINDOW_1800" in arch.blocks

    intr = ezdxf.read(io.StringIO(blank_dxf("A3", template="INTERIOR_RENO").decode("utf-8")))
    assert "BLK_DESK_1600" in intr.blocks
    assert "SYM_LED_DOWNLIGHT" in intr.blocks

    mep = ezdxf.read(io.StringIO(blank_dxf("A3", template="MEP_FACILITY").decode("utf-8")))
    assert "SYM_PANEL_BOARD" in mep.blocks
    assert "SYM_HVAC_FCU" in mep.blocks
    assert "SYM_VALVE" in mep.blocks
