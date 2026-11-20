"""CAM NX Upgrade Tests (C5~C8): 3D freeform, 2-axis turning, 3+2 indexed 5-axis, stock sim & multi-controller post (CAM-T06 ~ CAM-T10)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from core.cam.indexed5ax import check_holder_collision, tilted_workplane
from core.cam.mill3d import finish_scallop, rough_3d, sample_height_map, scallop_stepover
from core.cam.mill25d import ToolpathSegment
from core.cam.post import (
    SAFETY_HEADER,
    post_fanuc_lathe,
    post_heidenhain_tnc,
    post_siemens_840d,
)
from core.cam.stock_sim import simulate_stock_removal
from core.cam.tools import CamError, CamTool
from core.cam.turning import turn_finish, turn_groove, turn_rough, turn_thread


def test_cam_t06_3d_freeform_ball_nose_rough_and_constant_scallop() -> None:
    """CAM-T06: 3D dome surface ball-nose roughing + constant-scallop finishing (h=0.05 mm) with 0 target gouges."""
    ball = CamTool(
        tool_code="T05",
        tool_type="ENDMILL_BALL",
        diameter_mm=10.0,
        flutes=2,
        spindle_rpm=8000,
        feed_xy_mm_min=800.0,
        feed_z_mm_min=300.0,
        stepdown_mm=2.5,
        stepover_ratio=0.4,
    )
    # Exact scallop stepover formula: s = 2 * sqrt(2*R*h - h^2)
    h = 0.05
    expected_s = 2.0 * math.sqrt(2.0 * 5.0 * h - h * h)
    assert scallop_stepover(ball.radius_mm, h) == pytest.approx(expected_s, rel=1e-9)

    hmap = sample_height_map(
        surface_fn=lambda gx, gy: (
            10.0 * np.maximum(0.0, 1.0 - ((gx - 25.0) ** 2 + (gy - 25.0) ** 2) / 900.0)
        ),
        bbox=(0.0, 0.0, 0.0, 50.0, 50.0, 10.0),
        grid_step_mm=2.5,
    )
    r_tp = rough_3d(
        hmap, ball, stock_top_z=12.0, stepdown_mm=3.0, stock_allowance_mm=0.5, safe_z=25.0
    )
    assert r_tp.z_passes >= 4
    assert r_tp.cut_length_mm > 500.0

    f_tp = finish_scallop(hmap, ball, target_scallop_mm=0.1, safe_z=25.0)
    assert len(f_tp.segments) > 100
    # Verify zero gouges against target height map using ball-nose dexel stock sim
    sim = simulate_stock_removal(
        f_tp.segments,
        tool_diameter_mm=ball.diameter_mm,
        blank_bbox=(0.0, 0.0, 0.0, 50.0, 50.0, 12.0),
        grid_step_mm=2.5,
        is_ball_nose=True,
        target_height_map=hmap,
        gouge_tol_mm=0.15,
    )
    assert sim.gouge_count == 0
    assert sim.rapid_collision_count == 0
    assert sim.removed_volume_mm3 > 1000.0


def test_cam_t07_2ax_lathe_turning_and_fanuc_lathe_post() -> None:
    """CAM-T07: Stepped shaft OD rough/finish/groove/G76 thread emits X=2r diameters, G96/G76, and blocks injection."""
    profile = [(0.0, 10.0), (-20.0, 10.0), (-20.0, 15.0), (-50.0, 15.0)]
    r_res = turn_rough(
        profile, stock_radius_mm=20.0, doc_mm=2.5, finish_allowance_mm=0.3, mode="OD"
    )
    f_res = turn_finish(profile)
    g_res = turn_groove(z_start=-15.0, z_end=-20.0, outer_radius_mm=10.0, bottom_radius_mm=7.5)
    t_res = turn_thread(z_start=0.0, z_end=-18.0, major_diameter_mm=20.0, pitch_mm=2.5, passes=6)

    assert r_res.toolpath.z_passes == 4
    assert len(f_res.toolpath.segments) >= 5
    assert len(g_res.toolpath.segments) >= 3
    assert t_res.thread_cycle is not None
    assert t_res.thread_cycle["pitch_mm"] == pytest.approx(2.5)

    tool = CamTool(tool_code="T01", tool_type="ENDMILL_FLAT", diameter_mm=1.0)
    nc = post_fanuc_lathe(
        [
            (tool, r_res.toolpath, "OD_ROUGH", None),
            (tool, f_res.toolpath, "OD_FINISH", None),
            (tool, t_res.toolpath, "M20_THREAD", t_res.thread_cycle),
        ],
        program_name="O2026",
        css_m_min=180.0,
        max_rpm=2500,
    )
    assert SAFETY_HEADER in nc
    assert "G50 S2500" in nc
    assert "G96 S180 M03" in nc
    assert "G76 " in nc
    assert "X20.000" in nc  # diameter programming X = 2*r = 20.0

    with pytest.raises(CamError):
        post_fanuc_lathe([(tool, f_res.toolpath, "BAD\nM03", None)])


def test_cam_t08_3plus2_indexed_workplane_euler_and_holder_collision() -> None:
    """CAM-T08: 45-degree tilted workplane aligns local +Z to target normal within <=1e-9 and emits G68.2/CYCLE800/PLANE SPATIAL."""
    target_n = (0.0, math.sin(math.radians(45.0)), math.cos(math.radians(45.0)))
    wp = tilted_workplane(target_n, origin=(10.0, 20.0, 30.0))

    # Exact verification: R @ [0, 0, 1]^T == target_n
    z_mapped = wp.rot_matrix @ np.array([0.0, 0.0, 1.0])
    assert np.allclose(z_mapped, np.array(target_n), atol=1e-9)
    assert wp.b_deg == pytest.approx(45.0, abs=1e-6)

    fanuc_hdr = "\n".join(wp.tilt_header("FANUC_MILL"))
    siemens_hdr = "\n".join(wp.tilt_header("SIEMENS_840D"))
    heid_hdr = "\n".join(wp.tilt_header("HEIDENHAIN_TNC"))
    assert "G68.2" in fanuc_hdr and "G53.1" in fanuc_hdr
    assert "CYCLE800(" in siemens_hdr
    assert "PLANE SPATIAL" in heid_hdr

    # Holder collision detection: tall boss at z=35 mm while tool tip is at z=5 with 20mm flute (holder at z=25 < 35)
    tall_boss = sample_height_map(
        surface_fn=lambda gx, gy: np.where(
            (gx >= 15) & (gx <= 25) & (gy >= 15) & (gy <= 25), 35.0, 0.0
        ),
        bbox=(0.0, 0.0, 0.0, 40.0, 40.0, 35.0),
        grid_step_mm=2.0,
    )
    segs = [ToolpathSegment("CUT", (5.0, 20.0, 5.0), (10.0, 20.0, 5.0), feed=500.0)]
    cols = check_holder_collision(segs, tall_boss, flute_length_mm=20.0, holder_radius_mm=15.0)
    assert len(cols) == 1
    assert cols[0]["penetration_mm"] == pytest.approx(10.0, abs=1e-3)


def test_cam_t09_dexel_stock_simulation_detects_rapid_collision_and_removed_volume() -> None:
    """CAM-T09: Dexel stock simulator measures removed volume and catches intentional rapid plunge collision."""
    # Blank: 40 x 40 x 20 (32,000 mm3)
    # 1. Clean slot cut at z=10 (depth 10) with Ø10 flat endmill, plus 1 illegal RAPID move at z=5 through uncut stock
    segs = [
        ToolpathSegment("RAPID", (0.0, 10.0, 25.0), (5.0, 10.0, 25.0)),
        ToolpathSegment("PLUNGE", (5.0, 10.0, 25.0), (5.0, 10.0, 10.0), feed=200.0),
        ToolpathSegment("CUT", (5.0, 10.0, 10.0), (35.0, 10.0, 10.0), feed=600.0),
        ToolpathSegment("RAPID", (35.0, 10.0, 10.0), (35.0, 10.0, 25.0)),
        # Intentional illegal RAPID crashing through uncut stock at y=30, z=5!
        ToolpathSegment("RAPID", (5.0, 30.0, 5.0), (35.0, 30.0, 5.0)),
    ]
    sim = simulate_stock_removal(
        segs,
        tool_diameter_mm=10.0,
        blank_bbox=(0.0, 0.0, 0.0, 40.0, 40.0, 20.0),
        grid_step_mm=2.0,
    )
    assert sim.initial_volume_mm3 == pytest.approx(32_000.0, rel=1e-4)
    # Slot removed volume ~ (30 * 10 + pi * 5^2) * 10 = ~3,785 mm3
    assert 3000.0 <= sim.removed_volume_mm3 <= 4600.0
    assert sim.rapid_collision_count >= 1


def test_cam_t10_siemens_heidenhain_post_and_api(client, world) -> None:
    """CAM-T10: API E2E for MILL_3D + SIEMENS_840D + HEIDENHAIN_TNC + simulate-stock endpoint."""
    h = world.h["designer"]
    doc_id = world.doc

    # Siemens 840D 3D setup
    s_res = client.post(
        f"/api/documents/{doc_id}/cam/setups",
        headers=h,
        json={"kind": "MILL_3D", "post_target": "SIEMENS_840D", "safe_z_mm": 25.0},
    )
    assert s_res.status_code == 201
    sid = s_res.json()["data"]["setup_id"]

    op_res = client.post(
        f"/api/cam/setups/{sid}/operations",
        headers=h,
        json={
            "op_type": "FINISH_SCALLOP",
            "tool_code": "T01",
            "params": {
                "bbox": [0, 0, 0, 30, 30, 10],
                "target_scallop_mm": 0.2,
                "grid_step_mm": 5.0,
            },
        },
    )
    assert op_res.status_code == 201

    nc = client.get(f"/api/cam/setups/{sid}/gcode?program_name=SIEMENS_TEST", headers=h)
    assert nc.status_code == 200
    assert "G90 G17 G71 G40 G64" in nc.text
    assert 'T="T01" D1 M6' in nc.text

    sim = client.post(
        f"/api/cam/setups/{sid}/simulate-stock",
        headers=h,
        json={"blank_bbox": [0, 0, 0, 30, 30, 12], "grid_step_mm": 3.0, "tool_diameter_mm": 10.0},
    )
    assert sim.status_code == 200
    assert sim.json()["data"]["removed_volume_mm3"] > 0

    # Heidenhain TNC post unit check
    tool = CamTool(tool_code="T02", tool_type="ENDMILL_FLAT", diameter_mm=6.0)
    wp = tilted_workplane((0.0, 0.0, 1.0))
    from core.cam.mill25d import ToolpathResult

    heid_nc = post_heidenhain_tnc(
        [
            (
                tool,
                ToolpathResult(segments=[ToolpathSegment("CUT", (0, 0, 0), (10, 0, 0), feed=500)]),
                "FACE",
            )
        ],
        program_name="HEID_01",
        tilt_header=wp.tilt_header("HEIDENHAIN_TNC"),
        tilt_footer=wp.tilt_footer("HEIDENHAIN_TNC"),
    )
    assert "BEGIN PGM HEID_01 MM" in heid_nc
    assert "PLANE SPATIAL" in heid_nc
    assert "END PGM HEID_01 MM" in heid_nc

    # Siemens direct post check
    siem_nc = post_siemens_840d(
        [
            (
                tool,
                ToolpathResult(segments=[ToolpathSegment("CUT", (0, 0, 0), (10, 0, 0), feed=500)]),
                "FACE",
            )
        ],
        program_name="SIEM_01",
        tilt_header=wp.tilt_header("SIEMENS_840D"),
        tilt_footer=wp.tilt_footer("SIEMENS_840D"),
    )
    assert "CYCLE800(" in siem_nc
