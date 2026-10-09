"""master data (FN-16): versioned bundles of materials, process rules, price items, cost ratios,
mapping rules. A bundle is edited while DRAFT and frozen once ACTIVE (change = new version), so a
quote that references a version is reproducible (NFR-05).

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"

RATE = "numeric(7,6) CHECK ({c} >= 0 AND {c} <= 1)"  # ratios stored as fractions, 0..100 %
DDL = [
    """CREATE TABLE master_versions (
        version_id bigserial PRIMARY KEY,
        version_code text NOT NULL UNIQUE,
        effective_from date NOT NULL,
        status text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'ACTIVE')),
        note text,
        created_by bigint NOT NULL REFERENCES users (user_id),
        created_at timestamptz NOT NULL DEFAULT now(),
        activated_at timestamptz)""",
    """CREATE TABLE materials (
        material_id bigserial PRIMARY KEY,
        version_id bigint NOT NULL REFERENCES master_versions (version_id) ON DELETE CASCADE,
        material_code text NOT NULL,
        material_name text,
        thickness_min_mm numeric(10,3) CHECK (thickness_min_mm >= 0),
        thickness_max_mm numeric(10,3) CHECK (thickness_max_mm >= thickness_min_mm),
        density_g_cm3 numeric(10,4) NOT NULL CHECK (density_g_cm3 > 0),
        unit_price_per_kg numeric(18,2) CHECK (unit_price_per_kg >= 0),
        scrap_rate """
    + RATE.format(c="scrap_rate")
    + """,
        UNIQUE NULLS NOT DISTINCT (version_id, material_code, thickness_min_mm))""",
    """CREATE TABLE price_items (
        price_item_id bigserial PRIMARY KEY,
        version_id bigint NOT NULL REFERENCES master_versions (version_id) ON DELETE CASCADE,
        item_code text NOT NULL,
        item_type text NOT NULL CHECK (item_type IN ('LABOR', 'MACHINE', 'OUTSOURCE')),
        unit text NOT NULL,
        unit_price numeric(18,2) CHECK (unit_price >= 0),
        UNIQUE (version_id, item_code))""",
    """CREATE TABLE process_rules (
        process_rule_id bigserial PRIMARY KEY,
        version_id bigint NOT NULL REFERENCES master_versions (version_id) ON DELETE CASCADE,
        rule_code text NOT NULL,
        process_code text NOT NULL,
        process_name text,
        input_metric text NOT NULL,
        formula_text text NOT NULL,
        params jsonb NOT NULL DEFAULT '{}',
        labor_item_code text,
        machine_item_code text,
        UNIQUE (version_id, rule_code))""",
    """CREATE TABLE cost_ratios (
        version_id bigint PRIMARY KEY REFERENCES master_versions (version_id) ON DELETE CASCADE,
        overhead_basis text NOT NULL CHECK (overhead_basis IN ('MACHINE_HOUR', 'LABOR_RATIO')),
        overhead_rate """
    + RATE.format(c="overhead_rate")
    + """,
        admin_rate """
    + RATE.format(c="admin_rate")
    + """,
        profit_rate """
    + RATE.format(c="profit_rate")
    + """,
        vat_rate """
    + RATE.format(c="vat_rate")
    + """ NOT NULL DEFAULT 0.1,
        rounding_rule text NOT NULL CHECK (rounding_rule IN ('FLOOR', 'HALF_UP', 'CEILING')),
        rounding_unit integer NOT NULL CHECK (rounding_unit IN (1, 10, 100, 1000)),
        rounding_scope text NOT NULL CHECK (rounding_scope IN ('LINE', 'TOTAL')))""",
    """CREATE TABLE mapping_rules (
        mapping_rule_id bigserial PRIMARY KEY,
        version_id bigint NOT NULL REFERENCES master_versions (version_id) ON DELETE CASCADE,
        rule_type text NOT NULL CHECK (rule_type IN ('LAYER', 'LINETYPE', 'TITLE_TAG', 'PUNCH_MAX_DIA')),
        target text NOT NULL,
        pattern text NOT NULL,
        UNIQUE (version_id, rule_type, target, pattern))""",
    # frozen once ACTIVE: no row of an active bundle may change (DB-level, not only the API)
    """CREATE FUNCTION fn_master_frozen() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE v bigint := COALESCE(NEW.version_id, OLD.version_id);
    BEGIN
        IF (SELECT status FROM master_versions WHERE version_id = v) = 'ACTIVE' THEN
            RAISE EXCEPTION 'master data version % is active and frozen', v
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN COALESCE(NEW, OLD);
    END $$""",
]
CHILDREN = ("materials", "price_items", "process_rules", "cost_ratios", "mapping_rules")


def upgrade() -> None:
    for stmt in DDL:
        op.execute(stmt)
    op.execute(
        "CREATE TRIGGER trg_audit_master_versions AFTER INSERT OR UPDATE OR DELETE "
        "ON master_versions FOR EACH ROW EXECUTE FUNCTION fn_audit_log('version_id')"
    )
    for t in CHILDREN:
        op.execute(
            f"CREATE TRIGGER trg_frozen_{t} BEFORE INSERT OR UPDATE OR DELETE ON {t} "
            "FOR EACH ROW EXECUTE FUNCTION fn_master_frozen()"
        )
        # object_id = version_id: one audit trail per bundle
        op.execute(
            f"CREATE TRIGGER trg_audit_{t} AFTER INSERT OR UPDATE OR DELETE ON {t} "
            "FOR EACH ROW EXECUTE FUNCTION fn_audit_log('version_id')"
        )


def downgrade() -> None:
    for t in (*CHILDREN, "master_versions"):
        op.execute(f"DROP TABLE {t}")
    op.execute("DROP FUNCTION fn_master_frozen()")
