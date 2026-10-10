"""quotes: a CONFIRMED quote's lines cannot be adjusted (FN-19), enforced in the database too
(the API checks under a header lock; this catches any other path). S10 security review M4.

Revision ID: 0007
Revises: 0006
"""

from alembic import op

revision = "0007"
down_revision = "0006"


def upgrade() -> None:
    op.execute(
        """CREATE FUNCTION fn_quote_confirmed_frozen() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (SELECT status FROM quote_headers WHERE quote_id = NEW.quote_id) = 'CONFIRMED'
               AND (NEW.override_qty, NEW.override_unit_price, NEW.override_amount,
                    NEW.override_reason)
                   IS DISTINCT FROM
                   (OLD.override_qty, OLD.override_unit_price, OLD.override_amount,
                    OLD.override_reason) THEN
                RAISE EXCEPTION 'quote % is confirmed', NEW.quote_id
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$"""
    )
    op.execute(
        "CREATE TRIGGER trg_quote_lines_confirmed BEFORE UPDATE ON quote_lines "
        "FOR EACH ROW EXECUTE FUNCTION fn_quote_confirmed_frozen()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_quote_lines_confirmed ON quote_lines")
    op.execute("DROP FUNCTION IF EXISTS fn_quote_confirmed_frozen()")
