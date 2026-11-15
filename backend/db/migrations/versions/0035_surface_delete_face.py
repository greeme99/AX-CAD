"""plan §23 S7: feature type SURF_DELETE_FACE.

Revision ID: 0035
Revises: 0034
"""

from alembic import op

revision = "0035"
down_revision = "0034"

PREV = (
    "'EXTRUDE', 'REVOLVE', 'BOOLEAN', 'IMPORT', 'FILLET', 'CHAMFER', 'HOLE', 'PATTERN', "
    "'MIRROR', 'SHELL', 'SKETCH', 'POCKET', 'PRIMITIVE', 'SWEEP', 'LOFT', 'DRAFT', 'STANDARD', "
    "'SHEET_BASE', 'SHEET_FLANGE', 'SHEET_CUT', 'SHEET_CONVERT', "
    "'SHEET_UNFOLD', 'SHEET_REFOLD', 'SHEET_FORM', 'SHEET_CONVERT_SHARP', "
    "'SURF_LOFT', 'SURF_SWEEP', 'SURF_EXTRUDE', 'SURF_REVOLVE', "
    "'SURF_BOUNDARY', 'SURF_FILL', 'SURF_OFFSET', "
    "'SURF_EXTEND', 'SURF_TRIM', 'SURF_SPLIT', 'SURF_KNIT', 'SURF_THICKEN', 'SURF_REPLACE'"
)


def _check(features: str) -> None:
    op.execute("ALTER TABLE features DROP CONSTRAINT features_feature_type_check")
    op.execute(
        "ALTER TABLE features ADD CONSTRAINT features_feature_type_check "
        f"CHECK (feature_type IN ({features}))"
    )


def upgrade() -> None:
    _check(PREV + ", 'SURF_DELETE_FACE'")


def downgrade() -> None:
    _check(PREV)
