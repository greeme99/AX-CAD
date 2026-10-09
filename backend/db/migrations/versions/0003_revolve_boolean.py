"""features: REVOLVE + BOOLEAN types; audit trigger on project_members

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade() -> None:
    op.execute("ALTER TABLE features DROP CONSTRAINT features_feature_type_check")
    op.execute(
        "ALTER TABLE features ADD CONSTRAINT features_feature_type_check "
        "CHECK (feature_type IN ('EXTRUDE', 'REVOLVE', 'BOOLEAN'))"
    )
    # ponytail: object_id = project_id; the member's user_id is in old_value/new_value json.
    # Upgrade path: composite object_id if per-member lookups get slow.
    op.execute(
        "CREATE TRIGGER trg_audit_project_members "
        "AFTER INSERT OR UPDATE OR DELETE ON project_members "
        "FOR EACH ROW EXECUTE FUNCTION fn_audit_log('project_id')"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_project_members ON project_members")
    op.execute("ALTER TABLE features DROP CONSTRAINT features_feature_type_check")
    op.execute(
        "ALTER TABLE features ADD CONSTRAINT features_feature_type_check "
        "CHECK (feature_type IN ('EXTRUDE'))"
    )
