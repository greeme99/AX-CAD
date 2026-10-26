"""Track 8 P2-3: sweep, loft, draft and thickness kernels (volumes against formulas)."""

import math

import pytest

from core.geometry.dressup import draft, shell
from core.geometry.errors import GeomError
from core.geometry.features import extrude
from core.geometry.metrics import measure
from core.geometry.sweep import loft, sweep

XY = {"origin": [0, 0, 0], "normal": [0, 0, 1], "xdir": [1, 0, 0]}
YZ = {
    "origin": [0, 0, 0],
    "normal": [1, 0, 0],
    "xdir": [0, 1, 0],
}  # faces +X: profile across an X path
RECT = [
    {
        "type": "LWPOLYLINE",
        "points": [[0, 0, 0], [40, 0, 0], [40, 20, 0], [0, 20, 0]],
        "closed": True,
    }
]


def vol(shape):
    return measure(shape)["volume_mm3"]


def line(a, b):
    return {"type": "LINE", "start": list(a), "end": list(b)}


def circle(c, r):
    return {"type": "CIRCLE", "center": list(c), "radius": r}


def test_sweep_along_lines_and_arcs():
    tube = [circle((0, 0), 2)]
    assert vol(sweep(tube, YZ, [line((0, 0), (30, 0))], XY)) == pytest.approx(
        math.pi * 4 * 30, rel=1e-9
    )
    # a line then a quarter arc of radius 10 (chained in either drawing order)
    arc = {"type": "ARC", "center": [30, 10], "radius": 10, "start_angle": 270, "end_angle": 0}
    path = [arc, line((30, 0), (0, 0))]
    want = math.pi * 4 * (30 + math.pi / 2 * 10)  # Pappus: area x centroid path length
    assert vol(sweep(tube, YZ, path, XY)) == pytest.approx(want, rel=1e-6)
    # a hole in the profile stays a hole along the sweep
    pipe = [circle((0, 0), 3), circle((0, 0), 2)]
    assert vol(sweep(pipe, YZ, [line((0, 0), (30, 0))], XY)) == pytest.approx(
        math.pi * 5 * 30, rel=1e-9
    )
    # around a closed circle: a torus
    ring = sweep(
        [circle((10, 0), 2)],
        {"origin": [0, 0, 0], "normal": [0, -1, 0], "xdir": [1, 0, 0]},
        [circle((0, 0), 10)],
        XY,
    )
    assert vol(ring) == pytest.approx(2 * math.pi**2 * 10 * 4, rel=1e-6)


def test_sweep_path_must_be_one_chain():
    tube = [circle((0, 0), 2)]
    for path in (
        [line((0, 0), (10, 0)), line((20, 0), (30, 0))],
        [line((0, 0), (10, 0)), line((10, 0), (10, 5)), line((10, 0), (10, -5))],
    ):
        with pytest.raises(GeomError) as e:
            sweep(tube, YZ, path, XY)
        assert e.value.code == "GEOM_MULTIPLE_PROFILES"


def test_loft_sections():
    top = {"origin": [0, 0, 10], "normal": [0, 0, 1], "xdir": [1, 0, 0]}
    assert vol(loft([(RECT, XY), (RECT, top)])) == pytest.approx(8000, rel=1e-9)
    # square 40 to circle r 8 over 30 mm, ruled or smooth (two sections: the same solid)
    up = {"origin": [0, 0, 30], "normal": [0, 0, 1], "xdir": [1, 0, 0]}
    smooth = vol(loft([(RECT, XY), ([circle((20, 10), 8)], up)]))
    assert smooth == pytest.approx(
        vol(loft([(RECT, XY), ([circle((20, 10), 8)], up)], ruled=True)), rel=1e-6
    )
    assert math.pi * 64 * 30 < smooth < 800 * 30
    # three circles: ruled = two frustums; smooth bulges past them
    mid = {"origin": [0, 0, 10], "normal": [0, 0, 1], "xdir": [1, 0, 0]}
    end = {"origin": [0, 0, 20], "normal": [0, 0, 1], "xdir": [1, 0, 0]}
    secs = [([circle((0, 0), 5)], XY), ([circle((0, 0), 10)], mid), ([circle((0, 0), 5)], end)]
    frustum = math.pi * 10 / 3 * (25 + 100 + 50)
    assert vol(loft(secs, ruled=True)) == pytest.approx(2 * frustum, rel=1e-6)
    assert vol(loft(secs)) > 2 * frustum
    with pytest.raises(GeomError) as e:
        loft([(RECT, XY)])
    assert e.value.code == "GEOM_INVALID_PARAM"


def test_draft_sides():
    t = math.tan(math.radians(5))
    box = extrude(RECT, 10)
    want = 8000 - 60 * 100 * t + 4 / 3 * 1000 * t * t  # sides narrow upward from the bottom
    assert vol(draft(box, 5, "+Z", "BOTTOM")) == pytest.approx(want, rel=1e-9)
    assert vol(draft(box, 5, "-Z", "TOP")) == pytest.approx(want, rel=1e-9)  # mirror image
    cyl = extrude([circle((0, 0), 10)], 10)
    r1 = 10 - 10 * t
    assert vol(draft(cyl, 5, "+Z", "BOTTOM")) == pytest.approx(
        math.pi * 10 / 3 * (100 + r1 * r1 + 10 * r1), rel=1e-9
    )
    with pytest.raises(GeomError):
        draft(box, 60, "+Z", "BOTTOM")


@pytest.mark.parametrize(
    ("kw", "want"),
    [
        ({"open_face": "TOP"}, 8000 - 36 * 16 * 8),
        ({"open_face": "BOTH"}, 8000 - 36 * 16 * 10),
        ({"open_face": "NONE"}, 8000 - 36 * 16 * 6),
        ({"open_face": "NONE", "outward": True, "join": "INTERSECTION"}, 44 * 24 * 14 - 8000),
        ({"open_face": "BOTH", "outward": True, "join": "INTERSECTION"}, 44 * 24 * 10 - 8000),
    ],
)
def test_thickness(kw, want):
    assert vol(shell(extrude(RECT, 10), 2, **kw)) == pytest.approx(want, rel=1e-9)


def test_thickness_round_corners_and_errors():
    rounded = vol(shell(extrude(RECT, 10), 2, "BOTH", outward=True))  # ARC: rounded outer edges
    assert 44 * 24 * 10 - 8000 - 4 * 4 * 10 < rounded < 44 * 24 * 10 - 8000
    with pytest.raises(GeomError):
        shell(extrude(RECT, 10), 2, "SIDE")
