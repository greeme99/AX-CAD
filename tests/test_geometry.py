"""Pure kernel tests (no DB): sketch -> extrude -> measure/mesh/BREP, and the worker pool."""

import math
import os
import random

import pytest
from OCP.BRepCheck import BRepCheck_Analyzer

from core.geometry.errors import GeomError
from core.geometry.features import extrude, extrude_job
from core.geometry.mesh import mesh_job, tessellate
from core.geometry.metrics import measure
from core.geometry.serialize import from_brep
from core.geometry.worker import run_kernel


def rect(w=40.0, h=30.0):
    return {
        "type": "LWPOLYLINE",
        "points": [[0, 0, 0], [w, 0, 0], [w, h, 0], [0, h, 0]],
        "closed": True,
    }


def line(a, b):
    return {"type": "LINE", "start": list(a), "end": list(b)}


def check(geoms, dist, volume, area=None):
    shape = extrude(geoms, dist)
    assert BRepCheck_Analyzer(shape).IsValid()
    m = measure(shape)
    assert m["volume_mm3"] == pytest.approx(volume, rel=1e-6)
    if area is not None:
        assert m["surface_area_mm2"] == pytest.approx(area, rel=1e-6)
    return m


def test_rectangle_polyline():
    m = check([rect()], 10, 12000, 2 * (40 * 30 + 40 * 10 + 30 * 10))
    assert m["bbox"]["min"] == pytest.approx([0, 0, 0], abs=1e-9)
    assert m["bbox"]["max"] == pytest.approx([40, 30, 10], abs=1e-9)
    assert m["bbox"]["size"] == pytest.approx([40, 30, 10], abs=1e-9)
    assert m["solids"] == 1


def test_negative_z_direction():
    m = measure(extrude([rect()], 10, "-Z"))
    assert m["volume_mm3"] == pytest.approx(12000, rel=1e-6)
    assert m["bbox"]["min"][2] == pytest.approx(-10)


def test_four_lines_shuffled_and_reversed():
    pts = [(0, 0), (40, 0), (40, 30), (0, 30)]
    lines = [line(pts[i], pts[(i + 1) % 4]) for i in range(4)]
    rng = random.Random(1)
    for _ in range(6):
        rng.shuffle(lines)
        flipped = [line(g["end"], g["start"]) if rng.random() < 0.5 else g for g in lines]
        check(flipped, 10, 12000, 2 * (40 * 30 + 40 * 10 + 30 * 10))


def test_d_shape_arc_and_line():
    arc = {"type": "ARC", "center": [0, 0], "radius": 10, "start_angle": 0, "end_angle": 180}
    check([line((-10, 0), (10, 0)), arc], 5, math.pi * 100 / 2 * 5)


def test_arc_wrapping_past_360():
    arc = {"type": "ARC", "center": [0, 0], "radius": 10, "start_angle": 270, "end_angle": 90}
    a, b = (0, -10), (0, 10)
    check([arc, line(a, b)], 5, math.pi * 100 / 2 * 5)


def test_bulge_half_circle():
    poly = {"type": "LWPOLYLINE", "points": [[0, 0, 1], [20, 0, 0]], "closed": True}
    check([poly], 4, math.pi * 100 / 2 * 4)
    poly["points"][0][2] = -1  # bulges the other way, same area
    check([poly], 4, math.pi * 100 / 2 * 4)


def test_circle():
    c = {"type": "CIRCLE", "center": [3, 4], "radius": 5}
    check([c], 2, math.pi * 25 * 2)


def test_open_profile_reports_dangling_points():
    geoms = [line((0, 0), (10, 0)), line((10, 0), (10, 10)), line((10, 10), (0, 10))]
    with pytest.raises(GeomError) as e:
        extrude(geoms, 5)
    assert e.value.code == "GEOM_OPEN_WIRE" and e.value.http == 422
    assert sorted(e.value.details["dangling"]) == [[0, 0], [0, 10]]


def test_bow_tie_self_intersection():
    bow = {
        "type": "LWPOLYLINE",
        "points": [[0, 0, 0], [10, 10, 0], [10, 0, 0], [0, 10, 0]],
        "closed": True,
    }
    with pytest.raises(GeomError) as e:
        extrude([bow], 5)
    assert e.value.code == "GEOM_SELF_INTERSECTION"


def test_zero_length_line():
    geoms = [line((0, 0), (0, 0)), line((0, 0), (5, 0)), line((5, 0), (0, 5))]
    with pytest.raises(GeomError) as e:
        extrude(geoms, 5)
    assert e.value.code == "GEOM_DEGENERATE_EDGE"


def test_two_loops_rejected():
    far = [line((50, 0), (60, 0)), line((60, 0), (60, 10)), line((60, 10), (50, 0))]
    near = [line((0, 0), (10, 0)), line((10, 0), (0, 10)), line((0, 10), (0, 0))]
    with pytest.raises(GeomError) as e:
        extrude(near + far, 5)
    assert e.value.code == "GEOM_MULTIPLE_PROFILES"


@pytest.mark.parametrize("dist", [0, -1, math.inf, math.nan, 1e6 + 1])
def test_bad_distance(dist):
    with pytest.raises(GeomError) as e:
        extrude([rect()], dist)
    assert e.value.code == "GEOM_INVALID_PARAM"


def test_mesh_is_consistent():
    mesh = tessellate(extrude([{"type": "CIRCLE", "center": [0, 0], "radius": 5}], 2))
    assert mesh["triangle_count"] > 0
    for f in mesh["faces"]:
        n = len(f["positions"]) // 3
        assert len(f["normals"]) == 3 * n and max(f["indices"]) < n and len(f["indices"]) % 3 == 0
        for i in range(n):
            assert math.hypot(*f["normals"][3 * i : 3 * i + 3]) == pytest.approx(1.0, abs=1e-6)


def test_mesh_winding_matches_normals():
    """Outward normals everywhere (also for reversed faces and the -Z solid)."""
    for direction in ("+Z", "-Z"):
        mesh = tessellate(extrude([rect()], 10, direction))
        for f in mesh["faces"]:
            p, idx = f["positions"], f["indices"]
            for t in range(0, len(idx), 3):
                a, b, c = (p[3 * i : 3 * i + 3] for i in idx[t : t + 3])
                u, v = [b[k] - a[k] for k in range(3)], [c[k] - a[k] for k in range(3)]
                cross = [
                    u[1] * v[2] - u[2] * v[1],
                    u[2] * v[0] - u[0] * v[2],
                    u[0] * v[1] - u[1] * v[0],
                ]
                n = f["normals"][3 * idx[t] : 3 * idx[t] + 3]
                assert sum(x * y for x, y in zip(cross, n)) > 0


def crash():
    os._exit(1)


def test_worker_roundtrip_and_crash_recovery():
    brep, m = run_kernel(extrude_job, [rect()], 10.0, "+Z")
    assert isinstance(brep, bytes) and m["volume_mm3"] == pytest.approx(12000, rel=1e-6)
    assert measure(from_brep(brep))["volume_mm3"] == pytest.approx(12000, rel=1e-6)
    assert run_kernel(mesh_job, brep)["triangle_count"] > 0
    with pytest.raises(GeomError) as e:
        run_kernel(crash)
    assert e.value.code == "GEOM_KERNEL_CRASH" and e.value.http == 422
    assert run_kernel(extrude_job, [rect()], 20.0, "+Z")[1]["volume_mm3"] == pytest.approx(24000)


def test_worker_propagates_geom_error():
    with pytest.raises(GeomError) as e:
        run_kernel(extrude_job, [rect()], 0.0, "+Z")
    assert e.value.code == "GEOM_INVALID_PARAM"
