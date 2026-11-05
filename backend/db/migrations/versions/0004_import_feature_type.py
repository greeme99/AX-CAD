"""features: add IMPORT type for STEP/IGES import (S7: FN-12)

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade() -> None:
    op.execute("ALTER TABLE features DROP CONSTRAINT features_feature_type_check")
    op.execute(
        "ALTER TABLE features ADD CONSTRAINT features_feature_type_check "
        "CHECK (feature_type IN ('EXTRUDE', 'REVOLVE', 'BOOLEAN', 'IMPORT'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE features DROP CONSTRAINT features_feature_type_check")
    op.execute(
        "ALTER TABLE features ADD CONSTRAINT features_feature_type_check "
        "CHECK (feature_type IN ('EXTRUDE', 'REVOLVE', 'BOOLEAN'))"
    )
