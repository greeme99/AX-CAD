"""Admin CLI: uv run python -m backend.cli create-user --login ID --name NAME [--role R]... [--admin]"""

import argparse
import getpass
import sys

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


def main() -> int:
    parser = argparse.ArgumentParser(prog="backend.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create-user", help="create a user; the password is prompted twice")
    p.add_argument("--login", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--role", action="append", default=[], choices=ROLES)
    p.add_argument("--admin", action="store_true", help="shortcut for --role ADMIN")
    p.set_defaults(func=create_user)
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
