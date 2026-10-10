"""Auth, users, projects/members and audit-log endpoints."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import BigInteger, ColumnElement, case, false, func, or_, select

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
from backend.db.models import (
    ROLES,
    AuditLog,
    BomHeader,
    Document,
    Project,
    ProjectMember,
    QuoteHeader,
    User,
    UserRole,
)

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
    # FOR UPDATE serialises concurrent attempts on one account: no lost counter increments
    u = db.scalar(select(User).where(User.login_id == req.login_id).with_for_update())
    now = datetime.now(UTC)
    ok = verify_password(u.password_hash if u else None, req.password)  # always pay argon2 cost
    if u is not None and u.locked_until is not None and u.locked_until <= now:
        u.failed_login_count, u.locked_until = 0, None  # lock expired: start counting afresh
    locked = u is not None and u.locked_until is not None
    if u is None or locked or not ok or not u.is_active:
        if u is not None and not locked:
            u.failed_login_count += 1
            if u.failed_login_count >= MAX_FAILURES:
                u.locked_until = now + LOCK_FOR
        db.commit()  # persist the counter; no actor set, so audit rows are not misattributed
        # one answer for unknown/wrong/locked/inactive: no account enumeration
        raise ApiError(401, "AUTH_FAILED", "Invalid login or password")
    set_actor(db, u.user_id)
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
    db.flush()
    active_admins = db.scalar(
        select(func.count())
        .select_from(User)
        .join(UserRole)
        .where(User.is_active, UserRole.role_code == "ADMIN")
    )
    if not active_admins:
        db.rollback()
        raise ApiError(409, "LAST_ADMIN", "At least one active ADMIN must remain")
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
            raise ApiError(422, "REQUEST_INVALID", f"{k} cannot be null")
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
ADMIN_ONLY_LOGS = ("users", "user_roles", "integration_jobs")  # accounts, ERP answers
GLOBAL_LOGS = (  # company-wide master data (prices, rates): readable like the master API itself
    "master_versions",
    "materials",
    "price_items",
    "process_rules",
    "cost_ratios",
    "mapping_rules",
)


def _log_project() -> ColumnElement[Any]:
    """The project an audited row belongs to, read from the row image the trigger stored."""
    row = func.coalesce(AuditLog.new_value, AuditLog.old_value)

    def key(k: str) -> ColumnElement[Any]:  # numbers only: a text column of the same name is no 500
        is_num = func.jsonb_typeof(row.op("->")(k)) == "number"
        return case((is_num, row.op("->>")(k).cast(BigInteger)))

    via_doc = select(Document.project_id).where(Document.document_id == key("document_id"))
    via_quote = select(QuoteHeader.project_id).where(QuoteHeader.quote_id == key("quote_id"))
    via_bom = (
        select(Document.project_id)
        .join(BomHeader, BomHeader.document_id == Document.document_id)
        .where(BomHeader.bom_id == key("bom_id"))
    )
    return func.coalesce(
        key("project_id"),
        via_doc.scalar_subquery(),
        via_quote.scalar_subquery(),
        via_bom.scalar_subquery(),
    )


@router.get("/api/audit-logs")
def audit_logs(
    user: CurrentUser,
    db: Db,
    object_type: Annotated[str | None, Query(max_length=64)] = None,
    object_id: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> Any:
    need(user, "REVIEWER")
    where: list[ColumnElement[bool]] = []
    if "ADMIN" not in user.role_codes:
        where.append(AuditLog.object_type.not_in(ADMIN_ONLY_LOGS))
        mine = select(ProjectMember.project_id).where(ProjectMember.user_id == user.user_id)
        masters = (
            AuditLog.object_type.in_(GLOBAL_LOGS) if "ESTIMATOR" in user.role_codes else false()
        )
        where.append(or_(masters, _log_project().in_(mine)))
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
