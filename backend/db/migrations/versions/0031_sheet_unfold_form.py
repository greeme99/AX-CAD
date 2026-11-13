"""plan §23 S3b: sheet metal feature types SHEET_UNFOLD (switch to flat-pattern editing),
SHEET_REFOLD (back to folded), SHEET_FORM (emboss/dimple/louver), and SHEET_CONVERT_SHARP
(sharp-edge solid to sheet metal with bend radii assigned).

Revision ID: 0031
Revises: 0030
"""

from alembic import op

revision = "0031"
down_revision = "0030"

FEATURES = (
    "'EXTRUDE', 'REVOLVE', 'BOOLEAN', 'IMPORT', 'FILLET', 'CHAMFER', 'HOLE', 'PATTERN', "
    "'MIRROR', 'SHELL', 'SKETCH', 'POCKET', 'PRIMITIVE', 'SWEEP', 'LOFT', 'DRAFT', 'STANDARD', "
    "'SHEET_BASE', 'SHEET_FLANGE', 'SHEET_CUT', 'SHEET_CONVERT'"
)


def _check(features: str) -> None:
    op.execute("ALTER TABLE features DROP CONSTRAINT features_feature_type_check")
    op.execute(
        "ALTER TABLE features ADD CONSTRAINT features_feature_type_check "
        f"CHECK (feature_type IN ({features}))"
    )


def upgrade() -> None:
    _check(FEATURES + ", 'SHEET_UNFOLD', 'SHEET_REFOLD', 'SHEET_FORM', 'SHEET_CONVERT_SHARP'")


def downgrade() -> None:
    _check(FEATURES)
