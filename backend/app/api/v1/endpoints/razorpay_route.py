"""Razorpay Route provider onboarding (the payout path).

All routes here:

- require ``RAZORPAY_ROUTE_ENABLED`` (else 404, kill switch),
- require an authenticated org user (``get_current_user``),
- scope every read/write to ``user.org_id`` (provider ownership),
- call Razorpay ONLY through ``app.services.razorpay`` (platform creds),
- never accept raw Razorpay secrets / tokens from the frontend,
- never log or persist raw bank numbers, IFSCs, or PANs
  (only ``bank_last4`` + ``beneficiary_name`` are stored).

Money-affecting writes (stakeholder identity, TnC acceptance, settlement
details, payment-mode choice) additionally require ``deny_impersonation``.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api.v1.endpoints.auth import deny_impersonation, get_current_user
from app.db.database import get_db
from app.models.organization import Organization
from app.models.razorpay_route import (
    PaymentRouteMode,
    RazorpayLinkedAccount,
    RouteOnboardingStatus,
)
from app.models.user import User
from app.services.razorpay import route as route_svc
from app.services.razorpay.client import RouteAPIError, RouteClient, is_route_enabled
from app.services.razorpay.onboarding import (
    build_reference_id,
    derive_onboarding_status,
    frontend_message,
    is_transfer_ready,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_route_enabled() -> None:
    if not is_route_enabled():
        raise HTTPException(status_code=404, detail="Not found")


def _snapshot(row: Optional[RazorpayLinkedAccount]) -> dict:
    if row is None:
        return {
            "onboarding_status": RouteOnboardingStatus.NOT_STARTED,
            "payment_mode": PaymentRouteMode.AUTO,
            "transfer_ready": False,
            "message": frontend_message(RouteOnboardingStatus.NOT_STARTED),
        }
    status = row.onboarding_status or RouteOnboardingStatus.NOT_STARTED
    return {
        "onboarding_status": status,
        "payment_mode": row.payment_mode,
        "razorpay_account_id": row.razorpay_account_id,
        "razorpay_status": row.razorpay_status,
        "route_product_id": row.route_product_id,
        "route_product_status": row.route_product_status,
        "stakeholder_id": row.stakeholder_id,
        "bank_verified": bool(row.bank_verified),
        "bank_last4": row.bank_last4,
        "beneficiary_name": row.beneficiary_name,
        "transfer_ready": is_transfer_ready(status, row.route_product_status),
        "transfer_ready_at": row.transfer_ready_at.isoformat() if row.transfer_ready_at else None,
        "failure_reason": row.failure_reason,
        "message": frontend_message(status),
    }


async def _get_row(db: AsyncSession, org_id: object) -> Optional[RazorpayLinkedAccount]:
    res = await db.execute(select(RazorpayLinkedAccount).where(RazorpayLinkedAccount.org_id == org_id))  # type: ignore[arg-type]
    return res.scalars().first()


async def _with_price(db: AsyncSession, org_id: object, snap: dict) -> dict:
    """Merge appointment pricing (owned by the Route card UI now)."""
    from app.models.config import OrgConfig

    res = await db.execute(select(OrgConfig).where(OrgConfig.org_id == org_id))  # type: ignore[arg-type]
    cfg = res.scalars().first()
    return {
        **snap,
        "price_enabled": bool(getattr(cfg, "appointment_price_enabled", False)) if cfg else False,
        "price_paise": int(getattr(cfg, "appointment_price_paise", 0) or 0) if cfg else 0,
        "currency": (getattr(cfg, "appointment_currency", "INR") or "INR") if cfg else "INR",
    }


def _refresh_status(row: RazorpayLinkedAccount) -> RazorpayLinkedAccount:
    """Recompute onboarding_status from persisted fields (single place)."""
    row.onboarding_status = derive_onboarding_status(
        razorpay_account_id=row.razorpay_account_id,
        razorpay_account_status=row.razorpay_status,
        stakeholder_id=row.stakeholder_id,
        route_product_id=row.route_product_id,
        route_product_status=row.route_product_status,
        bank_last4=row.bank_last4,
        failure_reason=row.failure_reason,
    )
    if row.onboarding_status == RouteOnboardingStatus.TRANSFER_READY:
        row.bank_verified = True
        if row.transfer_ready_at is None:
            row.transfer_ready_at = datetime.now(timezone.utc)
    return row


# ── Schemas ──────────────────────────────────────────────────────────────

class LinkedAccountIn(BaseModel):
    phone: str = Field(..., min_length=8, max_length=15)
    email: Optional[str] = None  # default: authenticated user's email
    legal_business_name: Optional[str] = None  # default: org name
    business_type: str = "proprietorship"
    contact_name: Optional[str] = None
    category: str = "healthcare"
    subcategory: str = "clinic"
    # Registered business address — required by the create-linked-account docs
    # (profile → addresses → registered).
    street1: str = Field(..., min_length=1, max_length=100)
    street2: Optional[str] = Field(default=None, max_length=100)
    city: str = Field(..., min_length=1, max_length=100)
    state: str = Field(..., min_length=2, max_length=32)
    postal_code: str = Field(..., min_length=6, max_length=6)
    country: str = "IN"
    # KYC (docs legal_info): business PAN mandatory for company-type entities,
    # optional for proprietorship. Passed to Razorpay only — never stored.
    legal_pan: Optional[str] = Field(default=None, max_length=10)
    legal_gst: Optional[str] = Field(default=None, max_length=15)


class StakeholderIn(BaseModel):
    name: Optional[str] = None  # default: authenticated user's full name
    street: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    pan: Optional[str] = None  # passed through to Razorpay only, never stored


class ProductRequestIn(BaseModel):
    tnc_accepted: bool


class SettlementIn(BaseModel):
    account_number: str
    ifsc_code: str
    beneficiary_name: str
    tnc_accepted: bool


class PaymentModeIn(BaseModel):
    mode: str  # AUTO | LEGACY | ROUTE


# ── Status ───────────────────────────────────────────────────────────────

@router.get("/dashboard/razorpay/route/status")
async def route_status(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_route_enabled()
    row = await _get_row(db, user.org_id)
    return await _with_price(db, user.org_id, _snapshot(row))


@router.get("/dashboard/razorpay/route/linked-account")
async def get_linked_account(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return DB state plus a live Razorpay fetch (read-only reconcile)."""
    _require_route_enabled()
    row = await _get_row(db, user.org_id)
    if row is None or not row.razorpay_account_id:
        return _snapshot(row)
    try:
        live = await route_svc.fetch_linked_account(RouteClient.from_settings(), row.razorpay_account_id)
    except RouteAPIError as exc:
        logger.warning("route live fetch failed org=%s", user.org_id)
        return {**_snapshot(row), "live_error": str(exc)[:200]}
    if live.get("status") and live.get("status") != row.razorpay_status:
        row.razorpay_status = str(live.get("status"))[:32]
        _refresh_status(row)
        await db.commit()
        await db.refresh(row)
    return {**_snapshot(row), "live_status": live.get("status")}


# ── Linked account creation (idempotent per org) ─────────────────────────

@router.post("/dashboard/razorpay/route/linked-account", dependencies=[Depends(deny_impersonation)])
async def create_linked_account(
    payload: LinkedAccountIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_route_enabled()
    row = await _get_row(db, user.org_id)
    if row is not None and row.razorpay_account_id:
        return _snapshot(row)  # already exists — never create another

    org_res = await db.execute(select(Organization).where(Organization.id == user.org_id))
    org = org_res.scalars().first()
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    email = (payload.email or user.email).strip().lower()
    legal_name = (payload.legal_business_name or org.name).strip()
    reference_id = build_reference_id(user.org_id)
    try:
        rzp_payload = route_svc.build_linked_account_payload(
            email=email,
            phone=payload.phone.strip(),
            reference_id=reference_id,
            legal_business_name=legal_name,
            business_type=payload.business_type,
            contact_name=payload.contact_name or user.full_name,
            category=payload.category,
            subcategory=payload.subcategory,
            registered_address={
                "street1": payload.street1,
                "street2": payload.street2,
                "city": payload.city,
                "state": payload.state,
                "postal_code": payload.postal_code,
                "country": payload.country,
            },
            legal_pan=payload.legal_pan,
            legal_gst=payload.legal_gst,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        data = await route_svc.create_linked_account(RouteClient.from_settings(), rzp_payload)
    except RouteAPIError as exc:
        # No row written yet — safe to retry. Email conflicts mean an account
        # already exists for this email outside our DB; surface, don't guess.
        logger.warning("route linked account create failed org=%s", user.org_id)
        raise HTTPException(status_code=409 if exc.status_code == 400 else 502, detail=str(exc)[:300])

    account_id = data.get("id")
    if not account_id:
        raise HTTPException(status_code=502, detail="Razorpay did not return an account id")
    try:
        if row is None:
            row = RazorpayLinkedAccount(
                org_id=user.org_id,
                razorpay_account_id=account_id,
                razorpay_reference_id=reference_id,
                razorpay_status=str(data.get("status") or "created")[:32],
                onboarding_status=RouteOnboardingStatus.ACCOUNT_CREATED,
            )
            db.add(row)
        else:
            # Earlier attempt wrote a row but never got an account id.
            row.razorpay_account_id = account_id
            row.razorpay_reference_id = reference_id
            row.razorpay_status = str(data.get("status") or "created")[:32]
            row.failure_reason = None
        _refresh_status(row)
        await db.commit()
        await db.refresh(row)
    except IntegrityError:
        # Concurrent double-POST: UNIQUE(org_id) — return the winner's row.
        await db.rollback()
        row = await _get_row(db, user.org_id)
        return _snapshot(row)
    logger.info("route onboarding: linked account org=%s status=%s", user.org_id, row.onboarding_status)
    return _snapshot(row)


# ── Stakeholder (bound to the authenticated provider) ────────────────────

@router.post("/dashboard/razorpay/route/stakeholder", dependencies=[Depends(deny_impersonation)])
async def create_stakeholder(
    payload: StakeholderIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_route_enabled()
    row = await _get_row(db, user.org_id)
    if row is None or not row.razorpay_account_id:
        raise HTTPException(status_code=409, detail="Create the Linked Account first")
    if row.stakeholder_id:
        return _snapshot(row)  # idempotent — never create another

    # Identity binding: stakeholder email is ALWAYS the authenticated
    # provider's verified email. The frontend cannot substitute another identity.
    try:
        rzp_payload = route_svc.build_stakeholder_payload(
            name=(payload.name or user.full_name).strip(),
            email=user.email.strip().lower(),
            street=payload.street,
            city=payload.city,
            state=payload.state,
            postal_code=payload.postal_code,
            pan=payload.pan.strip() if payload.pan else None,
            org_id=str(user.org_id),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    try:
        data = await route_svc.create_stakeholder(
            RouteClient.from_settings(), row.razorpay_account_id, rzp_payload
        )
    except RouteAPIError as exc:
        logger.warning("route stakeholder create failed org=%s", user.org_id)
        raise HTTPException(status_code=502, detail=str(exc)[:300])
    if not data.get("id"):
        raise HTTPException(status_code=502, detail="Razorpay did not return a stakeholder id")
    row.stakeholder_id = str(data["id"])[:32]
    _refresh_status(row)
    await db.commit()
    await db.refresh(row)
    logger.info("route onboarding: stakeholder org=%s status=%s", user.org_id, row.onboarding_status)
    return _snapshot(row)


# ── Route product request / settlement update ────────────────────────────

@router.post("/dashboard/razorpay/route/product", dependencies=[Depends(deny_impersonation)])
async def request_product(
    payload: ProductRequestIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_route_enabled()
    if payload.tnc_accepted is not True:
        raise HTTPException(status_code=422, detail="Provider must accept the Route terms first (tnc_accepted=true)")
    row = await _get_row(db, user.org_id)
    if row is None or not row.razorpay_account_id:
        raise HTTPException(status_code=409, detail="Create the Linked Account first")
    if not row.stakeholder_id:
        raise HTTPException(status_code=409, detail="Create the stakeholder first")
    if row.route_product_id:
        # Idempotent: refresh live status instead of requesting again.
        try:
            live = await route_svc.fetch_product_config(
                RouteClient.from_settings(), row.razorpay_account_id, row.route_product_id
            )
            if live.get("activation_status"):
                row.route_product_status = str(live["activation_status"])[:32]
                _refresh_status(row)
                await db.commit()
                await db.refresh(row)
        except RouteAPIError as exc:
            logger.warning("route product fetch failed org=%s", user.org_id)
            raise HTTPException(status_code=502, detail=str(exc)[:300])
        return _snapshot(row)
    try:
        data = await route_svc.request_route_product(
            RouteClient.from_settings(), row.razorpay_account_id, tnc_accepted=True
        )
    except RouteAPIError as exc:
        logger.warning("route product request failed org=%s", user.org_id)
        raise HTTPException(status_code=502, detail=str(exc)[:300])
    row.route_product_id = str(data.get("id", ""))[:32] or None
    row.route_product_status = str(data.get("activation_status") or "requested")[:32]
    row.tnc_accepted_at = datetime.now(timezone.utc)
    _refresh_status(row)
    await db.commit()
    await db.refresh(row)
    logger.info("route onboarding: product org=%s status=%s", user.org_id, row.onboarding_status)
    return _snapshot(row)


@router.patch("/dashboard/razorpay/route/product", dependencies=[Depends(deny_impersonation)])
async def update_product_settlement(
    payload: SettlementIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Submit bank settlement details to Razorpay.

    Raw account number / IFSC travel to Razorpay over HTTPS and are NEVER
    stored or logged. Only last4 + beneficiary name persist.
    """
    _require_route_enabled()
    if payload.tnc_accepted is not True:
        raise HTTPException(status_code=422, detail="Provider must accept the Route terms first (tnc_accepted=true)")
    row = await _get_row(db, user.org_id)
    if row is None or not row.razorpay_account_id or not row.route_product_id:
        raise HTTPException(status_code=409, detail="Request the Route product first")
    try:
        data = await route_svc.update_product_settlement(
            RouteClient.from_settings(),
            row.razorpay_account_id,
            row.route_product_id,
            account_number=payload.account_number,
            ifsc_code=payload.ifsc_code,
            beneficiary_name=payload.beneficiary_name,
            tnc_accepted=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RouteAPIError as exc:
        logger.warning("route settlement update failed org=%s", user.org_id)
        raise HTTPException(status_code=502, detail=str(exc)[:300])
    safe = route_svc.summarize_settlement_for_storage(payload.beneficiary_name, payload.account_number)
    row.bank_last4 = safe["bank_last4"]
    row.beneficiary_name = safe["beneficiary_name"]
    if data.get("activation_status"):
        row.route_product_status = str(data["activation_status"])[:32]
    row.tnc_accepted_at = datetime.now(timezone.utc)
    _refresh_status(row)
    await db.commit()
    await db.refresh(row)
    logger.info("route onboarding: settlement org=%s status=%s", user.org_id, row.onboarding_status)
    return _snapshot(row)


# ── Explicit routing choice ──────────────────────────────────────────────

@router.patch("/dashboard/razorpay/route/payment-mode", dependencies=[Depends(deny_impersonation)])
async def set_payment_mode(
    payload: PaymentModeIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_route_enabled()
    mode = (payload.mode or "").upper()
    if mode not in PaymentRouteMode.ALL:
        raise HTTPException(status_code=422, detail="mode must be one of AUTO, LEGACY, ROUTE")
    row = await _get_row(db, user.org_id)
    if row is None:
        row = RazorpayLinkedAccount(
            org_id=user.org_id,
            razorpay_reference_id=build_reference_id(user.org_id),
            payment_mode=mode,
            onboarding_status=RouteOnboardingStatus.NOT_STARTED,
        )
        db.add(row)
    else:
        row.payment_mode = mode
    await db.commit()
    await db.refresh(row)
    logger.info("route payment-mode org=%s mode=%s", user.org_id, mode)
    return _snapshot(row)
