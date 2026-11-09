"""Quote drawings vs general drawings: projects and documents say whether they are quoted, so
the Copilot runs the quote checks (outer contour, material/thickness, ...) only where they apply.

- projects.needs_quote: asked in the new-project dialog; new documents in the project take it.
- documents.needs_quote: asked for every new drawing from 캐드 메뉴; can be changed later.
Existing rows stay quote drawings (the behaviour before this change).

Revision ID: 0027
Revises: 0026
"""

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"


def upgrade() -> None:
    for table in ("projects", "documents"):
        op.add_column(
            table, sa.Column("needs_quote", sa.Boolean(), nullable=False, server_default=sa.true())
        )


def downgrade() -> None:
    for table in ("documents", "projects"):
        op.drop_column(table, "needs_quote")
