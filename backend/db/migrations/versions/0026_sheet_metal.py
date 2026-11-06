"""Track 9 Q3: sheet metal feature types (plan.md §15.4).

- features SHEET_BASE (outline + thickness, inside radius, K-factor) and SHEET_FLANGE (a flange on
  one outline edge of the sheet it consumes).

Revision ID: 0026
Revises: 0025
"""

from alembic import op

revision = "0026"
down_revision = "0025"

FEATURES = (
    "'EXTRUDE', 'REVOLVE', 'BOOLEAN', 'IMPORT', 'FILLET', 'CHAMFER', 'HOLE', 'PATTERN', "
    "'MIRROR', 'SHELL', 'SKETCH', 'POCKET', 'PRIMITIVE', 'SWEEP', 'LOFT', 'DRAFT', 'STANDARD'"
)


def _check(features: str) -> None:
    op.execute("ALTER TABLE features DROP CONSTRAINT features_feature_type_check")
    op.execute(
        "ALTER TABLE features ADD CONSTRAINT features_feature_type_check "
        f"CHECK (feature_type IN ({features}))"
    )


def upgrade() -> None:
    _check(FEATURES + ", 'SHEET_BASE', 'SHEET_FLANGE'")


def downgrade() -> None:
    # fails while sheet metal features exist: remove them deliberately first
    _check(FEATURES)
