"""Track 9 Q3: sheet metal — folded body, flat pattern, DXF (plan.md §15.4, §15.8)."""

import io
import math

import ezdxf
import pytest

from core.geometry import sheetmetal as sm
from core.geometry.errors import GeomError
from core.geometry.metrics import measure
from core.geometry.serialize import from_brep
from core.quote_engine.metrics2d import compute_metrics

REL = 1e-6


def rect(w, h, x=0.0, y=0.0):
    pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    return [
        {"type": "LINE", "start": list(a), "end": list(b)}
        for a, b in zip(pts, pts[1:] + pts[:1], strict=True)
    ]


def spec(geoms, flanges, t=2.0, r=2.0, k=0.5, plane=None):
    return {
        "outline": sm.outline(geoms),
        "thickness": t,
        "bend_radius": r,
        "k_factor": k,
        "flanges": flanges,
        "plane": plane,
    }


def fl(edge, length, angle=90.0, up=True, inset_start=0.0, inset_end=0.0):
    return {
        "edge": edge,
        "length": length,
        "angle": angle,
        "up": up,
        "inset_start": inset_start,
        "inset_end": inset_end,
    }


def folded_volume(s):
    """Independent of K: plate + each bend (annular sector) + straight part."""
    t, r = s["thickness"], s["bend_radius"]
    es = sm.edges(s["outline"])
    v = sm.area(s["outline"]["outer"]) * t
    for f in s["flanges"]:
        w = es[f["edge"]]["length"] - f["inset_start"] - f["inset_end"]
        v += math.radians(f["angle"]) * t * (r + t / 2) * w + f["length"] * t * w
    return v


def test_outline_edges_and_orientation():
    clockwise = rect(100, 50)[::-1]
    clockwise = [{"type": "LINE", "start": g["end"], "end": g["start"]} for g in clockwise]
    o = sm.outline(clockwise)
    assert sm.area(o["outer"]) == pytest.approx(5000)  # normalised to counter-clockwise
    es = sm.edges(o)
    assert sorted(round(e["length"]) for e in es) == [50, 50, 100, 100]
    assert es[0]["start"] == [0, 0]  # still starts at the sketch's first point


def test_bracket_l():
    s = spec(rect(100, 50), [fl(0, 30)])
    shape = from_brep(sm.sheet_job(s)[0])
    m = measure(shape)
    ba = math.pi / 2 * (2 + 0.5 * 2)
    fp = sm.flat(s)
    assert fp["metrics"]["blank_area_mm2"] == pytest.approx(5000 + (ba + 30) * 100, rel=REL)
    # K = 0.5: the neutral fibre is the middle, folded volume == flat area * T
    assert m["volume_mm3"] == pytest.approx(fp["metrics"]["blank_area_mm2"] * 2, rel=REL)
    assert m["volume_mm3"] == pytest.approx(folded_volume(s), rel=REL)
    # the flange stands up: 2 (plate) + 2 (R) ... outer face up to T + R + L above the plate bottom
    assert m["bbox"]["size"][2] == pytest.approx(2 + 2 + 30, rel=REL)
    assert m["bbox"]["min"][1] == pytest.approx(-(2 + 2), rel=REL)  # wall outside the edge y = 0
    assert fp["metrics"]["flat_bbox"]["size"] == pytest.approx([100, 50 + ba + 30], rel=REL)
    assert fp["metrics"]["bend_count"] == 1 and fp["metrics"]["bend_length_mm"] == pytest.approx(
        100
    )


def test_u_channel():
    s = spec(rect(100, 60), [fl(0, 20), fl(2, 20)], t=1.5, r=1.5)
    m = measure(from_brep(sm.sheet_job(s)[0]))
    fp = sm.flat(s)
    ba = math.pi / 2 * (1.5 + 0.75)
    assert fp["metrics"]["flat_bbox"]["size"] == pytest.approx([100, 60 + 2 * (ba + 20)], rel=REL)
    assert m["volume_mm3"] == pytest.approx(fp["metrics"]["blank_area_mm2"] * 1.5, rel=REL)
    assert m["solids"] == 1


def test_box_four_flanges_with_corner_relief():
    """All four sides up; insets of T + R keep the corners apart (no auto corner treatment)."""
    g = rect(80, 80)
    s = spec(g, [fl(i, 25, inset_start=4, inset_end=4) for i in range(4)])
    m = measure(from_brep(sm.sheet_job(s)[0]))
    fp = sm.flat(s)
    ba = math.pi / 2 * 3
    assert fp["metrics"]["blank_area_mm2"] == pytest.approx(6400 + 4 * 72 * (ba + 25), rel=REL)
    assert m["volume_mm3"] == pytest.approx(fp["metrics"]["blank_area_mm2"] * 2, rel=REL)
    assert len(fp["outline"]) == 4 * 4 + 4 - 0  # 4 corners + 4 points per strip
    assert fp["metrics"]["sheet_cut_length_mm"] == pytest.approx(
        4 * (80 - 8) + 4 * 8 + 8 * (ba + 25), rel=REL
    )


def test_down_flange_any_angle_and_k():
    s = spec(rect(60, 40), [fl(1, 15, angle=45, up=False)], t=3, r=4, k=0.33)
    m = measure(from_brep(sm.sheet_job(s)[0]))
    assert m["volume_mm3"] == pytest.approx(folded_volume(s), rel=REL)
    assert m["bbox"]["min"][2] < 0  # goes down
    fp = sm.flat(s)
    ba = math.radians(45) * (4 + 0.33 * 3)
    assert fp["bends"][0]["allowance"] == pytest.approx(ba)
    assert fp["metrics"]["flat_bbox"]["size"][0] == pytest.approx(60 + ba + 15, rel=REL)


def test_holes_and_plane():
    g = rect(100, 50) + rect(10, 10, 20, 20) + [{"type": "CIRCLE", "center": [70, 25], "radius": 5}]
    plane = {"origin": [0, 0, 100], "normal": [1, 0, 0], "xdir": [0, 1, 0]}
    s = spec(g, [fl(0, 10)], plane=plane)
    m = measure(from_brep(sm.sheet_job(s)[0]))
    fp = sm.flat(s)
    assert fp["metrics"]["pierce_count"] == 2
    assert m["volume_mm3"] == pytest.approx(fp["metrics"]["blank_area_mm2"] * 2, rel=REL)
    assert m["bbox"]["min"][0] == pytest.approx(
        0, abs=1e-6
    )  # plate thickness along +x (the normal)
    hole_cut = 40 + 2 * math.pi * 5
    assert fp["metrics"]["sheet_cut_length_mm"] == pytest.approx(
        sm._perimeter([tuple(p) for p in fp["outline"]]) + hole_cut
    )


@pytest.mark.parametrize(
    ("geoms", "flanges"),
    [
        (rect(100, 50), [fl(0, 10), fl(0, 20)]),  # same edge twice
        (rect(100, 50), [fl(9, 10)]),  # no such edge
        (rect(100, 50), [fl(0, 10, angle=271)]),
        (rect(100, 50), [fl(0, 10, inset_start=60, inset_end=40)]),
        (rect(100, 50), [fl(0, -1)]),
    ],
)
def test_bad_flanges(geoms, flanges):
    with pytest.raises(GeomError) as e:
        sm.flat(spec(geoms, flanges))
    assert e.value.code == "SHEET_INVALID"


def test_bad_holes_zero_lines_and_overlapping_flats():
    """BUG-57: inputs that used to crash (IndexError) or give a negative blank are refused."""
    with pytest.raises(GeomError):
        sm.outline(rect(100, 50) + [{"type": "LINE", "start": [5, 5], "end": [5, 5]}])
    with pytest.raises(GeomError):  # hole cuts the outline
        sm.outline(rect(100, 50) + [{"type": "CIRCLE", "center": [5, 25], "radius": 8}])
    with pytest.raises(GeomError):  # holes overlap
        sm.outline(
            rect(100, 50)
            + [
                {"type": "CIRCLE", "center": [20, 25], "radius": 5},
                {"type": "CIRCLE", "center": [27, 25], "radius": 5},
            ]
        )
    with pytest.raises(GeomError):  # circle inside a hole loop
        sm.outline(
            rect(100, 50)
            + rect(20, 20, 10, 10)
            + [{"type": "CIRCLE", "center": [20, 20], "radius": 2}]
        )
    # a U outline: a long flange on the notch's right wall would lie over the left arm when flat
    pts = [(0, 0), (100, 0), (100, 100), (80, 100), (80, 20), (20, 20), (20, 100), (0, 100)]
    ell = [
        {"type": "LINE", "start": list(a), "end": list(b)}
        for a, b in zip(pts, pts[1:] + pts[:1], strict=True)
    ]
    with pytest.raises(GeomError) as e:
        sm.flat(spec(ell, [fl(3, 80)]))
    assert "overlap" in e.value.message
    with pytest.raises(GeomError):
        sm.flat(spec(rect(10, 10), [fl(0, 5, angle=0.5)]))
    with pytest.raises(GeomError):
        sm.flat(spec(rect(10, 10), [], t=1e-9))


def test_simple_polygon_check_is_fast():
    import time

    n = 500
    poly = [
        (math.cos(2 * math.pi * i / n) * 100, math.sin(2 * math.pi * i / n) * 100) for i in range(n)
    ]
    t0 = time.perf_counter()
    assert sm._simple(poly)
    assert time.perf_counter() - t0 < 0.1


def test_bad_outlines():
    with pytest.raises(GeomError):
        sm.outline(rect(100, 50)[:3])  # open
    bow = [((0, 0), (10, 10)), ((10, 10), (10, 0)), ((10, 0), (0, 10)), ((0, 10), (0, 0))]
    with pytest.raises(GeomError) as e:
        sm.outline([{"type": "LINE", "start": list(a), "end": list(b)} for a, b in bow])
    assert e.value.code == "GEOM_SELF_INTERSECTION"
    with pytest.raises(GeomError):
        sm.outline(
            rect(10, 10)
            + [{"type": "ARC", "center": [0, 0], "radius": 1, "start_angle": 0, "end_angle": 90}]
        )
    with pytest.raises(GeomError):
        sm.outline(rect(10, 10) + rect(5, 5, 50, 50))  # a second outline outside
    with pytest.raises(GeomError):
        sm.flat(spec(rect(10, 10), [], t=0))


def test_flat_dxf_reads_back_as_a_sheet_quote(tmp_path):
    """The blank DXF goes through the existing 2D quote metrics: same cut length and bends."""
    g = rect(100, 50) + [{"type": "CIRCLE", "center": [50, 25], "radius": 4}]
    s = spec(g, [fl(0, 30), fl(2, 30, angle=60, up=False)])
    data = sm.flat_dxf(s, "BRK-01")
    doc = ezdxf.read(io.StringIO(data.decode()))
    assert not doc.audit().has_errors
    assert {e.dxf.layer for e in doc.modelspace()} == {"CUT", "BEND"}
    path = tmp_path / "flat.dxf"
    path.write_bytes(data)
    m = compute_metrics(str(path))
    fp = sm.flat(s)["metrics"]
    assert m["bend_count"] == 2
    assert m["bend_length_mm"] == pytest.approx(fp["bend_length_mm"], rel=REL)
    assert m["cutting_length_mm"] == pytest.approx(fp["sheet_cut_length_mm"], rel=1e-5)
    assert m["net_area_mm2"] == pytest.approx(fp["blank_area_mm2"], rel=1e-5)
    assert m["hole_count"] == 1


# --- plan §23 S2: bends in a row (flange on flange, hem, jog), flanges on a flange's side ------


def ba(angle, r=2.0, t=2.0, k=0.5):
    return math.radians(angle) * (r + k * t)


def strip_checks(s, depth, bends):
    """A 100 x 50 base with one flange on edge 0: the blank is 100 x (50 + depth)."""
    m = sm.sheet_job(s)[1]
    fp = sm.flat(s)
    size = fp["metrics"]["flat_bbox"]["size"]
    assert size == pytest.approx([100, 50 + depth], abs=1e-6)
    assert fp["metrics"]["blank_area_mm2"] == pytest.approx(100 * (50 + depth), rel=REL)
    assert m["volume_mm3"] == pytest.approx(fp["metrics"]["blank_area_mm2"] * 2, rel=REL)  # K 0.5
    assert [(b["flange"], b["step"], b["up"], round(b["angle"], 6)) for b in fp["bends"]] == bends
    assert m["sheet"]["bend_count"] == len(bends)
    return fp


def test_flange_on_flange_z_shape():
    z = {**fl(0, 20), "steps": [{"angle": 90.0, "up": False, "length": 15.0}]}
    fp = strip_checks(
        spec(rect(100, 50), [z]), 2 * ba(90) + 35, [(0, 0, True, 90), (0, 1, False, 90)]
    )
    # bend lines: the first BA/2 from the edge, the second past the first leg
    ys = sorted(-b["start"][1] for b in fp["bends"])
    assert ys == pytest.approx([ba(90) / 2, ba(90) + 20 + ba(90) / 2], abs=1e-6)
    assert fp["bends"][0]["edge"] == 0 and fp["bends"][1]["edge"] is None


def test_hem_and_its_own_radius():
    hem = {**fl(0, 8, angle=180.0), "radius": 0.5, "style": "HEM"}
    fp = strip_checks(spec(rect(100, 50), [hem]), ba(180, r=0.5) + 8, [(0, 0, True, 180)])
    assert fp["bends"][0]["radius"] == pytest.approx(0.5) and fp["bends"][0]["style"] == "HEM"


def test_jog_two_opposite_bends():
    jog = {
        **fl(0, 6, angle=45.0),
        "steps": [{"angle": 45.0, "up": False, "length": 12.0}],
        "style": "JOG",
    }
    strip_checks(spec(rect(100, 50), [jog]), 2 * ba(45) + 18, [(0, 0, True, 45), (0, 1, False, 45)])


def test_flange_on_the_side_of_a_flange():
    side = {
        "parent": {"flange": 0, "step": 0, "side": "LEFT"},
        "length": 10.0, "angle": 90.0, "up": True, "inset_start": 5.0, "inset_end": 0.0,
    }  # fmt: skip
    s = spec(rect(100, 50), [fl(0, 30), side])
    m = sm.sheet_job(s)[1]
    fp = sm.flat(s)
    area = 100 * 50 + 100 * (ba(90) + 30) + 25 * (ba(90) + 10)
    assert fp["metrics"]["blank_area_mm2"] == pytest.approx(area, rel=REL)
    assert m["volume_mm3"] == pytest.approx(area * 2, rel=REL)
    assert [(b["flange"], b["length"]) for b in fp["bends"]] == [
        (0, pytest.approx(100)),
        (1, pytest.approx(25)),
    ]
    # the side strip sticks out past the base's x = 0 by BA + 10
    assert fp["metrics"]["flat_bbox"]["size"][0] == pytest.approx(100 + ba(90) + 10, abs=1e-6)
    doc = ezdxf.read(io.StringIO(sm.flat_dxf(s).decode()))
    assert not doc.audit().errors


@pytest.mark.parametrize(
    "flange",
    [
        # curls back down into the base
        {
            **fl(0, 20),
            "steps": [
                {"angle": 90.0, "up": True, "length": 20.0},
                {"angle": 90.0, "up": True, "length": 30.0},
            ],
        },
        {**fl(0, 0), "steps": [{"angle": 90.0, "up": False, "length": 5.0}]},  # no straight between
        {**fl(0, 5), "steps": [{"angle": 90.0, "up": True, "length": 5.0}] * 8},  # too many bends
        {
            "parent": {"flange": 0, "step": 0, "side": "LEFT"},
            **{k: v for k, v in fl(0, 5).items() if k != "edge"},
        },
    ],
)
def test_bad_bend_chains(flange):
    with pytest.raises(GeomError):
        sm.sheet_job(spec(rect(100, 50), [flange]))


# --- plan §23 S2b: reliefs, closed corners, mitres, a flange from its cross-section ------------


def test_bend_reliefs_take_their_area_out_of_the_blank():
    plain = fl(0, 20, inset_start=10, inset_end=10)
    base = sm.flat(spec(rect(100, 50), [plain]))["metrics"]
    rel = {
        **plain,
        "relief_start": {"type": "RECT", "width": 3.0, "depth": 6.0},
        "relief_end": {"type": "ROUND", "width": 4.0, "depth": 7.0},
    }
    s = spec(rect(100, 50), [rel])
    m, fp = sm.sheet_job(s)[1], sm.flat(s)
    gone = 3 * 6 + 4 * (7 - 2) + math.pi * 4 / 2
    # the round end is followed to 0.01 mm in the flat outline
    assert fp["metrics"]["blank_area_mm2"] == pytest.approx(base["blank_area_mm2"] - gone, abs=0.1)
    assert m["volume_mm3"] == pytest.approx((base["blank_area_mm2"] - gone) * 2, rel=REL)
    # the slots open on the edge: longer outline, no new pierce
    assert fp["metrics"]["pierce_count"] == 0
    assert fp["metrics"]["sheet_cut_length_mm"] > base["sheet_cut_length_mm"] + 2 * 6 + 2 * 5
    # a slot wider than the inset, a round one shallower than its radius: refused
    for bad in (
        {**plain, "relief_start": {"type": "RECT", "width": 12.0, "depth": 5.0}},
        {**plain, "relief_end": {"type": "ROUND", "width": 4.0, "depth": 1.0}},
    ):
        with pytest.raises(GeomError):
            sm.flat(spec(rect(100, 50), [bad]))


def test_corner_reliefs():
    plain = sm.flat(spec(rect(100, 50), [fl(0, 20), fl(1, 20)]))["metrics"]["blank_area_mm2"]
    s = spec(
        rect(100, 50),
        [
            {**fl(0, 20), "relief_end": {"type": "CORNER_ROUND", "width": 6.0, "depth": 6.0}},
            fl(1, 20),
        ],
    )
    fp = sm.flat(s)
    # a circle of diameter 6 on the corner (100, 0): its quarter inside the plate goes (only the
    # plate is cut; the flanges keep their width)
    assert sm.sheet_job(s)[1]["sheet"]["bend_count"] == 2
    assert fp["metrics"]["blank_area_mm2"] == pytest.approx(plain - math.pi * 9 / 4, abs=0.05)
    sq = spec(
        rect(100, 50),
        [{**fl(0, 20), "relief_start": {"type": "CORNER_SQUARE", "width": 4.0, "depth": 4.0}}],
    )
    one = sm.flat(spec(rect(100, 50), [fl(0, 20)]))["metrics"]["blank_area_mm2"]
    # the square's quarter inside the plate: 2 x 2
    assert sm.flat(sq)["metrics"]["blank_area_mm2"] == pytest.approx(one - 4, rel=REL)


def test_closed_corner_extends_the_first_leg():
    s = spec(rect(100, 50), [{**fl(0, 20), "close_start": 4.0, "close_end": 2.0}, fl(1, 20)])
    m, fp = sm.sheet_job(s)[1], sm.flat(s)
    area = 100 * 50 + 100 * (ba(90) + 20) + 50 * (ba(90) + 20) + 6 * 20
    assert fp["metrics"]["blank_area_mm2"] == pytest.approx(area, rel=REL)
    assert m["volume_mm3"] == pytest.approx(area * 2, rel=REL)
    # R + T closes the corner: flush with the outer face of the flange on edge 1
    flush = spec(rect(100, 50), [{**fl(0, 20), "close_end": 4.0}, fl(1, 20)])
    assert sm.sheet_job(flush)[1]["bbox"]["max"][0] == pytest.approx(104, abs=1e-6)
    with pytest.raises(GeomError):
        sm.sheet_job(spec(rect(100, 50), [{**fl(0, 20), "close_end": 500.0}]))


def test_mitred_return_flanges_meet_without_overlap():
    ret = {"steps": [{"angle": 90.0, "up": True, "length": 12.0}]}
    plain = [{**fl(e, 15), **ret} for e in range(4)]
    with pytest.raises(GeomError):  # the returns run into each other at every corner
        sm.sheet_job(spec(rect(100, 50), plain))
    mitred = [{**f, "miter_start": 1.0, "miter_end": 1.0} for f in plain]
    s = spec(rect(100, 50), mitred)
    m, fp = sm.sheet_job(s)[1], sm.flat(s)
    assert m["sheet"]["bend_count"] == 8
    # the return leg of edge 0 lies at y from -4 to 8 (inside face of the wall at -2, then R + T
    # and 12 on); the planes through the corners at 45 degrees, half the gap in, trim it to a
    # trapezium: full width less 2 * (y + gap / sqrt 2 ... ) — check it through the volume
    full = sm.sheet_job(
        spec(rect(100, 50), [mitred[0] | {"miter_start": None, "miter_end": None}])
    )[1]
    one = sm.sheet_job(spec(rect(100, 50), [mitred[0]]))[1]
    lost = full["volume_mm3"] - one["volume_mm3"]
    assert 0 < lost < 2 * (12 + 4) ** 2 * 2  # two corner triangles of the return, at most
    assert m["volume_mm3"] == pytest.approx(fp["metrics"]["blank_area_mm2"] * 2, rel=5e-3)
    doc = ezdxf.read(io.StringIO(sm.flat_dxf(s).decode()))
    assert not doc.audit().errors


def test_profile_steps_set_backs():
    steps = sm.profile_steps([[0, 0], [0, 20], [15, 20]], t=2.0, r=2.0)
    assert [(round(x["angle"], 9), x["up"], round(x["length"], 9)) for x in steps] == [
        (90.0, True, 14.0),  # 20 less (R + T) for the bend up and R for the one down
        (90.0, False, 13.0),
    ]
    first, rest = steps[0], steps[1:]
    m = sm.sheet_job(
        spec(rect(100, 50), [{**fl(0, first["length"]), "steps": rest, "style": "CONTOUR"}])
    )[1]
    # the offset leg's underside is 20 above the base's, its tip 4 (the first set-back) + 15 out
    assert m["bbox"]["max"][2] == pytest.approx(22, abs=1e-6)
    assert m["bbox"]["min"][1] == pytest.approx(-19, abs=1e-6)
    for bad in ([[0, 0], [10, 0]], [[1, 0], [1, 5]], [[0, 0], [0, 3], [5, 3]], [[0, 0]]):
        with pytest.raises(GeomError):
            sm.profile_steps(bad, t=2.0, r=2.0)
