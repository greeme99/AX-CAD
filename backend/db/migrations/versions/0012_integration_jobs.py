"""ERP transfer jobs (FN-24): one row per idempotency key (document + revision), its payload,
attempts (max 3 per run) and the ERP answer. Credentials never land here.

Revision ID: 0012
Revises: 0011
"""

from alembic import op

revision = "0012"
down_revision = "0011"


def upgrade() -> None:
    op.execute(
        """CREATE TABLE integration_jobs (
        job_id bigserial PRIMARY KEY,
        target_system text NOT NULL CHECK (target_system IN ('ERP')),
        job_type text NOT NULL CHECK (job_type IN ('BOM')),
        idempotency_key text NOT NULL UNIQUE,
        project_id bigint NOT NULL REFERENCES projects (project_id),
        bom_id bigint NOT NULL REFERENCES bom_headers (bom_id),
        status text NOT NULL DEFAULT 'PENDING'
            CHECK (status IN ('PENDING', 'RUNNING', 'SUCCESS', 'FAILED')),
        attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 3),
        run_id uuid,  -- the worker that owns the current run; a retry hands out a new one
        request_payload jsonb NOT NULL,
        response_payload jsonb,
        last_error text,
        created_by bigint NOT NULL REFERENCES users (user_id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now())"""
    )
    op.execute("CREATE INDEX ix_jobs_status ON integration_jobs (status, updated_at)")
    op.execute("CREATE INDEX ix_jobs_bom ON integration_jobs (bom_id)")
    # what was asked for and sent is fixed; a delivered job is final
    op.execute(
        """CREATE FUNCTION fn_job_frozen() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (NEW.idempotency_key, NEW.bom_id, NEW.project_id, NEW.request_payload)
               IS DISTINCT FROM
               (OLD.idempotency_key, OLD.bom_id, OLD.project_id, OLD.request_payload)
               OR OLD.status = 'SUCCESS' THEN
                RAISE EXCEPTION 'integration job % is frozen', OLD.job_id
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$"""
    )
    op.execute(
        "CREATE TRIGGER trg_integration_jobs_frozen BEFORE UPDATE ON integration_jobs "
        "FOR EACH ROW EXECUTE FUNCTION fn_job_frozen()"
    )
    op.execute(
        "CREATE TRIGGER trg_audit_integration_jobs AFTER INSERT OR UPDATE OR DELETE "
        "ON integration_jobs FOR EACH ROW EXECUTE FUNCTION fn_audit_log('job_id')"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS integration_jobs")
    op.execute("DROP FUNCTION IF EXISTS fn_job_frozen()")
