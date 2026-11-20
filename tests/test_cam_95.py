"""CAM 95-point tests: CAM-T11 (Simultaneous 5-Axis Swarf/Multi-Axis & RTCP), CAM-T12 (G-code Verifier)."""

from __future__ import annotations

import math

from core.cam.gcode_verify import MachineLimits, verify_gcode
from core.cam.mill5ax import (
    ac_to_tool_axis,
    apply_lead_tilt,
    multiaxis_surface_5ax,
    post_5ax_rtcp,
    swarf_5ax,
    tool_axis_to_ac,
)
from core.cam.tools import DEFAULT_TOOLS


def _tool():
    return DEFAULT_TOOLS[0]


def test_cam_t11_simultaneous_5ax_ik_and_rtcp() -> None:
    """CAM-T11: Inverse kinematics round-trip <= 1e-9, Swarf/Multi-Axis lead-tilt, RTCP Fanuc/Siemens/Heidenhain."""
    # 1. IK -> FK round-trip precision
    for axis in [
        (0.0, 0.0, 1.0),
        (0.3, -0.4, 0.8660254),
        (-0.5, 0.25, 0.8291562),
        (0.70710678, 0.0, 0.70710678),
    ]:
        n = math.sqrt(sum(c * c for c in axis))
        u = (axis[0] / n, axis[1] / n, axis[2] / n)
        a_deg, c_deg, _ = tool_axis_to_ac(u)
        rebuilt = ac_to_tool_axis(a_deg, c_deg)
        err = math.dist(u, rebuilt)
        assert err <= 1e-9, f"IK->FK error {err} exceeded 1e-9"

    # 2. Lead/Tilt rotates normal by exact angle
    tilted = apply_lead_tilt((0.0, 0.0, 1.0), (1.0, 0.0, 0.0), lead_deg=15.0, tilt_deg=0.0)
    angle_deg = math.degrees(math.acos(max(-1.0, min(1.0, tilted[2]))))
    assert abs(angle_deg - 15.0) < 1e-6

    # 3. Ruled-surface Swarf 5-axis toolpath with singularity detection
    bot = [(float(i * 10), 0.0, 0.0) for i in range(6)]
    top = [(float(i * 10), 0.0 if i == 0 else 5.0 + i * 2.0, 20.0) for i in range(6)]
    res = swarf_5ax(bot, top, _tool(), lead_deg=3.0, tilt_deg=2.0)
    assert len(res.poses) == 6
    assert res.toolpath.cut_length_mm > 49.0

    # 4. Multi-axis surface-following toolpath
    pts = [(float(i * 10), 0.0, math.sin(i * 0.3) * 5.0) for i in range(5)]
    nrms = [(0.1 * i, 0.0, 1.0) for i in range(5)]
    mres = multiaxis_surface_5ax(pts, nrms, _tool(), lead_deg=8.0, tilt_deg=-3.0)
    assert len(mres.poses) == 5

    # 5. RTCP post-processors: Fanuc G43.4, Siemens TRAORI, Heidenhain M128
    nc_fanuc = post_5ax_rtcp(res, _tool(), controller="FANUC_MILL", program_name="O5011")
    nc_siemens = post_5ax_rtcp(res, _tool(), controller="SIEMENS_840D", program_name="P5011")
    nc_hh = post_5ax_rtcp(res, _tool(), controller="HEIDENHAIN_TNC", program_name="5011")
    assert "G43.4" in nc_fanuc and "G49" in nc_fanuc
    assert "TRAORI" in nc_siemens and "TRAFOOF" in nc_siemens
    assert "M128" in nc_hh and "M129" in nc_hh


def test_cam_t12_gcode_reverse_parser_and_verifier() -> None:
    """CAM-T12: Reverse-parse NC programs, enforce machine limits & RTCP blocks, and verify stock removal."""
    bot = [(0.0, 0.0, -2.0), (40.0, 0.0, -2.0), (40.0, 30.0, -2.0)]
    top = [(0.0, 4.0, 18.0), (40.0, 4.0, 18.0), (40.0, 34.0, 18.0)]
    res5 = swarf_5ax(bot, top, _tool(), safe_z=25.0)
    nc_fanuc = post_5ax_rtcp(res5, _tool(), controller="FANUC_MILL", safe_z=25.0)

    # Valid program passes travel limits + RTCP block tracking + dexel stock verification
    v_ok = verify_gcode(
        nc_fanuc,
        limits=MachineLimits(x_min=-100, x_max=200, y_min=-100, y_max=200, z_min=-50, z_max=100),
        blank_bbox=(-10.0, -10.0, -10.0, 60.0, 50.0, 0.0),
        tool_diameter_mm=10.0,
        grid_step_mm=2.0,
    )
    assert v_ok.ok, f"Expected valid NC, got errors: {v_ok.errors}"
    assert v_ok.rtcp_active_blocks >= 3
    assert v_ok.segment_count >= 3
    assert v_ok.stock_sim is not None
    assert v_ok.stock_sim["removed_volume_mm3"] > 0.0

    # Over-travel & over-RPM violations are caught
    bad_nc = "G90 G21\nS45000 M03\nG01 X999.0 Y0.0 Z-5.0 F25000\nM30\n"
    v_bad = verify_gcode(
        bad_nc, limits=MachineLimits(x_max=500.0, max_rpm=24000, max_feed_mm_min=15000.0)
    )
    assert not v_bad.ok
    assert any("RPM" in e for e in v_bad.errors)
    assert any("X=" in e for e in v_bad.errors)
    assert any("feed" in e for e in v_bad.errors)
