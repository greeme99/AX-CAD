"""quote documents issued (FN-21 report registry): who exported which quote, in which form, and
the sha256 of the exact bytes. The file itself is not kept: the PDF is deterministic (official
copies are dated by the approval), so a document shown later can be checked against this row.

Revision ID: 0009
Revises: 0008
"""

from alembic import op

revision = "0009"
down_revision = "0008"


def upgrade() -> None:
    op.execute(
        """CREATE TABLE quote_reports (
        report_id bigserial PRIMARY KEY,
        quote_id bigint NOT NULL REFERENCES quote_headers (quote_id) ON DELETE CASCADE,
        format text NOT NULL CHECK (format IN ('pdf', 'xlsx')),
        official boolean NOT NULL,
        basis boolean NOT NULL,
        sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
        byte_size integer NOT NULL CHECK (byte_size > 0),
        created_by bigint NOT NULL REFERENCES users (user_id),
        created_at timestamptz NOT NULL DEFAULT now())"""
    )
    op.execute("CREATE INDEX ix_quote_reports_quote ON quote_reports (quote_id)")
    op.execute(
        "CREATE TRIGGER trg_quote_reports_frozen BEFORE UPDATE OR DELETE ON quote_reports "
        "FOR EACH ROW EXECUTE FUNCTION fn_quote_append_only()"
    )


def downgrade() -> None:
    # issued-document evidence: refuse instead of silently dropping it
    op.execute(
        """DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM quote_reports) THEN
            RAISE EXCEPTION 'quote reports exist: archive them before downgrading 0009';
        END IF;
    END $$"""
    )
    op.execute("DROP TABLE quote_reports")
