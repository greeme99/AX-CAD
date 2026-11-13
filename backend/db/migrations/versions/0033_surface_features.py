"""plan §23 S6: surface feature types for NURBS surface creation —
SURF_LOFT, SURF_SWEEP, SURF_EXTRUDE, SURF_REVOLVE, SURF_BOUNDARY, SURF_FILL, SURF_OFFSET.

Revision ID: 0033
Revises: 0032
"""

from alembic import op

revision = "0033"
down_revision = "0032"

FEATURES = (
    "'EXTRUDE', 'REVOLVE', 'BOOLEAN', 'IMPORT', 'FILLET', 'CHAMFER', 'HOLE', 'PATTERN', "
    "'MIRROR', 'SHELL', 'SKETCH', 'POCKET', 'PRIMITIVE', 'SWEEP', 'LOFT', 'DRAFT', 'STANDARD', "
    "'SHEET_BASE', 'SHEET_FLANGE', 'SHEET_CUT', 'SHEET_CONVERT', "
    "'SHEET_UNFOLD', 'SHEET_REFOLD', 'SHEET_FORM', 'SHEET_CONVERT_SHARP'"
)


def _check(features: str) -> None:
    op.execute("ALTER TABLE features DROP CONSTRAINT features_feature_type_check")
    op.execute(
        "ALTER TABLE features ADD CONSTRAINT features_feature_type_check "
        f"CHECK (feature_type IN ({features}))"
    )


SURF = (
    "'SURF_LOFT', 'SURF_SWEEP', 'SURF_EXTRUDE', 'SURF_REVOLVE', "
    "'SURF_BOUNDARY', 'SURF_FILL', 'SURF_OFFSET'"
)


def upgrade() -> None:
    _check(FEATURES + ", " + SURF)


def downgrade() -> None:
    _check(FEATURES)
