"""ORM mirror of migrations/versions/0001_initial.py (the migration is the schema source of truth)."""

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

ROLES = ("ADMIN", "DESIGNER", "ESTIMATOR", "REVIEWER", "MANUFACTURING", "VIEWER")


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
    }


def _pk() -> Mapped[int]:
    return mapped_column(BigInteger, primary_key=True)


def _fk(target: str) -> Mapped[int]:
    return mapped_column(BigInteger, ForeignKey(target))


def _now() -> Mapped[datetime]:
    return mapped_column(server_default=func.now())


class User(Base):
    __tablename__ = "users"
    user_id: Mapped[int] = _pk()
    login_id: Mapped[str] = mapped_column(Text, unique=True)
    user_name: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=True)
    failed_login_count: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[datetime | None]
    created_at: Mapped[datetime] = _now()
    roles: Mapped[list["UserRole"]] = relationship(cascade="all, delete-orphan", lazy="selectin")

    @property
    def role_codes(self) -> list[str]:
        return sorted(r.role_code for r in self.roles)


class Role(Base):
    __tablename__ = "roles"
    role_code: Mapped[str] = mapped_column(Text, primary_key=True)


class UserRole(Base):
    __tablename__ = "user_roles"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"), primary_key=True)
    role_code: Mapped[str] = mapped_column(ForeignKey("roles.role_code"), primary_key=True)


class Project(Base):
    __tablename__ = "projects"
    project_id: Mapped[int] = _pk()
    project_code: Mapped[str] = mapped_column(Text, unique=True)
    project_name: Mapped[str] = mapped_column(Text)
    customer_name: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="ACTIVE")
    created_by: Mapped[int] = _fk("users.user_id")
    created_at: Mapped[datetime] = _now()


class ProjectMember(Base):
    __tablename__ = "project_members"
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.project_id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"), primary_key=True)


class Document(Base):
    __tablename__ = "documents"
    document_id: Mapped[int] = _pk()
    project_id: Mapped[int] = _fk("projects.project_id")
    doc_no: Mapped[str] = mapped_column(Text)
    doc_type: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="DRAFT")
    current_revision_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("document_revisions.revision_id", use_alter=True)
    )
    created_by: Mapped[int] = _fk("users.user_id")
    created_at: Mapped[datetime] = _now()


class Revision(Base):
    __tablename__ = "document_revisions"
    revision_id: Mapped[str] = mapped_column(Text, primary_key=True)
    document_id: Mapped[int] = _fk("documents.document_id")
    revision_no: Mapped[str] = mapped_column(Text)
    parent_revision_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("document_revisions.revision_id")
    )
    file_format: Mapped[str] = mapped_column(Text, default="DXF")
    checksum: Mapped[str] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = _fk("users.user_id")
    created_at: Mapped[datetime] = _now()


class Approval(Base):
    __tablename__ = "approvals"
    approval_id: Mapped[int] = _pk()
    document_id: Mapped[int] = _fk("documents.document_id")
    revision_id: Mapped[str] = mapped_column(Text, ForeignKey("document_revisions.revision_id"))
    requested_by: Mapped[int] = _fk("users.user_id")
    approver_id: Mapped[int] = _fk("users.user_id")
    status: Mapped[str] = mapped_column(Text, default="PENDING")
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    decided_at: Mapped[datetime | None]


class AuditLog(Base):
    __tablename__ = "audit_logs"
    audit_log_id: Mapped[int] = _pk()
    object_type: Mapped[str] = mapped_column(Text)
    object_id: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    old_value: Mapped[dict[str, Any] | None]
    new_value: Mapped[dict[str, Any] | None]
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = _now()
