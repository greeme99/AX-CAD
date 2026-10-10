"""Track 3-4: the API's own DB role can work with data but cannot undo the database's protections."""

import secrets
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import errors
from sqlalchemy import text

from backend.cli import ensure_app_role
from backend.db.session import engine
from tests.conftest import TEST_URL

ROLE = "axcad_app_test"


@pytest.fixture
def app_conn(_schema):
    with engine().connect() as c:
        can = c.execute(
            text("SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user")
        ).scalar()
    if not can:
        pytest.skip("test database user cannot create roles")
    password = secrets.token_hex(16)
    ensure_app_role(ROLE, password)
    ensure_app_role(ROLE, password)  # idempotent: runs after every deploy's migration
    u = urlsplit(TEST_URL)
    netloc = f"{ROLE}:{password}@{u.hostname}:{u.port or 5432}"
    url = urlunsplit(u._replace(scheme="postgresql", netloc=netloc))  # libpq, not SQLAlchemy
    conn = psycopg.connect(url, autocommit=True)
    yield conn
    conn.close()
    with engine().begin() as c:  # roles are cluster-wide: leave nothing behind
        db = c.execute(text("SELECT current_database()")).scalar()
        for stmt in (
            f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE}",
            f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {ROLE}",
            f"REVOKE ALL ON SCHEMA public FROM {ROLE}",
            f'REVOKE ALL ON DATABASE "{db}" FROM {ROLE}',
            f"DROP ROLE {ROLE}",
        ):
            c.execute(text(stmt))


def test_app_role_reads_writes_but_cannot_lift_protections(app_conn):
    cur = app_conn.cursor()
    cur.execute("SELECT count(*) FROM users")  # ordinary data access works
    cur.execute("SELECT count(*) FROM audit_logs")
    denied = [
        "UPDATE audit_logs SET action = 'x'",
        "DELETE FROM audit_logs",
        "TRUNCATE quote_headers CASCADE",
        "ALTER TABLE audit_logs DISABLE TRIGGER ALL",
        "DROP TRIGGER trg_quote_headers_guard ON quote_headers",
        "CREATE TABLE app_made (x int)",
        "SELECT * FROM alembic_version",
    ]
    for stmt in denied:
        with pytest.raises((errors.InsufficientPrivilege, errors.WrongObjectType)):
            cur.execute(stmt)
