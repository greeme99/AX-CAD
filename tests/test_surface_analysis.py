"""S8: surface quality analysis tests — curvature, continuity, draft, zebra."""

import pytest

from core.geometry.errors import GeomError
from core.geometry.serialize import to_brep
from core.geometry.surface_analysis import (
    continuity_check,
    continuity_check_job,
    curvature_map,
    curvature_map_job,
    draft_analysis,
    draft_analysis_job,
    zebra_stripes,
    zebra_stripes_job,
)

# ── helpers ─────────────────────────────────────────────────────────────────


def _cylinder_face(radius: float = 10.0, height: float = 20.0):
    """Create a cylindrical face (full 360° cylinder)."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    cyl = BRepPrimAPI_MakeCylinder(radius, height).Shape()
    # The lateral face is the curved one (not caps)
    ex = TopExp_Explorer(cyl, TopAbs_FACE)
    while ex.More():
        face = TopoDS.Face(ex.Current())
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_Cylinder

        adaptor = BRepAdaptor_Surface(face)
        if adaptor.GetType() == GeomAbs_Cylinder:
            return face
        ex.Next()
    raise RuntimeError("No cylindrical face found")


def _sphere_face(radius: float = 10.0):
    """Create a spherical face."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    sph = BRepPrimAPI_MakeSphere(radius).Shape()
    ex = TopExp_Explorer(sph, TopAbs_FACE)
    if ex.More():
        return TopoDS.Face(ex.Current())
    raise RuntimeError("No spherical face found")


def _flat_face(w=10, h=10):
    """Create a flat face (planar surface) from a closed rect."""
    from core.geometry.sketch import profile_face

    return profile_face(
        [
            {"type": "LINE", "start": [0, 0], "end": [w, 0]},
            {"type": "LINE", "start": [w, 0], "end": [w, h]},
            {"type": "LINE", "start": [w, h], "end": [0, h]},
            {"type": "LINE", "start": [0, h], "end": [0, 0]},
        ],
        False,
    )


def _box_shape(w=10, h=10, d=10):
    """Create a box solid (6 faces sharing edges)."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

    return BRepPrimAPI_MakeBox(float(w), float(h), float(d)).Shape()


# ═══════════════════════════════════════════════════════════════════════════
# CURVATURE MAP
# ═══════════════════════════════════════════════════════════════════════════


class TestCurvatureMap:
    def test_cylinder_curvature(self):
        """§23.5: cylinder R=10 → |principal curvature| = 1/R = 0.1, gauss = 0."""
        face = _cylinder_face(radius=10.0)
        result = curvature_map(face, n_u=10, n_v=10)
        assert len(result["samples"]) > 0
        for s in result["samples"]:
            # Cylinder: one principal = ±1/R, other = 0
            # OCCT sign: convex → negative principal curvature
            assert s["gauss"] == pytest.approx(0.0, abs=1e-4)
            # One of k_min/k_max is ≈ ±0.1, the other ≈ 0
            assert abs(s["k_min"]) + abs(s["k_max"]) == pytest.approx(0.1, rel=1e-3)

    def test_sphere_curvature(self):
        """§23.5: sphere R=10 → |gauss| = 1/R² = 0.01, |mean| = 1/R = 0.1."""
        face = _sphere_face(radius=10.0)
        result = curvature_map(face, n_u=10, n_v=10)
        assert len(result["samples"]) > 0
        for s in result["samples"]:
            # OCCT sign: convex → negative curvature; use abs()
            assert abs(s["gauss"]) == pytest.approx(1.0 / 100.0, rel=1e-4)
            assert abs(s["mean"]) == pytest.approx(1.0 / 10.0, rel=1e-4)

    def test_flat_face_zero_curvature(self):
        """Flat face should have zero curvature everywhere."""
        face = _flat_face(10, 10)
        result = curvature_map(face, n_u=5, n_v=5)
        for s in result["samples"]:
            assert s["gauss"] == pytest.approx(0.0, abs=1e-10)
            assert s["mean"] == pytest.approx(0.0, abs=1e-10)

    def test_stats_populated(self):
        face = _cylinder_face(10.0)
        result = curvature_map(face, n_u=5, n_v=5)
        stats = result["stats"]
        # Cylinder: k_min ≈ -0.1 (convex), k_max ≈ 0.0 (along axis)
        assert stats["k_min_global"] is not None
        assert stats["k_min_global"] == pytest.approx(-0.1, rel=1e-3)

    def test_invalid_samples_rejected(self):
        with pytest.raises(GeomError, match="samples"):
            curvature_map(_flat_face(), n_u=1, n_v=1)

    def test_curvature_job(self):
        face = _cylinder_face(10.0)
        result = curvature_map_job(to_brep(face), 5, 5)
        assert len(result["samples"]) > 0


# ═══════════════════════════════════════════════════════════════════════════
# CONTINUITY CHECK
# ═══════════════════════════════════════════════════════════════════════════


class TestContinuityCheck:
    def test_box_g0_g1(self):
        """Box has sharp edges: G0 should pass (edges meet), G1 should fail (90° normals)."""
        box = _box_shape(10, 10, 10)
        result = continuity_check(box, n_samples=10, g0_tol=0.01, g1_tol_deg=0.1)
        summary = result["summary"]
        # All edges should be G0 (positions coincide)
        assert summary["g0_pass"] == summary["total_edges"]
        # G1 should fail — box has 90° normals at edges
        assert summary["g1_pass"] < summary["total_edges"]

    def test_single_face_no_edges(self):
        """A single face has no shared edges → empty result."""
        face = _flat_face(10, 10)
        result = continuity_check(face)
        assert result["summary"]["total_edges"] == 0
        assert result["summary"]["all_pass"] is True

    def test_continuity_job(self):
        box = _box_shape(10, 10, 10)
        result = continuity_check_job(to_brep(box), 10)
        assert result["summary"]["total_edges"] > 0

    def test_invalid_samples_rejected(self):
        with pytest.raises(GeomError, match="n_samples"):
            continuity_check(_box_shape(), n_samples=1)


# ═══════════════════════════════════════════════════════════════════════════
# DRAFT ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════


class TestDraftAnalysis:
    def test_box_draft_is_signed(self):
        """Box pulled in +Z: top +90, bottom -90 (undercut), the four walls 0."""
        box = _box_shape(10, 10, 10)
        result = draft_analysis(box, [0, 0, 1], n_u=5, n_v=5)
        drafts = sorted(f["draft_avg_deg"] for f in result["faces"])
        assert drafts == pytest.approx([-90, 0, 0, 0, 0, 90], abs=1e-6)
        assert [f["undercut"] for f in result["faces"]].count(True) == 1
        # pulling the other way swaps top and bottom
        other = draft_analysis(box, [0, 0, -1], n_u=5, n_v=5)
        flipped = {f["face_idx"]: f["draft_avg_deg"] for f in other["faces"]}
        for f in result["faces"]:
            assert flipped[f["face_idx"]] == pytest.approx(-f["draft_avg_deg"], abs=1e-6)

    def test_cylinder_draft(self):
        """Cylinder pulled in Z: lateral face has varying draft."""
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

        cyl = BRepPrimAPI_MakeCylinder(10.0, 20.0).Shape()
        result = draft_analysis(cyl, [0, 0, 1], n_u=5, n_v=5)
        assert len(result["faces"]) >= 1

    def test_invalid_direction(self):
        with pytest.raises(GeomError, match="direction"):
            draft_analysis(_box_shape(), [0, 0, 0])

    def test_draft_job(self):
        box = _box_shape(10, 10, 10)
        result = draft_analysis_job(to_brep(box), [0, 0, 1])
        assert len(result["faces"]) == 6


# ═══════════════════════════════════════════════════════════════════════════
# ZEBRA STRIPES
# ═══════════════════════════════════════════════════════════════════════════


class TestZebraStripes:
    def test_flat_face_stripes(self):
        """Flat face should produce uniform stripes."""
        face = _flat_face(10, 10)
        result = zebra_stripes(face, n_u=10, n_v=10)
        assert len(result["samples"]) > 0
        for s in result["samples"]:
            assert 0 <= s["intensity"] <= 1
            assert s["stripe"] in (0, 1)

    def test_cylinder_stripes(self):
        """Cylinder should produce varying stripe pattern with enough samples."""
        face = _cylinder_face(10.0)
        # Higher frequency and more samples to see stripe variation on curved surface
        result = zebra_stripes(face, stripe_dir=[1, 0, 0], n_u=40, n_v=40)
        assert len(result["samples"]) > 0
        # On a curved surface the reflected direction varies, producing intensity range
        intensities = [s["intensity"] for s in result["samples"]]
        assert max(intensities) - min(intensities) > 0.01

    def test_invalid_direction(self):
        with pytest.raises(GeomError, match="direction"):
            zebra_stripes(_flat_face(), stripe_dir=[0, 0, 0])

    def test_zebra_job(self):
        face = _cylinder_face(10.0)
        result = zebra_stripes_job(to_brep(face))
        assert len(result["samples"]) > 0


class TestCurvatureOfEveryFace:
    def test_box_is_sampled_on_all_six_faces(self):
        result = curvature_map(_box_shape(10, 10, 10), n_u=4, n_v=4)
        assert {s["face_idx"] for s in result["samples"]} == set(range(6))
        assert len(result["samples"]) == 6 * 16
        assert result["stats"]["gauss_max"] == pytest.approx(0.0, abs=1e-12)

    def test_solid_cylinder_wall_has_one_sign_and_caps_are_flat(self):
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

        result = curvature_map(BRepPrimAPI_MakeCylinder(10.0, 30.0).Shape(), n_u=6, n_v=6)
        curved = [s["k_min"] for s in result["samples"] if abs(s["k_min"]) > 1e-9]
        assert curved and all(k == pytest.approx(curved[0], rel=1e-9) for k in curved)
        assert abs(curved[0]) == pytest.approx(0.1, rel=1e-4)
        assert {s["face_idx"] for s in result["samples"]} == {0, 1, 2}

    def test_points_in_a_trimmed_away_hole_are_skipped(self):
        from core.geometry.surface_edit import surface_cut

        hole = [{"type": "CIRCLE", "center": [5, 5], "radius": 3}]
        plane = {"origin": [0, 0, 0], "normal": [0, 0, 1], "xdir": [1, 0, 0]}
        plate = surface_cut(_flat_face(), {"sketch": (hole, plane)}, "negative")
        result = curvature_map(plate, n_u=21, n_v=21)
        assert 0 < len(result["samples"]) < 21 * 21
        assert all((s["x"] - 5) ** 2 + (s["y"] - 5) ** 2 >= 9 - 1e-6 for s in result["samples"])

    def test_too_many_faces_for_the_grid_is_refused(self):
        with pytest.raises(GeomError, match="too many"):
            curvature_map(_box_shape(10, 10, 10), n_u=200, n_v=200)
