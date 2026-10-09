"""Password hashing, JWT, current-user dependency and project/document access helpers."""

import os
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.api.common import ApiError
from backend.db.models import Document, Project, ProjectMember, Revision, User
from backend.db.session import get_db, load_env

ACCESS_TTL = timedelta(minutes=30)
REFRESH_TTL = timedelta(hours=8)
_hasher = PasswordHasher()
_DUMMY_HASH = _hasher.hash(
    "timing-equalizer"
)  # unknown login burns the same time as a bad password
_bearer = HTTPBearer(auto_error=False)

Db = Annotated[Session, Depends(get_db)]


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def _secret() -> str:
    load_env()
    return os.environ["JWT_SECRET"]


def make_token(user_id: int, typ: str) -> str:
    ttl = ACCESS_TTL if typ == "access" else REFRESH_TTL
    claims = {"sub": str(user_id), "typ": typ, "exp": datetime.now(UTC) + ttl}
    return jwt.encode(claims, _secret(), algorithm="HS256")


def user_from_token(db: Session, token: str, typ: str) -> User:
    try:
        claims = jwt.decode(token, _secret(), algorithms=["HS256"], options={"require": ["exp"]})
        user = db.get(User, int(claims["sub"])) if claims.get("typ") == typ else None
    except (jwt.PyJWTError, KeyError, ValueError):
        user = None
    if user is None or not user.is_active:
        raise ApiError(401, "AUTH_INVALID", "Invalid or expired token")
    return user


def set_actor(db: Session, user_id: int) -> None:
    """Tell the audit trigger who acts; transaction-local, so call before the first write."""
    db.execute(select(func.set_config("app.user_id", str(user_id), True)))


def current_user(
    db: Db, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
) -> User:
    if creds is None:
        raise ApiError(401, "AUTH_REQUIRED", "Authentication required")
    user = user_from_token(db, creds.credentials, "access")
    set_actor(db, user.user_id)
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def need(user: User, *roles: str) -> None:
    """403 unless the user holds one of the roles; ADMIN is always allowed."""
    if not {"ADMIN", *roles} & set(user.role_codes):
        raise ApiError(403, "FORBIDDEN", "Insufficient role")


def is_admin(user: User) -> bool:
    return "ADMIN" in user.role_codes


def is_member(db: Session, user: User, project_id: int) -> bool:
    return db.get(ProjectMember, (project_id, user.user_id)) is not None


def get_project(db: Session, user: User, project_id: int) -> Project:
    """404 (not 403) when invisible, so project existence does not leak."""
    p = db.get(Project, project_id)
    if p is None or not (is_admin(user) or is_member(db, user, project_id)):
        raise ApiError(404, "PROJECT_NOT_FOUND", "Project not found")
    return p


def get_document(db: Session, user: User, document_id: int, lock: bool = False) -> Document:
    q = select(Document).where(Document.document_id == document_id)
    d = db.scalar(q.with_for_update() if lock else q)  # lock serializes revision numbering
    if d is None or not (is_admin(user) or is_member(db, user, d.project_id)):
        raise ApiError(404, "DOCUMENT_NOT_FOUND", "Document not found")
    return d


def get_revision(
    db: Session, user: User, revision_id: str, lock: bool = False
) -> tuple[Revision, Document]:
    r = db.get(Revision, revision_id)
    if r is None:
        raise ApiError(404, "REVISION_NOT_FOUND", "Revision not found")
    try:
        return r, get_document(db, user, r.document_id, lock)
    except ApiError:
        raise ApiError(404, "REVISION_NOT_FOUND", "Revision not found") from None
