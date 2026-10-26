"""features: POCKET (sketch cut out of a body) and PRIMITIVE (box, cylinder, cone, sphere, torus),
Track 8 P2-2.

Revision ID: 0018
Revises: 0017
"""

from alembic import op

revision = "0018"
down_revision = "0017"

BASE = (
    "'EXTRUDE', 'REVOLVE', 'BOOLEAN', 'IMPORT', "
    "'FILLET', 'CHAMFER', 'HOLE', 'PATTERN', 'MIRROR', 'SHELL', 'SKETCH'"
)


def _types(types: str) -> None:
    op.execute("ALTER TABLE features DROP CONSTRAINT features_feature_type_check")
    op.execute(
        "ALTER TABLE features ADD CONSTRAINT features_feature_type_check "
        f"CHECK (feature_type IN ({types}))"
    )


def upgrade() -> None:
    _types(BASE + ", 'POCKET', 'PRIMITIVE'")


def downgrade() -> None:
    # fails while such rows exist: delete them deliberately first, never silently here
    _types(BASE)
