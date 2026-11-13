"""plan §23 S5: material_basis NEST — a plate part's material is its share of a nested plate.

Revision ID: 0032
Revises: 0031
"""

from alembic import op

revision = "0032"
down_revision = "0031"


def _check(values: str) -> None:
    op.execute("ALTER TABLE cost_ratios DROP CONSTRAINT IF EXISTS cost_ratios_material_basis_check")
    op.execute(
        "ALTER TABLE cost_ratios ADD CONSTRAINT cost_ratios_material_basis_check "
        f"CHECK (material_basis IN ({values}))"
    )


def upgrade() -> None:
    _check("'NET', 'BBOX', 'NEST'")


def downgrade() -> None:
    op.execute("UPDATE cost_ratios SET material_basis = 'BBOX' WHERE material_basis = 'NEST'")
    _check("'NET', 'BBOX'")
