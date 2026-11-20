"""CAE E1~E3 Linear Static FEA tests: cantilever beam, simply-supported plate, underconstraint, mesh limits, and PDF report (CAE-T01 ~ CAE-T05)."""

from __future__ import annotations

import io

import numpy as np
import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Pnt
from pypdf import PdfReader

from core.cae.fea import solve_linear_static
from core.cae.mesh import CaeError, generate_tet4_mesh


def test_cae_t01_cantilever_beam_deflection_and_bending_stress() -> None:
    """CAE-T01: Steel cantilever beam (E=210 GPa, L=200, b=20, h=10, F=100 N) matches PL^3/(3EI) within ±2% and Mc/I within ±5%."""
    e_mpa = 210_000.0
    nu = 0.3
    sy_mpa = 250.0
    lx, ly, lz = 200.0, 20.0, 10.0
    force_n = -100.0

    shape = BRepPrimAPI_MakeBox(gp_Pnt(0.0, 0.0, 0.0), lx, ly, lz).Shape()
    mesh = generate_tet4_mesh(shape, mesh_size_mm=5.0)
    assert mesh.node_count > 100
    assert mesh.element_count > 300

    res = solve_linear_static(
        mesh,
        youngs_modulus_mpa=e_mpa,
        poissons_ratio=nu,
        yield_strength_mpa=sy_mpa,
        restraints=[{"axis": "x", "value": 0.0}],
        loads=[
            {
                "type": "FORCE",
                "face_ref": {"axis": "x", "value": lx},
                "vector_n": [0.0, 0.0, force_n],
            }
        ],
    )

    # Analytical Euler-Bernoulli values:
    # I = b * h^3 / 12 = 20 * 10^3 / 12 = 1666.6667 mm^4
    # w_max = F * L^3 / (3 * E * I) = 100 * 200^3 / (3 * 210000 * 1666.6667) = 0.76190476 mm
    # sigma_max = M * c / I = (100 * 200) * 5 / 1666.6667 = 60.0 MPa
    i_yy = (ly * (lz**3)) / 12.0
    expected_disp = (abs(force_n) * (lx**3)) / (3.0 * e_mpa * i_yy)
    expected_stress = (abs(force_n) * lx * (0.5 * lz)) / i_yy

    assert res.max_disp_mm == pytest.approx(expected_disp, rel=0.02)
    assert res.max_von_mises_mpa == pytest.approx(expected_stress, rel=0.05)
    assert res.min_safety_factor == pytest.approx(sy_mpa / res.max_von_mises_mpa, rel=1e-4)


def test_cae_t02_simply_supported_plate_uniform_pressure() -> None:
    """CAE-T02: Simply supported square plate (a=b=100, t=4, E=210 GPa, v=0.3, q=0.5 MPa) matches Navier analytical center deflection within ±3%."""
    e_mpa = 210_000.0
    nu = 0.3
    sy_mpa = 250.0
    a, b, t = 100.0, 100.0, 4.0
    q_mpa = 0.5

    mesh = generate_tet4_mesh(box_dims=(a, b, t), mesh_size_mm=5.0)
    res = solve_linear_static(
        mesh,
        youngs_modulus_mpa=e_mpa,
        poissons_ratio=nu,
        yield_strength_mpa=sy_mpa,
        restraints=[{"rim": True, "z": 0.0}],
        loads=[
            {
                "type": "PRESSURE",
                "face_ref": {"axis": "z", "value": t},
                "magnitude_mpa": q_mpa,
            }
        ],
    )

    # Navier analytical solution for simply-supported square plate under uniform load q:
    # w_max = 0.00406235 * q * a^4 / D, where D = E * t^3 / (12 * (1 - v^2))
    d_flex = (e_mpa * (t**3)) / (12.0 * (1.0 - nu * nu))
    expected_w_center = 0.00406235 * q_mpa * (a**4) / d_flex
    assert res.max_disp_mm == pytest.approx(expected_w_center, rel=0.03)
    assert res.min_safety_factor == pytest.approx(sy_mpa / res.max_von_mises_mpa, rel=1e-4)


def test_cae_t03_underconstrained_no_restraint_or_collinear_restraint() -> None:
    """CAE-T03: Missing restraints or collinear edge restraints raise CaeError('CAE_UNDERCONSTRAINED')."""
    mesh = generate_tet4_mesh(box_dims=(40.0, 20.0, 10.0), mesh_size_mm=10.0)
    load = [
        {"type": "FORCE", "face_ref": {"axis": "x", "value": 40.0}, "vector_n": [0.0, 0.0, -50.0]}
    ]

    # 1. Empty restraints
    with pytest.raises(CaeError) as exc1:
        solve_linear_static(
            mesh,
            youngs_modulus_mpa=210_000.0,
            poissons_ratio=0.3,
            yield_strength_mpa=250.0,
            restraints=[],
            loads=load,
        )
    assert exc1.value.code == "CAE_UNDERCONSTRAINED"

    # 2. Collinear restraint (only nodes along the line x=0, y=0)
    collinear_nodes = (np.abs(mesh.nodes[:, 0]) < 1e-3) & (np.abs(mesh.nodes[:, 1]) < 1e-3)
    # Use a narrow tol around midpoint of edge (0, 0, z) — wait, let's test _check_restraints_not_underconstrained via 2 points or collinear line
    from core.cae.fea import _check_restraints_not_underconstrained

    col_ids = np.nonzero(collinear_nodes)[0]
    assert col_ids.size >= 2
    with pytest.raises(CaeError) as exc2:
        _check_restraints_not_underconstrained(mesh.nodes, col_ids)
    assert exc2.value.code == "CAE_UNDERCONSTRAINED"


def test_cae_t04_max_nodes_exceeded_rejected_without_oom() -> None:
    """CAE-T04: Requesting an ultra-fine mesh exceeding MAX_NODES (100,000) raises CAE_MESH_LIMIT_EXCEEDED immediately."""
    with pytest.raises(CaeError) as exc:
        generate_tet4_mesh(box_dims=(500.0, 500.0, 500.0), mesh_size_mm=1.0)
    assert exc.value.code == "CAE_MESH_LIMIT_EXCEEDED"


def test_cae_api_and_pdf_report_disclaimer(client, world) -> None:
    """CAE API end-to-end: create study, solve cantilever beam, fetch result & PDF report with mandatory disclaimer."""
    h = world.h["designer"]
    doc_id = world.doc

    st_res = client.post(
        f"/api/documents/{doc_id}/cae/studies",
        headers=h,
        json={
            "material_code": "SS400",
            "youngs_modulus_mpa": 210_000.0,
            "poissons_ratio": 0.3,
            "yield_strength_mpa": 250.0,
            "mesh_size_mm": 5.0,
            "restraints": [{"axis": "x", "value": 0.0}],
            "loads": [
                {
                    "type": "FORCE",
                    "face_ref": {"axis": "x", "value": 200.0},
                    "vector_n": [0.0, 0.0, -100.0],
                }
            ],
        },
    )
    assert st_res.status_code == 201
    study_id = st_res.json()["data"]["study_id"]

    solve_res = client.post(
        f"/api/cae/studies/{study_id}/solve",
        headers=h,
        json={"box_dims": [200.0, 20.0, 10.0]},
    )
    assert solve_res.status_code == 200
    data = solve_res.json()["data"]
    assert data["status"] == "SOLVED"
    assert data["max_disp_mm"] == pytest.approx(0.761905, rel=0.02)
    assert data["max_von_mises_mpa"] == pytest.approx(60.0, rel=0.05)

    res_json = client.get(f"/api/cae/studies/{study_id}/result", headers=h)
    assert res_json.status_code == 200
    assert len(res_json.json()["data"]["von_mises_mpa"]) == data["node_count"]

    pdf_res = client.get(f"/api/cae/studies/{study_id}/report?format=pdf", headers=h)
    assert pdf_res.status_code == 200
    assert pdf_res.headers["content-type"] == "application/pdf"
    reader = PdfReader(io.BytesIO(pdf_res.content))
    assert len(reader.pages) == 1
    meta_title = str(reader.metadata.title or "") if reader.metadata else ""
    page_text = reader.pages[0].extract_text() or ""
    assert "선형 정적 근사 해석 참고용" in (page_text + meta_title)
