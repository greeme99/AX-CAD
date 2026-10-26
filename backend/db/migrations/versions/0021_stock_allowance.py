"""cost_ratios.stock_allowance_mm (Track 8 P4-1): machining allowance added to each side of a 3D
part's stock (block: bounding box; turned part: round bar diameter and length).

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


def downgrade() -> None:
    op.execute("ALTER TABLE cost_ratios DROP COLUMN stock_allowance_mm")
