"""Auth, users, projects/members and audit-log endpoints."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from backend.api.auth import (
    CurrentUser,
    Db,
    get_project,
    hash_password,
    make_token,
    need,
    set_actor,
    user_from_token,
    verify_password,
)
from backend.api.common import ApiError, body, pick
from backend.db.models import ROLES, AuditLog, Document, Project, ProjectMember, User, UserRole

router = APIRouter()
MAX_FAILURES = 5
LOCK_FOR = timedelta(minutes=10)
# ponytail: login attempts beyond the per-account lockout are not rate limited; add per-IP limiting
# at the reverse proxy / middleware when exposed beyond the intranet.

Role = Literal["ADMIN", "DESIGNER", "ESTIMATOR", "REVIEWER", "MANUFACTURING", "VIEWER"]
Code = Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")]
Name = Annotated[str, Field(min_length=1, max_length=200)]
Password = Annotated[str, Field(min_length=10, max_length=128)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def user_out(u: User) -> dict[str, Any]:
    return {**pick(u, "user_id", "login_id", "user_name"), "roles": u.role_codes}


# --- auth (FN-25) ---
class LoginReq(_Strict):
    login_id: Annotated[str, Field(min_length=1, max_length=64)]
    password: Annotated[str, Field(min_length=1, max_length=128)]


class RefreshReq(_Strict):
    refresh_token: Annotated[str, Field(min_length=1, max_length=4096)]


@router.post("/api/auth/login")
def login(req: LoginReq, db: Db) -> Any:
    u = db.scalar(select(User).where(User.login_id == req.login_id))
    now = datetime.now(UTC)
    if u is not None:
        set_actor(db, u.user_id)
        if u.locked_until is not None and u.locked_until > now:
            raise ApiError(423, "AUTH_LOCKED", "Account locked, retry later")
        if u.locked_until is not None:  # lock expired: start counting afresh
            u.failed_login_count, u.locked_until = 0, None
    if u is None or not verify_password(u.password_hash, req.password) or not u.is_active:
        if u is not None:
            u.failed_login_count += 1
            if u.failed_login_count >= MAX_FAILURES:
                u.locked_until = now + LOCK_FOR
        db.commit()  # persist the failure counter before answering 401
        raise ApiError(401, "AUTH_FAILED", "Invalid login or password")
    if u.failed_login_count:
        u.failed_login_count = 0
    db.commit()
    return body(
        {
            "access_token": make_token(u.user_id, "access"),
            "refresh_token": make_token(u.user_id, "refresh"),
            "user": user_out(u),
        }
    )


@router.post("/api/auth/refresh")
def refresh(req: RefreshReq, db: Db) -> Any:
    u = user_from_token(db, req.refresh_token, "refresh")
    return body({"access_token": make_token(u.user_id, "access")})


@router.get("/api/auth/me")
def me(user: CurrentUser) -> Any:
    return body(user_out(user))


# --- users (ADMIN) ---
class UserCreate(_Strict):
    login_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9._@-]{1,64}$")]
    user_name: Name
    password: Password
    roles: Annotated[list[Role], Field(max_length=len(ROLES))] = []


class UserPatch(_Strict):
    roles: Annotated[list[Role], Field(max_length=len(ROLES))] | None = None
    is_active: bool | None = None
    unlock: bool | None = None


def _admin_out(u: User) -> dict[str, Any]:
    locked = u.locked_until is not None and u.locked_until > datetime.now(UTC)
    return {**user_out(u), **pick(u, "is_active", "created_at"), "locked": locked}


@router.get("/api/users")
def list_users(user: CurrentUser, db: Db) -> Any:
    need(user)
    users = db.scalars(select(User).order_by(User.user_id)).all()
    return body({"items": [_admin_out(u) for u in users], "total": len(users)})


@router.post("/api/users")
def create_user(req: UserCreate, user: CurrentUser, db: Db) -> Any:
    need(user)
    u = User(
        login_id=req.login_id,
        user_name=req.user_name,
        password_hash=hash_password(req.password),
        roles=[UserRole(role_code=r) for r in sorted(set(req.roles))],
    )
    db.add(u)
    db.commit()
    return body(_admin_out(u))


@router.patch("/api/users/{user_id}")
def patch_user(user_id: int, req: UserPatch, user: CurrentUser, db: Db) -> Any:
    need(user)
    u = db.get(User, user_id)
    if u is None:
        raise ApiError(404, "USER_NOT_FOUND", "User not found")
    if req.roles is not None:
        want = set(req.roles)
        u.roles = [r for r in u.roles if r.role_code in want] + [
            UserRole(role_code=c) for c in sorted(want - {r.role_code for r in u.roles})
        ]
    if req.is_active is not None:
        u.is_active = req.is_active
    if req.unlock:
        u.failed_login_count, u.locked_until = 0, None
    db.commit()
    return body(_admin_out(u))


# --- projects (FN-08) ---
class ProjectCreate(_Strict):
    project_code: Code
    project_name: Name
    customer_name: Annotated[str, Field(max_length=200)] | None = None


class ProjectPatch(_Strict):
    project_name: Name | None = None
    customer_name: Annotated[str, Field(max_length=200)] | None = None
    status: Literal["ACTIVE", "ARCHIVED"] | None = None


def _project_out(db: Db, p: Project) -> dict[str, Any]:
    n = db.scalar(select(func.count()).where(Document.project_id == p.project_id))
    cols = ("project_id", "project_code", "project_name", "customer_name", "status")
    return {**pick(p, *cols, "created_by", "created_at"), "document_count": n}


@router.get("/api/projects")
def list_projects(
    user: CurrentUser,
    db: Db,
    q: Annotated[str | None, Query(max_length=100)] = None,
    status: Literal["ACTIVE", "ARCHIVED"] | None = None,
) -> Any:
    stmt = select(Project).order_by(Project.project_id)
    if "ADMIN" not in user.role_codes:
        stmt = stmt.join(ProjectMember).where(ProjectMember.user_id == user.user_id)
    if q:
        stmt = stmt.where(
            Project.project_code.icontains(q, autoescape=True)
            | Project.project_name.icontains(q, autoescape=True)
        )
    if status:
        stmt = stmt.where(Project.status == status)
    items = [_project_out(db, p) for p in db.scalars(stmt)]
    return body({"items": items, "total": len(items)})


@router.post("/api/projects")
def create_project(req: ProjectCreate, user: CurrentUser, db: Db) -> Any:
    need(user, "DESIGNER")
    p = Project(**req.model_dump(), created_by=user.user_id)
    db.add(p)
    db.flush()
    db.add(ProjectMember(project_id=p.project_id, user_id=user.user_id))
    db.commit()
    return body(_project_out(db, p))


@router.get("/api/projects/{project_id}")
def read_project(project_id: int, user: CurrentUser, db: Db) -> Any:
    return body(_project_out(db, get_project(db, user, project_id)))


@router.patch("/api/projects/{project_id}")
def patch_project(project_id: int, req: ProjectPatch, user: CurrentUser, db: Db) -> Any:
    p = get_project(db, user, project_id)
    need(user, "DESIGNER")
    for k, v in req.model_dump(exclude_unset=True).items():
        if v is None and k != "customer_name":
            raise ApiError(400, "REQUEST_INVALID", f"{k} cannot be null")
        setattr(p, k, v)
    db.commit()
    return body(_project_out(db, p))


# --- members ---
class MemberReq(_Strict):
    user_id: int


def _members(db: Db, project_id: int) -> Any:
    users = db.scalars(
        select(User).join(ProjectMember).where(ProjectMember.project_id == project_id)
    ).all()
    return body({"items": [user_out(u) for u in users], "total": len(users)})


@router.get("/api/projects/{project_id}/members")
def list_members(project_id: int, user: CurrentUser, db: Db) -> Any:
    get_project(db, user, project_id)  # any visible member may list (approver picker)
    return _members(db, project_id)


@router.post("/api/projects/{project_id}/members")
def add_member(project_id: int, req: MemberReq, user: CurrentUser, db: Db) -> Any:
    get_project(db, user, project_id)
    need(user)
    if db.get(User, req.user_id) is None:
        raise ApiError(404, "USER_NOT_FOUND", "User not found")
    if db.get(ProjectMember, (project_id, req.user_id)) is None:
        db.add(ProjectMember(project_id=project_id, user_id=req.user_id))
        db.commit()
    return _members(db, project_id)


@router.delete("/api/projects/{project_id}/members/{user_id}")
def remove_member(project_id: int, user_id: int, user: CurrentUser, db: Db) -> Any:
    get_project(db, user, project_id)
    need(user)
    m = db.get(ProjectMember, (project_id, user_id))
    if m is not None:
        db.delete(m)
        db.commit()
    return _members(db, project_id)


# --- audit logs (FN-26) ---
@router.get("/api/audit-logs")
def audit_logs(
    user: CurrentUser,
    db: Db,
    object_type: Annotated[str | None, Query(max_length=64)] = None,
    object_id: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> Any:
    need(user, "REVIEWER")
    # ponytail: REVIEWER sees every project's log; scope by membership if reviewers get partitioned
    where = []
    if object_type:
        where.append(AuditLog.object_type == object_type)
    if object_id:
        where.append(AuditLog.object_id == object_id)
    rows = db.scalars(
        select(AuditLog).where(*where).order_by(AuditLog.audit_log_id.desc()).limit(limit)
    ).all()
    total = db.scalar(select(func.count()).select_from(AuditLog).where(*where))
    cols = ("audit_log_id", "object_type", "object_id", "action", "old_value", "new_value")
    return body({"items": [pick(r, *cols, "user_id", "created_at") for r in rows], "total": total})
