"""plan §23 S7: surface editing feature types —
SURF_EXTEND, SURF_TRIM, SURF_SPLIT, SURF_KNIT, SURF_THICKEN, SURF_REPLACE.

Revision ID: 0034
Revises: 0033
"""

from alembic import op

revision = "0034"
down_revision = "0033"

# All prior feature types (through S6 / migration 0033)
PREV = (
    "'EXTRUDE', 'REVOLVE', 'BOOLEAN', 'IMPORT', 'FILLET', 'CHAMFER', 'HOLE', 'PATTERN', "
    "'MIRROR', 'SHELL', 'SKETCH', 'POCKET', 'PRIMITIVE', 'SWEEP', 'LOFT', 'DRAFT', 'STANDARD', "
    "'SHEET_BASE', 'SHEET_FLANGE', 'SHEET_CUT', 'SHEET_CONVERT', "
    "'SHEET_UNFOLD', 'SHEET_REFOLD', 'SHEET_FORM', 'SHEET_CONVERT_SHARP', "
    "'SURF_LOFT', 'SURF_SWEEP', 'SURF_EXTRUDE', 'SURF_REVOLVE', "
    "'SURF_BOUNDARY', 'SURF_FILL', 'SURF_OFFSET'"
)

SURF_EDIT = (
    "'SURF_EXTEND', 'SURF_TRIM', 'SURF_SPLIT', "
    "'SURF_KNIT', 'SURF_THICKEN', 'SURF_REPLACE'"
)


def _check(features: str) -> None:
    op.execute("ALTER TABLE features DROP CONSTRAINT features_feature_type_check")
    op.execute(
        "ALTER TABLE features ADD CONSTRAINT features_feature_type_check "
        f"CHECK (feature_type IN ({features}))"
    )


def upgrade() -> None:
    _check(PREV + ", " + SURF_EDIT)


def downgrade() -> None:
    _check(PREV)
