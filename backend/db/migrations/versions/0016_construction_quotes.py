"""construction/MEP quotes (Track 6): work items (공종 단가: 재료·노무·경비, 손실률, 수량식),
adjust factors (지역·시점 보정), price validity (valid_until), indirect rates of the 공사원가계산서,
QTO layer mapping, and quote source CONSTRUCTION (a 2D revision measured as rooms/walls/MEP).

Revision ID: 0016
Revises: 0015
"""

from alembic import op

revision = "0016"
down_revision = "0015"

RATE = "numeric(7,6) CHECK ({c} >= 0 AND {c} <= 1)"
PRICE = "numeric(18,2) CHECK ({c} >= 0)"
DDL = [
    # frozen bundles: adding a nullable column fires no row trigger
    "ALTER TABLE materials ADD COLUMN valid_until date",
    "ALTER TABLE price_items ADD COLUMN valid_until date",
    "ALTER TABLE cost_ratios ADD COLUMN indirect_labor_rate "
    + RATE.format(c="indirect_labor_rate"),
    "ALTER TABLE cost_ratios ADD COLUMN indirect_cost_rate " + RATE.format(c="indirect_cost_rate"),
    f"""CREATE TABLE work_items (
        work_item_id bigserial PRIMARY KEY,
        version_id bigint NOT NULL REFERENCES master_versions (version_id) ON DELETE CASCADE,
        item_code text NOT NULL,
        item_name text NOT NULL,
        spec text,
        work_category text NOT NULL,
        unit text NOT NULL,
        qty_formula text NOT NULL,
        loss_rate {RATE.format(c="loss_rate")},
        material_price {PRICE.format(c="material_price")},
        labor_price {PRICE.format(c="labor_price")},
        expense_price {PRICE.format(c="expense_price")},
        valid_until date,
        UNIQUE (version_id, item_code))""",
    """CREATE TABLE adjust_factors (
        adjust_factor_id bigserial PRIMARY KEY,
        version_id bigint NOT NULL REFERENCES master_versions (version_id) ON DELETE CASCADE,
        factor_type text NOT NULL CHECK (factor_type IN ('REGION', 'ESCALATION')),
        code text NOT NULL,
        factor numeric(8,6) NOT NULL CHECK (factor > 0 AND factor <= 10),
        note text,
        UNIQUE (version_id, factor_type, code))""",
    "ALTER TABLE mapping_rules DROP CONSTRAINT mapping_rules_rule_type_check",
    """ALTER TABLE mapping_rules ADD CONSTRAINT mapping_rules_rule_type_check
        CHECK (rule_type IN ('LAYER', 'LINETYPE', 'TITLE_TAG', 'PUNCH_MAX_DIA', 'QTO_LAYER'))""",
    "ALTER TABLE quote_headers DROP CONSTRAINT quote_headers_source_kind_check",
    """ALTER TABLE quote_headers ADD CONSTRAINT quote_headers_source_kind_check
        CHECK (source_kind IN ('REVISION', 'DOCUMENT_3D', 'CONSTRUCTION'))""",
    "ALTER TABLE quote_headers DROP CONSTRAINT quote_headers_check",
    # a construction quote measures a drawing revision too
    """ALTER TABLE quote_headers ADD CONSTRAINT quote_headers_check
        CHECK ((source_kind IN ('REVISION', 'CONSTRUCTION')) = (revision_id IS NOT NULL))""",
]
CHILDREN = ("work_items", "adjust_factors")


def upgrade() -> None:
    for stmt in DDL:
        op.execute(stmt)
    for t in CHILDREN:  # same frozen/audit pair as the 0005 bundle tables
        op.execute(
            f"CREATE TRIGGER trg_frozen_{t} BEFORE INSERT OR UPDATE OR DELETE ON {t} "
            "FOR EACH ROW EXECUTE FUNCTION fn_master_frozen()"
        )
        op.execute(
            f"CREATE TRIGGER trg_audit_{t} AFTER INSERT OR UPDATE OR DELETE ON {t} "
            "FOR EACH ROW EXECUTE FUNCTION fn_audit_log('version_id')"
        )


def downgrade() -> None:
    # fails while construction quotes or QTO rules exist: remove them deliberately first
    op.execute("ALTER TABLE quote_headers DROP CONSTRAINT quote_headers_check")
    op.execute(
        "ALTER TABLE quote_headers ADD CONSTRAINT quote_headers_check "
        "CHECK ((source_kind = 'REVISION') = (revision_id IS NOT NULL))"
    )
    op.execute("ALTER TABLE quote_headers DROP CONSTRAINT quote_headers_source_kind_check")
    op.execute(
        "ALTER TABLE quote_headers ADD CONSTRAINT quote_headers_source_kind_check "
        "CHECK (source_kind IN ('REVISION', 'DOCUMENT_3D'))"
    )
    op.execute("ALTER TABLE mapping_rules DROP CONSTRAINT mapping_rules_rule_type_check")
    op.execute(
        "ALTER TABLE mapping_rules ADD CONSTRAINT mapping_rules_rule_type_check "
        "CHECK (rule_type IN ('LAYER', 'LINETYPE', 'TITLE_TAG', 'PUNCH_MAX_DIA'))"
    )
    for t in CHILDREN:
        op.execute(f"DROP TABLE {t}")
    for c in ("indirect_labor_rate", "indirect_cost_rate"):
        op.execute(f"ALTER TABLE cost_ratios DROP COLUMN {c}")
    for t in ("materials", "price_items"):
        op.execute(f"ALTER TABLE {t} DROP COLUMN valid_until")
