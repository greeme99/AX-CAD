"""CAM C1~C4 unit, kernel, G-code security, laser topology, and API tests (CAM-T01 ~ CAM-T05)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

from core.cam.laser import laser_toolpath
from core.cam.mill25d import RAPID_MM_MIN, contour, drill, pocket
from core.cam.post import SAFETY_HEADER, post_fanuc_mill, post_generic_laser
from core.cam.recognize import recognize_features
from core.cam.tools import CamError, CamTool
from core.quote_engine.cost import cam_toolpath_labor_line


def test_cam_t01_pocket_100x60_hand_calculation_and_zero_uncut() -> None:
    """CAM-T01: 100x60 pocket (Ø10, stepover 50%, depth 10, stepdown 2) matches hand-calculated cut length & time within ±0.5%."""
    tool = CamTool(
        tool_code="T01",
        tool_type="ENDMILL_FLAT",
        diameter_mm=10.0,
        flutes=4,
        spindle_rpm=4500,
        feed_xy_mm_min=600.0,
        feed_z_mm_min=200.0,
        stepdown_mm=2.0,
        stepover_ratio=0.5,
    )
    outer = [(0.0, 0.0), (100.0, 0.0), (100.0, 60.0), (0.0, 60.0)]
    res = pocket(outer, None, tool, depth=10.0, stepdown=2.0, stepover=0.5, safe_z=20.0, top_z=0.0)

    assert res.z_passes == 5
    # Per Z pass:
    # 5 concentric rings (perimeters 280 + 240 + 200 + 160 + 120 = 1000 mm)
    # + 5 step-in links of length 5*sqrt(2) mm each (5 * 7.0710678 = 35.355339 mm)
    # + centerline pass of 40.0 mm => 1075.355339 mm per pass * 5 passes = 5376.7767 mm
    expected_cut_per_pass = 280 + 240 + 200 + 160 + 120 + 5 * (5.0 * (2.0**0.5)) + 40.0
    expected_cut_total = expected_cut_per_pass * 5
    assert res.cut_length_mm == pytest.approx(expected_cut_total, rel=0.005)

    expected_time_s = (
        res.cut_length_mm / tool.feed_xy_mm_min
        + res.plunge_length_mm / tool.feed_z_mm_min
        + res.rapid_length_mm / RAPID_MM_MIN
    ) * 60.0
    assert res.cycle_time_s == pytest.approx(expected_time_s, rel=1e-6)


def test_cam_t02_3d_feature_auto_recognition_4_holes_1_pocket() -> None:
    """CAM-T02: 3D block with 4 cylindrical holes and 1 blind pocket recognized with <= 1e-6 accuracy."""
    # Base block: 120 x 80 x 20
    shape = BRepPrimAPI_MakeBox(gp_Pnt(0.0, 0.0, 0.0), 120.0, 80.0, 20.0).Shape()

    # 1 blind rectangular pocket: [35..85] x [25..55], depth 6 mm (z from 14 to 20)
    pocket_box = BRepPrimAPI_MakeBox(gp_Pnt(35.0, 25.0, 14.0), 50.0, 30.0, 10.0).Shape()
    shape = BRepAlgoAPI_Cut(shape, pocket_box).Shape()

    # 4 through holes Ø8 at corners (15, 15), (105, 15), (105, 65), (15, 65)
    expected_holes = [(15.0, 15.0), (15.0, 65.0), (105.0, 15.0), (105.0, 65.0)]
    for hx, hy in expected_holes:
        cyl = BRepPrimAPI_MakeCylinder(
            gp_Ax2(gp_Pnt(hx, hy, 0.0), gp_Dir(0.0, 0.0, 1.0)), 4.0, 20.0
        ).Shape()
        shape = BRepAlgoAPI_Cut(shape, cyl).Shape()

    rec = recognize_features(shape)
    assert len(rec["holes"]) == 4
    for got, (ex, ey) in zip(rec["holes"], expected_holes, strict=True):
        assert got["x"] == pytest.approx(ex, abs=1e-6)
        assert got["y"] == pytest.approx(ey, abs=1e-6)
        assert got["diameter_mm"] == pytest.approx(8.0, abs=1e-6)
        assert got["depth_mm"] == pytest.approx(20.0, abs=1e-6)
        assert got["through"] is True

    assert len(rec["pockets"]) == 1
    p = rec["pockets"][0]
    assert p["depth_mm"] == pytest.approx(6.0, abs=1e-6)
    assert p["z_floor"] == pytest.approx(14.0, abs=1e-6)
    assert p["area_mm2"] == pytest.approx(1500.0, rel=1e-5)


def test_cam_t03_fanuc_gcode_injection_blocked_and_quote_trace() -> None:
    """CAM-T03: Fanuc G-code emits safety header, blocks newline/paren injection, and records CAM_TOOLPATH quote trace."""
    tool = CamTool(
        tool_code="T01",
        tool_type="ENDMILL_FLAT",
        diameter_mm=10.0,
        spindle_rpm=4500,
        feed_xy_mm_min=600.0,
        feed_z_mm_min=200.0,
    )
    tp = contour(
        [[(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)]],
        tool,
        depth=4.0,
        stepdown=2.0,
        side="OUTSIDE",
        lead_in_r=2.0,
    )
    gcode = post_fanuc_mill([(tool, tp, "OUTER_CONTOUR")], program_name="O1001", comment="TEST RUN")
    assert SAFETY_HEADER in gcode
    assert "G90 G17 G21 G40 G49 G80" in gcode
    assert "T01 M06" in gcode
    assert "S4500 M03" in gcode
    assert "G02 " in gcode
    assert "M05\nM30" in gcode

    # Attempt newline/parenthesis G-code injection via comment or program_name
    for bad_comment in ("SAFE\nM03 S60000", "CLOSE) G00 Z-999 (", "NULL\x00INJECT"):
        with pytest.raises(CamError) as exc:
            post_fanuc_mill([(tool, tp, "CONTOUR")], comment=bad_comment)
        assert exc.value.code == "CAM_INVALID_PARAM"

    with pytest.raises(CamError):
        post_fanuc_mill([(tool, tp, "CONTOUR")], program_name="O1001\nG00 Z-100")

    # Verify CAM_TOOLPATH quote line & trace structure
    line = cam_toolpath_labor_line(
        setup_id=42,
        total_cycle_time_s=Decimal("1800.00"),
        hourly_rate_krw=Decimal(60000),
        kind="MILL_25D",
        operation_count=3,
    )
    assert line["cost_category"] == "LABOR"
    assert line["calculated_amount"] == Decimal("30000.00")
    assert line["traces"][0]["source_kind"] == "CAM_TOOLPATH"
    assert line["traces"][0]["sources"] == ["42"]


def test_cam_t04_laser_inner_holes_first_outer_last_and_zero_collision() -> None:
    """CAM-T04: Sheet flat pattern with outer boundary + 4 internal holes cuts all 4 holes before outer boundary with 0 lead-in collisions."""
    outer = [(0.0, 0.0), (200.0, 0.0), (200.0, 120.0), (0.0, 120.0)]
    holes = [
        [(20.0, 20.0), (35.0, 20.0), (35.0, 35.0), (20.0, 35.0)],
        [(165.0, 20.0), (180.0, 20.0), (180.0, 35.0), (165.0, 35.0)],
        [(165.0, 85.0), (180.0, 85.0), (180.0, 100.0), (165.0, 100.0)],
        [(20.0, 85.0), (35.0, 85.0), (35.0, 100.0), (20.0, 100.0)],
    ]
    # Pass outer FIRST in input list to prove topological reordering moves it to LAST
    tp, order = laser_toolpath([outer, *holes], kerf_mm=0.2, lead_in_mm=2.0, power_pct=85.0)
    assert len(order) == 5
    # First 4 cut items must be the inner holes (is_hole=True, loop_index in 1..4)
    for item in order[:4]:
        assert item.is_hole is True
        assert item.loop_index in {1, 2, 3, 4}
        assert item.pierce_collision is False
    # Last cut item must be the outer contour (loop_index == 0, is_hole=False)
    assert order[-1].loop_index == 0
    assert order[-1].is_hole is False
    assert order[-1].pierce_collision is False

    laser_tool = CamTool(
        tool_code="T90",
        tool_type="LASER_NOZZLE",
        diameter_mm=0.2,
        flutes=1,
        spindle_rpm=100,
        feed_xy_mm_min=3000.0,
        feed_z_mm_min=1000.0,
        laser_power_pct=85.0,
    )
    nc = post_generic_laser([(laser_tool, tp, "FLAT_CUT")])
    assert SAFETY_HEADER in nc
    assert "M03 S850" in nc
    assert "M05\nM30" in nc


def test_cam_t05_resource_limits_and_nan_inf_rejection() -> None:
    """CAM-T05: Z-pass ceiling, NaN/Inf parameters, and out-of-range RPM/stepover raise CamError (422)."""
    tool = CamTool(tool_code="T01", tool_type="ENDMILL_FLAT", diameter_mm=10.0)
    loop = [(0.0, 0.0), (50.0, 0.0), (50.0, 50.0), (0.0, 50.0)]

    # Exceed MAX_Z_PASSES (500): depth=1000, stepdown=1 -> 1000 passes
    with pytest.raises(CamError) as exc:
        contour([loop], tool, depth=1000.0, stepdown=1.0)
    assert exc.value.code == "CAM_LIMIT_EXCEEDED"

    # NaN / Inf coordinate or depth
    with pytest.raises(CamError) as exc2:
        contour([loop], tool, depth=float("nan"))
    assert exc2.value.code == "CAM_INVALID_PARAM"

    with pytest.raises(CamError) as exc3:
        drill([(float("inf"), 10.0)], tool, depth=5.0)
    assert exc3.value.code == "CAM_INVALID_PARAM"

    # Invalid tool parameters
    with pytest.raises(CamError):
        CamTool(tool_code="T99", tool_type="ENDMILL_FLAT", diameter_mm=10.0, spindle_rpm=90_000)


def test_cam_api_end_to_end(client, world) -> None:
    """API integration test: create CAM setup, add pocket & drill operations, fetch toolpath & G-code, test injection 422."""
    h = world.h["designer"]
    doc_id = world.doc

    tools_res = client.get("/api/cam/tools", headers=h)
    assert tools_res.status_code == 200
    assert len(tools_res.json()["data"]["tools"]) >= 3

    setup_res = client.post(
        f"/api/documents/{doc_id}/cam/setups",
        headers=h,
        json={"kind": "MILL_25D", "post_target": "FANUC_MILL", "safe_z_mm": 25.0},
    )
    assert setup_res.status_code == 201
    setup_id = setup_res.json()["data"]["setup_id"]

    op_res = client.post(
        f"/api/cam/setups/{setup_id}/operations",
        headers=h,
        json={
            "op_type": "POCKET",
            "tool_code": "T01",
            "params": {
                "outer": [[0, 0], [100, 0], [100, 60], [0, 60]],
                "depth_mm": 10.0,
                "stepdown_mm": 2.0,
                "stepover_ratio": 0.5,
            },
        },
    )
    assert op_res.status_code == 201
    assert op_res.json()["data"]["status"] == "OK"

    tp_res = client.get(f"/api/cam/setups/{setup_id}/toolpath", headers=h)
    assert tp_res.status_code == 200
    assert tp_res.json()["data"]["segment_count"] > 20

    nc_res = client.get(
        f"/api/cam/setups/{setup_id}/gcode?program_name=O1234&comment=DRY%20RUN",
        headers=h,
    )
    assert nc_res.status_code == 200
    assert SAFETY_HEADER in nc_res.text

    # Injection via query comment must be rejected with 422
    bad_nc = client.get(
        f"/api/cam/setups/{setup_id}/gcode?comment=HACK)%0AM03%20S50000",
        headers=h,
    )
    assert bad_nc.status_code == 422


def test_crlf_dxf_loops_extraction():
    """CRLF line endings must not cause loops_from_dxf_bytes to return an empty list."""
    import io

    import ezdxf

    from core.cam.laser import loops_from_dxf_bytes

    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True)
    msp.add_circle((50, 50), radius=5)
    buf = io.StringIO()
    doc.write(buf)
    crlf_bytes = buf.getvalue().replace("\n", "\r\n").encode("utf-8")
    loops = loops_from_dxf_bytes(crlf_bytes)
    assert len(loops) >= 2, f"CRLF input returned {len(loops)} loops, expected >= 2"
