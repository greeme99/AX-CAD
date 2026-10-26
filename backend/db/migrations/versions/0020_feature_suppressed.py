"""features.suppressed (Track 8 P2-5): a suppressed feature passes its base body on unchanged.

Revision ID: 0020
Revises: 0019
"""

from alembic import op

revision = "0020"
down_revision = "0019"


def upgrade() -> None:
    op.execute("ALTER TABLE features ADD COLUMN suppressed boolean NOT NULL DEFAULT false")


def downgrade() -> None:
    op.execute("ALTER TABLE features DROP COLUMN suppressed")
