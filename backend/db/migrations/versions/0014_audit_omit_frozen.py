"""audit logs stop copying frozen bulk columns on UPDATE: fn_audit_log takes an optional second
argument, a comma-separated column list dropped from UPDATE row images. The INSERT image keeps
them, and those columns cannot change afterwards (fn_job_frozen), so nothing is lost.

ERP jobs were the case: every status change re-copied the whole BOM request payload.

Revision ID: 0014
Revises: 0013
"""

from alembic import op

revision = "0014"
down_revision = "0013"


def _audit_fn(omit_on_update: bool) -> str:
    omit = (
        """
        IF TG_OP = 'UPDATE' AND TG_NARGS > 1 THEN
            o := o - string_to_array(TG_ARGV[1], ',');
            n := n - string_to_array(TG_ARGV[1], ',');
        END IF;"""
        if omit_on_update
        else ""
    )
    return f"""CREATE OR REPLACE FUNCTION fn_audit_log() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE o jsonb; n jsonb;
    BEGIN
        IF TG_OP <> 'INSERT' THEN o := to_jsonb(OLD) - 'password_hash'; END IF;
        IF TG_OP <> 'DELETE' THEN n := to_jsonb(NEW) - 'password_hash'; END IF;{omit}
        INSERT INTO audit_logs (object_type, object_id, action, old_value, new_value, user_id)
        VALUES (TG_TABLE_NAME, COALESCE(n, o) ->> TG_ARGV[0], TG_OP, o, n,
                NULLIF(current_setting('app.user_id', true), '')::bigint);
        RETURN NULL;
    END $$"""


def _job_trigger(args: str) -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_integration_jobs ON integration_jobs")
    op.execute(
        "CREATE TRIGGER trg_audit_integration_jobs AFTER INSERT OR UPDATE OR DELETE "
        f"ON integration_jobs FOR EACH ROW EXECUTE FUNCTION fn_audit_log({args})"
    )


def upgrade() -> None:
    op.execute(_audit_fn(omit_on_update=True))
    _job_trigger("'job_id', 'request_payload'")


def downgrade() -> None:
    _job_trigger("'job_id'")
    op.execute(_audit_fn(omit_on_update=False))
