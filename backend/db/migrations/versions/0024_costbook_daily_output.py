"""Track 8 P6-6: 표준품셈 일당 시공량 and 원문 비고.

- costbook_items.daily_output: many 품셈 tables give a crew per day (인/일) and the 시공량 of that
  day (예: 미장공 2인, 시공량 50 ㎥/일). When set, the item's resource quantities are per day and
  the engine divides by it (exact, no rounding of 1/65).
- costbook_items.remark: the 비고 / [주] text of the original table (할증·감 rules, conditions) kept
  for the person applying them; the engine does not read it.

Revision ID: 0024
Revises: 0023
"""

from alembic import op

revision = "0024"
down_revision = "0023"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE costbook_items ADD COLUMN daily_output numeric(18,6) CHECK (daily_output > 0)"
    )
    op.execute(
        "ALTER TABLE costbook_items ADD COLUMN remark text CHECK (char_length(remark) <= 2000)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE costbook_items DROP COLUMN remark")
    op.execute("ALTER TABLE costbook_items DROP COLUMN daily_output")
