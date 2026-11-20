"""Assembly A5~A7 tests: 1,000-part BVH culling & LOD, Kinematics (Gear/Rack-Pinion/Screw), Exploded Drawing DXF (ASM-T11 ~ ASM-T13)."""

from __future__ import annotations

import io
import math
import time

import ezdxf
import pytest

from core.geometry.assembly_drawing import generate_assembly_drawing_dxf
from core.geometry.assembly_large import AABB, bvh_overlapping_pairs, lightweight_lod_plan
from core.geometry.assembly_motion import simulate_motion


def test_asm_t11_bvh_1000_parts_culling_speed_and_exact_pairs() -> None:
    """ASM-T11: BVH broad-phase AABB culling on 1,000 parts finds exact overlapping pairs in < 0.5s."""
    # Place 1,000 parts on a 10x10x10 grid spaced 20mm apart (each 10x10x10 -> 0 overlaps)
    boxes: list[AABB] = []
    cid = 1
    for ix in range(10):
        for iy in range(10):
            for iz in range(10):
                x0, y0, z0 = ix * 20.0, iy * 20.0, iz * 20.0
                boxes.append(AABB(cid, (x0, y0, z0), (x0 + 10.0, y0 + 10.0, z0 + 10.0)))
                cid += 1
    # Inject 2 deliberate overlapping parts overlapping part #1 and part #500
    boxes.append(AABB(1001, (5.0, 5.0, 5.0), (15.0, 15.0, 15.0)))
    boxes.append(AABB(1002, (85.0, 185.0, 185.0), (95.0, 195.0, 195.0)))

    t0 = time.perf_counter()
    pairs = bvh_overlapping_pairs(boxes, clearance=0.0)
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.5
    assert (1, 1001) in pairs
    assert (500, 1002) in pairs
    assert len(pairs) == 2

    # Lightweight LOD budget test on 1,000 components
    comps = [
        {
            "component_id": b.comp_id,
            "triangle_count": 1000,
            "bbox": {"min": list(b.min_pt), "max": list(b.max_pt)},
        }
        for b in boxes
    ]
    lod = lightweight_lod_plan(comps, triangle_budget=100_000)
    assert lod["full_count"] == 100
    assert lod["proxy_count"] == 902


def test_asm_t12_kinematics_gear_rack_pinion_and_screw_couplings() -> None:
    """ASM-T12: Motor + Gear (ratio 2:1) + Rack-Pinion (R=15mm) + Screw (pitch=4mm) match exact kinematic equations."""
    comps = [
        {"component_id": 1, "name": "DriveGear", "placement": {"t": [0, 0, 0], "r": [0, 0, 0]}},
        {"component_id": 2, "name": "DrivenGear", "placement": {"t": [40, 0, 0], "r": [0, 0, 0]}},
        {"component_id": 3, "name": "Rack", "placement": {"t": [0, 20, 0], "r": [0, 0, 0]}},
        {"component_id": 4, "name": "LeadNut", "placement": {"t": [0, 0, 10], "r": [0, 0, 0]}},
    ]
    drivers = [{"type": "MOTOR_ROTATE", "component_id": 1, "axis": [0, 0, 1], "deg_per_s": 180.0}]
    couplings = [
        {"type": "GEAR", "master_id": 1, "slave_id": 2, "ratio": 0.5, "axis": [0, 0, 1]},
        {
            "type": "RACK_PINION",
            "master_id": 1,
            "slave_id": 3,
            "pitch_radius_mm": 15.0,
            "direction": [1, 0, 0],
        },
        {"type": "SCREW", "master_id": 1, "slave_id": 4, "pitch_mm": 4.0, "direction": [0, 0, 1]},
    ]
    sim = simulate_motion(comps, drivers, couplings, duration_s=2.0, steps=10, check_clashes=False)
    last = sim["frames"][-1]["placements"]
    # After 2s at 180 deg/s -> master rotated 360 deg (1 full rev)
    assert last["1"]["r"][2] == pytest.approx(360.0, abs=1e-4)
    # Driven gear (ratio 0.5) -> -180 deg
    assert last["2"]["r"][2] == pytest.approx(-180.0, abs=1e-4)
    # Rack (R=15mm, 1 full rev = 2*pi*15 mm)
    assert last["3"]["t"][0] == pytest.approx(2.0 * math.pi * 15.0, rel=1e-4)
    # Lead screw nut (pitch=4mm, 1 full rev -> +4mm from base z=10 -> z=14)
    assert last["4"]["t"][2] == pytest.approx(14.0, abs=1e-4)


def test_asm_t13_exploded_assembly_drawing_dxf_with_balloons_and_parts_list() -> None:
    """ASM-T13: Exploded assembly drawing DXF contains OUTLINE, EXPLODE_LINE, numbered BALLOON circles, and BOM_TABLE."""
    comps = [
        {
            "component_id": 1,
            "name": "BasePlate",
            "placement": {"t": [0, 0, 0], "r": [0, 0, 0]},
            "bbox": {"min": [0, 0, 0], "max": [100, 60, 10]},
        },
        {
            "component_id": 2,
            "name": "CoverCap",
            "placement": {"t": [20, 10, 10], "r": [0, 0, 0]},
            "bbox": {"min": [0, 0, 0], "max": [60, 40, 15]},
        },
    ]
    explode_steps = [{"component_ids": [2], "direction": [0, 1, 0], "distance_mm": 50.0}]
    dxf_bytes = generate_assembly_drawing_dxf(
        comps, explode_steps=explode_steps, explode_factor=1.0, title="TEST ASM"
    )
    doc = ezdxf.read(io.StringIO(dxf_bytes.decode("utf-8")))
    msp = doc.modelspace()
    assert len(list(msp.query("CIRCLE[layer=='BALLOON']"))) == 2
    assert len(list(msp.query("LINE[layer=='EXPLODE_LINE']"))) == 1
    bom_texts = [e.dxf.text for e in msp.query("TEXT[layer=='BOM_TABLE']")]
    assert "BasePlate" in bom_texts
    assert "CoverCap" in bom_texts
