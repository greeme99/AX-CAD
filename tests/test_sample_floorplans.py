"""Tests for Sample Floorplan DXFs (Apartment 84m2 & Office 150m2) with placed standard blocks."""

from __future__ import annotations

import io
from pathlib import Path

import ezdxf

from core.dxf import param_blocks as pb
from core.dxf.sample_floorplans import (
    build_apartment_84m2_dxf,
    build_office_150m2_dxf,
)

SAMPLES_DIR = Path(__file__).resolve().parents[1] / "docs" / "samples"


def _verify_floorplan_dxf(raw: bytes, min_inserts: int, required_templates: set[str]) -> None:
    doc = ezdxf.read(io.StringIO(raw.decode("utf-8")))
    msp = doc.modelspace()
    inserts = list(msp.query("INSERT"))
    assert len(inserts) >= min_inserts

    found_templates: set[str] = set()
    for ins in inserts:
        bname = ins.dxf.name
        assert bname in doc.blocks, f"missing block definition: {bname}"
        blk = doc.blocks[bname]
        meta = pb.parse_description(blk.block.dxf.description)
        assert meta is not None, f"missing axcad_pb metadata on {bname}"
        found_templates.add(meta["template"])
        # Verify Layer 0 / ByLayer standardization inside every block definition
        for ent in blk:
            assert ent.dxf.layer == "0"
            assert ent.dxf.color == 256

    missing = required_templates - found_templates
    assert not missing, f"missing expected standard blocks: {missing}"


def test_apartment_84m2_sample_floorplan_dxf() -> None:
    raw = build_apartment_84m2_dxf()
    _verify_floorplan_dxf(
        raw,
        min_inserts=30,
        required_templates={
            "DOOR_ENTRANCE",
            "WINDOW_BALCONY",
            "WINDOW_SLIDING",
            "BED_QUEEN",
            "SOFA_3SEAT",
            "DINING_TABLE",
            "KITCHEN_SINK",
            "KITCHEN_COOKTOP",
            "REFRIGERATOR",
            "BATHTUB",
            "SHOWER_BOOTH",
            "BIDET_TOILET",
            "WASHER_DRYER",
            "AC_OUTDOOR_UNIT",
            "DRYING_RACK",
            "NORTH_ARROW",
        },
    )
    saved = SAMPLES_DIR / "AXCAD_Sample_Apartment_84m2.dxf"
    assert saved.exists() and saved.stat().st_size > 10_000


def test_office_150m2_sample_floorplan_dxf() -> None:
    raw = build_office_150m2_dxf()
    _verify_floorplan_dxf(
        raw,
        min_inserts=30,
        required_templates={
            "DOOR_DOUBLE",
            "DOOR_POCKET",
            "RECEPTION_DESK",
            "OFFICE_PARTITION",
            "OFFICE_DESK",
            "CONFERENCE_TABLE",
            "MFP_PRINTER",
            "FILE_CABINET",
            "WATER_PURIFIER",
            "KITCHEN_SINK",
            "TOILET",
            "BASIN",
            "HVAC_FCU_4WAY",
            "LED_PANEL",
            "PANEL_BOARD",
            "NORTH_ARROW",
        },
    )
    saved = SAMPLES_DIR / "AXCAD_Sample_Office_150m2.dxf"
    assert saved.exists() and saved.stat().st_size > 10_000
