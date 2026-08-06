"""Feature flag registry (Phase 7).

The main backend reads flags from super_admin.feature_flags on every request
(via is_feature_enabled / FlagGate) with a short in-memory cache so toggles
propagate quickly. Evaluation:

  - enabled=false            -> OFF everywhere (kill switch)
  - scope 'platform'         -> ON for everyone, subject to rollout_percent
  - scope 'org'              -> ON only when org_id in targeting.org_ids
  - scope 'plan'             -> ON only when the org's active plan code is in
                                targeting.plan_codes
  - rollout_percent < 100    -> canary: stable hash(code, org) < percent

Gated by FEATURE_FLAGS_ENABLED (default false) — while off, every call
returns the `default` value so the tenant product stays behavior-identical.
Failures log and return `default` (fail-open is deliberate: a flag registry
outage must never take the product down; kill switches still work because
they are just rows in the same registry).
"""
import hashlib
import logging
import time
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

logger = logging.getLogger(__name__)

_FLAG_CACHE: Optional[List[Dict[str, Any]]] = None
_FLAG_CACHE_AT = 0.0


def _cache_expired() -> bool:
    return time.monotonic() - _FLAG_CACHE_AT > settings.FLAGS_CACHE_TTL_SECONDS


async def get_flag_rows(db: AsyncSession) -> List[Dict[str, Any]]:
    """Cached snapshot of the flag registry (all columns needed to evaluate)."""
    global _FLAG_CACHE, _FLAG_CACHE_AT
    if _FLAG_CACHE is not None and not _cache_expired():
        return _FLAG_CACHE
    try:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT code, enabled, scope, targeting, rollout_percent
                    FROM super_admin.feature_flags
                    """
                )
            )
        ).mappings().all()
        _FLAG_CACHE = [dict(r) for r in rows]
        _FLAG_CACHE_AT = time.monotonic()
        return _FLAG_CACHE
    except Exception:
        logger.exception("Failed to read feature flag registry")
        return []


async def _org_plan_code(db: AsyncSession, org_id: UUID) -> Optional[str]:
    row = (
        await db.execute(
            text(
                """
                SELECT p.code
                FROM super_admin.plan_assignments pa
                JOIN super_admin.plans p ON p.id = pa.plan_id
                WHERE pa.org_id = :org_id AND pa.is_active = TRUE
                ORDER BY pa.created_at DESC
                LIMIT 1
                """
            ),
            {"org_id": org_id},
        )
    ).first()
    return row[0] if row else None


def _rollout_hit(code: str, org_id: Optional[UUID], percent: int) -> bool:
    if percent >= 100:
        return True
    if percent <= 0:
        return False
    bucket = str(org_id) if org_id else "platform"
    digest = hashlib.sha256(f"{code}|{bucket}".encode()).hexdigest()
    return int(digest[:8], 16) % 100 < percent


async def is_feature_enabled(
    db: AsyncSession,
    code: str,
    *,
    org_id: Optional[UUID] = None,
    default: bool = True,
) -> bool:
    """Evaluate one flag for an optional org context.

    FEATURE_FLAGS_ENABLED=false (default) short-circuits to `default` so the
    product runs unchanged until the GA cutover.
    """
    if not settings.FEATURE_FLAGS_ENABLED:
        return default
    for flag in await get_flag_rows(db):
        if flag["code"] != code:
            continue
        if not flag["enabled"]:
            return False
        targeting = flag["targeting"] or {}
        if flag["scope"] == "org":
            org_ids = targeting.get("org_ids") or []
            if org_id is None or str(org_id) not in org_ids:
                return False
        elif flag["scope"] == "plan":
            if org_id is None:
                return False
            plan_code = await _org_plan_code(db, org_id)
            if not plan_code or plan_code not in (targeting.get("plan_codes") or []):
                return False
        return _rollout_hit(
            code,
            org_id,
            flag["rollout_percent"] if flag["rollout_percent"] is not None else 100,
        )
    return default


def require_feature(flag_code: str, default: bool = True):
    """FastAPI dependency: 503 unless the flag evaluates enabled.

    Used to gate future additive features; 503 (not 403) so clients see a
    deliberate "feature is off" state, mirroring the bridge's 503 convention.
    """
    from fastapi import Depends, HTTPException

    from app.db.database import get_db

    async def dependency(db: AsyncSession = Depends(get_db)) -> None:
        if not await is_feature_enabled(db, flag_code, default=default):
            raise HTTPException(status_code=503, detail=f"Feature disabled: {flag_code}")

    return dependency
