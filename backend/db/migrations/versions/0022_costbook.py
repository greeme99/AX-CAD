"""Track 8 P4-6 standard construction costbook (건설공사 표준품셈) in the master data:

- costbook_items: 품셈 항목 with where it comes from (notice number, effective date, page of the
  original PDF, its SHA-256, how it was extracted, who reviewed it).
- costbook_resources: per unit of an item, the labour (인), material and equipment (시간) it takes.
- resource_prices: unit prices of those resources (노임·자재·기계경비) with their date and source.
- work_items.costbook_item_code: a work item priced from the costbook instead of its own prices.

Revision ID: 0022
Revises: 0021
"""

from alembic import op

revision = "0022"
down_revision = "0021"

KIND = "CHECK (resource_type IN ('LABOR', 'MATERIAL', 'EQUIPMENT'))"
DDL = [
    """CREATE TABLE costbook_items (
        costbook_item_id bigserial PRIMARY KEY,
        version_id bigint NOT NULL REFERENCES master_versions (version_id) ON DELETE CASCADE,
        item_code text NOT NULL,
        item_name text NOT NULL,
        spec text,
        unit text NOT NULL,
        category text,
        notice_no text,
        effective_from date,
        page_no integer CHECK (page_no > 0),
        source_sha256 text CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
        extract_method text CHECK (extract_method IN ('MANUAL', 'PDF', 'OCR')),
        reviewer text,
        reviewed_at date,
        UNIQUE (version_id, item_code))""",
    f"""CREATE TABLE costbook_resources (
        costbook_resource_id bigserial PRIMARY KEY,
        version_id bigint NOT NULL REFERENCES master_versions (version_id) ON DELETE CASCADE,
        item_code text NOT NULL,
        resource_type text NOT NULL {KIND},
        resource_code text NOT NULL,
        qty_per_unit numeric(18,6) NOT NULL CHECK (qty_per_unit > 0),
        UNIQUE (version_id, item_code, resource_code))""",
    f"""CREATE TABLE resource_prices (
        resource_price_id bigserial PRIMARY KEY,
        version_id bigint NOT NULL REFERENCES master_versions (version_id) ON DELETE CASCADE,
        resource_code text NOT NULL,
        resource_type text NOT NULL {KIND},
        resource_name text NOT NULL,
        unit text NOT NULL,
        unit_price numeric(18,2) CHECK (unit_price >= 0),
        price_date date,
        source text,
        valid_until date,
        UNIQUE (version_id, resource_code))""",
    "ALTER TABLE work_items ADD COLUMN costbook_item_code text",
]
CHILDREN = ("costbook_items", "costbook_resources", "resource_prices")


def upgrade() -> None:
    for stmt in DDL:
        op.execute(stmt)
    for t in CHILDREN:  # same frozen/audit pair as the other bundle tables
        op.execute(
            f"CREATE TRIGGER trg_frozen_{t} BEFORE INSERT OR UPDATE OR DELETE ON {t} "
            "FOR EACH ROW EXECUTE FUNCTION fn_master_frozen()"
        )
        op.execute(
            f"CREATE TRIGGER trg_audit_{t} AFTER INSERT OR UPDATE OR DELETE ON {t} "
            "FOR EACH ROW EXECUTE FUNCTION fn_audit_log('version_id')"
        )


def downgrade() -> None:
    op.execute("ALTER TABLE work_items DROP COLUMN costbook_item_code")
    for t in reversed(CHILDREN):
        op.drop_table(t)
