"""features: SKETCH (Track 8 P2-1, constrained 2D geometry on a plane; no body of its own).
A sketch-based EXTRUDE/REVOLVE names it in params like a BOOLEAN names its inputs.

Revision ID: 0017
Revises: 0016
"""

from alembic import op

revision = "0017"
down_revision = "0016"

BASE = (
    "'EXTRUDE', 'REVOLVE', 'BOOLEAN', 'IMPORT', "
    "'FILLET', 'CHAMFER', 'HOLE', 'PATTERN', 'MIRROR', 'SHELL'"
)


def _types(types: str) -> None:
    op.execute("ALTER TABLE features DROP CONSTRAINT features_feature_type_check")
    op.execute(
        "ALTER TABLE features ADD CONSTRAINT features_feature_type_check "
        f"CHECK (feature_type IN ({types}))"
    )


def upgrade() -> None:
    _types(BASE + ", 'SKETCH'")


def downgrade() -> None:
    # fails while such rows exist: delete them deliberately first, never silently here
    _types(BASE)
