"""BOM (FN-23): a header per generation from a DXF revision or the 3D model, items with the
expanded quantity, where they came from, and whether the part number was read (AUTO), typed in
(MANUAL) or is still missing (UNMAPPED).

Revision ID: 0011
Revises: 0010
"""

from alembic import op

revision = "0011"
down_revision = "0010"


def upgrade() -> None:
    op.execute(
        """CREATE TABLE bom_headers (
        bom_id bigserial PRIMARY KEY,
        bom_no text NOT NULL UNIQUE,
        project_id bigint NOT NULL REFERENCES projects (project_id),
        document_id bigint NOT NULL REFERENCES documents (document_id),
        revision_id text REFERENCES document_revisions (revision_id),
        source_type text NOT NULL CHECK (source_type IN ('DXF_BLOCK', 'STEP_ASSEMBLY')),
        source_hash text NOT NULL CHECK (source_hash ~ '^[0-9a-f]{64}$'),
        warnings jsonb NOT NULL,
        created_by bigint NOT NULL REFERENCES users (user_id),
        created_at timestamptz NOT NULL DEFAULT now(),
        CHECK ((source_type = 'DXF_BLOCK') = (revision_id IS NOT NULL)))"""
    )
    op.execute("CREATE INDEX ix_boms_document ON bom_headers (document_id)")
    op.execute(
        """CREATE TABLE bom_items (
        bom_item_id bigserial PRIMARY KEY,
        bom_id bigint NOT NULL REFERENCES bom_headers (bom_id) ON DELETE CASCADE,
        item_no integer NOT NULL,
        source_name text NOT NULL,
        part_no text,
        part_name text NOT NULL,
        qty bigint NOT NULL CHECK (qty > 0),
        unit text NOT NULL,
        level integer NOT NULL CHECK (level >= 1),
        mapping_status text NOT NULL CHECK (mapping_status IN ('AUTO', 'MANUAL', 'UNMAPPED')),
        source_refs jsonb NOT NULL,
        mapped_by bigint REFERENCES users (user_id),
        mapped_at timestamptz,
        UNIQUE (bom_id, item_no),
        CHECK ((mapping_status = 'UNMAPPED') = (part_no IS NULL)),
        CHECK (part_no IS NULL OR btrim(part_no) <> ''),
        CHECK ((mapping_status = 'MANUAL') = (mapped_by IS NOT NULL AND mapped_at IS NOT NULL)))"""
    )
    # the extracted quantities are evidence: only the part number mapping may change later
    op.execute(
        """CREATE FUNCTION fn_bom_qty_frozen() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (NEW.bom_id, NEW.item_no, NEW.source_name, NEW.qty, NEW.level, NEW.source_refs)
               IS DISTINCT FROM
               (OLD.bom_id, OLD.item_no, OLD.source_name, OLD.qty, OLD.level, OLD.source_refs)
            THEN
                RAISE EXCEPTION 'extracted BOM values are immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$"""
    )
    op.execute(
        "CREATE TRIGGER trg_bom_items_frozen BEFORE UPDATE ON bom_items "
        "FOR EACH ROW EXECUTE FUNCTION fn_bom_qty_frozen()"
    )
    op.execute(  # a BOM header never changes: a new extraction is a new BOM
        "CREATE TRIGGER trg_bom_headers_frozen BEFORE UPDATE ON bom_headers "
        "FOR EACH ROW EXECUTE FUNCTION fn_quote_append_only()"
    )
    for t in ("bom_headers", "bom_items"):
        op.execute(
            f"CREATE TRIGGER trg_audit_{t} AFTER INSERT OR UPDATE OR DELETE ON {t} "
            "FOR EACH ROW EXECUTE FUNCTION fn_audit_log('bom_id')"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS bom_items")
    op.execute("DROP TABLE IF EXISTS bom_headers")
    op.execute("DROP FUNCTION IF EXISTS fn_bom_qty_frozen()")
