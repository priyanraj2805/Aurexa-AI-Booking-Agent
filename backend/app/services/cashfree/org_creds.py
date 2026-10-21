"""Per-org Cashfree credential resolution.

Priority: org's OWN encrypted keys (money settles straight into the org's
bank) → platform settings fallback (existing .env behavior).
"""

import logging
from typing import Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.config import OrgConfig
from app.core.config import settings

logger = logging.getLogger(__name__)

SANDBOX_BASE = "https://sandbox.cashfree.com/pg"
PROD_BASE = "https://api.cashfree.com/pg"


async def get_org_cashfree_creds(
    db: AsyncSession, org_id, *, include_secret: bool = False
) -> Tuple[bool, str, str, str]:
    """Return (connected, app_id_or_masked, secret_or_empty, mode).

    Never returns a usable secret unless include_secret=True (server-side
    use only). Fail-closed: any error means not connected.
    """
    try:
        res = await db.execute(select(OrgConfig).where(OrgConfig.org_id == org_id))
        cfg = res.scalars().first()
        if not cfg or getattr(cfg, "payment_provider", "razorpay") != "cashfree":
            return False, "", "", "test"
        enc_id = getattr(cfg, "cashfree_app_id_enc", None)
        enc_secret = getattr(cfg, "cashfree_secret_enc", None)
        # Platform mode is authoritative (server CASHFREE_MODE). The dashboard
        # no longer offers test/live, so stored per-org mode is ignored —
        # flipping the env flips every tenant at once, no re-save needed.
        mode = (settings.CASHFREE_MODE or "test").strip().lower()
        if mode not in ("test", "live"):
            mode = "test"
        if not enc_id or not enc_secret:
            return False, "", "", mode
        from app.core import credential_crypto as _crypto

        app_id = _crypto.decrypt_secret(enc_id)
        if include_secret:
            secret = _crypto.decrypt_secret(enc_secret)
            return True, app_id, secret, mode
        return True, _crypto.mask_secret(app_id), "", mode
    except Exception:
        logger.warning("org cashfree creds unreadable org=%s", org_id)
        return False, "", "", "test"


async def resolve_cashfree_client(db: AsyncSession, org_id):
    """CashfreeClient for this org: own keys first, platform fallback."""
    from app.services.cashfree.client import CashfreeClient, require_cashfree_enabled

    require_cashfree_enabled()
    connected, app_id, secret, mode = await get_org_cashfree_creds(
        db, org_id, include_secret=True
    )
    if connected and app_id and secret:
        base = PROD_BASE if mode == "live" else SANDBOX_BASE
        return CashfreeClient(
            app_id=app_id,
            secret_key=secret,
            api_base=base,
            api_version=settings.CASHFREE_API_VERSION,
        ), True
    return CashfreeClient.from_settings(), False


async def resolve_cashfree_secret(db: AsyncSession, org_id) -> Optional[str]:
    """Webhook signing secret for this org (own keys first, else platform)."""
    connected, _, secret, _ = await get_org_cashfree_creds(
        db, org_id, include_secret=True
    )
    if connected and secret:
        return secret
    return settings.CASHFREE_SECRET_KEY or ""


async def is_cashfree_usable(db: AsyncSession, org_id) -> bool:
    """True when booking payments can actually be collected for this org.

    Requires the global kill switch to be on AND (org's own keys OR the
    platform Cashfree account as fallback). Used by the booking payment gate
    so a Cashfree tenant is never blocked by Razorpay Route onboarding.
    """
    try:
        from app.services.cashfree.client import is_cashfree_enabled

        if not is_cashfree_enabled():
            return False
    except Exception:
        return False
    connected, _, _, _ = await get_org_cashfree_creds(db, org_id)
    if connected:
        return True
    return bool(
        (settings.CASHFREE_APP_ID or "").strip() and (settings.CASHFREE_SECRET_KEY or "").strip()
    )
