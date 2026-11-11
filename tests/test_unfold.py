"""plan §23 S1: the general unfolder against the analytic flat pattern (§23.5 regression)."""

import math

import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

from core.geometry import sheetmetal as sm
from core.geometry.errors import GeomError
from core.geometry.unfold import unfold
from tests.test_sheetmetal import fl, rect, spec


def same_polygon(a, b, tol=1e-6):
    return len(a) == len(b) and max(min(math.dist(p, q) for q in b) for p in a) <= tol


CASES = {
    "bracket with hole": (
        rect(100, 50) + [{"type": "CIRCLE", "center": [50, 25], "radius": 5}],
        [fl(0, 30)],
        {"t": 2, "r": 2, "k": 0.44},
    ),
    "channel": (rect(200, 60), [fl(0, 25), fl(2, 25)], {"t": 1.6, "r": 1.6, "k": 0.44}),
    "box with insets": (
        rect(120, 80),
        [fl(e, 20, inset_start=1.5, inset_end=1.5) for e in range(4)],
        {"t": 1.2, "r": 1, "k": 0.4},
    ),
    "down 45 degrees": (rect(80, 40), [fl(1, 15, angle=45, up=False)], {"t": 3, "r": 4, "k": 0.5}),
    "obtuse up, square hole": (
        rect(60, 60) + rect(10, 10, 25, 25),
        [fl(3, 12, angle=135)],
        {"t": 2, "r": 3, "k": 0.33},
    ),
}


@pytest.mark.parametrize("name", list(CASES))
def test_unfolded_blank_matches_the_analytic_layout(name):
    geoms, flanges, kw = CASES[name]
    s = spec(geoms, flanges, **kw)
    a, u = sm.flat_analytic(s), sm.flat(s)  # flat() is the unfolder now
    assert same_polygon([tuple(p) for p in a["outline"]], [tuple(p) for p in u["outline"]])
    assert len(a["holes"]) == len(u["holes"])
    for h in a["holes"]:
        assert any(same_polygon([tuple(p) for p in h], [tuple(q) for q in g]) for g in u["holes"])
    assert u["circles"] == a["circles"]
    for k in ("blank_area_mm2", "sheet_cut_length_mm", "bend_length_mm"):
        assert u["metrics"][k] == pytest.approx(a["metrics"][k], abs=1e-6), k
    assert u["metrics"]["bend_count"] == a["metrics"]["bend_count"]
    key = lambda b: (round(b["start"][0], 6), round(b["start"][1], 6))
    for x, y in zip(sorted(a["bends"], key=key), sorted(u["bends"], key=key), strict=True):
        assert math.dist(x["start"], y["start"]) <= 1e-6 and math.dist(x["end"], y["end"]) <= 1e-6
        for k in ("angle", "radius", "allowance", "length"):
            assert y[k] == pytest.approx(x[k], abs=1e-6), k
        assert x["up"] == y["up"]


def test_without_a_hint_the_largest_face_holds_still_and_the_blank_is_the_same():
    s = spec(rect(100, 50), [fl(0, 30), fl(2, 30, up=False)], t=2, r=2, k=0.44)
    u = unfold(sm.folded(s), s["thickness"], s["k_factor"])  # any frame, same shape
    (outer,) = u["loops"]
    assert abs(sm.area(outer)) == pytest.approx(sm.flat_analytic(s)["metrics"]["blank_area_mm2"])
    assert sorted(b["up"] for b in u["bends"]) == [False, True]


def test_k_half_folded_volume_is_blank_area_times_thickness():
    s = spec(rect(90, 70), [fl(0, 20), fl(1, 15, angle=60)], t=1.5, r=2, k=0.5)
    from core.geometry.metrics import measure

    vol = measure(sm.folded(s))["volume_mm3"]
    assert vol == pytest.approx(sm.flat(s)["metrics"]["blank_area_mm2"] * 1.5, rel=1e-6)


def test_a_custom_allowance_is_used_for_every_bend():
    s = spec(rect(50, 50), [fl(0, 10)], t=2, r=2, k=0.44)
    u = unfold(sm.folded(s), 2, 0.44, allowance=lambda th, r, t: 7.0)
    assert [b["allowance"] for b in u["bends"]] == [7.0]
    assert abs(sm.area(u["loops"][0])) == pytest.approx(50 * (50 + 7 + 10))


def test_a_body_with_no_flat_face_is_refused():
    with pytest.raises(GeomError) as e:
        unfold(BRepPrimAPI_MakeCylinder(5, 10).Shape(), 1, 0.44)
    assert e.value.code == "SHEET_UNFOLD_UNSUPPORTED"
