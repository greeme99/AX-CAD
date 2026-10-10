"""official quote documents are issued once and kept (S11-b security review M-2/M-3):
the first official PDF/XLSX of a quote is stored with its hash and every later download returns
those exact bytes, so later edits to supplier settings, customer or document names cannot change
an issued document. Drafts are no longer registered. UPDATE stays blocked; DELETE follows the
quote (like traces and logs) so the ON DELETE CASCADE keeps working.

Revision ID: 0010
Revises: 0009
"""

from alembic import op

revision = "0010"
down_revision = "0009"


def upgrade() -> None:
    op.execute("ALTER TABLE quote_reports ADD COLUMN content bytea")
    op.execute(
        "CREATE UNIQUE INDEX ux_quote_reports_official ON quote_reports (quote_id, format) "
        "WHERE official"
    )
    op.execute("DROP TRIGGER trg_quote_reports_frozen ON quote_reports")
    op.execute(
        "CREATE TRIGGER trg_quote_reports_frozen BEFORE UPDATE ON quote_reports "
        "FOR EACH ROW EXECUTE FUNCTION fn_quote_append_only()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_quote_reports_frozen ON quote_reports")
    op.execute(
        "CREATE TRIGGER trg_quote_reports_frozen BEFORE UPDATE OR DELETE ON quote_reports "
        "FOR EACH ROW EXECUTE FUNCTION fn_quote_append_only()"
    )
    op.execute("DROP INDEX ux_quote_reports_official")
    op.execute("ALTER TABLE quote_reports DROP COLUMN content")
