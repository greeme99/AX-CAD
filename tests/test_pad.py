"""Track 8 P2-2: Pad/Pocket length modes, taper, up to face; primitive solids."""

import math

import pytest

from core.geometry.errors import GeomError
from core.geometry.features import extrude_job
from core.geometry.metrics import measure
from core.geometry.pad import pocket_job, primitive, solid

RECT = [
    {
        "type": "LWPOLYLINE",
        "points": [[0, 0, 0], [40, 0, 0], [40, 20, 0], [0, 20, 0]],
        "closed": True,
    }
]
XZ = {"origin": [0, 0, 0], "normal": [0, -1, 0], "xdir": [1, 0, 0]}
TOP = {"origin": [0, 0, 10], "normal": [0, 0, 1], "xdir": [1, 0, 0]}
HOLE = [{"type": "CIRCLE", "center": [20, 10], "radius": 4}]


def m(shape):
    x = measure(shape)
    return x["volume_mm3"], x["bbox"]["min"], x["bbox"]["max"]


@pytest.mark.parametrize(
    ("p", "vol", "zmin", "zmax"),
    [
        ({"distance": 10, "direction": "+Z"}, 8000, 0, 10),
        ({"distance": 10, "direction": "+Z", "mode": "SYMMETRIC"}, 8000, -5, 5),
        ({"distance": 10, "distance2": 4, "direction": "-Z", "mode": "TWO_SIDED"}, 11200, -10, 4),
        (
            {
                "direction": "+Z",
                "mode": "UP_TO_FACE",
                "up_to": {"origin": [0, 0, 15], "normal": [0, 0, 1]},
            },
            12000,
            0,
            15,
        ),
    ],
)
def test_length_modes(p, vol, zmin, zmax):
    v, lo, hi = m(solid(RECT, p, None))
    assert v == pytest.approx(vol, rel=1e-9)
    assert (lo[2], hi[2]) == pytest.approx((zmin, zmax), abs=1e-9)


def test_taper_is_exact_and_keeps_holes():
    t = math.tan(math.radians(5))
    # cross-section (40 - 2s)(20 - 2s), s = z tan: integrate over 0..10
    want = 8000 - 60 * 100 * t + 4 / 3 * 1000 * t * t
    assert m(solid(RECT, {"distance": 10, "direction": "+Z", "taper_deg": 5}, None))[
        0
    ] == pytest.approx(want, rel=1e-9)
    holed = solid(RECT + HOLE, {"distance": 10, "direction": "+Z", "taper_deg": 5}, TOP)
    r1 = 4 + 10 * t  # the hole widens as the outline narrows
    assert m(holed)[0] == pytest.approx(want - math.pi * 10 / 3 * (16 + r1 * r1 + 4 * r1), rel=1e-9)


def test_up_to_tilted_face_and_sketch_plane():
    a = 0.3
    tilt = {"origin": [0, 0, 10], "normal": [-math.sin(a), 0, math.cos(a)]}
    want = 8000 + 20 * 0.5 * math.tan(a) * 40 * 40  # a wedge on top of the 40 x 20 x 10 block
    assert m(solid(RECT, {"direction": "+Z", "mode": "UP_TO_FACE", "up_to": tilt}, None))[
        0
    ] == pytest.approx(want, rel=1e-9)
    v, lo, hi = m(
        solid(
            RECT,
            {
                "direction": "+Z",
                "mode": "UP_TO_FACE",
                "up_to": {"origin": [0, -7, 0], "normal": [0, 1, 0]},
            },
            XZ,
        )
    )
    assert (
        v == pytest.approx(5600) and lo[1] == pytest.approx(-7) and hi == pytest.approx([40, 0, 20])
    )


@pytest.mark.parametrize(
    "p",
    [
        {
            "direction": "+Z",
            "mode": "UP_TO_FACE",
            "up_to": {"origin": [0, 0, -5], "normal": [0, 0, 1]},
        },  # behind
        {
            "direction": "+Z",
            "mode": "UP_TO_FACE",
            "up_to": {"origin": [0, 0, 5], "normal": [1, 0, 0]},
        },  # parallel
        {"direction": "+Z", "mode": "UP_TO_FACE"},  # no face
        {"direction": "+Z", "distance": 10, "taper_deg": 70},
        {"direction": "+Z", "distance": 10, "mode": "TWO_SIDED"},  # no second length
    ],
)
def test_invalid(p):
    with pytest.raises(GeomError) as e:
        solid(RECT, p, None)
    assert e.value.code == "GEOM_INVALID_PARAM"


def test_pocket_through_all_and_depth():
    box = extrude_job(RECT, 10, "+Z")[0]
    through = pocket_job(HOLE, {"direction": "-Z", "mode": "THROUGH_ALL"}, TOP, box)[1]
    assert through["volume_mm3"] == pytest.approx(8000 - math.pi * 16 * 10, rel=1e-9)
    blind = pocket_job(HOLE, {"direction": "-Z", "distance": 3}, TOP, box)[1]
    assert blind["volume_mm3"] == pytest.approx(8000 - math.pi * 16 * 3, rel=1e-9)
    with pytest.raises(GeomError) as e:  # pocketing away from the body cuts nothing
        pocket_job(HOLE, {"direction": "+Z", "distance": 3}, {**TOP, "origin": [0, 0, 20]}, box)
    assert e.value.code == "GEOM_EMPTY_RESULT"


@pytest.mark.parametrize(
    ("p", "vol", "lo", "hi"),
    [
        ({"kind": "BOX", "length": 10, "width": 20, "height": 30}, 6000, [1, 2, 3], [11, 22, 33]),
        (
            {"kind": "CYLINDER", "radius": 5, "height": 10, "axis": "X"},
            math.pi * 250,
            [1, -3, -2],
            [11, 7, 8],
        ),
        (
            {"kind": "CONE", "radius": 5, "radius2": 0, "height": 9},
            math.pi * 25 * 3,
            [-4, -3, 3],
            [6, 7, 12],
        ),
        ({"kind": "SPHERE", "radius": 5}, 4 / 3 * math.pi * 125, [-4, -3, -2], [6, 7, 8]),
        (
            {"kind": "TORUS", "radius": 10, "radius2": 2, "axis": "Y"},
            2 * math.pi**2 * 10 * 4,
            [-11, 0, -9],
            [13, 4, 15],
        ),
    ],
)
def test_primitives(p, vol, lo, hi):
    v, a, b = m(primitive({"origin": [1, 2, 3], **p}))
    assert v == pytest.approx(vol, rel=1e-9)
    assert a == pytest.approx(lo, abs=1e-6) and b == pytest.approx(hi, abs=1e-6)
