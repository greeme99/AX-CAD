"""quote revisions (S11, FN-19 "a confirmed quote is changed by a new revision"): a revision is a
new quote that points at the confirmed one it replaces, numbered within its family; once the
revision is approved the previous one becomes SUPERSEDED (its issued documents stay available).
A draft revision nobody wants is ABANDONED (kept for the audit trail, frees the chain).

The header itself is guarded from here on: what a quote was computed from and what it amounts
to never changes, and its status only moves along the approval flow.

Revision ID: 0013
Revises: 0012
"""

from alembic import op

revision = "0013"
down_revision = "0012"

FROZEN = (
    "quote_no, project_id, source_kind, revision_id, document_id, master_version_id, inputs, "
    "metrics, has_errors, material_cost, labor_cost, overhead_cost, outsource_cost, "
    "manufacturing_cost, admin_cost, total_cost, profit, supply_amount, vat_amount, "
    "total_amount, created_by, created_at, parent_quote_id, revision_no, change_note"
)


def _cols(row: str) -> str:
    return ", ".join(f"{row}.{c.strip()}" for c in FROZEN.split(","))


MOVES = (
    "('DRAFT','IN_REVIEW'), ('IN_REVIEW','DRAFT'), ('IN_REVIEW','CONFIRMED'), "
    "('CONFIRMED','SUPERSEDED'), ('DRAFT','ABANDONED')"
)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE quote_headers "
        "ADD COLUMN parent_quote_id bigint REFERENCES quote_headers (quote_id), "
        "ADD COLUMN revision_no integer NOT NULL DEFAULT 0 CHECK (revision_no >= 0), "
        "ADD COLUMN change_note text, "
        "ADD CONSTRAINT quote_revision_link CHECK ((parent_quote_id IS NULL) = (revision_no = 0)),"
        " ADD CONSTRAINT quote_revision_note"
        " CHECK (revision_no = 0 OR length(btrim(change_note)) >= 5),"
        " ADD CONSTRAINT quote_revision_self CHECK (parent_quote_id <> quote_id)"
    )
    # one chain, no branches: a quote has at most one live revision
    op.execute(
        "CREATE UNIQUE INDEX ux_quote_revision ON quote_headers (parent_quote_id) "
        "WHERE status <> 'ABANDONED'"
    )
    op.execute("ALTER TABLE quote_headers DROP CONSTRAINT quote_headers_status_check")
    op.execute(
        "ALTER TABLE quote_headers ADD CONSTRAINT quote_headers_status_check "
        "CHECK (status IN ('DRAFT', 'IN_REVIEW', 'CONFIRMED', 'SUPERSEDED', 'ABANDONED'))"
    )
    op.execute(
        f"""CREATE FUNCTION fn_quote_header_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF ROW({_cols("NEW")}) IS DISTINCT FROM ROW({_cols("OLD")}) THEN
                RAISE EXCEPTION 'quote % header values are immutable', OLD.quote_id
                    USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.status <> OLD.status AND (OLD.status, NEW.status) NOT IN ({MOVES}) THEN
                RAISE EXCEPTION 'quote % cannot move from % to %', OLD.quote_id, OLD.status,
                    NEW.status USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$"""
    )
    op.execute(
        "CREATE TRIGGER trg_quote_headers_guard BEFORE UPDATE ON quote_headers "
        "FOR EACH ROW EXECUTE FUNCTION fn_quote_header_guard()"
    )


def downgrade() -> None:
    op.execute(
        """DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM quote_headers
                   WHERE revision_no > 0 OR status IN ('SUPERSEDED', 'ABANDONED')) THEN
            RAISE EXCEPTION 'quote revisions exist: archive them before downgrading 0013';
        END IF;
    END $$"""
    )
    op.execute("DROP TRIGGER IF EXISTS trg_quote_headers_guard ON quote_headers")
    op.execute("DROP FUNCTION IF EXISTS fn_quote_header_guard()")
    op.execute("ALTER TABLE quote_headers DROP CONSTRAINT quote_headers_status_check")
    op.execute(
        "ALTER TABLE quote_headers ADD CONSTRAINT quote_headers_status_check "
        "CHECK (status IN ('DRAFT', 'IN_REVIEW', 'CONFIRMED'))"
    )
    op.execute("DROP INDEX IF EXISTS ux_quote_revision")
    op.execute(
        "ALTER TABLE quote_headers DROP CONSTRAINT IF EXISTS quote_revision_self, "
        "DROP CONSTRAINT IF EXISTS quote_revision_note, "
        "DROP CONSTRAINT IF EXISTS quote_revision_link, DROP COLUMN IF EXISTS change_note, "
        "DROP COLUMN IF EXISTS revision_no, DROP COLUMN IF EXISTS parent_quote_id"
    )
