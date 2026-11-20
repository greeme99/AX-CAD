"""Assembly A3: ignored interference pairs and exploded views (plan.md §23.10).

Revision ID: 0038
Revises: 0037
"""

from alembic import op

revision = "0038"
down_revision = "0037"


def upgrade() -> None:
    op.execute(
        """CREATE TABLE assembly_ignored_pairs (
        ignore_id bigserial PRIMARY KEY,
        document_id bigint NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
        comp_a_id bigint NOT NULL,
        comp_b_id bigint NOT NULL,
        reason text NOT NULL DEFAULT '' CHECK (char_length(reason) <= 500),
        created_by bigint NOT NULL REFERENCES users(user_id),
        created_at timestamptz NOT NULL DEFAULT now(),
        CHECK (comp_a_id < comp_b_id),
        UNIQUE (document_id, comp_a_id, comp_b_id))"""
    )
    op.execute("CREATE INDEX ix_assembly_ignored_pairs_doc ON assembly_ignored_pairs (document_id)")
    op.execute(
        """CREATE TABLE assembly_explodes (
        explode_id bigserial PRIMARY KEY,
        document_id bigint NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
        name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200),
        steps jsonb NOT NULL DEFAULT '[]'::jsonb,
        created_by bigint NOT NULL REFERENCES users(user_id),
        created_at timestamptz NOT NULL DEFAULT now())"""
    )
    op.execute("CREATE INDEX ix_assembly_explodes_doc ON assembly_explodes (document_id)")
    for table, key in (
        ("assembly_ignored_pairs", "ignore_id"),
        ("assembly_explodes", "explode_id"),
    ):
        op.execute(
            f"CREATE TRIGGER trg_audit_{table} AFTER INSERT OR UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION fn_audit_log('{key}')"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS assembly_explodes")
    op.execute("DROP TABLE IF EXISTS assembly_ignored_pairs")
