"""Assembly A3 tests: interference/contact/clearance classification, ignored pairs, drag & explode (ASM-T07~T08)."""

from __future__ import annotations

import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Pnt

from core.geometry import assembly as asm
from core.geometry.serialize import to_brep


def _box_brep(w: float = 10.0, d: float = 10.0, h: float = 10.0) -> bytes:
    return to_brep(BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), w, d, h).Shape())


def test_asm_t07_clearance_contact_interference_and_ignored_pairs() -> None:
    """0.3mm gap (<0.5) -> near; 0mm touching -> contact (NOT clash); overlap -> clash; ignored excluded."""
    parts = {"b": _box_brep(10.0, 10.0, 10.0)}
    comps = [
        {"id": 1, "part": "b", "t": [0.0, 0.0, 0.0], "r": [0.0, 0.0, 0.0]},
        # Comp 2 touches Comp 1 at x=10 (CONTACT, not clash)
        {"id": 2, "part": "b", "t": [10.0, 0.0, 0.0], "r": [0.0, 0.0, 0.0]},
        # Comp 3 is 0.3 mm away from Comp 2 at x=20.3 (CLEARANCE violation for threshold 0.5)
        {"id": 3, "part": "b", "t": [20.3, 0.0, 0.0], "r": [0.0, 0.0, 0.0]},
        # Comp 4 overlaps Comp 3 by 2 mm at x=28.3 (INTERFERENCE clash)
        {"id": 4, "part": "b", "t": [28.3, 0.0, 0.0], "r": [0.0, 0.0, 0.0]},
    ]
    res = asm.interference_job(parts, comps, clearance=0.5)
    assert [(c["a"], c["b"]) for c in res["clashes"]] == [(3, 4)]
    assert [(c["a"], c["b"]) for c in res["contacts"]] == [(1, 2)]
    clr = [n for n in res["near"] if n["kind"] == "CLEARANCE"]
    assert len(clr) == 1
    assert (clr[0]["a"], clr[0]["b"]) == (2, 3)
    assert clr[0]["distance_mm"] == pytest.approx(0.3, abs=1e-6)

    # Ignoring pair (3, 4) removes the clash cleanly
    res_ign = asm.interference_job(parts, comps, clearance=0.5, ignored_pairs=[(3, 4)])
    assert res_ign["clashes"] == []
    assert len([n for n in res_ign["near"] if n["kind"] == "CLEARANCE"]) == 1


def test_asm_t08_explode_3_steps_and_dof_drag_with_limit() -> None:
    """3-step explode computes exact cumulative offsets without mutating base placements; drag respects limits."""
    base = {
        1: {"t": [0.0, 0.0, 0.0], "r": [0.0, 0.0, 0.0]},
        2: {"t": [0.0, 0.0, 10.0], "r": [0.0, 0.0, 0.0]},
        3: {"t": [0.0, 0.0, 20.0], "r": [0.0, 0.0, 0.0]},
    }
    steps = [
        {"step_no": 1, "component_ids": [2, 3], "delta": [0.0, 0.0, 30.0]},
        {"step_no": 2, "component_ids": [3], "delta": [0.0, 0.0, 40.0]},
        {"step_no": 3, "component_ids": [3], "delta": [15.0, 0.0, 0.0]},
    ]
    exp = asm.apply_explode(base, steps)
    assert exp[1]["t"] == [0.0, 0.0, 0.0]
    assert exp[2]["t"] == [0.0, 0.0, 40.0]
    assert exp[3]["t"] == [15.0, 0.0, 90.0]
    # Original placements remain untouched on collapse
    assert base[2]["t"] == [0.0, 0.0, 10.0]
    assert base[3]["t"] == [0.0, 0.0, 20.0]

    # DOF drag along a LIMIT_DISTANCE [10, 50] slider: dragging +100 clamps to 50
    comps = [
        {"id": 1, "t": [0.0, 0.0, 0.0], "r": [0.0, 0.0, 0.0], "fixed": True},
        {"id": 2, "t": [0.0, 0.0, 20.0], "r": [0.0, 0.0, 0.0], "fixed": False},
    ]
    mates = [
        {
            "type": "LIMIT_DISTANCE",
            "a": {"component": 1, "kind": "PLANE", "p": [0, 0, 0], "n": [0, 0, 1]},
            "b": {"component": 2, "kind": "PLANE", "p": [0, 0, 0], "n": [0, 0, -1]},
            "value_min": 10.0,
            "value_max": 50.0,
        }
    ]
    dragged = asm.drag_solve(comps, mates, component_id=2, target_delta_t=[0.0, 0.0, 100.0])
    assert dragged["mates"][0]["status"] == "OK"
    assert dragged["placements"][2]["t"][2] == pytest.approx(50.0, abs=1e-5)
