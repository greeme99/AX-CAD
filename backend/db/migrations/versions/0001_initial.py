"""initial schema: users/roles, projects, documents, revisions, approvals, audit

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None

ROLES = ("ADMIN", "DESIGNER", "ESTIMATOR", "REVIEWER", "MANUFACTURING", "VIEWER")
AUDITED = {  # table -> PK column passed to fn_audit_log as TG_ARGV[0] (Research defect D1 fix)
    "projects": "project_id",
    "documents": "document_id",
    "document_revisions": "revision_id",
    "approvals": "approval_id",
    "users": "user_id",
    "user_roles": "user_id",  # role changes are security-relevant; object_id = the user
}
TABLES = [
    "audit_logs",
    "approvals",
    "documents",
    "document_revisions",
    "project_members",
    "projects",
    "user_roles",
    "roles",
    "users",
]

DDL = [
    """CREATE TABLE users (
        user_id bigserial PRIMARY KEY,
        login_id text NOT NULL UNIQUE,
        user_name text NOT NULL,
        password_hash text NOT NULL,
        is_active boolean NOT NULL DEFAULT true,
        failed_login_count integer NOT NULL DEFAULT 0,
        locked_until timestamptz,
        created_at timestamptz NOT NULL DEFAULT now())""",
    "CREATE TABLE roles (role_code text PRIMARY KEY)",
    "INSERT INTO roles (role_code) VALUES " + ", ".join(f"('{r}')" for r in ROLES),
    """CREATE TABLE user_roles (
        user_id bigint NOT NULL REFERENCES users ON DELETE CASCADE,
        role_code text NOT NULL REFERENCES roles,
        PRIMARY KEY (user_id, role_code))""",
    """CREATE TABLE projects (
        project_id bigserial PRIMARY KEY,
        project_code text NOT NULL UNIQUE,
        project_name text NOT NULL,
        customer_name text,
        status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'ARCHIVED')),
        created_by bigint NOT NULL REFERENCES users,
        created_at timestamptz NOT NULL DEFAULT now())""",
    """CREATE TABLE project_members (
        project_id bigint NOT NULL REFERENCES projects ON DELETE CASCADE,
        user_id bigint NOT NULL REFERENCES users ON DELETE CASCADE,
        PRIMARY KEY (project_id, user_id))""",
    """CREATE TABLE documents (
        document_id bigserial PRIMARY KEY,
        project_id bigint NOT NULL REFERENCES projects,
        doc_no text NOT NULL,
        doc_type text NOT NULL CHECK (doc_type IN ('DRAWING', 'PART', 'ASSEMBLY')),
        title text NOT NULL,
        status text NOT NULL DEFAULT 'DRAFT'
            CHECK (status IN ('DRAFT', 'IN_REVIEW', 'APPROVED', 'REJECTED', 'RELEASED')),
        current_revision_id text,
        created_by bigint NOT NULL REFERENCES users,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (project_id, doc_no))""",
    """CREATE TABLE document_revisions (
        revision_id text PRIMARY KEY,
        document_id bigint NOT NULL REFERENCES documents,
        revision_no text NOT NULL,
        parent_revision_id text REFERENCES document_revisions,
        file_format text NOT NULL,
        checksum text NOT NULL,
        note text,
        created_by bigint NOT NULL REFERENCES users,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (document_id, revision_no))""",
    """ALTER TABLE documents ADD CONSTRAINT fk_documents_current_revision
        FOREIGN KEY (current_revision_id) REFERENCES document_revisions""",
    """CREATE TABLE approvals (
        approval_id bigserial PRIMARY KEY,
        document_id bigint NOT NULL REFERENCES documents,
        revision_id text NOT NULL REFERENCES document_revisions,
        requested_by bigint NOT NULL REFERENCES users,
        approver_id bigint NOT NULL REFERENCES users,
        status text NOT NULL DEFAULT 'PENDING'
            CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED')),
        comment text,
        created_at timestamptz NOT NULL DEFAULT now(),
        decided_at timestamptz,
        CHECK (requested_by <> approver_id))""",
    """CREATE TABLE audit_logs (
        audit_log_id bigserial PRIMARY KEY,
        object_type text NOT NULL,
        object_id text NOT NULL,
        action text NOT NULL,
        old_value jsonb,
        new_value jsonb,
        user_id bigint,
        created_at timestamptz NOT NULL DEFAULT now())""",
    "CREATE INDEX ix_documents_project ON documents (project_id)",
    "CREATE INDEX ix_revisions_document ON document_revisions (document_id)",
    "CREATE INDEX ix_approvals_inbox ON approvals (approver_id, status)",
    "CREATE INDEX ix_audit_object ON audit_logs (object_type, object_id)",
    """CREATE FUNCTION fn_audit_log() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE o jsonb; n jsonb;
    BEGIN
        IF TG_OP <> 'INSERT' THEN o := to_jsonb(OLD) - 'password_hash'; END IF;
        IF TG_OP <> 'DELETE' THEN n := to_jsonb(NEW) - 'password_hash'; END IF;
        INSERT INTO audit_logs (object_type, object_id, action, old_value, new_value, user_id)
        VALUES (TG_TABLE_NAME, COALESCE(n, o) ->> TG_ARGV[0], TG_OP, o, n,
                NULLIF(current_setting('app.user_id', true), '')::bigint);
        RETURN NULL;
    END $$""",
    # ponytail: single DB role, so immutability is a trigger not GRANTs; TRUNCATE/owner can still
    # bypass it. Upgrade path: separate app role without UPDATE/DELETE on audit_logs.
    """CREATE FUNCTION fn_audit_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'audit_logs is append-only'; END $$""",
    """CREATE TRIGGER trg_audit_immutable BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION fn_audit_immutable()""",
]


def upgrade() -> None:
    for stmt in DDL:
        op.execute(stmt)
    for table, pk in AUDITED.items():
        op.execute(
            f"CREATE TRIGGER trg_audit_{table} AFTER INSERT OR UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION fn_audit_log('{pk}')"
        )


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS fn_audit_log()")
    op.execute("DROP FUNCTION IF EXISTS fn_audit_immutable()")
