"""Track 9 Q2: assembly placements, mate solver and interference (plan.md §15.3, §15.8)."""

import math
import time

import numpy as np
import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from OCP.TopAbs import TopAbs_FACE

from core.geometry import assembly as asm
from core.geometry.errors import GeomError
from core.geometry.metrics import measure
from core.geometry.refs import _items, face_sig
from core.geometry.serialize import from_brep, to_brep

TOL = 1e-6


def plane(comp, p, n):
    return {"component": comp, "kind": "PLANE", "p": list(p), "n": list(n)}


def cyl(comp, p, n):
    return {"component": comp, "kind": "CYLINDER", "p": list(p), "n": list(n)}


def comp(cid, t=(0, 0, 0), r=(0, 0, 0), fixed=False):
    return {"id": cid, "t": list(t), "r": list(r), "fixed": fixed}


@pytest.mark.parametrize(
    "r", [(0, 0, 0), (30, -20, 75), (10, 90, 0), (-45, -90, 0), (180, 0, 0), (0, 0, 179)]
)
def test_euler_round_trip(r):
    m = asm.rot(r)
    assert np.allclose(asm.rot(asm.euler(m)), m, atol=1e-12)
    assert np.allclose(m @ m.T, np.eye(3), atol=1e-12)


def test_rotation_convention():
    # Rz(90): x -> y
    assert np.allclose(asm.rot([0, 0, 90]) @ [1, 0, 0], [0, 1, 0], atol=1e-12)
    # X first, then Z: y --Rx(90)--> z --Rz(90)--> z
    assert np.allclose(asm.rot([90, 0, 90]) @ [0, 1, 0], [0, 0, 1], atol=1e-12)


def test_coincident_planes_stack_a_block_on_a_plate():
    """A 40x40x10 block sits on a fixed plate's top (z = 10): its bottom face (z = 0, normal -z)
    against the plate's top (normal +z). Start it rotated and floating."""
    comps = [comp("plate", fixed=True), comp("block", t=(5, 3, 50), r=(20, -10, 30))]
    mates = [
        {
            "type": "COINCIDENT",
            "a": plane("plate", (50, 50, 10), (0, 0, 1)),
            "b": plane("block", (20, 20, 0), (0, 0, -1)),
        },
        # block side x = 0 (normal -x) 15 mm from the plate's side x = 0 (normal -x), both facing -x
        {
            "type": "DISTANCE",
            "a": plane("plate", (0, 50, 5), (-1, 0, 0)),
            "b": plane("block", (0, 20, 5), (-1, 0, 0)),
            "value": -15,
            "flip": True,
        },
    ]
    with pytest.raises(
        GeomError
    ):  # DISTANCE flip: same-facing planes need COINCIDENT/flip semantics checked
        asm.solve(comps, [{**mates[1], "type": "CONCENTRIC"}])
    out = asm.solve(comps, mates)
    assert all(m["status"] == "OK" for m in out["mates"]), out
    p = out["placements"]["block"]
    assert p["t"][2] == pytest.approx(10, abs=TOL)
    r = asm.rot(p["r"])
    assert np.allclose(r @ [0, 0, -1], [0, 0, -1], atol=TOL)  # bottom still faces down
    assert np.allclose(r @ [-1, 0, 0], [-1, 0, 0], atol=TOL)
    assert p["t"][0] == pytest.approx(15, abs=TOL)  # 15 mm inside the plate's edge
    assert out["placements"]["plate"] == {"t": [0, 0, 0], "r": [0, 0, 0]}
    assert out["dof"] == 1  # free to slide along y


def test_concentric_shaft_in_bushing():
    comps = [comp("bush", fixed=True), comp("shaft", t=(7, -4, 2), r=(15, 25, 0))]
    mates = [
        {
            "type": "CONCENTRIC",
            "a": cyl("bush", (100, 50, 0), (0, 0, 1)),
            "b": cyl("shaft", (0, 0, 0), (0, 0, 1)),
        },
        {
            "type": "COINCIDENT",
            "a": plane("bush", (100, 50, 30), (0, 0, 1)),
            "b": plane("shaft", (0, 0, 30), (0, 0, -1)),
        },
    ]
    out = asm.solve(comps, mates)
    assert all(m["status"] == "OK" for m in out["mates"])
    p = out["placements"]["shaft"]
    r, t = asm.rot(p["r"]), np.array(p["t"])
    axis_pt = r @ [0, 0, 0] + t
    assert np.allclose(np.cross(r @ [0, 0, 1], [0, 0, 1]), 0, atol=TOL)
    assert np.allclose(axis_pt[:2], [100, 50], atol=TOL)
    # shaft shoulder (z=30, facing down) on the bushing top (z=30, facing up)
    assert (r @ [0, 0, 30] + t)[2] == pytest.approx(30, abs=TOL)
    assert out["dof"] == 1  # spin about the axis


def test_parallel_and_angle():
    comps = [comp("a", fixed=True), comp("b", r=(0, 0, 10))]
    out = asm.solve(
        comps,
        [
            {
                "type": "ANGLE",
                "a": plane("a", (0, 0, 0), (1, 0, 0)),
                "b": plane("b", (0, 0, 0), (1, 0, 0)),
                "value": 60,
            }
        ],
    )
    r = asm.rot(out["placements"]["b"]["r"])
    assert math.degrees(math.acos(np.clip((r @ [1, 0, 0]) @ [1, 0, 0], -1, 1))) == pytest.approx(
        60, abs=1e-5
    )
    out = asm.solve(
        comps,
        [
            {
                "type": "PARALLEL",
                "a": plane("a", (0, 0, 0), (0, 0, 1)),
                "b": plane("b", (0, 0, 0), (0, 1, 0)),
            }
        ],
    )
    r = asm.rot(out["placements"]["b"]["r"])
    assert np.allclose(np.cross(r @ [0, 1, 0], [0, 0, 1]), 0, atol=TOL)


def test_wrong_way_planes_are_turned_over():
    """Both tops (+z) picked: the free block is turned over onto the plate (a saddle otherwise)."""
    comps = [comp("plate", fixed=True), comp("block", t=(0, 0, 40))]
    m = {
        "type": "COINCIDENT",
        "a": plane("plate", (50, 50, 10), (0, 0, 1)),
        "b": plane("block", (20, 20, 20), (0, 0, 1)),
    }
    out = asm.solve(comps, [m])
    assert out["mates"][0]["status"] == "OK"
    p = out["placements"]["block"]
    r, t = asm.rot(p["r"]), np.array(p["t"])
    assert np.allclose(r @ [0, 0, 1], [0, 0, -1], atol=TOL)  # upside down
    assert (r @ [20, 20, 20] + t)[2] == pytest.approx(10, abs=TOL)  # its top on the plate top
    # flip: same direction wanted, already so: nothing turns
    out = asm.solve(comps, [{**m, "flip": True}])
    assert np.allclose(asm.rot(out["placements"]["block"]["r"]) @ [0, 0, 1], [0, 0, 1], atol=TOL)


def test_wrong_way_planes_free_side_a():
    """The free part is on side A (B fixed): it is the one turned over."""
    comps = [comp("block", t=(0, 0, 40), r=(0, 0, 30)), comp("plate", fixed=True)]
    m = {
        "type": "COINCIDENT",
        "a": plane("block", (20, 20, 20), (0, 0, 1)),
        "b": plane("plate", (50, 50, 10), (0, 0, 1)),
    }
    out = asm.solve(comps, [m])
    assert out["mates"][0]["status"] == "OK"
    assert np.allclose(asm.rot(out["placements"]["block"]["r"]) @ [0, 0, 1], [0, 0, -1], atol=TOL)


def test_conflicting_mates_are_reported_not_hidden():
    comps = [comp("a", fixed=True), comp("b")]
    m1 = {
        "type": "DISTANCE",
        "a": plane("a", (0, 0, 10), (0, 0, 1)),
        "b": plane("b", (0, 0, 0), (0, 0, -1)),
        "value": 5,
    }
    m2 = {**m1, "value": 8}
    out = asm.solve(comps, [m1, m2])
    assert [m["status"] for m in out["mates"]].count("FAILED") >= 1


def test_unconstrained_stays_put_and_validation():
    out = asm.solve([comp("a", fixed=True), comp("b", t=(1, 2, 3), r=(4, 5, 6))], [])
    assert out["placements"]["b"]["t"] == [1, 2, 3] and out["dof"] == 6
    assert np.allclose(out["placements"]["b"]["r"], [4, 5, 6])
    bad = [
        {
            "type": "CONCENTRIC",
            "a": plane("a", (0, 0, 0), (0, 0, 1)),
            "b": plane("b", (0, 0, 0), (0, 0, 1)),
        },
        {
            "type": "DISTANCE",
            "a": cyl("a", (0, 0, 0), (0, 0, 1)),
            "b": cyl("b", (0, 0, 0), (0, 0, 1)),
            "value": 1,
        },
        {
            "type": "COINCIDENT",
            "a": plane("a", (0, 0, 0), (0, 0, 1)),
            "b": plane("a", (0, 0, 0), (0, 0, 1)),
        },
        {
            "type": "WELD",
            "a": plane("a", (0, 0, 0), (0, 0, 1)),
            "b": plane("b", (0, 0, 0), (0, 0, 1)),
        },
        {
            "type": "COINCIDENT",
            "a": plane("a", (0, 0, 0), (0, 0, 1)),
            "b": plane("zz", (0, 0, 0), (0, 0, 1)),
        },
    ]
    for m in bad:
        with pytest.raises(GeomError) as e:
            asm.solve([comp("a", fixed=True), comp("b")], [m])
        assert e.value.code == "ASSEMBLY_MATE_INVALID"
    with pytest.raises(GeomError):
        asm.solve([comp(i) for i in range(asm.MAX_COMPONENTS + 1)], [])


def test_solver_scales_to_200_components():
    """A stack of 200 plates, each sitting on the one below (199 coincident mates)."""
    comps = [comp(0, fixed=True)] + [
        comp(i, t=(i * 0.3, -i * 0.2, i * 7.0), r=(i % 7, -(i % 5), i % 11)) for i in range(1, 200)
    ]
    mates = [
        {
            "type": "COINCIDENT",
            "a": plane(i - 1, (10, 10, 5), (0, 0, 1)),
            "b": plane(i, (10, 10, 0), (0, 0, -1)),
        }
        for i in range(1, 200)
    ]
    t0 = time.perf_counter()
    out = asm.solve(comps, mates)
    took = time.perf_counter() - t0
    assert all(m["status"] == "OK" for m in out["mates"])
    assert out["placements"][199]["t"][2] == pytest.approx(199 * 5, abs=1e-5)
    assert took < 5, took


# --- OCC: faces, interference, compound ---------------------------------------------------------


def box_brep(x, y, z):
    return to_brep(BRepPrimAPI_MakeBox(x, y, z).Shape())


def cyl_brep(r, h):
    return to_brep(BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), r, h).Shape())


def face(brep, kind, mid_z=None):
    shape = from_brep(brep)
    for f in _items(shape, TopAbs_FACE):
        s = face_sig(f)
        if s["kind"] == kind and (mid_z is None or abs(s["mid"][2] - mid_z) < 1e-9):
            return s
    raise AssertionError("face not found")


def test_face_geometry_is_outward():
    b = box_brep(40, 30, 10)
    top, bottom = asm.faces_job(b, [face(b, "PLANE", 10), face(b, "PLANE", 0)])
    assert (
        top["kind"] == "PLANE"
        and np.allclose(top["n"], [0, 0, 1])
        and top["p"][2] == pytest.approx(10)
    )
    assert np.allclose(bottom["n"], [0, 0, -1])
    c = cyl_brep(5, 20)
    side = asm.faces_job(c, [face(c, "CYLINDER")])[0]
    assert side["kind"] == "CYLINDER" and side["radius"] == pytest.approx(5)
    assert np.allclose(np.abs(side["n"]), [0, 0, 1]) and np.allclose(side["p"][:2], [0, 0])


def test_mated_from_real_faces_then_no_clash():
    """Block on plate solved from picked faces; the result touches but does not interfere."""
    plate, block = box_brep(100, 100, 10), box_brep(40, 40, 20)
    a = {**asm.faces_job(plate, [face(plate, "PLANE", 10)])[0], "component": "plate"}
    b = {**asm.faces_job(block, [face(block, "PLANE", 0)])[0], "component": "block"}
    out = asm.solve(
        [comp("plate", fixed=True), comp("block", t=(30, 30, 33), r=(3, -2, 5))],
        [{"type": "COINCIDENT", "a": a, "b": b}],
    )
    pb = out["placements"]["block"]
    parts = {"P": plate, "B": block}
    comps = [
        {"id": "plate", "part": "P", "t": [0, 0, 0], "r": [0, 0, 0]},
        {"id": "block", "part": "B", **pb},
    ]
    res = asm.interference_job(parts, comps, clearance=0.5)
    assert res["clashes"] == [] and res["pairs_checked"] == 1
    assert [n["distance_mm"] for n in res["near"]] == [pytest.approx(0, abs=1e-6)]  # touching


def test_interference_volume_matches_overlap():
    parts = {"A": box_brep(100, 100, 10), "C": cyl_brep(5, 30)}
    comps = [
        {"id": 1, "part": "A", "t": [0, 0, 0], "r": [0, 0, 0]},
        {"id": 2, "part": "A", "t": [50, 60, 4], "r": [0, 0, 0]},  # 50 x 40 x 6 overlap
        {"id": 3, "part": "C", "t": [500, 0, 0], "r": [0, 0, 0]},  # far away
        {"id": 4, "part": "C", "t": [20, 20, -10], "r": [0, 0, 0]},  # pin through plate 1: pi*25*10
    ]
    res = asm.interference_job(parts, comps)
    got = {(c["a"], c["b"]): c["volume_mm3"] for c in res["clashes"]}
    assert got[(1, 2)] == pytest.approx(50 * 40 * 6, rel=1e-9)
    assert got[(1, 4)] == pytest.approx(math.pi * 25 * 10, rel=1e-6)
    assert set(got) == {(1, 2), (1, 4)}
    assert res["pairs_checked"] == 2  # box filter skipped the far pin
    comp_shape = from_brep(asm.compound_job(parts, comps))
    total = 2 * 100 * 100 * 10 + 2 * math.pi * 25 * 30
    assert measure(comp_shape)["volume_mm3"] == pytest.approx(total, rel=1e-6)


def test_interference_200_parts_under_30_s():
    parts = {"A": box_brep(20, 20, 10)}
    comps = [
        {"id": i, "part": "A", "t": [(i % 20) * 21.0, (i // 20) * 21.0, 0], "r": [0, 0, 0]}
        for i in range(199)
    ]
    comps.append(
        {"id": 999, "part": "A", "t": [5, 5, 5], "r": [0, 0, 45]}
    )  # clashes with #0 (and maybe #1, #20)
    t0 = time.perf_counter()
    res = asm.interference_job(parts, comps)
    took = time.perf_counter() - t0
    assert 999 in {c["b"] for c in res["clashes"]} and all(
        999 in (c["a"], c["b"]) for c in res["clashes"]
    )
    assert took < 30, took


def test_diverged_solution_is_rejected(monkeypatch):
    comps = [comp("a", fixed=True), comp("b", t=(0, 0, 9e5))]
    m = {
        "type": "DISTANCE",
        "a": plane("a", (0, 0, 0), (0, 0, 1)),
        "b": plane("b", (0, 0, 0), (0, 0, -1)),
        "value": 5e6,
    }
    with pytest.raises(GeomError) as e:
        asm.solve(comps, [m])
    assert e.value.code == "ASSEMBLY_SOLVE_DIVERGED"


def test_solver_time_budget(monkeypatch):
    monkeypatch.setattr(asm, "SOLVE_BUDGET_S", 0.0)  # stops at once: unsolved mates are FAILED
    comps = [comp("a", fixed=True), comp("b", t=(0, 0, 50))]
    m = {
        "type": "COINCIDENT",
        "a": plane("a", (0, 0, 10), (0, 0, 1)),
        "b": plane("b", (0, 0, 0), (0, 0, -1)),
    }
    assert asm.solve(comps, [m])["mates"][0]["status"] == "FAILED"
