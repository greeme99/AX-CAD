"""features: SWEEP and LOFT (bodies from sketches) and DRAFT (side faces tilted), Track 8 P2-3.
Thickness options extend SHELL params and need no schema change.

Revision ID: 0019
Revises: 0018
"""

from alembic import op

revision = "0019"
down_revision = "0018"

BASE = (
    "'EXTRUDE', 'REVOLVE', 'BOOLEAN', 'IMPORT', "
    "'FILLET', 'CHAMFER', 'HOLE', 'PATTERN', 'MIRROR', 'SHELL', 'SKETCH', 'POCKET', 'PRIMITIVE'"
)


def _types(types: str) -> None:
    op.execute("ALTER TABLE features DROP CONSTRAINT features_feature_type_check")
    op.execute(
        "ALTER TABLE features ADD CONSTRAINT features_feature_type_check "
        f"CHECK (feature_type IN ({types}))"
    )


def upgrade() -> None:
    _types(BASE + ", 'SWEEP', 'LOFT', 'DRAFT'")


def downgrade() -> None:
    # fails while such rows exist: delete them deliberately first, never silently here
    _types(BASE)
