"""CAE Simcenter upgrade (E4~E7): Tet10, Modal, Linear Buckling, Thermal-Structural.

Revision ID: 0042
Revises: 0041
"""

from alembic import op

revision = "0042"
down_revision = "0041"


def upgrade() -> None:
    op.execute(
        """ALTER TABLE cae_studies
        ADD COLUMN analysis_type text NOT NULL DEFAULT 'STATIC'
            CHECK (analysis_type IN ('STATIC', 'MODAL', 'BUCKLING', 'THERMAL', 'THERMAL_STRUCTURAL')),
        ADD COLUMN element_order smallint NOT NULL DEFAULT 1
            CHECK (element_order IN (1, 2)),
        ADD COLUMN density_kg_m3 numeric(10,2) NOT NULL DEFAULT 7850
            CHECK (density_kg_m3 > 0),
        ADD COLUMN thermal_conductivity_w_mk numeric(10,4) NOT NULL DEFAULT 50.0
            CHECK (thermal_conductivity_w_mk > 0),
        ADD COLUMN thermal_expansion_1_k numeric(12,9) NOT NULL DEFAULT 0.000012
            CHECK (thermal_expansion_1_k >= 0),
        ADD COLUMN ambient_temp_c numeric(8,2) NOT NULL DEFAULT 20.0,
        ADD COLUMN thermal_bcs jsonb NOT NULL DEFAULT '[]'::jsonb,
        ADD COLUMN mode_count smallint NOT NULL DEFAULT 6
            CHECK (mode_count BETWEEN 1 AND 20),
        ADD COLUMN summary_metrics jsonb NOT NULL DEFAULT '{}'::jsonb"""
    )


def downgrade() -> None:
    op.execute(
        """ALTER TABLE cae_studies
        DROP COLUMN IF EXISTS summary_metrics,
        DROP COLUMN IF EXISTS mode_count,
        DROP COLUMN IF EXISTS thermal_bcs,
        DROP COLUMN IF EXISTS ambient_temp_c,
        DROP COLUMN IF EXISTS thermal_expansion_1_k,
        DROP COLUMN IF EXISTS thermal_conductivity_w_mk,
        DROP COLUMN IF EXISTS density_kg_m3,
        DROP COLUMN IF EXISTS element_order,
        DROP COLUMN IF EXISTS analysis_type"""
    )
