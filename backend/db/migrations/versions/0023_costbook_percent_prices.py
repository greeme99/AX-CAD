"""Track 8 P6-1: 표준품셈 percent inputs and dated resource prices.

- costbook_resources.percent_of: a row with it is a 비율 input (잡재료비·공구손료: qty_per_unit is
  a percent, at most 100, of the item's labour / material / equipment amount per unit).
- resource_prices: the same resource may have several rows with different price dates (a quote
  uses the latest one on or before its date), so the key becomes (version, resource, date).

Revision ID: 0023
Revises: 0022
"""

from alembic import op

revision = "0023"
down_revision = "0022"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE costbook_resources ADD COLUMN percent_of text "
        "CHECK (percent_of IN ('LABOR', 'MATERIAL', 'EQUIPMENT'))"
    )
    op.execute(
        "ALTER TABLE costbook_resources ADD CONSTRAINT costbook_resources_percent_check "
        "CHECK (percent_of IS NULL OR qty_per_unit <= 100)"
    )
    op.execute(
        "ALTER TABLE resource_prices DROP CONSTRAINT resource_prices_version_id_resource_code_key"
    )
    op.execute(
        "ALTER TABLE resource_prices ADD CONSTRAINT resource_prices_version_code_date_key "
        "UNIQUE NULLS NOT DISTINCT (version_id, resource_code, price_date)"
    )


def downgrade() -> None:
    op.execute("DELETE FROM costbook_resources WHERE percent_of IS NOT NULL")
    op.execute(  # keep the newest price of each resource
        "DELETE FROM resource_prices p USING resource_prices n WHERE p.version_id = n.version_id "
        "AND p.resource_code = n.resource_code AND (p.price_date, p.resource_price_id) < "
        "(n.price_date, n.resource_price_id)"
    )
    op.execute("ALTER TABLE resource_prices DROP CONSTRAINT resource_prices_version_code_date_key")
    op.execute(
        "ALTER TABLE resource_prices ADD CONSTRAINT resource_prices_version_id_resource_code_key "
        "UNIQUE (version_id, resource_code)"
    )
    op.execute("ALTER TABLE costbook_resources DROP CONSTRAINT costbook_resources_percent_check")
    op.execute("ALTER TABLE costbook_resources DROP COLUMN percent_of")
