"""2D underlays U1: PDF/DWFx reference files of a project.

The file itself is kept outside the database in a content-addressed store; this table says whose
it is, what it is and how many pages it has. One row per (project, content): uploading the same
file again returns the row that exists.

Revision ID: 0028
Revises: 0027
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0028"
down_revision = "0027"


def upgrade() -> None:
    op.create_table(
        "underlay_files",
        sa.Column("underlay_id", sa.Text(), primary_key=True),
        sa.Column(
            "project_id", sa.BigInteger(), sa.ForeignKey("projects.project_id"), nullable=False
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("pages", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("kind in ('pdf','dwfx')", name="ck_underlay_kind"),
        sa.UniqueConstraint("project_id", "sha256", name="uq_underlay_project_sha"),
    )
    op.create_index("ix_underlay_project", "underlay_files", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_underlay_project", table_name="underlay_files")
    op.drop_table("underlay_files")
