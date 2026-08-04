import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Text, Table
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from app.db.base import Base

# ── Role → permission mapping (composite PK) ────────────────────────────────
role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column(
        "role_id",
        UUID(as_uuid=True),
        ForeignKey("super_admin.roles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "permission_id",
        UUID(as_uuid=True),
        ForeignKey("super_admin.permissions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    schema="super_admin",
)


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = {"schema": "super_admin"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(50), unique=True, nullable=False)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    is_system = Column(Boolean, default=True, nullable=False)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Permission(Base):
    __tablename__ = "permissions"
    __table_args__ = {"schema": "super_admin"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(100), unique=True, nullable=False)
    module = Column(String(50), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text)


class SuperAdmin(Base):
    __tablename__ = "super_admins"
    __table_args__ = {"schema": "super_admin"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(150))
    role_id = Column(
        UUID(as_uuid=True),
        ForeignKey("super_admin.roles.id"),
        nullable=False,
        index=True,
    )
    status = Column(String(20), default="active", nullable=False)
    mfa_required = Column(Boolean, default=True, nullable=False)
    last_login_at = Column(DateTime(timezone=True))
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AdminMFA(Base):
    __tablename__ = "admin_mfa"
    __table_args__ = {"schema": "super_admin"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_id = Column(
        UUID(as_uuid=True),
        ForeignKey("super_admin.super_admins.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    totp_secret = Column(String(64), nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)
    verified_at = Column(DateTime(timezone=True))
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AdminSession(Base):
    __tablename__ = "admin_sessions"
    __table_args__ = {"schema": "super_admin"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_id = Column(
        UUID(as_uuid=True),
        ForeignKey("super_admin.super_admins.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    refresh_token_hash = Column(String(255), nullable=False)
    ip_address = Column(String(45))
    user_agent = Column(Text)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True))
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    __table_args__ = {"schema": "super_admin"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), nullable=False, index=True)
    admin_id = Column(
        UUID(as_uuid=True),
        ForeignKey("super_admin.super_admins.id", ondelete="SET NULL"),
    )
    ip_address = Column(String(45))
    success = Column(Boolean, default=False, nullable=False)
    attempted_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = {"schema": "super_admin"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_id = Column(
        UUID(as_uuid=True),
        ForeignKey("super_admin.super_admins.id", ondelete="SET NULL"),
    )
    actor_email = Column(String(255))
    action = Column(String(100), nullable=False)
    resource_type = Column(String(100), nullable=False)
    resource_id = Column(String(255))
    old_values = Column(postgresql.JSONB(astext_type=Text()))
    new_values = Column(postgresql.JSONB(astext_type=Text()))
    ip_address = Column(String(45))
    user_agent = Column(Text)
    previous_hash = Column(String(64))
    record_hash = Column(String(64), nullable=False)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
