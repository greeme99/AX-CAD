"""Assembly A1: expand mate types, limits, suppression, and extra payload (plan.md §23.10).

Revision ID: 0036
Revises: 0035
"""

from alembic import op

revision = "0036"
down_revision = "0035"

OLD_TYPES = "'COINCIDENT', 'DISTANCE', 'PARALLEL', 'ANGLE', 'CONCENTRIC'"
NEW_TYPES = (
    f"{OLD_TYPES}, 'TANGENT', 'PERPENDICULAR', 'LOCK', 'SYMMETRIC', 'WIDTH', "
    "'LIMIT_DISTANCE', 'LIMIT_ANGLE'"
)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE assembly_mates DROP CONSTRAINT IF EXISTS assembly_mates_mate_type_check"
    )
    op.execute(
        f"ALTER TABLE assembly_mates ADD CONSTRAINT assembly_mates_mate_type_check "
        f"CHECK (mate_type IN ({NEW_TYPES}))"
    )
    op.execute("ALTER TABLE assembly_mates DROP CONSTRAINT IF EXISTS assembly_mates_status_check")
    op.execute(
        "ALTER TABLE assembly_mates ADD CONSTRAINT assembly_mates_status_check "
        "CHECK (status IN ('NEW', 'OK', 'FAILED', 'SUPPRESSED', 'BROKEN'))"
    )
    op.execute("ALTER TABLE assembly_mates ADD COLUMN IF NOT EXISTS value_min double precision")
    op.execute("ALTER TABLE assembly_mates ADD COLUMN IF NOT EXISTS value_max double precision")
    op.execute(
        "ALTER TABLE assembly_mates ADD COLUMN IF NOT EXISTS suppressed boolean NOT NULL DEFAULT false"
    )
    op.execute("ALTER TABLE assembly_mates ADD COLUMN IF NOT EXISTS extra jsonb")


def downgrade() -> None:
    op.execute(f"DELETE FROM assembly_mates WHERE mate_type NOT IN ({OLD_TYPES})")
    op.execute("UPDATE assembly_mates SET status = 'NEW' WHERE status IN ('SUPPRESSED', 'BROKEN')")
    op.execute("ALTER TABLE assembly_mates DROP COLUMN IF EXISTS extra")
    op.execute("ALTER TABLE assembly_mates DROP COLUMN IF EXISTS suppressed")
    op.execute("ALTER TABLE assembly_mates DROP COLUMN IF EXISTS value_max")
    op.execute("ALTER TABLE assembly_mates DROP COLUMN IF EXISTS value_min")
    op.execute("ALTER TABLE assembly_mates DROP CONSTRAINT IF EXISTS assembly_mates_status_check")
    op.execute(
        "ALTER TABLE assembly_mates ADD CONSTRAINT assembly_mates_status_check "
        "CHECK (status IN ('NEW', 'OK', 'FAILED'))"
    )
    op.execute(
        "ALTER TABLE assembly_mates DROP CONSTRAINT IF EXISTS assembly_mates_mate_type_check"
    )
    op.execute(
        f"ALTER TABLE assembly_mates ADD CONSTRAINT assembly_mates_mate_type_check "
        f"CHECK (mate_type IN ({OLD_TYPES}))"
    )
