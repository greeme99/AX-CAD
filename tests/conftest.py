import io
import os
from pathlib import Path
from types import SimpleNamespace

import ezdxf
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.api import main
from backend.api.auth import hash_password
from backend.db.models import User, UserRole
from backend.db.session import engine, load_env

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "correct-horse-1"
load_env()
TEST_URL = os.environ.get("TEST_DATABASE_URL")
os.environ["DATABASE_URL"] = TEST_URL or ""  # tests must never fall back to the dev database
ALL_TABLES = (
    "users, user_roles, projects, project_members, documents, document_revisions, "
    "approvals, audit_logs"
)  # everything but the seeded roles


@pytest.fixture(scope="session")
def _schema():
    if not TEST_URL:
        pytest.skip("TEST_DATABASE_URL not set: DB-backed API tests skipped")
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "backend" / "db" / "migrations"))
    with engine().begin() as conn:  # leftover rows of newer feature types would block downgrades
        if conn.scalar(text("SELECT to_regclass('features')")) is not None:
            conn.execute(text("TRUNCATE features"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


@pytest.fixture
def client(_schema, tmp_path, monkeypatch):
    monkeypatch.setattr(main, "VAR_DIR", tmp_path / "var")
    with engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {ALL_TABLES} RESTART IDENTITY CASCADE"))
    return TestClient(main.app)


@pytest.fixture
def make_user(client):
    def make(login, *roles, active=True):
        with Session(engine()) as db:
            u = User(
                login_id=login,
                user_name=login.title(),
                password_hash=hash_password(PASSWORD),
                is_active=active,
                roles=[UserRole(role_code=r) for r in roles],
            )
            db.add(u)
            db.commit()
            return u.user_id

    return make


@pytest.fixture
def headers(client):
    def get(login):
        r = client.post("/api/auth/login", json={"login_id": login, "password": PASSWORD})
        return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}

    return get


@pytest.fixture
def dxf():
    def make(lines=1):
        d = ezdxf.new(setup=True)
        for i in range(lines):
            d.modelspace().add_line((0, 0), (10 + i, 5))
        buf = io.StringIO()
        d.write(buf)
        return buf.getvalue().encode()

    return make


@pytest.fixture
def world(client, make_user, headers):
    """admin/designer/reviewer/viewer, all members of one project holding one empty document."""
    ids = {
        "admin": make_user("admin", "ADMIN"),
        "designer": make_user("designer", "DESIGNER"),
        "reviewer": make_user("reviewer", "REVIEWER"),
        "viewer": make_user("viewer", "VIEWER"),
    }
    h = {name: headers(name) for name in ids}
    r = client.post(
        "/api/projects", json={"project_code": "P1", "project_name": "P"}, headers=h["admin"]
    )
    pid = r.json()["data"]["project_id"]
    for name in ("designer", "reviewer", "viewer"):
        client.post(f"/api/projects/{pid}/members", json={"user_id": ids[name]}, headers=h["admin"])
    r = client.post(
        f"/api/projects/{pid}/documents",
        json={"doc_no": "D1", "doc_type": "DRAWING", "title": "T"},
        headers=h["designer"],
    )
    return SimpleNamespace(ids=ids, h=h, pid=pid, doc=r.json()["data"]["document_id"])
