"""CAE 95-point tests: CAE-T11 (Multi-Body Contact), CAE-T12 (Nonlinear Elastoplasticity), CAE-T13 (Transient Thermal)."""

from __future__ import annotations

import math

from core.cae.contact import ContactBodySpec, ContactPairSpec, solve_contact_assembly
from core.cae.mesh import generate_tet4_mesh
from core.cae.nonlinear import solve_nonlinear_elastoplastic
from core.cae.thermal import solve_transient_thermal


def test_cae_t11_multibody_contact_bonded_and_unilateral() -> None:
    """CAE-T11: Bonded transfers tension across interface; No-Penetration transfers compression & separates under tension."""
    mesh_a = generate_tet4_mesh(None, 5.0, box_dims=(20.0, 10.0, 10.0))
    mesh_b = generate_tet4_mesh(None, 5.0, box_dims=(20.0, 10.0, 10.0))
    # Shift Body B to touch Body A at x = 20.0
    mesh_b.nodes = mesh_b.nodes.copy()
    mesh_b.nodes[:, 0] += 20.0
    mesh_b.bbox_min = (20.0, 0.0, 0.0)
    mesh_b.bbox_max = (40.0, 10.0, 10.0)

    body_a = ContactBodySpec("BlockA", mesh_a, youngs_modulus_mpa=210_000.0)
    body_b = ContactBodySpec("BlockB", mesh_b, youngs_modulus_mpa=210_000.0)

    restraints = [{"body_index": 0, "face_ref": {"axis": "x", "value": 0.0}}]

    # 1. BONDED under tension (+X): Analytical u_max = F*L/(E*A) = 10000*40/(210000*100) = 0.019048 mm
    res_bonded = solve_contact_assembly(
        [body_a, body_b],
        [ContactPairSpec(0, 1, contact_type="BONDED", normal=(1.0, 0.0, 0.0))],
        restraints=restraints,
        loads=[
            {
                "body_index": 1,
                "face_ref": {"axis": "x", "value": 40.0},
                "force_n": [10_000.0, 0.0, 0.0],
            }
        ],
    )
    assert res_bonded.converged
    u_exact = (10_000.0 * 40.0) / (210_000.0 * 100.0)
    end_nodes_b = mesh_b.select_face_nodes({"axis": "x", "value": 40.0}) + mesh_a.node_count
    u_bonded_mean = float(abs(res_bonded.displacements_mm[end_nodes_b, 0].mean()))
    assert abs(u_bonded_mean - u_exact) / u_exact < 0.05

    # 2. NO_PENETRATION under compression (-X): stays active and matches axial compression
    res_comp = solve_contact_assembly(
        [body_a, body_b],
        [ContactPairSpec(0, 1, contact_type="NO_PENETRATION", normal=(1.0, 0.0, 0.0))],
        restraints=restraints,
        loads=[
            {
                "body_index": 1,
                "face_ref": {"axis": "x", "value": 40.0},
                "force_n": [-10_000.0, 0.0, 0.0],
            }
        ],
    )
    assert res_comp.converged
    assert res_comp.active_contact_nodes > 0
    u_comp_mean = float(abs(res_comp.displacements_mm[end_nodes_b, 0].mean()))
    assert abs(u_comp_mean - u_exact) / u_exact < 0.05

    # 3. NO_PENETRATION under tension (+X): releases all contact nodes (clean separation!)
    res_sep = solve_contact_assembly(
        [body_a, body_b],
        [ContactPairSpec(0, 1, contact_type="NO_PENETRATION", normal=(1.0, 0.0, 0.0))],
        restraints=restraints,
        loads=[
            {
                "body_index": 1,
                "face_ref": {"axis": "x", "value": 40.0},
                "force_n": [10_000.0, 0.0, 0.0],
            }
        ],
    )
    assert res_sep.converged
    assert res_sep.active_contact_nodes == 0


def test_cae_t12_nonlinear_elastoplastic_j2_return_mapping() -> None:
    """CAE-T12: J2 isotropic hardening Newton-Raphson converges (res <= 1e-5) and develops plastic strain above yield."""
    # Uniaxial tensile bar 40 x 10 x 10 mm (A = 100 mm^2), yield = 250 MPa
    # Force = 30,000 N -> nominal stress = 300 MPa > 250 MPa (yields!)
    mesh = generate_tet4_mesh(None, 5.0, box_dims=(40.0, 10.0, 10.0))
    res = solve_nonlinear_elastoplastic(
        mesh,
        youngs_modulus_mpa=200_000.0,
        poissons_ratio=0.3,
        yield_strength_mpa=250.0,
        tangent_modulus_mpa=20_000.0,
        restraints=[{"face_ref": {"axis": "x", "value": 0.0}}],
        loads=[{"face_ref": {"axis": "x", "value": 40.0}, "force_n": [30_000.0, 0.0, 0.0]}],
        load_steps=6,
    )
    assert res.converged, f"NR did not converge, residual={res.final_residual_norm}"
    assert res.final_residual_norm <= 1e-5
    assert res.max_plastic_strain > 1e-4, "Expected non-zero plastic strain above 250 MPa yield"
    # Plastic displacement must exceed purely linear elastic displacement F*L/(E*A) = 0.06 mm
    u_elastic = (30_000.0 * 40.0) / (200_000.0 * 100.0)
    assert res.max_disp_mm > u_elastic * 1.15


def test_cae_t13_transient_thermal_lumped_capacitance_cooling() -> None:
    """CAE-T13: Backward Euler transient cooling matches lumped-capacitance T(t)=T_inf+(T0-T_inf)*exp(-t/tau) within +-2%."""
    # High-conductivity aluminum cube (10 x 10 x 10 mm, Biot << 0.1) cooling by convection on x=max face
    # V = 1e-6 m^3, A = 1e-4 m^2, rho = 2700 kg/m^3, cp = 900 J/(kg*K), h = 250 W/(m^2*K)
    # tau = (rho * cp * V) / (h * A) = (2700 * 900 * 1e-6) / (250 * 1e-4) = 97.2 s
    mesh = generate_tet4_mesh(None, 5.0, box_dims=(10.0, 10.0, 10.0))
    t0 = 120.0
    t_inf = 20.0
    t_end = 100.0
    res = solve_transient_thermal(
        mesh,
        thermal_conductivity_w_mk=200.0,
        density_kg_m3=2700.0,
        specific_heat_j_kgk=900.0,
        initial_temp_c=t0,
        ambient_temp_c=t_inf,
        thermal_bcs=[
            {
                "type": "CONVECTION",
                "face_ref": {"axis": "x", "value": 10.0},
                "h_w_m2k": 250.0,
                "ambient_temp_c": t_inf,
            }
        ],
        time_end_s=t_end,
        time_steps=50,
    )
    tau = (2700.0 * 900.0 * 1e-6) / (250.0 * 1e-4)
    t_exact = t_inf + (t0 - t_inf) * math.exp(-t_end / tau)
    t_sim = res.mean_temp_history_c[-1]
    rel_err = abs(t_sim - t_exact) / (t0 - t_inf)
    assert rel_err < 0.02, (
        f"Transient thermal error {rel_err:.4f} >= 2% (sim={t_sim:.2f}, exact={t_exact:.2f})"
    )
    # Temperature monotonically decreases during cooling
    assert (
        res.mean_temp_history_c[0]
        > res.mean_temp_history_c[len(res.mean_temp_history_c) // 2]
        > t_sim
    )
