"""ORM mirror of migrations/versions/ (the migrations are the schema source of truth)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, Text, func
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


class Feature(Base):
    __tablename__ = "features"
    feature_id: Mapped[int] = _pk()
    document_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("documents.document_id", ondelete="CASCADE")
    )
    seq: Mapped[int]
    feature_type: Mapped[str] = mapped_column(Text)
    params: Mapped[dict[str, Any]]
    status: Mapped[str] = mapped_column(Text, default="OK")
    error_code: Mapped[str | None] = mapped_column(Text)
    brep_key: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[dict[str, Any] | None]
    created_by: Mapped[int] = _fk("users.user_id")
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


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


# --- master data (FN-16, migration 0005): versioned, frozen once ACTIVE ---


class MasterVersion(Base):
    __tablename__ = "master_versions"
    version_id: Mapped[int] = _pk()
    version_code: Mapped[str] = mapped_column(Text, unique=True)
    effective_from: Mapped[date]
    status: Mapped[str] = mapped_column(Text, default="DRAFT")
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = _fk("users.user_id")
    created_at: Mapped[datetime] = _now()
    activated_at: Mapped[datetime | None]


def _version_fk() -> Mapped[int]:
    return mapped_column(BigInteger, ForeignKey("master_versions.version_id", ondelete="CASCADE"))


class Material(Base):
    __tablename__ = "materials"
    material_id: Mapped[int] = _pk()
    version_id: Mapped[int] = _version_fk()
    material_code: Mapped[str] = mapped_column(Text)
    material_name: Mapped[str | None] = mapped_column(Text)
    thickness_min_mm: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    thickness_max_mm: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    density_g_cm3: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    unit_price_per_kg: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    scrap_rate: Mapped[Decimal | None] = mapped_column(Numeric(7, 6))


class PriceItem(Base):
    __tablename__ = "price_items"
    price_item_id: Mapped[int] = _pk()
    version_id: Mapped[int] = _version_fk()
    item_code: Mapped[str] = mapped_column(Text)
    item_type: Mapped[str] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(Text)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))


class ProcessRule(Base):
    __tablename__ = "process_rules"
    process_rule_id: Mapped[int] = _pk()
    version_id: Mapped[int] = _version_fk()
    rule_code: Mapped[str] = mapped_column(Text)
    process_code: Mapped[str] = mapped_column(Text)
    process_name: Mapped[str | None] = mapped_column(Text)
    input_metric: Mapped[str] = mapped_column(Text)
    formula_text: Mapped[str] = mapped_column(Text)
    params: Mapped[dict[str, Any]] = mapped_column(default=dict)
    labor_item_code: Mapped[str | None] = mapped_column(Text)
    machine_item_code: Mapped[str | None] = mapped_column(Text)


class CostRatios(Base):
    __tablename__ = "cost_ratios"
    version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("master_versions.version_id", ondelete="CASCADE"), primary_key=True
    )
    overhead_basis: Mapped[str] = mapped_column(Text)
    overhead_rate: Mapped[Decimal | None] = mapped_column(Numeric(7, 6))
    admin_rate: Mapped[Decimal | None] = mapped_column(Numeric(7, 6))
    profit_rate: Mapped[Decimal | None] = mapped_column(Numeric(7, 6))
    vat_rate: Mapped[Decimal] = mapped_column(Numeric(7, 6), default=Decimal("0.1"))
    rounding_rule: Mapped[str] = mapped_column(Text)
    rounding_unit: Mapped[int]
    rounding_scope: Mapped[str] = mapped_column(Text)
    material_basis: Mapped[str] = mapped_column(Text, default="NET")


class MappingRule(Base):
    __tablename__ = "mapping_rules"
    mapping_rule_id: Mapped[int] = _pk()
    version_id: Mapped[int] = _version_fk()
    rule_type: Mapped[str] = mapped_column(Text)
    target: Mapped[str] = mapped_column(Text)
    pattern: Mapped[str] = mapped_column(Text)


# --- quotes (FN-17/18, migration 0006) ---


class QuoteHeader(Base):
    __tablename__ = "quote_headers"
    quote_id: Mapped[int] = _pk()
    quote_no: Mapped[str] = mapped_column(Text, unique=True)
    project_id: Mapped[int] = _fk("projects.project_id")
    source_kind: Mapped[str] = mapped_column(Text)
    revision_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("document_revisions.revision_id")
    )
    document_id: Mapped[int] = _fk("documents.document_id")
    master_version_id: Mapped[int] = _fk("master_versions.version_id")
    inputs: Mapped[dict[str, Any]]
    metrics: Mapped[dict[str, Any]]
    status: Mapped[str] = mapped_column(Text, default="DRAFT")
    has_errors: Mapped[bool]
    material_cost: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    labor_cost: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    overhead_cost: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    outsource_cost: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    manufacturing_cost: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    admin_cost: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    total_cost: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    profit: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    supply_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    vat_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    created_by: Mapped[int] = _fk("users.user_id")
    created_at: Mapped[datetime] = _now()


class QuoteLine(Base):
    __tablename__ = "quote_lines"
    quote_line_id: Mapped[int] = _pk()
    quote_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quote_headers.quote_id", ondelete="CASCADE")
    )
    line_no: Mapped[int]
    cost_category: Mapped[str] = mapped_column(Text)
    item_code: Mapped[str] = mapped_column(Text)
    item_name: Mapped[str] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(Text)
    calculated_qty: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    calculated_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    calculated_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    excluded: Mapped[bool]
    override_qty: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    override_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    override_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    override_reason: Mapped[str | None] = mapped_column(Text)
    overridden_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.user_id"))
    overridden_at: Mapped[datetime | None]


class QuoteTrace(Base):
    __tablename__ = "quote_traces"
    quote_trace_id: Mapped[int] = _pk()
    quote_line_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quote_lines.quote_line_id", ondelete="CASCADE")
    )
    source_kind: Mapped[str] = mapped_column(Text)
    sources: Mapped[list[str]] = mapped_column(JSONB)  # capped list of handles / feature ids
    source_count: Mapped[int]  # full number, the list may be shorter
    revision_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("document_revisions.revision_id")
    )
    rule_code: Mapped[str] = mapped_column(Text)
    price_item_code: Mapped[str | None] = mapped_column(Text)
    unit_price: Mapped[str | None] = mapped_column(Text)
    inputs: Mapped[dict[str, Any]]
    formula_text: Mapped[str] = mapped_column(Text)


class QuoteLog(Base):
    __tablename__ = "quote_validation_logs"
    log_id: Mapped[int] = _pk()
    quote_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quote_headers.quote_id", ondelete="CASCADE")
    )
    severity: Mapped[str] = mapped_column(Text)
    code: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)


class QuoteApproval(Base):
    __tablename__ = "quote_approvals"
    approval_id: Mapped[int] = _pk()
    quote_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quote_headers.quote_id", ondelete="CASCADE")
    )
    requested_by: Mapped[int] = _fk("users.user_id")
    approver_id: Mapped[int] = _fk("users.user_id")
    status: Mapped[str] = mapped_column(Text, default="PENDING")
    comment: Mapped[str | None] = mapped_column(Text)
    decision_comment: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.user_id"))
    created_at: Mapped[datetime] = _now()
    decided_at: Mapped[datetime | None]
