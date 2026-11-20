"""CAE E1~E3: linear static FEA studies table (plan.md §24).

Revision ID: 0040
Revises: 0039
"""

from alembic import op

revision = "0040"
down_revision = "0039"


def upgrade() -> None:
    op.execute(
        """CREATE TABLE cae_studies (
        study_id bigserial PRIMARY KEY,
        document_id bigint NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
        feature_id bigint REFERENCES features(feature_id) ON DELETE SET NULL,
        material_code text NOT NULL DEFAULT 'STEEL' CHECK (char_length(material_code) BETWEEN 1 AND 32),
        youngs_modulus_mpa numeric(12,2) NOT NULL CHECK (youngs_modulus_mpa > 0),
        poissons_ratio numeric(5,4) NOT NULL CHECK (poissons_ratio > 0 AND poissons_ratio < 0.495),
        yield_strength_mpa numeric(10,2) NOT NULL CHECK (yield_strength_mpa > 0),
        mesh_size_mm numeric(8,3) NOT NULL CHECK (mesh_size_mm > 0),
        restraints jsonb NOT NULL DEFAULT '[]'::jsonb,
        loads jsonb NOT NULL DEFAULT '[]'::jsonb,
        status text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'SOLVED', 'ERROR')),
        error_code text,
        node_count integer,
        element_count integer,
        max_disp_mm numeric(12,6),
        max_von_mises_mpa numeric(12,4),
        min_safety_factor numeric(12,4),
        result_path text,
        created_by bigint NOT NULL REFERENCES users(user_id),
        created_at timestamptz NOT NULL DEFAULT now())"""
    )
    op.execute("CREATE INDEX ix_cae_studies_doc ON cae_studies (document_id)")
    op.execute(
        "CREATE TRIGGER trg_audit_cae_studies AFTER INSERT OR UPDATE OR DELETE ON cae_studies "
        "FOR EACH ROW EXECUTE FUNCTION fn_audit_log('study_id')"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS cae_studies")
