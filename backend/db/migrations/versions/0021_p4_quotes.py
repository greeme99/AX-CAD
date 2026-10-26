"""Track 8 P4 quotes.

- cost_ratios.stock_allowance_mm (P4-1): machining allowance added to each side of a 3D part's
  stock (block: bounding box; turned part: round bar diameter and length).
- quote_headers (P4-4): a 3D quote may name the drawing generated from the model that it was made
  from (revision_id); 2D and construction quotes still need theirs.
- features STANDARD and price items PURCHASE (P4-5): purchased standard parts and their prices.

Revision ID: 0021
Revises: 0020
"""

from alembic import op

revision = "0021"
down_revision = "0020"

FEATURES = (
    "'EXTRUDE', 'REVOLVE', 'BOOLEAN', 'IMPORT', 'FILLET', 'CHAMFER', 'HOLE', 'PATTERN', "
    "'MIRROR', 'SHELL', 'SKETCH', 'POCKET', 'PRIMITIVE', 'SWEEP', 'LOFT', 'DRAFT'"
)
PRICES = "'LABOR', 'MACHINE', 'OUTSOURCE'"


def _checks(features: str, prices: str) -> None:
    op.execute("ALTER TABLE features DROP CONSTRAINT features_feature_type_check")
    op.execute(
        "ALTER TABLE features ADD CONSTRAINT features_feature_type_check "
        f"CHECK (feature_type IN ({features}))"
    )
    op.execute("ALTER TABLE price_items DROP CONSTRAINT price_items_item_type_check")
    op.execute(
        "ALTER TABLE price_items ADD CONSTRAINT price_items_item_type_check "
        f"CHECK (item_type IN ({prices}))"
    )


def upgrade() -> None:
    op.execute(
        "ALTER TABLE cost_ratios ADD COLUMN stock_allowance_mm numeric(8, 3) NOT NULL DEFAULT 2 "
        "CHECK (stock_allowance_mm >= 0 AND stock_allowance_mm <= 100)"
    )
    op.execute("ALTER TABLE quote_headers DROP CONSTRAINT quote_headers_check")
    op.execute(
        "ALTER TABLE quote_headers ADD CONSTRAINT quote_headers_check "
        "CHECK (source_kind = 'DOCUMENT_3D' OR revision_id IS NOT NULL)"
    )
    _checks(FEATURES + ", 'STANDARD'", PRICES + ", 'PURCHASE'")


def downgrade() -> None:
    # fails while 3D quotes of generated drawings, standard parts or purchase prices exist:
    # remove them deliberately first
    _checks(FEATURES, PRICES)
    op.execute("ALTER TABLE quote_headers DROP CONSTRAINT quote_headers_check")
    op.execute(
        "ALTER TABLE quote_headers ADD CONSTRAINT quote_headers_check "
        "CHECK ((source_kind IN ('REVISION', 'CONSTRUCTION')) = (revision_id IS NOT NULL))"
    )
    op.execute("ALTER TABLE cost_ratios DROP COLUMN stock_allowance_mm")
