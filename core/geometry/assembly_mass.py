"""Assembly A4: mass properties (parallel-axis inertia tensor), multi-level BOM, and A-ASM labor (plan.md §23.10)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import numpy as np
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopoDS import TopoDS_Shape

from core.geometry import assembly as asm
from core.geometry.serialize import from_brep

DEFAULT_DENSITY_G_CM3 = 7.85  # carbon steel fallback (g/cm^3)


def part_mass_props(
    shape: TopoDS_Shape, density_g_cm3: float = DEFAULT_DENSITY_G_CM3
) -> dict[str, Any]:
    """Exact volume (mm^3), mass (kg), CoG (mm), and inertia tensor about CoG (kg*mm^2)."""
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    vol = float(props.Mass())  # mm^3
    scale = float(density_g_cm3) * 1e-6  # (g/cm^3) -> kg / mm^3
    mass_kg = vol * scale
    cg = props.CentreOfMass()
    cog = [float(cg.X()), float(cg.Y()), float(cg.Z())]
    # OCCT MatrixOfInertia() returns I about CoG with unit density: diagonal positive, off-diag -∫xy dV
    mat = props.MatrixOfInertia()
    inertia = [[float(mat.Value(r, c)) * scale for c in (1, 2, 3)] for r in (1, 2, 3)]
    return {
        "volume_mm3": vol,
        "mass_kg": mass_kg,
        "density_g_cm3": float(density_g_cm3),
        "cog_mm": cog,
        "inertia_kg_mm2": inertia,
    }


def assembly_mass_job(
    parts: dict[str, bytes],
    comps: list[dict[str, Any]],
    densities: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Combine placed components into total mass, CoG, and parallel-axis inertia tensor."""
    dens = densities or {}
    shapes = {k: from_brep(v) for k, v in parts.items()}
    items: list[dict[str, Any]] = []
    total_mass = 0.0
    total_vol = 0.0
    weighted_cog = np.zeros(3, dtype=float)

    for c in comps:
        d = float(dens.get(c["part"], DEFAULT_DENSITY_G_CM3))
        placed_shape = asm.placed(shapes[c["part"]], c["t"], c["r"])
        mp = part_mass_props(placed_shape, d)
        items.append({"id": c["id"], "name": c["name"], **mp})
        total_mass += mp["mass_kg"]
        total_vol += mp["volume_mm3"]
        weighted_cog += mp["mass_kg"] * np.asarray(mp["cog_mm"], dtype=float)

    cog_total = (weighted_cog / total_mass) if total_mass > 0 else np.zeros(3, dtype=float)
    i_total = np.zeros((3, 3), dtype=float)
    eye3 = np.eye(3, dtype=float)
    for it in items:
        m_k = it["mass_kg"]
        d_vec = np.asarray(it["cog_mm"], dtype=float) - cog_total
        # Parallel axis theorem: I_total = sum( I_k + m_k * (|d|^2 I - d d^T) )
        i_k = np.asarray(it["inertia_kg_mm2"], dtype=float)
        i_total += i_k + m_k * (float(d_vec @ d_vec) * eye3 - np.outer(d_vec, d_vec))

    return {
        "total_mass_kg": total_mass,
        "total_volume_mm3": total_vol,
        "center_of_gravity_mm": [float(v) for v in cog_total],
        "inertia_kg_mm2": [[float(i_total[r, c]) for c in range(3)] for r in range(3)],
        "components": items,
    }


def build_assembly_bom(
    flat_items: list[dict[str, Any]], mode: str = "indented"
) -> list[dict[str, Any]]:
    """Build indented (hierarchical) or rolled_up (aggregated by part) BOM from expanded items."""
    if mode == "indented":
        return [
            {
                "level": int(it.get("depth", 0)) + 1,
                "path": it["path"],
                "name": it["name"],
                "component_id": it["component_id"],
                "source_document_id": it["source_document_id"],
                "source_feature_id": it["source_feature_id"],
                "qty": 1,
            }
            for it in flat_items
        ]
    if mode == "rolled_up":
        grouped: dict[tuple[int, int | None], dict[str, Any]] = {}
        for it in flat_items:
            key = (int(it["source_document_id"]), it["source_feature_id"])
            if key not in grouped:
                grouped[key] = {
                    "source_document_id": key[0],
                    "source_feature_id": key[1],
                    "name": it["name"],
                    "qty": 0,
                    "paths": [],
                }
            grouped[key]["qty"] += 1
            grouped[key]["paths"].append(it["path"])
        return list(grouped.values())
    raise ValueError(f"Unknown BOM mode: {mode}")


def compute_assembly_labor(
    part_count: int,
    mate_count: int,
    *,
    base_min_per_part: Decimal = Decimal("2.0"),
    min_per_mate: Decimal = Decimal("1.5"),
    hourly_rate: Decimal = Decimal(45000),
) -> dict[str, str | int]:
    """A-ASM assembly labor quote rule using exact Decimal arithmetic."""
    minutes = (Decimal(part_count) * base_min_per_part) + (Decimal(mate_count) * min_per_mate)
    cost = (minutes / Decimal(60) * hourly_rate).quantize(Decimal(1))
    return {
        "rule_code": "A-ASM",
        "part_count": part_count,
        "mate_count": mate_count,
        "labor_minutes": str(minutes),
        "labor_cost_krw": str(cost),
    }
