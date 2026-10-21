"""features: machining types on a body (FreeCAD PartDesign style): FILLET, CHAMFER, HOLE, PATTERN,
MIRROR, SHELL. Inputs live in params like BOOLEAN's.

Revision ID: 0015
Revises: 0014
"""

from alembic import op

revision = "0015"
down_revision = "0014"

BASE = "'EXTRUDE', 'REVOLVE', 'BOOLEAN', 'IMPORT'"


def _types(types: str) -> None:
    op.execute("ALTER TABLE features DROP CONSTRAINT features_feature_type_check")
    op.execute(
        "ALTER TABLE features ADD CONSTRAINT features_feature_type_check "
        f"CHECK (feature_type IN ({types}))"
    )


def upgrade() -> None:
    _types(BASE + ", 'FILLET', 'CHAMFER', 'HOLE', 'PATTERN', 'MIRROR', 'SHELL'")


def downgrade() -> None:
    # fails while such rows exist: delete them deliberately first, never silently here
    _types(BASE)
