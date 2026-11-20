"""Construction Process, Material Takeoff & Cost Simulation Plans (TASK-025).

Revision ID: 0045
Revises: 0044
"""

from alembic import op

revision = "0045"
down_revision = "0044"


def upgrade() -> None:
    op.execute(
        """CREATE TABLE construction_plans (
            plan_id bigserial PRIMARY KEY,
            project_id bigint NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
            name text NOT NULL,
            domain text NOT NULL CHECK (domain IN (
                'BUILDING', 'CIVIL_LANDSCAPE', 'MEP_ELECTRICAL',
                'MEP_PLUMBING', 'MEP_HVAC', 'INTERIOR_RENO'
            )),
            work_mode text NOT NULL DEFAULT 'INSTALL' CHECK (work_mode IN (
                'NEW_BUILD', 'INSTALL', 'REPLACE', 'DEMOLISH', 'RENOVATE'
            )),
            scale_qty numeric(12,2) NOT NULL DEFAULT 100.0 CHECK (scale_qty > 0),
            scale_unit text NOT NULL DEFAULT 'm2',
            cost_rates jsonb NOT NULL DEFAULT '{}'::jsonb,
            tasks jsonb NOT NULL DEFAULT '[]'::jsonb,
            last_simulation jsonb,
            created_by bigint NOT NULL REFERENCES users(user_id),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )"""
    )
    op.execute(
        "CREATE INDEX idx_construction_plans_project ON construction_plans(project_id, updated_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS construction_plans")
