import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
import sqlalchemy as sa
from sqlalchemy import Column, Table
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
import os
import sys

# Add backend directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.db.base import Base
from app.models.organization import Organization
from app.models.config import OrgConfig
from app.models.session import ChatSession
from app.models.booking import TimeSlot, Booking
from app.models.service_unit import ServiceUnit, Provider, UnitProvider, ProviderFee
from app.models.otp import OTPRecord, AuthOTPRecord
from app.models.user import User
from app.models.message import ChatMessage
from app.models.audit_log import AuditLog
from app.models.email_delivery import EmailDeliveryLog
from app.models.system_alert import SystemAlert
from app.models.payment import PaymentAttempt, RazorpayWebhookEvent
from app.models.razorpay_route import RazorpayLinkedAccount, RazorpayTransfer
from app.models.whatsapp_connection import WhatsAppConnection
from app.models.booking_directive import BookingDirective
from app.models.broadcast import BroadcastDelivery, BroadcastRead
from app.models.impersonation import ImpersonationSession
from app.models.document_chunk import DocumentChunk
from app.models.escalation_policy import EscalationPolicy, EscalationRoutingTarget, EscalationRotationWindow
from app.models.login_code import LoginCode
from app.models.org_notification import OrgNotification
from app.models.rbac import Permission, Role, RolePermission, UserRole, DenyRule
from app.core.config import settings

target_metadata = Base.metadata
Table(
    "broadcast_notifications",
    Base.metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True),
    schema="super_admin",
    info={"skip_autogenerate": True},
)


def include_object(object, name, type_, reflected, compare_to):
    if type_ == "table" and object.info.get("skip_autogenerate"):
        return False
    return True


def compare_type(context, inspected_column, metadata_column, inspected_type, metadata_type):
    if (
        isinstance(metadata_type, sa.Enum)
        and metadata_type.native_enum is False
        and isinstance(inspected_type, (sa.String, sa.Text))
    ):
        return False
    return None

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = settings.SQLALCHEMY_DATABASE_URI
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        compare_type=compare_type,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        compare_type=compare_type,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = settings.SQLALCHEMY_DATABASE_URI
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
