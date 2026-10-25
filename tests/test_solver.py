"""Track 8 P2-1: sketch constraint solver (FreeCAD Sketcher behaviour)."""

import math

import pytest

from core.geometry.errors import GeomError
from core.geometry.solver import solve


def line(i, a, b):
    return {"id": i, "type": "LINE", "start": list(a), "end": list(b)}


def ref(e, p="edge"):
    return {"e": e, "p": p}


def c(t, *refs, value=None):
    out = {"type": t, "refs": list(refs)}
    if value is not None:
        out["value"] = value
    return out


def by_id(res):
    return {e["id"]: e for e in res["entities"]}


def rect_sketch(w=40.0, h=25.0):
    """A roughly drawn rectangle, fully constrained to the origin (FreeCAD tutorial)."""
    ents = [
        line(1, (0.3, -0.2), (38, 1)),
        line(2, (38, 1), (41, 27)),
        line(3, (41, 27), (-1, 24)),
        line(4, (-1, 24), (0.3, -0.2)),
    ]
    cons = [
        c("COINCIDENT", ref(1, "end"), ref(2, "start")),
        c("COINCIDENT", ref(2, "end"), ref(3, "start")),
        c("COINCIDENT", ref(3, "end"), ref(4, "start")),
        c("COINCIDENT", ref(4, "end"), ref(1, "start")),
        c("HORIZONTAL", ref(1)),
        c("HORIZONTAL", ref(3)),
        c("VERTICAL", ref(2)),
        c("VERTICAL", ref(4)),
        c("COINCIDENT", ref(1, "start"), ref(-1, "start")),  # corner on the origin
        c("DISTANCE", ref(1), value=w),
        c("DISTANCE", ref(2), value=h),
    ]
    return ents, cons


def test_rectangle_fully_constrained():
    res = solve(*rect_sketch())
    assert res["status"] == "OK" and res["dof"] == 0
    e = by_id(res)
    assert e[1]["start"] == pytest.approx([0, 0], abs=1e-9)
    assert e[2]["end"] == pytest.approx([40, 25], abs=1e-9)
    assert e[3]["end"] == pytest.approx([0, 25], abs=1e-9)


def test_under_constrained_counts_dof_and_moves_little():
    ents, cons = rect_sketch()
    res = solve(ents, cons[:8])  # closed and square-cornered: position x2, width, height free
    assert res["status"] == "OK" and res["dof"] == 4
    assert by_id(res)[1]["start"] == pytest.approx([0.3, -0.2], abs=1.5)  # stays near the drawing


def test_redundant_and_conflicting():
    ents, cons = rect_sketch()
    red = solve(ents, [*cons, c("PARALLEL", ref(1), ref(3))])  # already implied by two HORIZONTALs
    assert red["status"] == "REDUNDANT" and red["redundant"] == [11] and red["dof"] == 0
    bad = solve(ents, [*cons, c("DISTANCE", ref(3), value=50)])  # width is 40 already
    assert bad["status"] == "CONFLICT" and 11 in bad["conflicting"]
    assert bad["entities"] == ents  # nothing applied


def test_circle_arc_tangent_radius_angle():
    ents = [
        {"id": 1, "type": "CIRCLE", "center": [10, 10], "radius": 4},
        line(2, (0, 0), (30, 2)),
        {
            "id": 3,
            "type": "ARC",
            "center": [30, 9],
            "radius": 6,
            "start_angle": -80,
            "end_angle": 90,
        },
        line(4, (0, 0), (20, 20)),
    ]
    cons = [
        c("TANGENT", ref(2), ref(1)),
        c("RADIUS", ref(1), value=5),
        c("DIAMETER", ref(3), value=14),
        c("TANGENT", ref(2, "end"), ref(3, "start")),  # joined smoothly (no separate coincident)
        c("ANGLE", ref(2), ref(4), value=30),
        c("FIXED", ref(2, "start")),
        c("HORIZONTAL", ref(2)),
    ]
    res = solve(ents, cons)
    assert res["status"] == "OK"
    e = by_id(res)
    l2, l4 = e[2], e[4]
    assert e[1]["radius"] == pytest.approx(5) and e[3]["radius"] == pytest.approx(7)
    assert l2["start"] == pytest.approx([0, 0], abs=1e-9) and l2["end"][1] == pytest.approx(
        0, abs=1e-9
    )
    assert abs(e[1]["center"][1]) == pytest.approx(5)  # tangent to the x axis line
    # arc starts on the line end and is tangent there: its centre is straight above/below it
    assert e[3]["center"][0] == pytest.approx(l2["end"][0], abs=1e-9)
    # 16 unknowns - 10 equations: circle x, line 2 length, arc end angle, line 4 position + length
    assert res["dof"] == 6
    d4 = (l4["end"][0] - l4["start"][0], l4["end"][1] - l4["start"][1])
    assert math.degrees(math.atan2(d4[1], d4[0])) == pytest.approx(30)


def test_point_on_object_symmetric_equal_concentric_perpendicular():
    ents = [
        {"id": 1, "type": "POINT", "point": [3, 4]},
        {"id": 2, "type": "CIRCLE", "center": [1, 1], "radius": 10},
        {"id": 3, "type": "CIRCLE", "center": [2, 0], "radius": 3},
        line(4, (-5, 1), (5, 2)),
        line(5, (0, -5), (1, 5)),
    ]
    cons = [
        c("POINT_ON_OBJECT", ref(1, "center"), ref(2)),
        c("CONCENTRIC", ref(2), ref(3)),
        c("EQUAL", ref(4), ref(5)),
        c("PERPENDICULAR", ref(4), ref(5)),
        c("SYMMETRIC", ref(4, "start"), ref(4, "end"), ref(-2)),  # about the vertical axis
        c("DISTANCE_X", ref(4, "start"), ref(4, "end"), value=12),
    ]
    res = solve(ents, cons)
    assert res["status"] == "OK"
    e = by_id(res)
    assert math.dist(e[1]["point"], e[2]["center"]) == pytest.approx(e[2]["radius"])
    assert e[2]["center"] == pytest.approx(e[3]["center"])
    a, b = e[4], e[5]
    assert a["start"][0] == pytest.approx(-6) and a["end"][0] == pytest.approx(6)
    la = math.dist(a["start"], a["end"])
    assert la == pytest.approx(math.dist(b["start"], b["end"]))


@pytest.mark.parametrize(
    ("cons", "code"),
    [
        ([c("HORIZONTAL", ref(9))], "SKETCH_INVALID_ENTITY"),
        ([c("RADIUS", ref(1), value=2)], "SKETCH_INVALID_CONSTRAINT"),  # a line has no radius
        ([c("DISTANCE", ref(1))], "SKETCH_INVALID_CONSTRAINT"),  # value missing
        ([c("DISTANCE", ref(1), value=-1)], "SKETCH_INVALID_CONSTRAINT"),
        ([c("COINCIDENT", ref(1, "center"), ref(1, "end"))], "SKETCH_INVALID_CONSTRAINT"),
    ],
)
def test_invalid_constraints(cons, code):
    with pytest.raises(GeomError) as e:
        solve([line(1, (0, 0), (1, 0))], cons)
    assert e.value.code == code


def test_no_constraints_and_limits():
    res = solve([line(1, (0, 0), (1, 1))], [])
    assert res == {**res, "dof": 4, "status": "OK"}
    with pytest.raises(GeomError) as e:
        solve([line(i, (0, 0), (1, 1)) for i in range(1, 502)], [])
    assert e.value.code == "SKETCH_TOO_LARGE"
