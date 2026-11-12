"""plan §23 S1: sheet metal bend table of a master data version (material x thickness -> inside
radius, K-factor or 90-degree bend deduction). A sheet with no radius / K of its own reads it.

Revision ID: 0029
Revises: 0028
"""

from alembic import op

revision = "0029"
down_revision = "0028"


def upgrade() -> None:
    op.execute(
        """CREATE TABLE sheet_bends (
        sheet_bend_id bigserial PRIMARY KEY,
        version_id bigint NOT NULL REFERENCES master_versions (version_id) ON DELETE CASCADE,
        material_code text NOT NULL,
        thickness_mm numeric(10,3) NOT NULL CHECK (thickness_mm > 0 AND thickness_mm <= 50),
        inner_radius_mm numeric(10,3) NOT NULL
            CHECK (inner_radius_mm >= 0.01 AND inner_radius_mm <= 1000),
        k_factor numeric(5,4) CHECK (k_factor >= 0 AND k_factor <= 1),
        bd90_mm numeric(10,4) CHECK (bd90_mm >= 0 AND bd90_mm <= 1000),
        note text,
        CHECK (k_factor IS NOT NULL OR bd90_mm IS NOT NULL),
        UNIQUE (version_id, material_code, thickness_mm))"""
    )
    # same frozen / audit pair as every master data table (0005, 0016)
    op.execute(
        "CREATE TRIGGER trg_frozen_sheet_bends BEFORE INSERT OR UPDATE OR DELETE ON sheet_bends "
        "FOR EACH ROW EXECUTE FUNCTION fn_master_frozen()"
    )
    op.execute(
        "CREATE TRIGGER trg_audit_sheet_bends AFTER INSERT OR UPDATE OR DELETE ON sheet_bends "
        "FOR EACH ROW EXECUTE FUNCTION fn_audit_log('version_id')"
    )


def downgrade() -> None:
    op.execute("DROP TABLE sheet_bends")
