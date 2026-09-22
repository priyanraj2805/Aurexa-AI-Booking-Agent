"""RBAC tables: permissions (global codes), roles (per-org), grants."""

import uuid

import sqlalchemy as sa
from sqlalchemy import Column, ForeignKey, String, Boolean, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.base import Base


class Permission(Base):
    __tablename__ = "permissions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(80), nullable=False, unique=True)
    description = Column(String(255), nullable=True)


class Role(Base):
    __tablename__ = "roles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    code = Column(String(80), nullable=False)
    name = Column(String(120), nullable=False)
    is_system = Column(Boolean, nullable=False, default=True, server_default=sa.true())
    # GCP parity: folder-level bindings inherited by child projects.
    inherit_to_children = Column(
        Boolean, nullable=False, default=False, server_default=sa.false()
    )
    # GCP parity: etag-style version for read-modify-write policy updates.
    version = Column(sa.Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        sa.UniqueConstraint("org_id", "code", name="uq_roles_org_code"),
    )


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id = Column(
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    permission_id = Column(
        UUID(as_uuid=True),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    )


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role_id = Column(
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    org_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    granted_by = Column(UUID(as_uuid=True), nullable=True)
    granted_at = Column(DateTime(timezone=True), server_default=func.now())
    # GCP parity: time condition on the binding (request.time < expires_at).
    expires_at = Column(DateTime(timezone=True), nullable=True)


class DenyRule(Base):
    """Deny policy rule: evaluated BEFORE allow, inherited downward."""

    __tablename__ = "deny_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    permission_code = Column(String(80), nullable=False)
    reason = Column(String(255), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default=sa.true())
    created_at = Column(DateTime(timezone=True), server_default=func.now())
