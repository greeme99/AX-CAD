"""quote revisions (S11, FN-19 "a confirmed quote is changed by a new revision"): a revision is a
new quote that points at the confirmed one it replaces, numbered within its family; once the
revision is approved the previous one becomes SUPERSEDED (its issued documents stay available).

Revision ID: 0013
Revises: 0012
"""

from alembic import op

revision = "0013"
down_revision = "0012"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE quote_headers "
        "ADD COLUMN parent_quote_id bigint UNIQUE REFERENCES quote_headers (quote_id), "
        "ADD COLUMN revision_no integer NOT NULL DEFAULT 0 CHECK (revision_no >= 0), "
        "ADD COLUMN change_note text, "
        # one chain, no branches: a quote has at most one revision (UNIQUE above)
        "ADD CONSTRAINT quote_revision_link CHECK ((parent_quote_id IS NULL) = (revision_no = 0)),"
        " ADD CONSTRAINT quote_revision_note CHECK (revision_no = 0 OR change_note IS NOT NULL)"
    )
    op.execute("ALTER TABLE quote_headers DROP CONSTRAINT quote_headers_status_check")
    op.execute(
        "ALTER TABLE quote_headers ADD CONSTRAINT quote_headers_status_check "
        "CHECK (status IN ('DRAFT', 'IN_REVIEW', 'CONFIRMED', 'SUPERSEDED'))"
    )


def downgrade() -> None:
    op.execute(
        """DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM quote_headers WHERE revision_no > 0 OR status = 'SUPERSEDED')
        THEN
            RAISE EXCEPTION 'quote revisions exist: archive them before downgrading 0013';
        END IF;
    END $$"""
    )
    op.execute("ALTER TABLE quote_headers DROP CONSTRAINT quote_headers_status_check")
    op.execute(
        "ALTER TABLE quote_headers ADD CONSTRAINT quote_headers_status_check "
        "CHECK (status IN ('DRAFT', 'IN_REVIEW', 'CONFIRMED'))"
    )
    op.execute(
        "ALTER TABLE quote_headers DROP CONSTRAINT quote_revision_note, "
        "DROP CONSTRAINT quote_revision_link, DROP COLUMN change_note, "
        "DROP COLUMN revision_no, DROP COLUMN parent_quote_id"
    )
