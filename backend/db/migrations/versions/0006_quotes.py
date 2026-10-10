"""quotes (FN-17/18): header + lines (calculated_* only; override_* reserved for FN-19) + traces
(source -> rule -> price -> formula) + validation logs. cost_ratios.material_basis (G1 agenda:
plate weight from net area or from the bounding rectangle).

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"

MONEY = "numeric(18,2)"
DDL = [
    # frozen bundles: adding a column with a default fires no row trigger
    (
        "ALTER TABLE cost_ratios ADD COLUMN material_basis text NOT NULL DEFAULT 'NET' "
        "CHECK (material_basis IN ('NET', 'BBOX'))"
    ),
    f"""CREATE TABLE quote_headers (
        quote_id bigserial PRIMARY KEY,
        quote_no text NOT NULL UNIQUE,
        project_id bigint NOT NULL REFERENCES projects (project_id),
        source_kind text NOT NULL CHECK (source_kind IN ('REVISION', 'DOCUMENT_3D')),
        revision_id text REFERENCES document_revisions (revision_id),
        document_id bigint NOT NULL REFERENCES documents (document_id),
        master_version_id bigint NOT NULL REFERENCES master_versions (version_id) ON DELETE RESTRICT,
        inputs jsonb NOT NULL,
        metrics jsonb NOT NULL,
        status text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'CONFIRMED')),
        has_errors boolean NOT NULL,
        material_cost {MONEY} NOT NULL, labor_cost {MONEY} NOT NULL,
        overhead_cost {MONEY} NOT NULL, outsource_cost {MONEY} NOT NULL,
        manufacturing_cost {MONEY} NOT NULL, admin_cost {MONEY} NOT NULL,
        total_cost {MONEY} NOT NULL, profit {MONEY} NOT NULL,
        supply_amount {MONEY} NOT NULL, vat_amount {MONEY} NOT NULL, total_amount {MONEY} NOT NULL,
        created_by bigint NOT NULL REFERENCES users (user_id),
        created_at timestamptz NOT NULL DEFAULT now(),
        CHECK ((source_kind = 'REVISION') = (revision_id IS NOT NULL)))""",
    "CREATE INDEX ix_quotes_project ON quote_headers (project_id)",
    f"""CREATE TABLE quote_lines (
        quote_line_id bigserial PRIMARY KEY,
        quote_id bigint NOT NULL REFERENCES quote_headers (quote_id) ON DELETE CASCADE,
        line_no integer NOT NULL,
        cost_category text NOT NULL
            CHECK (cost_category IN ('MATERIAL', 'LABOR', 'OVERHEAD', 'OUTSOURCE')),
        item_code text NOT NULL,
        item_name text NOT NULL,
        unit text NOT NULL,
        calculated_qty numeric(18,6) NOT NULL CHECK (calculated_qty > 0),
        calculated_unit_price {MONEY},
        calculated_amount {MONEY},
        excluded boolean NOT NULL,
        override_qty numeric(18,6), override_unit_price {MONEY}, override_amount {MONEY},
        override_reason text, overridden_by bigint REFERENCES users (user_id),
        overridden_at timestamptz,
        UNIQUE (quote_id, line_no),
        CHECK (excluded OR calculated_amount IS NOT NULL))""",
    """CREATE TABLE quote_traces (
        quote_trace_id bigserial PRIMARY KEY,
        quote_line_id bigint NOT NULL REFERENCES quote_lines (quote_line_id) ON DELETE CASCADE,
        source_kind text NOT NULL CHECK (source_kind IN ('ENTITY', 'FEATURE', 'REVISION')),
        source_ref text NOT NULL,
        revision_id text REFERENCES document_revisions (revision_id),
        rule_code text NOT NULL,
        price_item_code text,
        unit_price text,
        inputs jsonb NOT NULL,
        formula_text text NOT NULL)""",
    "CREATE INDEX ix_traces_line ON quote_traces (quote_line_id)",
    """CREATE TABLE quote_validation_logs (
        log_id bigserial PRIMARY KEY,
        quote_id bigint NOT NULL REFERENCES quote_headers (quote_id) ON DELETE CASCADE,
        severity text NOT NULL CHECK (severity IN ('INFO', 'WARN', 'ERROR')),
        code text NOT NULL,
        message text NOT NULL)""",
]


def upgrade() -> None:
    for stmt in DDL:
        op.execute(stmt)
    for t in ("quote_headers", "quote_lines"):
        op.execute(
            f"CREATE TRIGGER trg_audit_{t} AFTER INSERT OR UPDATE OR DELETE ON {t} "
            "FOR EACH ROW EXECUTE FUNCTION fn_audit_log('quote_id')"
        )


def downgrade() -> None:
    for t in ("quote_validation_logs", "quote_traces", "quote_lines", "quote_headers"):
        op.execute(f"DROP TABLE {t}")
    op.execute("ALTER TABLE cost_ratios DROP COLUMN material_basis")
