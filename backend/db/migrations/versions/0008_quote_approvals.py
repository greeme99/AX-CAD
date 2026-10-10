"""quote approval (FN-22 for quotes, S11): DRAFT -> IN_REVIEW -> CONFIRMED | (rejected) DRAFT.
Lines are frozen from the moment a review is requested, not only once confirmed.

Revision ID: 0008
Revises: 0007
"""

from alembic import op

revision = "0008"
down_revision = "0007"

# FOR SHARE: a status change (header row lock) cannot slip between this check and the commit
GUARD = """CREATE OR REPLACE FUNCTION fn_quote_confirmed_frozen() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE s text;
    BEGIN
        SELECT status INTO s FROM quote_headers WHERE quote_id = NEW.quote_id FOR SHARE;
        IF s {cond}
           AND (NEW.override_qty, NEW.override_unit_price, NEW.override_amount,
                NEW.override_reason{who})
               IS DISTINCT FROM
               (OLD.override_qty, OLD.override_unit_price, OLD.override_amount,
                OLD.override_reason{old_who}) THEN
            RAISE EXCEPTION 'quote % is %', NEW.quote_id, s USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END $$"""


def upgrade() -> None:
    op.execute("ALTER TABLE quote_headers DROP CONSTRAINT quote_headers_status_check")
    op.execute(
        "ALTER TABLE quote_headers ADD CONSTRAINT quote_headers_status_check "
        "CHECK (status IN ('DRAFT', 'IN_REVIEW', 'CONFIRMED'))"
    )
    # request and decision keep their own comment; decided_by tells an ADMIN cancel apart
    op.execute(
        """CREATE TABLE quote_approvals (
        approval_id bigserial PRIMARY KEY,
        quote_id bigint NOT NULL REFERENCES quote_headers (quote_id) ON DELETE CASCADE,
        requested_by bigint NOT NULL REFERENCES users (user_id),
        approver_id bigint NOT NULL REFERENCES users (user_id),
        status text NOT NULL DEFAULT 'PENDING'
            CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'CANCELLED')),
        comment text,
        decision_comment text,
        decided_by bigint REFERENCES users (user_id),
        created_at timestamptz NOT NULL DEFAULT now(),
        decided_at timestamptz,
        CHECK (requested_by <> approver_id),
        CHECK ((status = 'PENDING') = (decided_by IS NULL AND decided_at IS NULL)))"""
    )
    # one open review per quote, whatever the API does
    op.execute(
        "CREATE UNIQUE INDEX ux_quote_approvals_pending ON quote_approvals (quote_id) "
        "WHERE status = 'PENDING'"
    )
    op.execute("CREATE INDEX ix_quote_approvals_inbox ON quote_approvals (approver_id, status)")
    op.execute("CREATE INDEX ix_quote_approvals_quote ON quote_approvals (quote_id)")
    op.execute(
        "CREATE TRIGGER trg_audit_quote_approvals AFTER INSERT OR UPDATE OR DELETE "
        "ON quote_approvals FOR EACH ROW EXECUTE FUNCTION fn_audit_log('quote_id')"
    )
    op.execute(
        GUARD.format(
            cond="<> 'DRAFT'",
            who=", NEW.overridden_by, NEW.overridden_at",
            old_who=", OLD.overridden_by, OLD.overridden_at",
        )
    )


def downgrade() -> None:
    # approval history is audit evidence: refuse instead of silently dropping it
    op.execute(
        """DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM quote_approvals)
           OR EXISTS (SELECT 1 FROM quote_headers WHERE status = 'IN_REVIEW') THEN
            RAISE EXCEPTION 'quote approvals exist: archive them before downgrading 0008';
        END IF;
    END $$"""
    )
    op.execute(GUARD.format(cond="= 'CONFIRMED'", who="", old_who=""))
    op.execute("DROP TABLE quote_approvals")
    op.execute("ALTER TABLE quote_headers DROP CONSTRAINT quote_headers_status_check")
    op.execute(
        "ALTER TABLE quote_headers ADD CONSTRAINT quote_headers_status_check "
        "CHECK (status IN ('DRAFT', 'CONFIRMED'))"
    )
