"""Track 8 P4 quotes.

- cost_ratios.stock_allowance_mm (P4-1): machining allowance added to each side of a 3D part's
  stock (block: bounding box; turned part: round bar diameter and length).
- quote_headers (P4-4): a 3D quote may name the drawing generated from the model that it was made
  from (revision_id); 2D and construction quotes still need theirs.

Revision ID: 0021
Revises: 0020
"""

from alembic import op

revision = "0021"
down_revision = "0020"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE cost_ratios ADD COLUMN stock_allowance_mm numeric(8, 3) NOT NULL DEFAULT 2 "
        "CHECK (stock_allowance_mm >= 0 AND stock_allowance_mm <= 100)"
    )
    op.execute("ALTER TABLE quote_headers DROP CONSTRAINT quote_headers_check")
    op.execute(
        "ALTER TABLE quote_headers ADD CONSTRAINT quote_headers_check "
        "CHECK (source_kind = 'DOCUMENT_3D' OR revision_id IS NOT NULL)"
    )


def downgrade() -> None:
    # fails while 3D quotes of generated drawings exist: remove them deliberately first
    op.execute("ALTER TABLE quote_headers DROP CONSTRAINT quote_headers_check")
    op.execute(
        "ALTER TABLE quote_headers ADD CONSTRAINT quote_headers_check "
        "CHECK ((source_kind IN ('REVISION', 'CONSTRUCTION')) = (revision_id IS NOT NULL))"
    )
    op.execute("ALTER TABLE cost_ratios DROP COLUMN stock_allowance_mm")
