"""PR #78~#82 regression & boundary tests (Surface S6~S8 extensions & Built-in DWG).

Passing tests guard PR #78~#82 invariants today.
Tests marked ``@pytest.mark.xfail(strict=True)`` lock in verified P1/P2 defects
from ``docs/reviews/pr78-82-review.md``; remove ``xfail`` as each defect is fixed.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import ezdxf
import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

from core.dxf import dwg, dwg_builtin
from core.geometry import surface_analysis as sa
from core.geometry import surface_edit as se
from core.geometry.errors import GeomError


def _flat_face():
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.gp import gp_Pln

    return BRepBuilderAPI_MakeFace(
        gp_Pln(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 0.0, 40.0, 0.0, 40.0
    ).Shape()


# ---------------------------------------------------------------------------
# 1. Active boundary guards (must PASS today)
# ---------------------------------------------------------------------------


def test_curvature_map_full_body_is_json_serializable() -> None:
    """PR #81 full-body curvature map produces strict JSON (no NaN/Inf)."""
    box = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 30.0, 20.0, 10.0).Shape()
    res = sa.curvature_map(box, n_u=4, n_v=4)
    assert len(res["samples"]) == 6 * 16
    encoded = json.dumps(res, allow_nan=False)
    assert '"stats"' in encoded


def test_signed_draft_box_vertical_top_and_undercut_faces() -> None:
    """PR #81 signed draft distinguishes +90 top, 0 vertical walls, and -90 undercut bottom."""
    box = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 20.0, 20.0, 10.0).Shape()
    res = sa.draft_analysis(box, pull_direction=(0.0, 0.0, 1.0), n_u=3, n_v=3)
    assert len(res["faces"]) == 6
    assert any(f["undercut"] for f in res["faces"])
    assert any(f["draft_max_deg"] > 80.0 for f in res["faces"])


@pytest.mark.skipif(not dwg.builtin_available(), reason="ezdwg is not installed")
def test_dwg_builtin_roundtrip_basic_entities(tmp_path: Path) -> None:
    """PR #82 built-in DWG writer/reader preserves LINE and CIRCLE without external ODA."""
    doc = ezdxf.new("R2000", setup=False)
    msp = doc.modelspace()
    msp.add_line((0, 0), (100, 0))
    msp.add_circle((50, 50), 20)
    src_dxf = tmp_path / "in.dxf"
    out_dwg = tmp_path / "out.dwg"
    out_dxf = tmp_path / "back.dxf"
    rep_w = tmp_path / "rep_w.json"
    rep_r = tmp_path / "rep_r.json"
    doc.saveas(src_dxf)

    dwg_builtin.write(str(src_dxf), str(out_dwg), str(rep_w), "2000")
    dwg_builtin.read(str(out_dwg), str(out_dxf), str(rep_r))
    back = ezdxf.read(io.StringIO(out_dxf.read_text(encoding="utf-8")))
    kinds = sorted(e.dxftype() for e in back.modelspace())
    assert kinds == ["CIRCLE", "LINE"]


# ---------------------------------------------------------------------------
# 2. Fixed P1/P2 regression tests (formerly strict xfail — now PASS)
# ---------------------------------------------------------------------------


def test_r82_01_surface_cut_plane_miss_single_side_rejected() -> None:
    """A plane that does not intersect the face must be refused for keep='positive' too."""
    with pytest.raises(GeomError) as exc:
        se.surface_cut(
            _flat_face(), {"plane": ((100.0, 0.0, 0.0), (1.0, 0.0, 0.0))}, keep="positive"
        )
    assert exc.value.code == "GEOM_INVALID_PARAM"


def test_r82_02_draft_analysis_enforces_max_sample_points() -> None:
    """Requesting > MAX_SAMPLE_POINTS in draft_analysis must raise GEOM_INVALID_PARAM."""
    box = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 10.0, 10.0, 10.0).Shape()
    # 6 faces * 200 * 200 = 240,000 > MAX_SAMPLE_POINTS (200,000)
    with pytest.raises(GeomError) as exc:
        sa.draft_analysis(box, pull_direction=(0.0, 0.0, 1.0), n_u=200, n_v=200)
    assert exc.value.code == "GEOM_INVALID_PARAM"


def test_r82_06_fill_hole_rejects_closed_cylinder_seam_edge() -> None:
    """A closed cylinder's vertical seam edge belongs to one face twice and is not a free hole edge."""
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    cyl = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 10.0, 20.0).Shape()
    # Delete top & bottom faces so only the closed cylindrical wall remains (seam edge is vertical)
    faces = se._subs(cyl, se.TopAbs_FACE)
    # Keep only the cylindrical face (non-planar)
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    wall = next(
        f for f in faces if BRepAdaptor_Surface(TopoDS.Face(f)).GetType() == GeomAbs_Cylinder
    )
    exp = TopExp_Explorer(wall, TopAbs_EDGE)
    edges = []
    while exp.More():
        edges.append(TopoDS.Edge(exp.Current()))
        exp.Next()
    # Passing all edges (including the seam edge) to fill_hole must fail with GEOM_INVALID_PARAM
    with pytest.raises(GeomError) as exc:
        se.fill_hole(wall, edges, continuity="G0")
    assert exc.value.code == "GEOM_INVALID_PARAM"


@pytest.mark.skipif(not dwg.builtin_available(), reason="ezdwg is not installed")
def test_r82_08_dwg_builtin_preserves_2d_polyline_elevation(tmp_path: Path) -> None:
    """2D POLYLINE with non-zero Z elevation must preserve its Z height across DWG roundtrip."""
    doc = ezdxf.new("R2000", setup=False)
    msp = doc.modelspace()
    pl = msp.add_polyline2d([(0, 0), (50, 0), (50, 30)], dxfattribs={"elevation": (0, 0, 15.0)})
    assert pl.dxf.elevation.z == pytest.approx(15.0)
    src_dxf = tmp_path / "poly.dxf"
    out_dwg = tmp_path / "poly.dwg"
    out_dxf = tmp_path / "poly_back.dxf"
    doc.saveas(src_dxf)
    dwg_builtin.write(str(src_dxf), str(out_dwg), str(tmp_path / "rw.json"), "2000")
    dwg_builtin.read(str(out_dwg), str(out_dxf), str(tmp_path / "rr.json"))
    back = ezdxf.read(io.StringIO(out_dxf.read_text(encoding="utf-8")))
    pls = [e for e in back.modelspace() if e.dxftype() in ("LWPOLYLINE", "POLYLINE")]
    assert len(pls) == 1
    elev = getattr(pls[0].dxf, "elevation", 0.0)
    z = elev.z if hasattr(elev, "z") else float(elev)
    assert z == pytest.approx(15.0, abs=1e-3)
