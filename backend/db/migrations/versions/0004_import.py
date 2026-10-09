"""features: IMPORT type (STEP/IGES upload, FN-12)

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"


def _types(types: str) -> None:
    op.execute("ALTER TABLE features DROP CONSTRAINT features_feature_type_check")
    op.execute(
        "ALTER TABLE features ADD CONSTRAINT features_feature_type_check "
        f"CHECK (feature_type IN ({types}))"
    )


def upgrade() -> None:
    _types("'EXTRUDE', 'REVOLVE', 'BOOLEAN', 'IMPORT'")


def downgrade() -> None:
    # fails while IMPORT rows exist: delete them deliberately first, never silently here
    _types("'EXTRUDE', 'REVOLVE', 'BOOLEAN'")
