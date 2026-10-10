"""Admin CLI: uv run python -m backend.cli create-user --login ID --name NAME [--role R]... [--admin]
uv run python -m backend.cli app-role   (deploy: least-privilege DB role for the API)
"""

import argparse
import getpass
import os
import sys

from psycopg import sql
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.api.auth import hash_password
from backend.db.models import ROLES, User, UserRole
from backend.db.session import engine

MIN_PASSWORD = 10


def create_user(args: argparse.Namespace) -> int:
    password = getpass.getpass("Password: ")  # never echoed, never taken from argv
    if len(password) < MIN_PASSWORD:
        print(f"Password must be at least {MIN_PASSWORD} characters", file=sys.stderr)
        return 1
    if password != getpass.getpass("Repeat password: "):
        print("Passwords do not match", file=sys.stderr)
        return 1
    roles = set(args.role) | ({"ADMIN"} if args.admin else set())
    user = User(
        login_id=args.login,
        user_name=args.name,
        password_hash=hash_password(password),
        roles=[UserRole(role_code=r) for r in sorted(roles)],
    )
    with Session(engine()) as db:
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            print(f"Login '{args.login}' already exists", file=sys.stderr)
            return 1
        print(f"Created user {user.user_id} ({args.login}) roles={sorted(roles)}")
    return 0


# The API connects as this role: data read/write only. It cannot disable triggers, alter or drop
# tables, TRUNCATE, rewind sequences, or remove evidence: the owner account stays with migrate.
# DELETE only where the API deletes (members, 3D features, draft master data); evidence is append-only.
APP_DELETABLE = (
    "project_members", "features", "master_versions",
    "materials", "price_items", "process_rules", "cost_ratios", "mapping_rules",
)  # fmt: skip
APP_APPEND_ONLY = (
    "audit_logs",
    "quote_reports",
    "quote_traces",
    "quote_validation_logs",
    "bom_headers",
)
APP_ROLE_SQL = (
    "GRANT USAGE ON SCHEMA public TO {r}",
    "GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO {r}",
    "REVOKE DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public FROM {r}",  # undo wider older grants
    "GRANT DELETE ON {deletable} TO {r}",
    "REVOKE UPDATE ON {append_only} FROM {r}",
    "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {r}",
    "REVOKE UPDATE ON ALL SEQUENCES IN SCHEMA public FROM {r}",  # no setval()
    "REVOKE ALL ON alembic_version FROM {r}",
)


def _tables(names: tuple[str, ...]) -> sql.Composable:
    return sql.SQL(", ").join(sql.Identifier(n) for n in names)


def ensure_app_role(name: str, password: str) -> None:
    """Create or update the API role and (re)grant it on every table: run after each migration."""
    with engine().begin() as conn:
        exists = conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": name})
        raw = conn.connection.driver_connection  # psycopg: identifiers/literals quoted client-side
        assert raw is not None
        cur = raw.cursor()
        role = sql.Identifier(name)
        # only a SCRAM verifier crosses the wire: the plain password never reaches server logs
        verifier = raw.pgconn.encrypt_password(password.encode(), name.encode(), b"scram-sha-256")
        # a new role gets no elevated attributes by default; only a superuser may even state them
        cur.execute(
            sql.SQL("{} ROLE {} LOGIN PASSWORD {}").format(
                sql.SQL("ALTER" if exists else "CREATE"), role, sql.Literal(verifier.decode())
            )
        )
        elevated = conn.scalar(
            text(
                "SELECT rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls"
                " OR EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member = r.oid)"
                " FROM pg_roles r WHERE rolname = :r"
            ),
            {"r": name},
        )
        if elevated:
            raise SystemExit(f"role {name} has elevated attributes or memberships: remove them")
        db = conn.scalar(text("SELECT current_database()"))
        cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(db), role))
        tables = {"deletable": _tables(APP_DELETABLE), "append_only": _tables(APP_APPEND_ONLY)}
        for stmt in APP_ROLE_SQL:
            cur.execute(sql.SQL(stmt).format(r=role, **tables))


def app_role(args: argparse.Namespace) -> int:
    password = os.environ.get("APP_DB_PASSWORD", "")
    if len(password) < 16:
        print("APP_DB_PASSWORD must be set (16+ characters)", file=sys.stderr)
        return 1
    ensure_app_role(args.role_name, password)
    print(f"App role {args.role_name} ready")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="backend.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create-user", help="create a user; the password is prompted twice")
    p.add_argument("--login", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--role", action="append", default=[], choices=ROLES)
    p.add_argument("--admin", action="store_true", help="shortcut for --role ADMIN")
    p.set_defaults(func=create_user)
    r = sub.add_parser("app-role", help="create/update the API's DB role from APP_DB_PASSWORD")
    r.add_argument("--role-name", default=os.environ.get("APP_DB_USER", "axcad_app"))
    r.set_defaults(func=app_role)
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
