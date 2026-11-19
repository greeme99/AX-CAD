"""Assembly A1 tests: 7 new mate types, EDGE/VERTEX/CONE/SPHERE refs, DOF, overconstrained (ASM-T01~T04)."""

from __future__ import annotations

import math

import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeSphere
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

from core.geometry import assembly as asm
from core.geometry.refs import edge_sig


def _comp(cid: int, t=(0.0, 0.0, 0.0), r=(0.0, 0.0, 0.0), fixed: bool = False) -> dict:
    return {"id": cid, "t": list(t), "r": list(r), "fixed": fixed}


# ---------------------------------------------------------------------------
# ASM-T01: TANGENT, PERPENDICULAR, SYMMETRIC, WIDTH, LOCK
# ---------------------------------------------------------------------------


def test_asm_t01_tangent_roller_cylinder_on_flat_plate() -> None:
    """Cylinder (R=15) tangent to top face of plate (z=10): axis distance to plane == 15."""
    comps = [_comp(1, fixed=True), _comp(2, t=(0.0, 0.0, 40.0), r=(10.0, 0.0, 0.0))]
    mates = [
        {
            "type": "TANGENT",
            "a": {"component": 1, "kind": "PLANE", "p": [0.0, 0.0, 10.0], "n": [0.0, 0.0, 1.0]},
            "b": {
                "component": 2,
                "kind": "CYLINDER",
                "p": [0.0, 0.0, 0.0],
                "n": [1.0, 0.0, 0.0],
                "radius": 15.0,
            },
            "flip": False,
        }
    ]
    res = asm.solve(comps, mates)
    assert res["mates"][0]["status"] == "OK"
    assert res["mates"][0]["residual"] <= asm.SOLVED_TOL
    # Cylinder axis z must sit at 10 + 15 = 25 mm
    assert res["placements"][2]["t"][2] == pytest.approx(25.0, abs=1e-6)


def test_asm_t01_perpendicular_symmetric_width_and_lock() -> None:
    """PERPENDICULAR, SYMMETRIC, WIDTH, and LOCK mates all converge <= 1e-7."""
    # 1) PERPENDICULAR
    comps = [_comp(1, fixed=True), _comp(2, r=(0.0, 35.0, 0.0))]
    res = asm.solve(
        comps,
        [
            {
                "type": "PERPENDICULAR",
                "a": {"component": 1, "kind": "PLANE", "p": [0, 0, 0], "n": [0, 0, 1]},
                "b": {"component": 2, "kind": "PLANE", "p": [0, 0, 0], "n": [0, 0, 1]},
            }
        ],
    )
    assert res["mates"][0]["status"] == "OK"
    assert res["mates"][0]["residual"] <= asm.SOLVED_TOL

    # 2) SYMMETRIC (two blocks symmetric about X=0 plane on component 1)
    comps_sym = [
        _comp(1, fixed=True),
        _comp(2, t=(20.0, 0.0, 0.0), fixed=True),
        _comp(3, t=(-5.0, 0.0, 0.0)),
    ]
    res_sym = asm.solve(
        comps_sym,
        [
            {
                "type": "SYMMETRIC",
                "a": {"component": 2, "kind": "PLANE", "p": [0, 0, 0], "n": [1, 0, 0]},
                "b": {"component": 3, "kind": "PLANE", "p": [0, 0, 0], "n": [-1, 0, 0]},
                "extra": {
                    "plane": {"component": 1, "kind": "PLANE", "p": [0, 0, 0], "n": [1, 0, 0]}
                },
            }
        ],
    )
    assert res_sym["mates"][0]["status"] == "OK"
    assert res_sym["placements"][3]["t"][0] == pytest.approx(-20.0, abs=1e-6)

    # 3) WIDTH (center block 2 of width 20 inside slot of width 40 on block 1)
    comps_w = [_comp(1, fixed=True), _comp(2, t=(15.0, 0.0, 0.0))]
    res_w = asm.solve(
        comps_w,
        [
            {
                "type": "WIDTH",
                "a": {"component": 1, "kind": "PLANE", "p": [-20, 0, 0], "n": [1, 0, 0]},
                "b": {"component": 2, "kind": "PLANE", "p": [-10, 0, 0], "n": [1, 0, 0]},
                "extra": {
                    "a2": {"component": 1, "kind": "PLANE", "p": [20, 0, 0], "n": [-1, 0, 0]},
                    "b2": {"component": 2, "kind": "PLANE", "p": [10, 0, 0], "n": [-1, 0, 0]},
                },
            }
        ],
    )
    assert res_w["mates"][0]["status"] == "OK"
    assert res_w["placements"][2]["t"][0] == pytest.approx(0.0, abs=1e-6)

    # 4) LOCK (relative transform [10, 20, 30] preserved with 0 DOF)
    comps_l = [_comp(1, t=(5.0, 0.0, 0.0), fixed=True), _comp(2, t=(0.0, 0.0, 0.0))]
    res_l = asm.solve(
        comps_l,
        [
            {
                "type": "LOCK",
                "a": {"component": 1, "kind": "PLANE", "p": [0, 0, 0], "n": [0, 0, 1]},
                "b": {"component": 2, "kind": "PLANE", "p": [0, 0, 0], "n": [0, 0, 1]},
                "extra": {"rel_t": [10.0, 20.0, 30.0], "rel_r": [0.0, 0.0, 0.0]},
            }
        ],
    )
    assert res_l["mates"][0]["status"] == "OK"
    assert res_l["placements"][2]["t"] == pytest.approx([15.0, 20.0, 30.0], abs=1e-6)
    assert res_l["dof_by_component"][2] == 0


# ---------------------------------------------------------------------------
# ASM-T02: LIMIT_DISTANCE & LIMIT_ANGLE
# ---------------------------------------------------------------------------


def test_asm_t02_limit_distance_slider_clamps_bounds_and_keeps_dof_inside() -> None:
    """Slider [10, 50] stays at 25 inside range, clamps to 10 below min, clamps to 50 above max."""
    mate = {
        "type": "LIMIT_DISTANCE",
        "a": {"component": 1, "kind": "PLANE", "p": [0, 0, 0], "n": [0, 0, 1]},
        "b": {"component": 2, "kind": "PLANE", "p": [0, 0, 0], "n": [0, 0, -1]},
        "value_min": 10.0,
        "value_max": 50.0,
    }
    # Inside range: stays at 25
    r_in = asm.solve([_comp(1, fixed=True), _comp(2, t=(0, 0, 25))], [mate])
    assert r_in["mates"][0]["status"] == "OK"
    assert r_in["placements"][2]["t"][2] == pytest.approx(25.0, abs=1e-6)
    assert r_in["dof_by_component"][2] >= 1

    # Below min: clamps to 10
    r_lo = asm.solve([_comp(1, fixed=True), _comp(2, t=(0, 0, 2))], [mate])
    assert r_lo["mates"][0]["status"] == "OK"
    assert r_lo["placements"][2]["t"][2] == pytest.approx(10.0, abs=1e-6)

    # Above max: clamps to 50
    r_hi = asm.solve([_comp(1, fixed=True), _comp(2, t=(0, 0, 80))], [mate])
    assert r_hi["mates"][0]["status"] == "OK"
    assert r_hi["placements"][2]["t"][2] == pytest.approx(50.0, abs=1e-5)


def test_asm_t02_limit_angle_clamps_to_bound() -> None:
    """LIMIT_ANGLE [15, 60] clamps a 90-deg angle back to 60 deg."""
    mate = {
        "type": "LIMIT_ANGLE",
        "a": {"component": 1, "kind": "PLANE", "p": [0, 0, 0], "n": [0, 0, 1]},
        "b": {"component": 2, "kind": "PLANE", "p": [0, 0, 0], "n": [0, 0, 1]},
        "value_min": 15.0,
        "value_max": 60.0,
    }
    res = asm.solve([_comp(1, fixed=True), _comp(2, r=(90.0, 0.0, 0.0))], [mate])
    assert res["mates"][0]["status"] == "OK"
    nb = asm.rot(res["placements"][2]["r"]) @ [0.0, 0.0, 1.0]
    ang = math.degrees(math.acos(max(-1.0, min(1.0, float(nb[2])))))
    assert ang == pytest.approx(60.0, abs=1e-4)


# ---------------------------------------------------------------------------
# ASM-T03: EDGE (CIRCLE/LINE), VERTEX (POINT), SPHERE refs
# ---------------------------------------------------------------------------


def test_asm_t03_circle_edge_concentric_and_vertex_sphere_refs() -> None:
    """Circle edge ref resolves identically to cylindrical axis; vertex & sphere refs solve."""
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    cyl = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(10, 20, 0), gp_Dir(0, 0, 1)), 8.0, 25.0).Shape()
    ex = TopExp_Explorer(cyl, TopAbs_EDGE)
    circ_sig = None
    while ex.More():
        sig = edge_sig(TopoDS.Edge(ex.Current()))
        if sig["kind"] == "CIRCLE":
            circ_sig = sig
            break
        ex.Next()
    assert circ_sig is not None
    g_edge = asm.ref_geom(cyl, circ_sig, "EDGE")
    assert g_edge["kind"] == "CIRCLE"
    assert g_edge["p"][:2] == pytest.approx([10.0, 20.0], abs=1e-6)

    # Concentric between circle edge (comp 1 fixed) and cylinder (comp 2 free)
    res = asm.solve(
        [_comp(1, fixed=True), _comp(2, t=(50.0, -30.0, 5.0))],
        [
            {
                "type": "CONCENTRIC",
                "a": {**g_edge, "component": 1},
                "b": {
                    "component": 2,
                    "kind": "CYLINDER",
                    "p": [0, 0, 0],
                    "n": [0, 0, 1],
                    "radius": 8.0,
                },
            }
        ],
    )
    assert res["mates"][0]["status"] == "OK"
    assert res["placements"][2]["t"][:2] == pytest.approx([10.0, 20.0], abs=1e-6)

    # Vertex ref on box + sphere tangent
    box = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 20.0, 20.0, 10.0).Shape()
    gv = asm.ref_geom(box, {"point": [20.0, 20.0, 10.0]}, "VERTEX")
    assert gv == {"kind": "POINT", "p": pytest.approx([20.0, 20.0, 10.0])}

    sph = BRepPrimAPI_MakeSphere(gp_Pnt(0, 0, 0), 12.0).Shape()
    from OCP.TopAbs import TopAbs_FACE

    from core.geometry.refs import face_sig

    fex = TopExp_Explorer(sph, TopAbs_FACE)
    gs = asm.ref_geom(sph, face_sig(TopoDS.Face(fex.Current())), "FACE")
    assert gs["kind"] == "SPHERE" and gs["radius"] == pytest.approx(12.0)


# ---------------------------------------------------------------------------
# ASM-T04: Overconstrained diagnostics, suppression & per-component DOF
# ---------------------------------------------------------------------------


def test_asm_t04_overconstrained_diagnostics_and_suppression() -> None:
    """Conflicting DISTANCE mates flag overconstrained + conflicting_mate_ids; suppressing fixes it."""
    m1 = {
        "id": 101,
        "type": "DISTANCE",
        "a": {"component": 1, "kind": "PLANE", "p": [0, 0, 0], "n": [0, 0, 1]},
        "b": {"component": 2, "kind": "PLANE", "p": [0, 0, 0], "n": [0, 0, -1]},
        "value": 10.0,
    }
    m2 = {
        "id": 102,
        "type": "DISTANCE",
        "a": {"component": 1, "kind": "PLANE", "p": [0, 0, 0], "n": [0, 0, 1]},
        "b": {"component": 2, "kind": "PLANE", "p": [0, 0, 0], "n": [0, 0, -1]},
        "value": 25.0,
    }
    res_bad = asm.solve([_comp(1, fixed=True), _comp(2, t=(0, 0, 15))], [m1, m2])
    assert res_bad["overconstrained"] is True
    assert set(res_bad["conflicting_mate_ids"]) == {101, 102}

    # Suppressing mate 102 resolves the conflict cleanly
    res_ok = asm.solve(
        [_comp(1, fixed=True), _comp(2, t=(0, 0, 15))], [m1, {**m2, "suppressed": True}]
    )
    assert res_ok["overconstrained"] is False
    assert res_ok["conflicting_mate_ids"] == []
    assert res_ok["mates"][0]["status"] == "OK"
    assert res_ok["mates"][1]["status"] == "SUPPRESSED"
    assert res_ok["placements"][2]["t"][2] == pytest.approx(10.0, abs=1e-6)
    assert res_ok["dof_by_component"][1] == 0
    assert res_ok["dof_by_component"][2] == 3  # planar distance leaves Tx, Ty, Rz
