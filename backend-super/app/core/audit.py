"""
Tamper-proof audit trail for Super Admin actions.

Each record carries:
  - previous_hash: record_hash of the previous row (GENESIS for the first)
  - record_hash:   SHA256(previous_hash | canonical row content)

Editing any historical row breaks every hash after it. The chain can be
recomputed and compared via verify_chain().
"""
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

GENESIS_HASH = "0" * 64

# Columns that participate in the hash (everything except id / hash fields)
CANONICAL_COLUMNS = [
    "actor_id",
    "actor_email",
    "action",
    "resource_type",
    "resource_id",
    "old_values",
    "new_values",
    "ip_address",
    "user_agent",
    "created_at",
]


def _canonical(row: Dict[str, Any]) -> str:
    """Deterministic serialization of the hashable row content."""
    parts = []
    for col in CANONICAL_COLUMNS:
        value = row.get(col)
        if isinstance(value, UUID):
            value = str(value)
        if value is None:
            value = ""
        parts.append(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str))
    return "|".join(parts)


def compute_record_hash(previous_hash: str, row: Dict[str, Any]) -> str:
    return hashlib.sha256(
        f"{previous_hash}|{_canonical(row)}".encode("utf-8")
    ).hexdigest()


async def write_audit_log(
    *,
    actor_id: Optional[UUID] = None,
    actor_email: Optional[str] = None,
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    old_values: Optional[Dict[str, Any]] = None,
    new_values: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """Append one audit record in an isolated session (never fails the request)."""
    try:
        async with AsyncSessionLocal() as db:
            prev = await db.execute(
                select(text("record_hash"))
                .select_from(text("super_admin.audit_logs"))
                .order_by(text("created_at DESC, id DESC"))
                .limit(1)
            )
            prev_row = prev.first()
            previous_hash = prev_row[0] if prev_row else GENESIS_HASH

            # created_at is fixed BEFORE hashing so the chain can be recomputed
            created_at = datetime.now(timezone.utc)

            row = {
                "actor_id": actor_id,
                "actor_email": actor_email,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "old_values": old_values,
                "new_values": new_values,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "created_at": created_at,
            }
            record_hash = compute_record_hash(previous_hash, row)

            await db.execute(
                text(
                    """
                    INSERT INTO super_admin.audit_logs
                        (actor_id, actor_email, action, resource_type, resource_id,
                         old_values, new_values, ip_address, user_agent,
                         previous_hash, record_hash, created_at)
                    VALUES
                        (:actor_id, :actor_email, :action, :resource_type, :resource_id,
                         :old_values, :new_values, :ip_address, :user_agent,
                         :previous_hash, :record_hash, :created_at)
                    """
                ),
                {
                    "actor_id": actor_id,
                    "actor_email": actor_email,
                    "action": action,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "old_values": json.dumps(old_values) if old_values is not None else None,
                    "new_values": json.dumps(new_values) if new_values is not None else None,
                    "ip_address": ip_address,
                    "user_agent": user_agent,
                    "previous_hash": previous_hash,
                    "record_hash": record_hash,
                    "created_at": created_at,
                },
            )
            await db.commit()
    except Exception:
        logger.exception("Failed to write super admin audit log")


async def verify_chain(db: AsyncSession) -> Dict[str, Any]:
    """Recompute every record hash and compare with the stored value."""
    rows = (
        await db.execute(
            text(
                """
                SELECT id, previous_hash, record_hash, actor_id, actor_email, action,
                       resource_type, resource_id, old_values, new_values,
                       ip_address, user_agent, created_at
                FROM super_admin.audit_logs
                ORDER BY created_at ASC, id ASC
                """
            )
        )
    ).mappings().all()

    prev = GENESIS_HASH
    broken_at = None
    for idx, r in enumerate(rows):
        expected = compute_record_hash(prev, dict(r))
        if expected != r["record_hash"]:
            broken_at = idx
            break
        if r["previous_hash"] != prev:
            broken_at = idx
            break
        prev = r["record_hash"]

    return {
        "total_records": len(rows),
        "chain_valid": broken_at is None,
        "first_broken_index": broken_at,
    }
