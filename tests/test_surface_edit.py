"""S7: surface editing & solidification tests — extend, trim, split, knit, thicken, replace."""

import pytest

from core.geometry.errors import GeomError
from core.geometry.serialize import to_brep
from core.geometry.surface import (
    sheet_measure,
    surface_extrude,
)
from core.geometry.surface_edit import (
    solid_measure,
    surface_extend,
    surface_extend_job,
    surface_knit,
    surface_knit_job,
    surface_replace,
    surface_replace_job,
    surface_split,
    surface_split_job,
    surface_thicken,
    surface_thicken_job,
    surface_trim,
    surface_trim_job,
)


# ── helpers ─────────────────────────────────────────────────────────────────
def _line(a, b):
    return {"type": "LINE", "start": list(a), "end": list(b)}


def _closed_rect(w=10, h=10):
    return [
        _line((0, 0), (w, 0)),
        _line((w, 0), (w, h)),
        _line((w, h), (0, h)),
        _line((0, h), (0, 0)),
    ]


def _flat_face(w=10, h=10):
    """Create a flat face (planar surface) from a closed rect."""
    from core.geometry.sketch import profile_face

    return profile_face(_closed_rect(w, h), False)


def _box_faces(w=10, h=10, d=10):
    """Create 6 separate planar faces forming a box — for sewing test."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer

    box = BRepPrimAPI_MakeBox(float(w), float(h), float(d)).Shape()
    faces = []
    ex = TopExp_Explorer(box, TopAbs_FACE)
    while ex.More():
        faces.append(ex.Current())
        ex.Next()
    return faces


# ═══════════════════════════════════════════════════════════════════════════
# EXTEND
# ═══════════════════════════════════════════════════════════════════════════


class TestSurfaceExtend:
    @pytest.mark.parametrize("mode", ["natural", "linear"])
    def test_extend_flat_face_stays_a_sheet(self, mode):
        """10 x 10 extended 5 mm past every side: a 20 x 20 sheet, not a block."""
        m = solid_measure(surface_extend(_flat_face(10, 10), 5.0, mode))
        assert m["kind"] == "sheet" and m["faces"] == 1
        assert m["surface_area_mm2"] == pytest.approx(400, rel=1e-9)

    def test_extend_zero_rejected(self):
        with pytest.raises(GeomError, match="distance"):
            surface_extend(_flat_face(), 0.0)

    def test_extend_negative_rejected(self):
        with pytest.raises(GeomError, match="distance"):
            surface_extend(_flat_face(), -5.0)

    def test_extend_job(self):
        face = _flat_face(10, 10)
        brep, m = surface_extend_job(to_brep(face), 5.0)
        assert len(brep) > 0
        assert m["kind"] == "sheet"


# ═══════════════════════════════════════════════════════════════════════════
# TRIM
# ═══════════════════════════════════════════════════════════════════════════


class TestSurfaceTrim:
    def test_trim_face_by_plane(self):
        """Trim a 10×10 face at x=5 → remaining half should be ~50 mm²."""
        face = _flat_face(10, 10)
        result = surface_trim(face, [5, 0, 0], [1, 0, 0], "negative")
        m = solid_measure(result)
        assert m["surface_area_mm2"] > 0
        # The kept half is x < 5 → 5×10 = 50 mm²
        assert m["surface_area_mm2"] == pytest.approx(50.0, rel=0.05)

    def test_trim_positive_side(self):
        """Trim keeping the positive side."""
        face = _flat_face(10, 10)
        result = surface_trim(face, [5, 0, 0], [1, 0, 0], "positive")
        m = solid_measure(result)
        assert m["surface_area_mm2"] == pytest.approx(50.0, rel=0.05)

    def test_trim_invalid_normal(self):
        with pytest.raises(GeomError, match="normal"):
            surface_trim(_flat_face(), [0, 0, 0], [0, 0, 0])

    def test_trim_job(self):
        face = _flat_face(10, 10)
        brep, m = surface_trim_job(to_brep(face), [5, 0, 0], [1, 0, 0])
        assert len(brep) > 0
        assert m["surface_area_mm2"] > 0


# ═══════════════════════════════════════════════════════════════════════════
# SPLIT
# ═══════════════════════════════════════════════════════════════════════════


class TestSurfaceSplit:
    def test_split_face_in_two(self):
        """Split a 10×10 face at x=5 → two halves ~50 mm² each."""
        face = _flat_face(10, 10)
        pos, neg = surface_split(face, [5, 0, 0], [1, 0, 0])
        m_pos = solid_measure(pos)
        m_neg = solid_measure(neg)
        assert m_pos["surface_area_mm2"] == pytest.approx(50.0, rel=0.05)
        assert m_neg["surface_area_mm2"] == pytest.approx(50.0, rel=0.05)

    def test_split_job_returns_positive(self):
        face = _flat_face(10, 10)
        brep, m = surface_split_job(to_brep(face), [5, 0, 0], [1, 0, 0])
        assert len(brep) > 0
        assert m["surface_area_mm2"] == pytest.approx(50.0, rel=0.05)


# ═══════════════════════════════════════════════════════════════════════════
# KNIT (Sewing → Solid)
# ═══════════════════════════════════════════════════════════════════════════


class TestSurfaceKnit:
    def test_sew_box_faces_to_solid(self):
        """§23.5: 6 faces of a 10×10×10 box → sew → solid, volume = 1000 mm³."""
        faces = _box_faces(10, 10, 10)
        assert len(faces) == 6
        result = surface_knit(faces)
        m = solid_measure(result)
        assert m["kind"] == "solid"
        assert m["volume_mm3"] == pytest.approx(1000.0, rel=1e-6)

    def test_sew_two_adjacent_faces(self):
        """Sew two non-closing faces → remains sheet."""
        face1 = _flat_face(10, 10)
        # Second face offset in Z

        # Create just one face at Z=10
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        from OCP.gp import gp_Dir, gp_Pln, gp_Pnt

        pln = gp_Pln(gp_Pnt(0, 0, 10), gp_Dir(0, 0, 1))
        face2 = BRepBuilderAPI_MakeFace(pln, 0, 10, 0, 10).Face()

        result = surface_knit([face1, face2])
        m = solid_measure(result)
        # Two parallel faces can't form a closed shell
        assert m["kind"] == "sheet"

    def test_knit_job(self):
        """Job entry point for sewing."""
        faces = _box_faces(10, 10, 10)
        brep_list = [to_brep(f) for f in faces]
        brep, m = surface_knit_job(brep_list)
        assert len(brep) > 0
        assert m["kind"] == "solid"
        assert m["volume_mm3"] == pytest.approx(1000.0, rel=1e-6)

    def test_knit_empty_rejected(self):
        with pytest.raises(GeomError, match="shapes"):
            surface_knit([])

    def test_knit_tolerance_rejected(self):
        with pytest.raises(GeomError, match="tolerance"):
            surface_knit([_flat_face()], tolerance=0.0)


# ═══════════════════════════════════════════════════════════════════════════
# THICKEN (Sheet → Solid)
# ═══════════════════════════════════════════════════════════════════════════


class TestSurfaceThicken:
    def test_thicken_flat_face(self):
        """§23.5: flat 10×10 face, thickness 5 → solid volume = 10×10×5 = 500 mm³."""
        face = _flat_face(10, 10)
        result = surface_thicken(face, 5.0)
        m = solid_measure(result)
        assert m["kind"] == "solid"
        assert m["volume_mm3"] == pytest.approx(500.0, rel=0.01)

    def test_thicken_negative(self):
        """Negative thickness = offset in reverse direction."""
        face = _flat_face(10, 10)
        result = surface_thicken(face, -3.0)
        m = solid_measure(result)
        assert m["kind"] == "solid"
        assert m["volume_mm3"] == pytest.approx(300.0, rel=0.01)

    def test_thicken_area_times_t(self):
        """§23.5 verification: thickness volume = surface_area × |t|."""
        face = _flat_face(20, 15)
        t = 2.5
        result = surface_thicken(face, t)
        m = solid_measure(result)
        expected = 20 * 15 * t  # 750 mm³
        assert m["kind"] == "solid"
        assert m["volume_mm3"] == pytest.approx(expected, rel=0.01)

    def test_thicken_zero_rejected(self):
        with pytest.raises(GeomError, match="thickness"):
            surface_thicken(_flat_face(), 0.0)

    def test_thicken_job(self):
        face = _flat_face(10, 10)
        brep, m = surface_thicken_job(to_brep(face), 5.0)
        assert len(brep) > 0
        assert m["kind"] == "solid"
        assert m["volume_mm3"] == pytest.approx(500.0, rel=0.01)


# ═══════════════════════════════════════════════════════════════════════════
# REPLACE
# ═══════════════════════════════════════════════════════════════════════════


class TestSurfaceReplace:
    def test_replace_face_in_box(self):
        """Replace one face of a box with another face → result still has faces."""
        faces = _box_faces(10, 10, 10)
        # Sew all into a body first
        body = surface_knit(faces)
        # Create a replacement face (slightly different position)
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        from OCP.gp import gp_Dir, gp_Pln, gp_Pnt

        pln = gp_Pln(gp_Pnt(0, 0, 10), gp_Dir(0, 0, 1))
        new_face = BRepBuilderAPI_MakeFace(pln, 0, 10, 0, 10).Face()

        result = surface_replace(body, 0, new_face)
        m = solid_measure(result)
        assert m["faces"] >= 5  # at least 5 faces remain

    def test_replace_bad_index(self):
        """Out-of-range face index."""
        face = _flat_face(10, 10)
        with pytest.raises(GeomError, match="out of range"):
            surface_replace(face, 99, face)

    def test_replace_negative_index(self):
        with pytest.raises(GeomError, match="face index"):
            surface_replace(_flat_face(), -1, _flat_face())

    def test_replace_job(self):
        faces = _box_faces(10, 10, 10)
        body = surface_knit(faces)
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        from OCP.gp import gp_Dir, gp_Pln, gp_Pnt

        pln = gp_Pln(gp_Pnt(0, 0, 10), gp_Dir(0, 0, 1))
        new_face = BRepBuilderAPI_MakeFace(pln, 0, 10, 0, 10).Face()

        brep, m = surface_replace_job(to_brep(body), to_brep(new_face), 0)
        assert len(brep) > 0
        assert m["faces"] >= 5


# ═══════════════════════════════════════════════════════════════════════════
# STEP roundtrip (§23.5): write + read → same volume/area
# ═══════════════════════════════════════════════════════════════════════════


class TestStepRoundtrip:
    def test_sheet_step_roundtrip(self):
        """STEP export/import of a sheet body preserves surface area."""
        import tempfile

        from core.geometry.exchange import export_job, import_job

        # Create a simple sheet via extrude of open wire
        geoms = [_line((0, 0), (10, 0)), _line((10, 0), (10, 10))]
        shape = surface_extrude(geoms, 20.0, "+Z")
        brep_bytes = to_brep(shape)
        m_orig = sheet_measure(shape)

        with tempfile.NamedTemporaryFile(suffix=".step", delete=False) as f:
            path = f.name
        try:
            # Export returns bytes — write to file
            step_bytes = export_job([("sheet", brep_bytes)], "STEP")
            with open(path, "wb") as fp:
                fp.write(step_bytes)
            # Import
            brep_back, _metrics = import_job(path, "STEP")
            from core.geometry.serialize import from_brep

            shape_back = from_brep(brep_back)
            m_back = sheet_measure(shape_back)
            # Area should be preserved
            assert m_back["surface_area_mm2"] == pytest.approx(m_orig["surface_area_mm2"], rel=0.01)
        finally:
            import os

            os.unlink(path)

    def test_solid_step_roundtrip(self):
        """STEP roundtrip of a solid (sewn box) preserves volume."""
        import tempfile

        from core.geometry.exchange import export_job, import_job

        faces = _box_faces(10, 10, 10)
        solid = surface_knit(faces)
        brep_bytes = to_brep(solid)
        m_orig = solid_measure(solid)

        with tempfile.NamedTemporaryFile(suffix=".step", delete=False) as f:
            path = f.name
        try:
            step_bytes = export_job([("solid", brep_bytes)], "STEP")
            with open(path, "wb") as fp:
                fp.write(step_bytes)
            brep_back, _metrics = import_job(path, "STEP")
            from core.geometry.serialize import from_brep

            shape_back = from_brep(brep_back)
            m_back = solid_measure(shape_back)
            assert m_back["kind"] == "solid"
            assert m_back["volume_mm3"] == pytest.approx(m_orig["volume_mm3"], rel=0.001)
        finally:
            import os

            os.unlink(path)
