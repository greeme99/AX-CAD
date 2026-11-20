"""Assembly A2: rigid sub-assemblies, patterns, mirror, visibility/suppression (plan.md §23.10).

Revision ID: 0037
Revises: 0036
"""

from alembic import op

revision = "0037"
down_revision = "0036"


def upgrade() -> None:
    op.execute(
        """CREATE TABLE assembly_patterns (
        pattern_id bigserial PRIMARY KEY,
        document_id bigint NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
        source_component_id bigint NOT NULL REFERENCES assembly_components(component_id) ON DELETE CASCADE,
        pattern_type text NOT NULL CHECK (pattern_type IN ('LINEAR', 'CIRCULAR')),
        params jsonb NOT NULL,
        created_by bigint NOT NULL REFERENCES users(user_id),
        created_at timestamptz NOT NULL DEFAULT now())"""
    )
    op.execute("CREATE INDEX ix_assembly_patterns_doc ON assembly_patterns (document_id)")
    op.execute(
        "CREATE TRIGGER trg_audit_assembly_patterns AFTER INSERT OR UPDATE OR DELETE "
        "ON assembly_patterns FOR EACH ROW EXECUTE FUNCTION fn_audit_log('pattern_id')"
    )

    op.execute("ALTER TABLE assembly_components ALTER COLUMN source_feature_id DROP NOT NULL")
    op.execute(
        "ALTER TABLE assembly_components ADD COLUMN IF NOT EXISTS component_kind text "
        "NOT NULL DEFAULT 'PART' CHECK (component_kind IN ('PART', 'SUBASSEMBLY'))"
    )
    op.execute(
        "ALTER TABLE assembly_components ADD COLUMN IF NOT EXISTS visible boolean NOT NULL DEFAULT true"
    )
    op.execute(
        "ALTER TABLE assembly_components ADD COLUMN IF NOT EXISTS suppressed boolean NOT NULL DEFAULT false"
    )
    op.execute(
        "ALTER TABLE assembly_components ADD COLUMN IF NOT EXISTS pattern_id bigint "
        "REFERENCES assembly_patterns(pattern_id) ON DELETE CASCADE"
    )
    op.execute("ALTER TABLE assembly_components ADD COLUMN IF NOT EXISTS pattern_index integer")
    op.execute(
        "ALTER TABLE assembly_components ADD COLUMN IF NOT EXISTS mirrored_from_id bigint "
        "REFERENCES assembly_components(component_id) ON DELETE SET NULL"
    )
    op.execute("ALTER TABLE assembly_components ADD COLUMN IF NOT EXISTS mirror_plane jsonb")


def downgrade() -> None:
    op.execute(
        "DELETE FROM assembly_components WHERE component_kind = 'SUBASSEMBLY' OR pattern_id IS NOT NULL"
    )
    op.execute("ALTER TABLE assembly_components DROP COLUMN IF EXISTS mirror_plane")
    op.execute("ALTER TABLE assembly_components DROP COLUMN IF EXISTS mirrored_from_id")
    op.execute("ALTER TABLE assembly_components DROP COLUMN IF EXISTS pattern_index")
    op.execute("ALTER TABLE assembly_components DROP COLUMN IF EXISTS pattern_id")
    op.execute("ALTER TABLE assembly_components DROP COLUMN IF EXISTS suppressed")
    op.execute("ALTER TABLE assembly_components DROP COLUMN IF EXISTS visible")
    op.execute("ALTER TABLE assembly_components DROP COLUMN IF EXISTS component_kind")
    op.execute("ALTER TABLE assembly_components ALTER COLUMN source_feature_id SET NOT NULL")
    op.execute("DROP TABLE IF EXISTS assembly_patterns")
