"""
Tenant payment provider configuration (admin dashboard).

Lets an org choose its collection rail and connect its OWN Cashfree
account so money settles straight into the org's bank:
- GET    /dashboard/payments/config           — status (secrets never returned)
- PUT    /dashboard/payments/config           — set provider + keys + mode
- POST   /dashboard/payments/cashfree/test    — verify keys against Cashfree
- DELETE /dashboard/payments/cashfree         — disconnect (clears keys)

Secrets are Fernet-encrypted at rest (PAYMENT_CREDENTIALS_KEY) and never
leave the server except masked (last 4 chars) for display.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from pydantic import BaseModel, field_validator

from app.db.database import get_db
from app.models.config import OrgConfig
from app.models.user import User
from app.api.v1.endpoints.auth import deny_impersonation, get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)

PROVIDERS = ("razorpay", "cashfree")
MODES = ("test", "live")


class PaymentsConfigUpdate(BaseModel):
    provider: str = "razorpay"
    cashfree_app_id: Optional[str] = None
    cashfree_secret_key: Optional[str] = None
    cashfree_mode: str = "test"

    @field_validator("provider")
    @classmethod
    def _provider_ok(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in PROVIDERS:
            raise ValueError(f"provider must be one of {PROVIDERS}")
        return v

    @field_validator("cashfree_mode")
    @classmethod
    def _mode_ok(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in MODES:
            raise ValueError(f"cashfree_mode must be one of {MODES}")
        return v


async def _org_config(db: AsyncSession, org_id) -> OrgConfig:
    res = await db.execute(select(OrgConfig).where(OrgConfig.org_id == org_id))
    cfg = res.scalars().first()
    if not cfg:
        raise HTTPException(status_code=404, detail="Org config not found")
    return cfg


def _status_payload(cfg: OrgConfig) -> dict:
    from app.core import credential_crypto as _crypto
    from app.core.config import settings as _settings

    provider = (getattr(cfg, "payment_provider", "razorpay") or "razorpay").lower()
    # Effective mode is platform-controlled, not per-org.
    mode = (_settings.CASHFREE_MODE or "test").strip().lower()
    if mode not in ("test", "live"):
        mode = "test"
    connected = False
    masked = ""
    if provider == "cashfree":
        try:
            app_id = _crypto.decrypt_secret(getattr(cfg, "cashfree_app_id_enc", "") or "")
            _crypto.decrypt_secret(getattr(cfg, "cashfree_secret_enc", "") or "")
            connected = bool(app_id)
            masked = _crypto.mask_secret(app_id)
        except Exception:
            connected = False
    return {
        "provider": provider,
        "cashfree": {
            "connected": connected,
            "mode": mode,
            "app_id_masked": masked,
        },
        "webhook_url": _platform_webhook_url(),
    }


def _platform_webhook_url() -> str:
    """Single platform webhook URL shown to every tenant for their
    Cashfree dashboard (optional — per-order notify_url is sent anyway)."""
    from app.core.config import settings as _settings

    base = (_settings.PUBLIC_API_BASE or "").rstrip("/")
    if base.endswith("/api/v1"):
        base = base[: -len("/api/v1")]
    if not base:
        return ""
    return base + "/api/v1/widget/payment/cashfree/webhook"


@router.get(
    "/dashboard/payments/config",
    dependencies=[Depends(deny_impersonation)],
)
async def get_payments_config(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cfg = await _org_config(db, user.org_id)
    return _status_payload(cfg)


@router.put(
    "/dashboard/payments/config",
    dependencies=[Depends(deny_impersonation)],
)
async def put_payments_config(
    payload: PaymentsConfigUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.core import credential_crypto as _crypto

    cfg = await _org_config(db, user.org_id)
    if payload.provider == "cashfree":
        app_id = (payload.cashfree_app_id or "").strip()
        secret = (payload.cashfree_secret_key or "").strip()
        if not app_id or not secret:
            # Allow provider-only updates when keys already stored.
            if not getattr(cfg, "cashfree_app_id_enc", None) or not getattr(
                cfg, "cashfree_secret_enc", None
            ):
                raise HTTPException(
                    status_code=422,
                    detail="cashfree_app_id and cashfree_secret_key are required",
                )
        else:
            try:
                cfg.cashfree_app_id_enc = _crypto.encrypt_secret(app_id)
                cfg.cashfree_secret_enc = _crypto.encrypt_secret(secret)
            except ValueError as exc:
                logger.warning("payments config encrypt failed org=%s", user.org_id)
                raise HTTPException(status_code=500, detail=str(exc))
        # Environment is platform-controlled (CASHFREE_MODE), never tenant
        # input — the dashboard no longer offers a test/live toggle.
        from app.core.config import settings as _settings

        _platform_mode = (_settings.CASHFREE_MODE or "test").strip().lower()
        cfg.cashfree_mode = _platform_mode if _platform_mode in ("test", "live") else "test"
    cfg.payment_provider = payload.provider
    await db.commit()
    await db.refresh(cfg)
    return _status_payload(cfg)


@router.post(
    "/dashboard/payments/cashfree/test",
    dependencies=[Depends(deny_impersonation)],
)
async def test_cashfree_connection(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Verify stored keys by hitting Cashfree (bogus order → 404 means auth OK)."""
    from app.services.cashfree.org_creds import resolve_cashfree_client
    from app.services.cashfree.client import CashfreeAPIError

    try:
        client, owned = await resolve_cashfree_client(db, user.org_id)
    except CashfreeAPIError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not owned:
        raise HTTPException(status_code=409, detail="No Cashfree keys connected for this organization")
    try:
        await client.fetch_order("connectivity_probe_nonexistent_order")
    except CashfreeAPIError as exc:
        # 404 on a bogus order proves the credentials authenticate.
        if exc.status_code == 404:
            return {"ok": True, "message": "Cashfree keys verified successfully"}
        logger.warning("cashfree test failed org=%s status=%s", user.org_id, exc.status_code)
        raise HTTPException(status_code=502, detail="Cashfree rejected the request (check keys/mode)")
    # A 200 on a bogus id would be bizarre — treat as inconclusive, not failure.
    return {"ok": True, "message": "Cashfree reachable (unexpected order found)"}


@router.delete(
    "/dashboard/payments/cashfree",
    dependencies=[Depends(deny_impersonation)],
)
async def disconnect_cashfree(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cfg = await _org_config(db, user.org_id)
    cfg.cashfree_app_id_enc = None
    cfg.cashfree_secret_enc = None
    if (getattr(cfg, "payment_provider", "") or "") == "cashfree":
        cfg.payment_provider = "razorpay"
    await db.commit()
    await db.refresh(cfg)
    return _status_payload(cfg)


@router.get(
    "/dashboard/payments/attempts",
    dependencies=[Depends(deny_impersonation)],
)
async def list_payment_attempts(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = 50,
    offset: int = 0,
):
    """Org's payment attempts, newest first (admin tracking). No secrets."""
    from sqlalchemy import func as _func
    from app.models.payment import PaymentAttempt

    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    total = (
        await db.execute(
            select(_func.count()).select_from(PaymentAttempt).where(
                PaymentAttempt.org_id == user.org_id
            )
        )
    ).scalar() or 0
    res = await db.execute(
        select(PaymentAttempt)
        .where(PaymentAttempt.org_id == user.org_id)
        .order_by(PaymentAttempt.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = res.scalars().all()
    return {
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "attempts": [
            {
                "id": str(r.id),
                "provider": getattr(r, "provider", "razorpay") or "razorpay",
                "order_id": getattr(r, "provider_order_id", None)
                or getattr(r, "razorpay_order_id", None),
                "payment_id": getattr(r, "provider_payment_id", None)
                or getattr(r, "razorpay_payment_id", None),
                "amount_paise": r.amount_paise,
                "currency": r.currency,
                "status": r.status,
                "verification_status": r.verification_status,
                "session_id": str(r.session_id) if r.session_id else None,
                "booking_id": str(r.booking_id) if r.booking_id else None,
                "expires_at": r.expires_at.isoformat() if r.expires_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }
