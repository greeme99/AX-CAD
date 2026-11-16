"""S7: surface editing & solidification tests — extend, trim, split, knit, thicken, replace."""

import pytest

from core.geometry.errors import GeomError
from core.geometry.serialize import to_brep
from core.geometry.surface import (
    sheet_measure,
    surface_extrude,
)
from core.geometry.surface_edit import (
    delete_faces,
    delete_faces_job,
    fill_hole,
    fill_hole_job,
    solid_measure,
    surface_cut,
    surface_cut_job,
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

    @pytest.mark.parametrize("t", [2.0, -2.0])
    def test_thicken_is_not_inside_out(self, t):
        from core.geometry.surface_edit import _volume

        assert _volume(surface_thicken(_flat_face(), t)) == pytest.approx(200.0, rel=1e-6)

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


# ═══════════════════════════════════════════════════════════════════════════
# CUT by plane / sketch / tool surface, one side or both (plan §23 S7)
# ═══════════════════════════════════════════════════════════════════════════


def _wall(x=5.0):
    """A vertical sheet at x, wider than the 10 mm test bodies (normal ±X)."""
    from core.geometry.features import place

    plane = {"origin": [x, -20, -20], "normal": [1, 0, 0], "xdir": [0, 1, 0]}
    return place(_flat_face(50, 50), plane)


def _box(w=10.0, h=10.0, d=10.0):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

    return BRepPrimAPI_MakeBox(w, h, d).Shape()


XY = {"origin": [0, 0, 0], "normal": [0, 0, 1], "xdir": [1, 0, 0]}
HOLE = [{"type": "CIRCLE", "center": [5, 5], "radius": 2}]


class TestSurfaceCut:
    def test_split_keeps_both_halves_as_two_faces(self):
        m = solid_measure(surface_cut(_flat_face(), {"plane": ([3, 0, 0], [1, 0, 0])}, "both"))
        assert m["faces"] == 2 and m["kind"] == "sheet"
        assert m["surface_area_mm2"] == pytest.approx(100.0, abs=1e-6)

    def test_sketch_outline_keeps_inside_or_outside(self):
        import math

        disc = math.pi * 4
        inside = surface_cut(_flat_face(), {"sketch": (HOLE, XY)}, "positive")
        outside = surface_cut(_flat_face(), {"sketch": (HOLE, XY)}, "negative")
        assert solid_measure(inside)["surface_area_mm2"] == pytest.approx(disc, rel=1e-6)
        assert solid_measure(outside)["surface_area_mm2"] == pytest.approx(100 - disc, rel=1e-6)

    def test_sketch_on_another_plane_projects_along_its_normal(self):
        # outline drawn 30 mm above the face still cuts it
        plane = {"origin": [0, 0, 30], "normal": [0, 0, 1], "xdir": [1, 0, 0]}
        both = surface_cut(_flat_face(), {"sketch": (HOLE, plane)}, "both")
        assert solid_measure(both)["faces"] == 2

    def test_tool_surface_trims_a_sheet(self):
        pos = surface_cut(_flat_face(), {"tool": _wall(3.0)}, "positive")
        neg = surface_cut(_flat_face(), {"tool": _wall(3.0)}, "negative")
        areas = sorted(solid_measure(s)["surface_area_mm2"] for s in (pos, neg))
        assert areas == pytest.approx([30.0, 70.0], abs=1e-6)
        # the two sides are the two different halves
        assert solid_measure(pos)["bbox"]["min"][0] != solid_measure(neg)["bbox"]["min"][0]

    def test_curved_tool_cuts_a_solid(self):
        # half-cylinder sheet R=4 through the box along Y: removes/keeps the tunnel
        import math

        from core.geometry.features import place

        arc = [{"type": "ARC", "center": [5, 0], "radius": 4, "start_angle": 0, "end_angle": 180}]
        plane = {"origin": [10, -10, 0], "normal": [0, 1, 0], "xdir": [-1, 0, 0]}
        tool = place(surface_extrude(arc, 30.0), plane)
        tunnel = math.pi * 16 / 2 * 10
        vols = sorted(
            solid_measure(surface_cut(_box(), {"tool": tool}, k))["volume_mm3"]
            for k in ("positive", "negative")
        )
        assert vols == pytest.approx([tunnel, 1000 - tunnel], rel=1e-6)

    def test_plane_and_sketch_cut_a_solid(self):
        half = surface_cut(_box(), {"plane": ([4, 0, 0], [1, 0, 0])}, "negative")
        assert solid_measure(half)["volume_mm3"] == pytest.approx(400.0, abs=1e-6)
        both = solid_measure(surface_cut(_box(), {"sketch": (HOLE, XY)}, "both"))
        assert both["solids"] == 2 and both["volume_mm3"] == pytest.approx(1000.0, abs=1e-6)

    def test_cutter_that_misses_is_refused(self):
        with pytest.raises(GeomError, match="divide"):
            surface_cut(_flat_face(), {"tool": _wall(50.0)}, "positive")
        with pytest.raises(GeomError, match="divide"):
            surface_cut(_flat_face(), {"plane": ([50, 0, 0], [1, 0, 0])}, "both")
        with pytest.raises(GeomError, match="surface body"):
            surface_cut(_flat_face(), {"tool": _box()}, "positive")
        with pytest.raises(GeomError, match="exactly one"):
            surface_cut(_flat_face(), {}, "positive")

    def test_job_takes_the_tool_as_brep(self):
        _, m = surface_cut_job(to_brep(_box()), {"tool": to_brep(_wall(2.0))}, "both")
        assert m["solids"] == 2 and m["volume_mm3"] == pytest.approx(1000.0, abs=1e-6)


# ═══════════════════════════════════════════════════════════════════════════
# DELETE FACE / FILL A HOLE from the body's edges (plan §23.5)
# ═══════════════════════════════════════════════════════════════════════════


def _at(shape, what, pred):
    """Edges or faces of a body whose signature passes pred."""
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE

    from core.geometry.refs import _items, edge_sig, face_sig

    kind, sig = (TopAbs_EDGE, edge_sig) if what == "EDGE" else (TopAbs_FACE, face_sig)
    return [x for x in _items(shape, kind) if pred(sig(x))]


def _holed_cylinder():
    """Half cylinder R=10 x 30 with an 8 x 10 window cut through its crown."""
    arc = [{"type": "ARC", "center": [0, 0], "radius": 10, "start_angle": 0, "end_angle": 180}]
    win = [
        _line(a, b)
        for a, b in (
            ((-4, 10), (4, 10)),
            ((4, 10), (4, 20)),
            ((4, 20), (-4, 20)),
            ((-4, 20), (-4, 10)),
        )
    ]
    plane = {"origin": [0, 0, 0], "normal": [0, -1, 0], "xdir": [1, 0, 0]}
    holed = surface_cut(surface_extrude(arc, 30.0), {"sketch": (win, plane)}, "negative")
    rim = _at(
        holed,
        "EDGE",
        lambda s: 9.9 < s["mid"][2] < 20.1 and abs(s["mid"][0]) < 4.1 and s["mid"][1] > 5,
    )
    return holed, rim


class TestDeleteAndFill:
    def test_delete_a_face_opens_the_solid_and_fill_closes_it(self):
        box = _box()
        opened = delete_faces(box, _at(box, "FACE", lambda s: s["mid"][2] == 10))
        m = solid_measure(opened)
        assert (m["kind"], m["faces"]) == ("sheet", 5)
        assert m["surface_area_mm2"] == pytest.approx(500.0, abs=1e-6)
        rim = _at(opened, "EDGE", lambda s: s["mid"][2] == 10)
        closed = solid_measure(fill_hole(opened, rim, "G0"))
        assert closed["kind"] == "solid"
        assert closed["volume_mm3"] == pytest.approx(1000.0, rel=1e-6)

    @pytest.mark.parametrize(("cont", "g1_deg", "g2_pct"), [("G1", 0.01, None), ("G2", 0.01, 1.0)])
    def test_fill_is_continuous_with_the_face_around_the_hole(self, cont, g1_deg, g2_pct):
        import math

        from core.geometry.surface_analysis import continuity_check

        holed, rim = _holed_cylinder()
        assert len(rim) == 4
        filled = fill_hole(holed, rim, cont)
        m = solid_measure(filled)
        assert m["faces"] == 2
        assert m["surface_area_mm2"] == pytest.approx(math.pi * 10 * 30, rel=1e-4)
        edges = continuity_check(filled)["edges"]
        assert max(e["g0_max_mm"] for e in edges) < 1e-4
        assert max(e["g1_max_deg"] for e in edges) < g1_deg
        if g2_pct:
            assert max(e["g2_max_pct"] for e in edges) < g2_pct

    def test_bad_picks_are_refused(self):
        box = _box()
        with pytest.raises(GeomError, match="open boundary"):
            fill_hole(box, _at(box, "EDGE", lambda s: s["mid"][2] == 10), "G1")
        holed, rim = _holed_cylinder()
        with pytest.raises(GeomError, match="continuity"):
            fill_hole(holed, rim, "G3")
        with pytest.raises(GeomError, match="closed loop"):
            fill_hole(holed, rim[:2], "G1")
        with pytest.raises(GeomError, match="every face"):
            delete_faces(holed, _at(holed, "FACE", lambda s: True))
        with pytest.raises(GeomError, match="at least one"):
            delete_faces(holed, _at(box, "FACE", lambda s: True))

    def test_jobs_take_signatures(self):
        from core.geometry.refs import edge_sig, face_sig

        top = [face_sig(f) for f in _at(_box(), "FACE", lambda s: s["mid"][2] == 10)]
        brep, m = delete_faces_job(to_brep(_box()), top)
        assert m["faces"] == 5
        from core.geometry.serialize import from_brep

        rim = [edge_sig(e) for e in _at(from_brep(brep), "EDGE", lambda s: s["mid"][2] == 10)]
        assert fill_hole_job(brep, rim, "G0")[1]["kind"] == "solid"
