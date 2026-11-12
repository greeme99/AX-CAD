"""plan §23 S3: sheet metal feature types SHEET_CUT (a sketch cut out of the folded sheet, bends
included) and SHEET_CONVERT (a body of constant thickness becomes sheet metal).

Revision ID: 0030
Revises: 0029
"""

from alembic import op

revision = "0030"
down_revision = "0029"

FEATURES = (
    "'EXTRUDE', 'REVOLVE', 'BOOLEAN', 'IMPORT', 'FILLET', 'CHAMFER', 'HOLE', 'PATTERN', "
    "'MIRROR', 'SHELL', 'SKETCH', 'POCKET', 'PRIMITIVE', 'SWEEP', 'LOFT', 'DRAFT', 'STANDARD', "
    "'SHEET_BASE', 'SHEET_FLANGE'"
)


def _check(features: str) -> None:
    op.execute("ALTER TABLE features DROP CONSTRAINT features_feature_type_check")
    op.execute(
        "ALTER TABLE features ADD CONSTRAINT features_feature_type_check "
        f"CHECK (feature_type IN ({features}))"
    )


def upgrade() -> None:
    _check(FEATURES + ", 'SHEET_CUT', 'SHEET_CONVERT'")


def downgrade() -> None:
    # fails while such features exist: remove them deliberately first
    _check(FEATURES)
