"""S6: surface creation tests — NURBS curve, loft, sweep, boundary, fill, extrude, revolve, offset."""

import math

import pytest

from core.geometry.errors import GeomError
from core.geometry.surface import (
    curve_edge,
    nurbs_curve_control,
    nurbs_curve_interpolate,
    sheet_measure,
    surface_boundary,
    surface_extrude,
    surface_extrude_job,
    surface_fill,
    surface_fill_job,
    surface_loft,
    surface_loft_job,
    surface_offset,
    surface_revolve,
    surface_sweep,
)


# ── helpers ─────────────────────────────────────────────────────────────────
def _line(a, b):
    """A straight line segment for sketch geom (uppercase type per sketch.py)."""
    return {"type": "LINE", "start": list(a), "end": list(b)}


def _open_L():
    """An open L-shaped profile (3 points)."""
    return [_line((0, 0), (10, 0)), _line((10, 0), (10, 10))]


def _closed_rect(w=10, h=5):
    """A closed rectangular profile."""
    return [
        _line((0, 0), (w, 0)),
        _line((w, 0), (w, h)),
        _line((w, h), (0, h)),
        _line((0, h), (0, 0)),
    ]


XY = {"origin": [0, 0, 0], "normal": [0, 0, 1], "xdir": [1, 0, 0]}
XZ = {"origin": [0, 0, 0], "normal": [0, -1, 0], "xdir": [1, 0, 0]}


def _plane_at_z(z):
    return {"origin": [0, 0, z], "normal": [0, 0, 1], "xdir": [1, 0, 0]}


# ═══════════════════════════════════════════════════════════════════════════
# NURBS curve
# ═══════════════════════════════════════════════════════════════════════════


class TestNurbsCurve:
    def test_interpolate_basic(self):
        pts = [[0, 0, 0], [5, 5, 0], [10, 0, 0]]
        c = nurbs_curve_interpolate(pts, degree=3)
        # curve should start at first point and end at last
        p0 = c.Value(c.FirstParameter())
        p1 = c.Value(c.LastParameter())
        assert abs(p0.X() - 0) < 1e-6
        assert abs(p1.X() - 10) < 1e-6

    def test_interpolate_too_few_points(self):
        with pytest.raises(GeomError, match="points"):
            nurbs_curve_interpolate([[0, 0, 0]])

    def test_control_points_basic(self):
        poles = [[0, 0, 0], [3, 5, 0], [6, 5, 0], [10, 0, 0]]
        c = nurbs_curve_control(poles, degree=3)
        assert c.Degree() == 3

    def test_control_with_weights(self):
        poles = [[0, 0, 0], [5, 5, 0], [10, 0, 0]]
        c = nurbs_curve_control(poles, degree=2, weights=[1.0, 2.0, 1.0])
        assert c.Degree() == 2

    def test_degree_too_high(self):
        with pytest.raises(GeomError, match="degree"):
            nurbs_curve_control([[0, 0, 0], [1, 0, 0]], degree=3)

    def test_edge_from_curve(self):
        c = nurbs_curve_interpolate([[0, 0, 0], [5, 5, 0], [10, 0, 0]])
        e = curve_edge(c)
        assert not e.IsNull()


# ═══════════════════════════════════════════════════════════════════════════
# Surface loft (open sections → sheet)
# ═══════════════════════════════════════════════════════════════════════════


class TestSurfaceLoft:
    def test_two_sections_sheet(self):
        """Loft two open L profiles at different Z → sheet body."""
        s = surface_loft([(_open_L(), _plane_at_z(0)), (_open_L(), _plane_at_z(20))], ruled=True)
        m = sheet_measure(s)
        assert m["kind"] == "sheet"
        assert m["faces"] >= 1
        assert m["surface_area_mm2"] > 0

    def test_two_sections_solid(self):
        """Loft two closed rects → solid."""
        s = surface_loft(
            [(_closed_rect(), _plane_at_z(0)), (_closed_rect(), _plane_at_z(20))],
            ruled=True,
            solid=True,
        )
        m = sheet_measure(s)
        assert m["kind"] == "solid"

    def test_job_returns_brep_and_metrics(self):
        brep, m = surface_loft_job(
            [(_open_L(), _plane_at_z(0)), (_open_L(), _plane_at_z(15))], ruled=True
        )
        assert len(brep) > 0
        assert m["kind"] == "sheet"

    def test_too_few_sections(self):
        with pytest.raises(GeomError, match="sections"):
            surface_loft([(_open_L(), XY)])


# ═══════════════════════════════════════════════════════════════════════════
# Surface sweep
# ═══════════════════════════════════════════════════════════════════════════


class TestSurfaceSweep:
    def test_open_profile_sweep(self):
        """Sweep an open L along a straight path → sheet."""
        path = [_line((0, 0), (0, 30))]
        s = surface_sweep(_open_L(), XY, path, XZ)
        m = sheet_measure(s)
        assert m["kind"] == "sheet"
        assert m["surface_area_mm2"] > 0


# ═══════════════════════════════════════════════════════════════════════════
# Surface extrude (open wire → shell)
# ═══════════════════════════════════════════════════════════════════════════


class TestSurfaceExtrude:
    def test_open_extrude(self):
        """Extrude an open L upward → sheet body."""
        s = surface_extrude(_open_L(), 20.0, "+Z")
        m = sheet_measure(s)
        assert m["kind"] == "sheet"
        # A 20mm extrusion of an L (10+10=20mm long wire) = 400 mm² area
        assert m["surface_area_mm2"] == pytest.approx(400.0, abs=1.0)

    def test_job(self):
        brep, m = surface_extrude_job(_open_L(), 15.0, "+Z")
        assert len(brep) > 0
        assert m["kind"] == "sheet"


# ═══════════════════════════════════════════════════════════════════════════
# Surface revolve (open wire → shell)
# ═══════════════════════════════════════════════════════════════════════════


class TestSurfaceRevolve:
    def test_open_revolve_half_cylinder(self):
        """Revolve a vertical line 180° around Y axis → half-cylinder surface."""
        profile = [_line((10, 0), (10, 20))]
        s = surface_revolve(profile, (0, 0), (0, 1), 180.0)
        m = sheet_measure(s)
        assert m["kind"] == "sheet"
        # Half-cylinder: π * R * H = π * 10 * 20 ≈ 628.3
        assert m["surface_area_mm2"] == pytest.approx(math.pi * 10 * 20, rel=0.01)


# ═══════════════════════════════════════════════════════════════════════════
# Boundary surface (3–4 edge curves)
# ═══════════════════════════════════════════════════════════════════════════


class TestSurfaceBoundary:
    def test_four_edge_patch(self):
        """4-edge boundary patch forming a rough rectangle."""
        e1 = nurbs_curve_interpolate([[0, 0, 0], [5, 1, 0], [10, 0, 0]])
        e2 = nurbs_curve_interpolate([[10, 0, 0], [11, 5, 0], [10, 10, 0]])
        e3 = nurbs_curve_interpolate([[10, 10, 0], [5, 9, 0], [0, 10, 0]])
        e4 = nurbs_curve_interpolate([[0, 10, 0], [-1, 5, 0], [0, 0, 0]])
        s = surface_boundary([e1, e2, e3, e4], "G0")
        m = sheet_measure(s)
        assert m["faces"] >= 1

    def test_too_few_edges(self):
        e1 = nurbs_curve_interpolate([[0, 0, 0], [10, 0, 0]])
        e2 = nurbs_curve_interpolate([[10, 0, 0], [10, 10, 0]])
        with pytest.raises(GeomError, match="3 or 4"):
            surface_boundary([e1, e2])


# ═══════════════════════════════════════════════════════════════════════════
# Fill surface (N-sided)
# ═══════════════════════════════════════════════════════════════════════════


class TestSurfaceFill:
    def test_five_sided_fill(self):
        """5-edge fill — a pentagon-like hole."""
        pts = [
            [10, 0, 0],
            [3.09, 9.51, 0],
            [-8.09, 5.88, 0],
            [-8.09, -5.88, 0],
            [3.09, -9.51, 0],
        ]
        # create edges between successive points
        edges = []
        for i in range(5):
            a, b = pts[i], pts[(i + 1) % 5]
            edges.append(nurbs_curve_interpolate([a, b]))
        s = surface_fill(edges, "G0")
        m = sheet_measure(s)
        assert m["faces"] >= 1

    def test_fill_job(self):
        edge_pts = [
            [[0, 0, 0], [10, 0, 0]],
            [[10, 0, 0], [10, 10, 0]],
            [[10, 10, 0], [0, 10, 0]],
            [[0, 10, 0], [0, 0, 0]],
        ]
        brep, m = surface_fill_job(edge_pts, "G0")
        assert len(brep) > 0
        assert m["faces"] >= 1


# ═══════════════════════════════════════════════════════════════════════════
# Offset surface
# ═══════════════════════════════════════════════════════════════════════════


class TestSurfaceOffset:
    def test_offset_flat_face(self):
        """Offset a planar face → parallel face."""
        # create a flat square face
        geoms = _closed_rect(10, 10)
        from core.geometry.sketch import profile_face

        face = profile_face(geoms, False)
        result = surface_offset(face, 5.0)
        m = sheet_measure(result)
        # offset of a flat face produces another face
        assert m["faces"] >= 1

    def test_offset_zero_rejected(self):
        from core.geometry.sketch import profile_face

        face = profile_face(_closed_rect(), False)
        with pytest.raises(GeomError, match="non-zero"):
            surface_offset(face, 0.0)


# ═══════════════════════════════════════════════════════════════════════════
# Verification: cylinder curvature (§23.5)
# ═══════════════════════════════════════════════════════════════════════════


class TestCurvatureVerification:
    def test_cylinder_curvature(self):
        """§23.5: revolve a line 360° → cylinder R=10; curvature = 1/R."""
        R = 10.0
        profile = [_line((R, 0), (R, 20))]
        s = surface_revolve(profile, (0, 0), (0, 1), 360.0)
        # extract the face and check curvature at a sample point
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.BRepLProp import BRepLProp_SLProps
        from OCP.TopAbs import TopAbs_FACE
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS

        ex = TopExp_Explorer(s, TopAbs_FACE)
        assert ex.More()
        face = TopoDS.Face(ex.Current())
        adaptor = BRepAdaptor_Surface(face)
        u = (adaptor.FirstUParameter() + adaptor.LastUParameter()) / 2
        v = (adaptor.FirstVParameter() + adaptor.LastVParameter()) / 2
        props = BRepLProp_SLProps(adaptor, u, v, 2, 1e-6)
        if props.IsCurvatureDefined():
            k1 = props.MinCurvature()
            k2 = props.MaxCurvature()
            # one principal curvature = 1/R, the other = 0 (along axis)
            curvatures = sorted([abs(k1), abs(k2)])
            assert curvatures[0] == pytest.approx(0.0, abs=1e-4)  # axial direction
            assert curvatures[1] == pytest.approx(1.0 / R, rel=1e-4)  # circumferential
