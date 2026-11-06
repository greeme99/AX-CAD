"""Track 9 Q2: assemblies (plan.md §15.3).

- assembly_components: a body of a PART document placed in an ASSEMBLY document (translation mm,
  XYZ Euler rotation deg). The body is the part's result that descends from source_feature_id, so
  later machining of the part shows up in the assembly.
- assembly_mates: a face of one component tied to a face of another (face signatures in part
  coordinates, as the feature refs of Track 8 P2-4).

Revision ID: 0025
Revises: 0024
"""

from alembic import op

revision = "0025"
down_revision = "0024"


def upgrade() -> None:
    op.execute(
        """CREATE TABLE assembly_components (
        component_id bigserial PRIMARY KEY,
        document_id bigint NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
        source_document_id bigint NOT NULL REFERENCES documents(document_id),
        source_feature_id bigint NOT NULL,
        name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200),
        placement jsonb NOT NULL,
        fixed boolean NOT NULL DEFAULT false,
        seq integer NOT NULL,
        created_by bigint NOT NULL REFERENCES users(user_id),
        created_at timestamptz NOT NULL DEFAULT now(),
        CHECK (document_id <> source_document_id))"""
    )
    op.execute("CREATE INDEX ix_assembly_components_doc ON assembly_components (document_id, seq)")
    op.execute(
        "CREATE INDEX ix_assembly_components_source ON assembly_components (source_document_id)"
    )
    op.execute(
        """CREATE TABLE assembly_mates (
        mate_id bigserial PRIMARY KEY,
        document_id bigint NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
        mate_type text NOT NULL
            CHECK (mate_type IN ('COINCIDENT', 'DISTANCE', 'PARALLEL', 'ANGLE', 'CONCENTRIC')),
        a jsonb NOT NULL,
        b jsonb NOT NULL,
        value double precision,
        flip boolean NOT NULL DEFAULT false,
        status text NOT NULL DEFAULT 'NEW' CHECK (status IN ('NEW', 'OK', 'FAILED')),
        residual double precision,
        created_by bigint NOT NULL REFERENCES users(user_id),
        created_at timestamptz NOT NULL DEFAULT now())"""
    )
    op.execute("CREATE INDEX ix_assembly_mates_doc ON assembly_mates (document_id)")
    for table, key in (("assembly_components", "component_id"), ("assembly_mates", "mate_id")):
        op.execute(
            f"CREATE TRIGGER trg_audit_{table} AFTER INSERT OR UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION fn_audit_log('{key}')"
        )
    op.execute("ALTER TABLE bom_headers DROP CONSTRAINT IF EXISTS bom_headers_source_type_check")
    op.execute(
        "ALTER TABLE bom_headers ADD CONSTRAINT bom_headers_source_type_check "
        "CHECK (source_type IN ('DXF_BLOCK', 'STEP_ASSEMBLY', 'ASSEMBLY'))"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM bom_items WHERE bom_id IN "
        "(SELECT bom_id FROM bom_headers WHERE source_type = 'ASSEMBLY')"
    )
    op.execute("DELETE FROM bom_headers WHERE source_type = 'ASSEMBLY'")
    op.execute("ALTER TABLE bom_headers DROP CONSTRAINT bom_headers_source_type_check")
    op.execute(
        "ALTER TABLE bom_headers ADD CONSTRAINT bom_headers_source_type_check "
        "CHECK (source_type IN ('DXF_BLOCK', 'STEP_ASSEMBLY'))"
    )
    op.execute("DROP TABLE assembly_mates")
    op.execute("DROP TABLE assembly_components")
