"""features: parametric 3D features per document (S5: EXTRUDE)

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    op.execute(
        """CREATE TABLE features (
        feature_id bigserial PRIMARY KEY,
        document_id bigint NOT NULL REFERENCES documents ON DELETE CASCADE,
        seq integer NOT NULL,
        feature_type text NOT NULL CHECK (feature_type IN ('EXTRUDE')),
        params jsonb NOT NULL,
        status text NOT NULL DEFAULT 'OK' CHECK (status IN ('OK', 'ERROR')),
        error_code text,
        brep_key text,
        metrics jsonb,
        created_by bigint NOT NULL REFERENCES users,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (document_id, seq))"""
    )
    op.execute(
        "CREATE TRIGGER trg_audit_features AFTER INSERT OR UPDATE OR DELETE ON features "
        "FOR EACH ROW EXECUTE FUNCTION fn_audit_log('feature_id')"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS features CASCADE")
