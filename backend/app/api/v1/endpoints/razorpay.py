"""
Razorpay Route — platform payments + provider payouts.

Legacy Technology Partner sub-merchant flow removed: booking payments are
created on the platform account and split to providers via Route transfers.
"""
import hashlib
import hmac
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from pydantic import BaseModel

from app.db.database import get_db
from app.models.config import OrgConfig
from app.models.organization import Organization
from app.models.payment import PaymentAttempt, RazorpayWebhookEvent
from app.models.booking import Booking
from app.models.session import ChatSession
from app.api.v1.endpoints.auth import deny_impersonation, get_current_user
from app.models.user import User
from app.core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)


def verify_webhook_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    """HMAC-SHA256 webhook check (kept here after partner module removal)."""
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


class PriceUpdate(BaseModel):
    enabled: bool
    amount_paise: int = 0
    currency: Optional[str] = "INR"


@router.put("/dashboard/razorpay/price", dependencies=[Depends(deny_impersonation)])
async def update_razorpay_price(
    payload: PriceUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if payload.amount_paise < 0 or payload.amount_paise > 10000000:
        raise HTTPException(status_code=422, detail="Invalid amount")
    res = await db.execute(select(OrgConfig).where(OrgConfig.org_id == user.org_id))
    cfg = res.scalars().first()
    if not cfg:
        raise HTTPException(status_code=404, detail="Config not found")
    cfg.appointment_price_enabled = bool(payload.enabled)
    cfg.appointment_price_paise = int(payload.amount_paise) if payload.enabled else 0
    cfg.appointment_currency = (payload.currency or "INR").upper()
    await db.commit()
    return {"ok": True, "enabled": cfg.appointment_price_enabled, "amount_paise": cfg.appointment_price_paise, "currency": cfg.appointment_currency}


# ── Widget payment: create order + verify (public widget_token) ──
class WidgetOrderRequest(BaseModel):
    session_id: str
    widget_token: Optional[str] = None
    api_key: Optional[str] = None  # legacy

class WidgetOrderResponse(BaseModel):
    order_id: str
    amount_paise: int
    currency: str
    key: str  # platform key_id for Checkout
    razorpay_account_id: Optional[str] = None
    mock: bool = False

async def _resolve_org_from_widget(widget_token: Optional[str], api_key: Optional[str], db: AsyncSession) -> Organization:
    from app.models.config import OrgConfig as OC
    from app.crud.organization import validate_api_key
    org = None
    if widget_token:
        r = await db.execute(select(OC).where(OC.widget_token == widget_token))
        c = r.scalars().first()
        if c:
            r2 = await db.execute(select(Organization).where(Organization.id == c.org_id))
            org = r2.scalars().first()
    elif api_key:
        org = await validate_api_key(db, api_key)
    if not org or not org.is_active:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org

@router.post("/widget/payment/order", response_model=WidgetOrderResponse)
async def widget_create_order(
    payload: WidgetOrderRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    # Resolve org
    widget_token = payload.widget_token or request.headers.get("X-Widget-Token") or request.query_params.get("widget_token")
    api_key = payload.api_key or request.headers.get("X-Org-API-Key")
    org = await _resolve_org_from_widget(widget_token, api_key, db)
    # Load org config pricing
    res = await db.execute(select(OrgConfig).where(OrgConfig.org_id == org.id))
    cfg = res.scalars().first()
    if not cfg:
        raise HTTPException(status_code=500, detail="Org config missing")
    price_enabled = bool(getattr(cfg, "appointment_price_enabled", False))
    amount = int(getattr(cfg, "appointment_price_paise", 0) or 0)
    currency = getattr(cfg, "appointment_currency", "INR") or "INR"
    # If no price enabled, no order needed — return mock free
    if not price_enabled or amount <= 0:
        raise HTTPException(status_code=400, detail="Payments not enabled for this organization")
    # Verify session belongs to org
    sess_res = await db.execute(select(ChatSession).where(ChatSession.id == payload.session_id, ChatSession.org_id == org.id))
    sess = sess_res.scalars().first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    # ── Route-only classification ──
    # ROUTE orders are created on the PLATFORM account for later transfer.
    # Anything else gets a 409 here — the legacy sub-merchant flow is removed
    # and there is no silent fallback.
    route_order_account_id: Optional[str] = None
    route_mode_value: Optional[str] = None
    order: Optional[dict] = None
    try:
        from app.services.razorpay import routing as route_routing
        from app.services.razorpay import route as route_svc
        from app.services.razorpay import client as route_client_mod
        route_ctx = await route_routing.get_org_route_context(db, org.id)
        payment_route = route_routing.resolve_payment_route(
            route_enabled=route_client_mod.is_route_enabled(),
            payment_mode=route_ctx.payment_mode,
            onboarding_status=route_ctx.onboarding_status,
            route_product_status=route_ctx.route_product_status,
        )
    except Exception as exc:
        from app.services.razorpay.routing import ProviderPayoutNotReady as _NotReady
        if isinstance(exc, _NotReady):
            raise HTTPException(
                status_code=409,
                detail={"code": "provider_payout_not_ready", "message": str(exc)},
            )
        raise
    if payment_route == "ROUTE":
        if currency != "INR":
            raise HTTPException(status_code=422, detail="Route booking payments support INR only")
        route_receipt = f"route_{str(org.id)[:8]}_{str(payload.session_id)[:8]}_{int(datetime.now(timezone.utc).timestamp())}"
        try:
            order = await route_svc.create_platform_order(
                route_client_mod.RouteClient.from_settings(),
                amount_paise=amount,
                receipt=route_receipt,
                currency=currency,
                notes={"org_id": str(org.id), "session_id": str(sess.id)},
            )
        except Exception as exc:
            logger.warning("route platform order failed org=%s: %s", org.id, type(exc).__name__)
            raise HTTPException(status_code=502, detail="Failed to create platform payment order")
        route_mode_value = "ROUTE"
        route_order_account_id = route_ctx.razorpay_account_id
    else:
        # Legacy sub-merchant flow removed: booking payments require a
        # TRANSFER_READY Route payout account. No silent fallback exists.
        raise HTTPException(
            status_code=409,
            detail={
                "code": "provider_payout_not_ready",
                "message": (
                    "Provider payouts are not ready "
                    f"(onboarding: {route_ctx.onboarding_status}). "
                    "Complete bank onboarding to accept payments."
                ),
            },
        )
    if not order or not order.get("id"):
        raise HTTPException(status_code=502, detail="Failed to create Razorpay order")
    # Store payment attempt
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    attempt = PaymentAttempt(
        org_id=org.id,
        session_id=sess.id,
        razorpay_order_id=order["id"],
        amount_paise=amount,
        currency=currency,
        status="created",
        verification_status="pending",
        expires_at=expires_at,
        route_mode=route_mode_value,
    )
    db.add(attempt)
    # Extend slot lock for payment window (15min total) if slot locked
    try:
        from app.models.booking import TimeSlot
        if getattr(sess, "state_data", None) and isinstance(sess.state_data, dict):
            locked_id = sess.state_data.get("locked_slot_id")
            if locked_id:
                slot_res = await db.execute(select(TimeSlot).where(TimeSlot.id == locked_id, TimeSlot.org_id == org.id))
                slot = slot_res.scalars().first()
                if slot and slot.locked_until:
                    new_until = datetime.now(timezone.utc) + timedelta(minutes=15)
                    if slot.locked_until < new_until:
                        slot.locked_until = new_until
    except Exception as e:
        logger.warning("extend lock failed: %s", e)
    await db.commit()
    # Platform order: Checkout key is the platform key_id (public-safe).
    # Non-ROUTE routes raise 409 above, so this is the only response path.
    return WidgetOrderResponse(
        order_id=order["id"],
        amount_paise=amount,
        currency=currency,
        key=getattr(settings, "RAZORPAY_KEY_ID", ""),
        razorpay_account_id=route_order_account_id,
        mock=False,
    )

class WidgetVerifyRequest(BaseModel):
    session_id: str
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    widget_token: Optional[str] = None
    api_key: Optional[str] = None

@router.post("/widget/payment/verify")
async def widget_verify_payment(
    payload: WidgetVerifyRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    widget_token = payload.widget_token or request.headers.get("X-Widget-Token") or request.query_params.get("widget_token")
    api_key = payload.api_key or request.headers.get("X-Org-API-Key")
    org = await _resolve_org_from_widget(widget_token, api_key, db)
    res = await db.execute(select(OrgConfig).where(OrgConfig.org_id == org.id))
    cfg = res.scalars().first()
    if not cfg:
        raise HTTPException(status_code=404, detail="Config not found")
    # Find attempt by order_id and org (tenant isolation)
    attempt_res = await db.execute(select(PaymentAttempt).where(PaymentAttempt.razorpay_order_id == payload.razorpay_order_id, PaymentAttempt.org_id == org.id))
    attempt = attempt_res.scalars().first()
    if not attempt:
        raise HTTPException(status_code=404, detail="Order not found for this organization")
    if attempt.status == "paid":
        return {"ok": True, "status": "paid", "already_verified": True}
    # Verify amount/currency server-side (never trust frontend)
    if attempt.amount_paise is None or attempt.currency is None:
        raise HTTPException(status_code=500, detail="Attempt missing amount")
    # Route platform verification: standard Checkout HMAC (order|payment with
    # the platform key secret) + a server-side platform fetch confirming the
    # payment is captured for this order and amount.
    platform_secret = settings.RAZORPAY_KEY_SECRET or ""
    if not platform_secret:
        raise HTTPException(status_code=502, detail="Payment verification unavailable")
    expected_sig = hmac.new(
        platform_secret.encode(),
        f"{payload.razorpay_order_id}|{payload.razorpay_payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_sig, payload.razorpay_signature or ""):
        raise HTTPException(status_code=400, detail="Payment signature verification failed")
    try:
        from app.services.razorpay.client import RouteClient
        rzp_client = RouteClient.from_settings()
        payment = await rzp_client.fetch_payment(payload.razorpay_payment_id)
    except Exception:
        logger.warning("platform payment fetch failed org=%s", org.id)
        raise HTTPException(status_code=502, detail="Could not verify payment with Razorpay")
    # Verify payment captured, order_id matches, amount matches
    if payment.get("order_id") != payload.razorpay_order_id:
        raise HTTPException(status_code=400, detail="Payment order mismatch")
    if int(payment.get("amount", 0)) != attempt.amount_paise:
        raise HTTPException(status_code=400, detail="Amount mismatch")
    if payment.get("status") != "captured":
        raise HTTPException(status_code=400, detail=f"Payment not captured: {payment.get('status')}")
    # Verify currency
    if payment.get("currency") != attempt.currency:
        raise HTTPException(status_code=400, detail="Currency mismatch")
    attempt.razorpay_payment_id = payload.razorpay_payment_id
    attempt.razorpay_signature = payload.razorpay_signature
    attempt.status = "paid"
    attempt.verification_status = "verified"
    await db.commit()
    # ── Route settlement (additive, idempotent, best-effort) ──
    # Converges with the payment.captured webhook in settle_payment().
    # Payout progress never fails this response: the customer payment stands
    # regardless, and transfer retries reconcile via Razorpay GET first.
    try:
        from app.services.razorpay import settlement as route_settlement
        settle_res = await route_settlement.settle_payment(
            db,
            org_id=org.id,
            razorpay_order_id=payload.razorpay_order_id,
            razorpay_payment_id=payload.razorpay_payment_id,
        )
        if settle_res.get("outcome") not in ("not_route", "unknown_order"):
            logger.info(
                "route settle after verify org=%s outcome=%s",
                org.id,
                settle_res.get("outcome"),
            )
    except Exception:
        logger.exception("route settle after verify failed org=%s", org.id)
    # Now confirm booking: delegate to existing booking engine but via slot hold
    # Find session and trigger booking creation if slot locked
    sess_res = await db.execute(select(ChatSession).where(ChatSession.id == payload.session_id, ChatSession.org_id == org.id))
    sess = sess_res.scalars().first()
    if sess:
        # Update attempt booking reference after booking created (booking creation happens via message_processor continuation)
        # Instead, we mark session intent to CONFIRM_BOOKING and process a synthetic confirm
        from app.engine.message_processor import process_message
        # Use a synthetic "payment verified" message to trigger booking (preserve existing engine)
        # The engine will create Booking and is idempotent via slot lock.
        try:
            # We call process_message with CONFIRM intent via direct booking helper if possible
            # Fallback: let frontend call dispatch with "confirm" after verify
            pass
        except Exception as e:
            logger.warning("post-payment booking trigger failed: %s", e)
    # Link booking if exists, else try to confirm booking now that payment is verified
    booking_res = await db.execute(select(Booking).where(Booking.razorpay_order_id == payload.razorpay_order_id, Booking.org_id == org.id))
    booking = booking_res.scalars().first()
    if booking:
        attempt.booking_id = booking.id
        await db.commit()
        return {"ok": True, "status": "paid", "razorpay_payment_id": payload.razorpay_payment_id, "booking_id": str(booking.id)}
    # No booking yet — create it now via existing engine (payment-gated path)
    # We synthesize a CONFIRM_BOOKING dispatch by directly calling attempt_booking with session's locked slot
    try:
        from app.models.booking import TimeSlot
        from app.crud.booking import attempt_booking as _attempt
        from app.models.config import OrgConfig as _OC
        # Load session to get slot and customer data
        sess_for_book = sess
        if sess_for_book:
            # Check lock still valid
            lock_id = (sess_for_book.state_data or {}).get("locked_slot_id") if isinstance(sess_for_book.state_data, dict) else None
            slot = None
            if lock_id:
                slot_res = await db.execute(select(TimeSlot).where(TimeSlot.id == lock_id, TimeSlot.org_id == org.id))
                slot = slot_res.scalars().first()
            if slot and not slot.is_booked:
                # Need customer fields from session
                data = sess_for_book.state_data or {}
                if data.get("name") and data.get("email"):
                    created = await _attempt(
                        db=db,
                        session_id=str(sess_for_book.id),
                        org_id=str(org.id),
                        slot_id=str(slot.id),
                        customer_name=data.get("name"),
                        customer_email=data.get("email"),
                        customer_phone=data.get("phone",""),
                        payment_status="paid",
                        razorpay_order_id=payload.razorpay_order_id,
                        razorpay_payment_id=payload.razorpay_payment_id,
                        amount_paise=attempt.amount_paise,
                        currency=attempt.currency,
                    )
                    sess_for_book.session_status = "CONFIRMED"
                    await db.commit()
                    attempt.booking_id = created.id
                    await db.commit()
                    return {"ok": True, "status": "paid", "booking_id": str(created.id), "razorpay_payment_id": payload.razorpay_payment_id}
    except Exception as be:
        logger.warning("auto-booking after payment verify failed: %s", be)
    return {"ok": True, "status": "paid", "razorpay_payment_id": payload.razorpay_payment_id, "booking_pending": True}


# ── Webhook (Partner onboarding + payments) ──
@router.post("/webhooks/razorpay")
async def razorpay_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    raw = await request.body()
    sig = request.headers.get("X-Razorpay-Signature", "")
    secret = settings.RAZORPAY_WEBHOOK_SECRET or ""
    if not secret:
        # Empty-secret bypass is TEST-only. Route money movement in production
        # (live mode) MUST have signature verification — refuse otherwise.
        from app.services.razorpay.client import is_route_enabled as _route_on
        _live = (settings.RAZORPAY_PARTNER_MODE or "test").lower() == "live"
        if _route_on() and _live:
            logger.warning("razorpay webhook rejected: no secret with Route enabled in live mode")
            raise HTTPException(status_code=400, detail="Webhook secret not configured")
        logger.warning("razorpay webhook secret not configured, skipping signature verify")
        # In test without secret, accept
    else:
        if not verify_webhook_signature(raw, sig, secret):
            raise HTTPException(status_code=400, detail="Invalid webhook signature")
    try:
        import json as _json
        payload = _json.loads(raw.decode() or "{}")
    except Exception:
        payload = {}
    event = payload.get("event") or ""
    # Deterministic idempotency — use real event ID if present, else SHA-256 fallback
    raw_event_id = payload.get("id") or payload.get("event_id") or payload.get("razorpay_event_id") or ""
    if raw_event_id:
        event_id = str(raw_event_id)
    else:
        import hashlib as _hashlib
        _fallback_created = str(payload.get("created_at") or "")
        _fallback_raw = f"{event}|{_fallback_created}"
        event_id = "evt_fallback_" + _hashlib.sha256(_fallback_raw.encode()).hexdigest()[:24]
    # Idempotency: check exists BEFORE any status update, insert BEFORE update, same transaction
    exists = await db.execute(select(RazorpayWebhookEvent).where(RazorpayWebhookEvent.event_id == event_id))
    if exists.scalars().first():
        return {"ok": True, "deduplicated": True}
    try:
        evt = RazorpayWebhookEvent(event_id=event_id, event_type=event, payload=payload, status="processed")
        db.add(evt)
        await db.flush()
    except Exception:
        await db.rollback()
        return {"ok": True, "deduplicated": True}
    committed = False

    # Payment events: payment.captured → mark attempt paid + create booking if needed
    if event in ("payment.captured", "payment.authorized", "order.paid"):
        payment = payload.get("payload", {}).get("payment", {}).get("entity", {}) or payload.get("payload", {}).get("order", {}).get("entity", {})
        # payment.captured contains order_id, id, amount, currency, status
        razorpay_payment_id = payment.get("id")
        razorpay_order_id = payment.get("order_id")
        if razorpay_order_id and razorpay_payment_id:
            att_res = await db.execute(select(PaymentAttempt).where(PaymentAttempt.razorpay_order_id == razorpay_order_id))
            att = att_res.scalars().first()
            if att and att.status != "paid":
                # Verify amount matches attempt
                if int(payment.get("amount", 0)) == att.amount_paise:
                    att.status = "paid"
                    att.razorpay_payment_id = razorpay_payment_id
                    att.verification_status = "verified"
                    att.webhook_payload = payload
                    await db.commit()
                    committed = True
                    logger.info("webhook paid order %s payment %s org %s", razorpay_order_id, razorpay_payment_id, att.org_id)
            # ── Route settlement (converged with verify endpoint) ──
            # Runs whether or not this webhook did the paid-marking (idempotent),
            # so webhook-before-verify and verify-before-webhook both settle once.
            if att is not None and event == "payment.captured":
                try:
                    from app.services.razorpay import settlement as route_settlement
                    settle_res = await route_settlement.settle_payment(
                        db,
                        org_id=att.org_id,
                        razorpay_order_id=razorpay_order_id,
                        razorpay_payment_id=razorpay_payment_id,
                    )
                    if settle_res.get("outcome") not in ("not_route", "unknown_order"):
                        committed = True
                        logger.info(
                            "route settle after webhook org=%s outcome=%s",
                            att.org_id,
                            settle_res.get("outcome"),
                        )
                except Exception:
                    logger.exception("route settle after webhook failed org=%s", att.org_id)

    # ── Route transfer lifecycle events (additive; existing branches above unchanged) ──
    # Documented names: transfer.processed, transfers.failed (plural), settlement.processed
    # https://razorpay.com/docs/payments/route/subscribe-to-webhooks
    # The singular transfer.failed is also accepted defensively (same handling).
    if event in ("transfer.processed", "transfers.failed", "transfer.failed"):
        entity = (
            payload.get("payload", {}).get("transfer", {}).get("entity", {})
            or payload.get("payload", {}).get("entity", {})
        )
        if entity.get("id"):
            try:
                from app.services.razorpay import settlement as route_settlement
                applied = await route_settlement.apply_transfer_webhook(db, entity)
                if applied.get("outcome") == "updated":
                    committed = True
                    logger.info(
                        "route webhook %s transfer %s -> %s",
                        event,
                        str(entity.get("id"))[:16],
                        applied.get("status"),
                    )
            except Exception:
                logger.exception("route transfer webhook apply failed")
    elif event == "settlement.processed":
        # Informational: recipient settlement completed at Razorpay. Recorded via
        # the idempotency row (inserted above); no local ledger mutation needed.
        logger.info("route webhook settlement.processed received")

    if not committed:
        # Persist the idempotency event row even when no status/payment branch matched
        await db.commit()
    return {"ok": True}
