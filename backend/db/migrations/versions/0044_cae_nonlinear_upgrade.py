"""CAE 95-point upgrade (E8~E10): Multi-body Contact, Nonlinear Elastoplasticity & Transient Thermal.

Revision ID: 0044
Revises: 0043
"""

from alembic import op

revision = "0044"
down_revision = "0043"


def upgrade() -> None:
    op.execute("ALTER TABLE cae_studies DROP CONSTRAINT cae_studies_analysis_type_check")
    op.execute(
        """ALTER TABLE cae_studies ADD CONSTRAINT cae_studies_analysis_type_check
        CHECK (analysis_type IN (
            'STATIC', 'MODAL', 'BUCKLING', 'THERMAL', 'THERMAL_STRUCTURAL',
            'CONTACT', 'NONLINEAR_STATIC', 'TRANSIENT_THERMAL'
        ))"""
    )
    op.execute(
        """ALTER TABLE cae_studies
        ADD COLUMN specific_heat_j_kgk numeric(10,2) NOT NULL DEFAULT 500.0
            CHECK (specific_heat_j_kgk > 0),
        ADD COLUMN tangent_modulus_mpa numeric(12,2) NOT NULL DEFAULT 10000.0
            CHECK (tangent_modulus_mpa >= 0),
        ADD COLUMN time_end_s numeric(10,2) NOT NULL DEFAULT 60.0
            CHECK (time_end_s > 0),
        ADD COLUMN time_steps smallint NOT NULL DEFAULT 10
            CHECK (time_steps BETWEEN 1 AND 500),
        ADD COLUMN contact_pairs jsonb NOT NULL DEFAULT '[]'::jsonb"""
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM cae_studies WHERE analysis_type IN ('CONTACT', 'NONLINEAR_STATIC', 'TRANSIENT_THERMAL')"
    )
    op.execute(
        """ALTER TABLE cae_studies
        DROP COLUMN IF EXISTS contact_pairs,
        DROP COLUMN IF EXISTS time_steps,
        DROP COLUMN IF EXISTS time_end_s,
        DROP COLUMN IF EXISTS tangent_modulus_mpa,
        DROP COLUMN IF EXISTS specific_heat_j_kgk"""
    )
    op.execute("ALTER TABLE cae_studies DROP CONSTRAINT cae_studies_analysis_type_check")
    op.execute(
        """ALTER TABLE cae_studies ADD CONSTRAINT cae_studies_analysis_type_check
        CHECK (analysis_type IN ('STATIC', 'MODAL', 'BUCKLING', 'THERMAL', 'THERMAL_STRUCTURAL'))"""
    )
