"""Track 5-3 kernel tests: machining features on a body (FreeCAD PartDesign style), exact volumes."""

import math

import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.TopAbs import TopAbs_SOLID

from core.geometry import dressup as d
from core.geometry.errors import GeomError
from core.geometry.metrics import count, measure
from core.geometry.serialize import to_brep

REL = 1e-6


def box(w=40, h=30, t=10):
    return BRepPrimAPI_MakeBox(w, h, t).Shape()


def vol(s):
    return measure(s)["volume_mm3"]


def test_edge_sets_of_a_plate():
    b = box()
    assert [len(d.edges(b, k)) for k in ("ALL", "VERTICAL", "TOP", "BOTTOM")] == [12, 4, 4, 4]


def test_fillet_and_chamfer_corner_volumes():
    r = 2
    assert vol(d.fillet(box(), r, "VERTICAL")) == pytest.approx(
        12000 - 4 * (r * r - math.pi * r * r / 4) * 10, rel=REL
    )
    assert vol(d.chamfer(box(), 2, "VERTICAL")) == pytest.approx(12000 - 4 * 2 * 10, rel=REL)
    assert vol(d.fillet(box(), 1, "TOP")) < 12000


def test_holes_through_and_blind():
    assert vol(d.holes(box(), [(10, 10), (30, 20)], 6, None)) == pytest.approx(
        12000 - 2 * math.pi * 9 * 10, rel=REL
    )
    assert vol(d.holes(box(), [(10, 10)], 6, 4)) == pytest.approx(12000 - math.pi * 9 * 4, rel=REL)


def test_patterns_and_mirror():
    lp = d.linear_pattern(box(), (50, 0, 0), 3)
    assert vol(lp) == pytest.approx(36000, rel=REL) and count(lp, TopAbs_SOLID) == 3
    pp = d.polar_pattern(box(10, 10, 10), (-20, 0), 4)
    assert vol(pp) == pytest.approx(4000, rel=REL) and count(pp, TopAbs_SOLID) == 4
    m = d.mirror(box(), "YZ", 0)  # touching at x=0: one 80 x 30 x 10 solid
    assert count(m, TopAbs_SOLID) == 1
    assert measure(m)["bbox"]["size"] == pytest.approx([80, 30, 10], abs=1e-6)


def test_shell_open_top():
    assert vol(d.shell(box(), 1, "TOP")) == pytest.approx(12000 - 38 * 28 * 9, rel=REL)


@pytest.mark.parametrize(
    "bad",
    [
        lambda: d.fillet(box(), 50, "VERTICAL"),  # radius larger than the plate
        lambda: d.holes(box(), [(100, 100)], 6, None),  # misses the body
        lambda: d.shell(box(), 20, "TOP"),  # wall thicker than the part
        lambda: d.linear_pattern(box(), (0, 0, 0), 3),
        lambda: d.fillet(box(), 1, "SIDEWAYS"),
        lambda: d.mirror(box(), "AB", 0),
        lambda: d.holes(box(), [(1, 1)] * (d.MAX_HOLES + 1), 1, None),
    ],
)
def test_failures_are_reported_not_crashing(bad):
    with pytest.raises(GeomError) as e:
        bad()
    assert e.value.code in ("GEOM_FEATURE_FAILED", "GEOM_INVALID_PARAM")


def test_worker_job_contract():
    brep, m = d.dressup_job("CHAMFER", to_brep(box()), {"distance": 2, "edges": "VERTICAL"})
    assert isinstance(brep, bytes) and m["volume_mm3"] == pytest.approx(11920, rel=REL)
    assert "solids" not in m
