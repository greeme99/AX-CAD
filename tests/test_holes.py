"""Track 8 P2-4: hole wizard table (KS B 0201 / ISO 262, KS B 1007 / ISO 273) and hole kernel."""

import math

import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

from core.geometry import dressup as d
from core.geometry import holes_std as h
from core.geometry.metrics import measure
from core.geometry.serialize import to_brep

REL = 1e-6


def box():
    return BRepPrimAPI_MakeBox(40, 30, 10).Shape()


def vol(s):
    return measure(s)["volume_mm3"]


@pytest.mark.parametrize(
    "spec,drill,label",
    [
        ({"size": "M8", "kind": "TAPPED"}, 6.75, "M8"),
        ({"size": "M8", "kind": "TAPPED", "pitch": 1.0}, 7.0, "M8x1"),
        ({"size": "M1.6", "kind": "TAPPED"}, 1.25, "M1.6"),
        ({"size": "M64", "kind": "TAPPED", "pitch": 4.0}, 60.0, "M64x4"),
    ],
)
def test_tap_drills(spec, drill, label):
    r = h.resolve(spec)
    assert r["diameter"] == drill and r["thread"]["label"] == label


def test_clearance_and_heads():
    assert [
        h.resolve({"size": "M8", "kind": "CLEARANCE", "fit": f})["diameter"] for f in h.FITS
    ] == [
        8.4,
        9.0,
        10.0,
    ]
    cb = h.resolve({"size": "M8", "kind": "CLEARANCE", "head": "COUNTERBORE"})
    assert (
        cb["head"] == {"kind": "COUNTERBORE", "diameter": 14.0, "depth": 8.6} and "thread" not in cb
    )
    cs = h.resolve({"size": "M6", "kind": "CLEARANCE", "head": "COUNTERSINK"})
    assert cs["head"]["diameter"] == 13.44


@pytest.mark.parametrize(
    "spec",
    [
        {"size": "M7", "kind": "TAPPED"},
        {"size": "M6", "kind": "TAPPED", "pitch": 0.75},  # M6 has no fine pitch in the table
        {"size": "M2", "kind": "CLEARANCE", "head": "COUNTERBORE"},  # no standard counterbore
        {"size": "M8", "kind": "CLEARANCE", "head": "COUNTERSINK", "head_diameter": 5},
    ],
)
def test_bad_specs(spec):
    with pytest.raises(h.SpecError):
        h.resolve(spec)


def test_counterbore_and_countersink_volumes():
    cb, _, _ = d.holes(
        box(), [(20, 15)], 9, None, head={"kind": "COUNTERBORE", "diameter": 14, "depth": 4}
    )
    assert vol(cb) == pytest.approx(12000 - math.pi * (4.5**2 * 6 + 7**2 * 4), rel=REL)
    cs, _, _ = d.holes(box(), [(20, 15)], 6, None, head={"kind": "COUNTERSINK", "diameter": 12})
    cone = math.pi * 3 / 3 * (36 + 18 + 9)  # frustum r 6 -> 3, 3 deep (90 deg)
    assert vol(cs) == pytest.approx(12000 - cone - math.pi * 9 * 7, rel=REL)


def test_holes_on_a_side_plane():
    """Drilled into the +X wall: positions are (y, z) on the plane, drilling goes -X."""
    side = {"origin": [40, 0, 0], "normal": [1, 0, 0], "xdir": [0, 1, 0]}
    res, centers, axis = d.holes(box(), [(15, 5)], 4, 12, side)
    assert centers == [[40.0, 15.0, 5.0]] and axis == [-1.0, 0.0, 0.0]
    assert vol(res) == pytest.approx(12000 - math.pi * 4 * 12, rel=REL)


def test_hole_job_metrics_carry_the_thread():
    p = {
        "positions": [[10, 10], [30, 20]],
        "depth": 8,
        "spec": {"size": "M6", "kind": "TAPPED", "thread_depth": 6},
    }
    _, m = d.dressup_job("HOLE", to_brep(box()), p)
    assert m["volume_mm3"] == pytest.approx(12000 - 2 * math.pi * 2.5**2 * 8, rel=REL)
    holes = m["holes"]
    assert holes["count"] == 2 and holes["diameter"] == 5.0 and holes["axis"] == [0.0, 0.0, -1.0]
    assert holes["thread"] == {"label": "M6", "major": 6, "pitch": 1.0, "fine": False, "length": 6}
    assert holes["centers"] == [[10.0, 10.0, 10.0], [30.0, 20.0, 10.0]]
