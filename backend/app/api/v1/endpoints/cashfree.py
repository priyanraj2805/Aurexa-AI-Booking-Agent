"""
Cashfree — platform payments (additive second provider).

Same platform-account model as Razorpay Route: orders are created on the
platform Cashfree account (App ID + Secret from settings); provider splits
happen later via Easy Split. No per-org keys in this revision.

Endpoints (all under the tenant api_router, no auth except widget token):
- POST /widget/payment/cashfree/order   — create order for a booking session
- POST /widget/payment/cashfree/webhook — Cashfree payment webhooks (signed)
- GET  /widget/payment/cashfree/status  — reconcile fallback (widget polling)

Security rules:
- Webhook signature verified (HMAC-SHA256 over timestamp + raw body) with
  timestamp freshness. Fail closed: unverified events never mark paid.
- Amount + order match enforced before marking paid (spoofed order_id with
  a smaller real payment cannot confirm a booking).
- Idempotent: cashfree_webhook_events dedupes retried deliveries.
- Never logs secrets, keys, or full payloads.
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from pydantic import BaseModel

from app.db.database import get_db
from app.models.config import OrgConfig
from app.models.organization import Organization
from app.models.payment import PaymentAttempt, CashfreeWebhookEvent
from app.models.session import ChatSession
from app.core.config import settings
from app.services.cashfree.client import CashfreeClient, CashfreeAPIError, require_cashfree_enabled
from app.services.cashfree.webhooks import verify_webhook_signature
from app.services.payment_service import payment_service

router = APIRouter()
logger = logging.getLogger(__name__)

PAYMENT_WINDOW_MINUTES = 10
SLOT_HOLD_MINUTES = 15


class CashfreeOrderRequest(BaseModel):
    session_id: str
    widget_token: Optional[str] = None
    api_key: Optional[str] = None  # legacy


class CashfreeOrderResponse(BaseModel):
    order_id: str
    payment_session_id: str
    amount_paise: int
    currency: str
    environment: str  # sandbox | production
    mock: bool = False


async def _resolve_org_from_widget(
    widget_token: Optional[str], api_key: Optional[str], db: AsyncSession
) -> Organization:
    # Same resolution as the Razorpay widget endpoints.
    from app.api.v1.endpoints.razorpay import _resolve_org_from_widget as _resolve

    return await _resolve(widget_token, api_key, db)


def _environment(api_base: Optional[str] = None) -> str:
    """sandbox | production for the base actually used by the client.

    Per-org keys may point at production while the platform account is on
    sandbox, so the client's own base is the source of truth.
    """
    base = (api_base or settings.CASHFREE_API_BASE or "").lower()
    return "production" if "api.cashfree.com" in base else "sandbox"


@router.post("/widget/payment/cashfree/order", response_model=CashfreeOrderResponse)
async def cashfree_create_order(
    payload: CashfreeOrderRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        require_cashfree_enabled()
    except CashfreeAPIError:
        raise HTTPException(status_code=503, detail="Cashfree payments are disabled")
    org = await _resolve_org_from_widget(payload.widget_token, payload.api_key, db)
    res = await db.execute(select(OrgConfig).where(OrgConfig.org_id == org.id))
    cfg = res.scalars().first()
    if not cfg:
        raise HTTPException(status_code=500, detail="Org config missing")
    price_enabled = bool(getattr(cfg, "appointment_price_enabled", False))
    sess_res = await db.execute(
        select(ChatSession).where(ChatSession.id == payload.session_id, ChatSession.org_id == org.id)
    )
    sess = sess_res.scalars().first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    state = sess.state_data if isinstance(sess.state_data, dict) else {}
    # Charge = catalog fee for this booking's department/member, flat org
    # price as fallback. Tenants price per department/member in the catalog;
    # the dashboard price box is only a legacy fallback.
    _ouid = getattr(sess, "unit_id", None) or state.get("unit_id")
    _opid = getattr(sess, "provider_id", None) or state.get("provider_id")
    amount, currency = await payment_service.resolve_charge(
        db, org.id, cfg, _ouid, _opid
    )
    if not price_enabled or amount <= 0:
        raise HTTPException(status_code=400, detail="Payments not enabled for this organization")
    if currency != "INR":
        raise HTTPException(status_code=422, detail="Cashfree booking payments support INR only")
    order_id = (
        f"cf_{str(org.id)[:8]}_{str(sess.id)[:8]}"
        f"_{int(datetime.now(timezone.utc).timestamp())}"
    )
    notify_url = ""
    return_url = ""
    _public_base = (settings.PUBLIC_API_BASE or "").rstrip("/")
    # PUBLIC_API_BASE may or may not include the /api/v1 suffix.
    if _public_base.endswith("/api/v1"):
        _public_base = _public_base[: -len("/api/v1")]
    if _public_base:
        notify_url = _public_base + "/api/v1/widget/payment/cashfree/webhook"
        # Bank/OTP pages refuse to load inside the checkout modal iframe, so
        # the widget opens checkout in a NEW TAB (redirectTarget _blank).
        # Cashfree returns the customer here after payment; the page polls our
        # status endpoint while the original chat tab reconciles the booking.
        return_url = (
            _public_base
            + f"/static/widget/payment-return.html?sid={sess.id}&oid={order_id}"
        )
    try:
        from app.services.cashfree.org_creds import resolve_cashfree_client

        client, _owned = await resolve_cashfree_client(db, org.id)
        order = await client.create_order(
            order_id=order_id,
            order_amount=round(amount / 100.0, 2),
            order_currency=currency,
            customer_id=str(sess.id)[:36],
            customer_email=str(state.get("email") or ""),
            customer_phone=str(state.get("phone") or ""),
            notify_url=notify_url,
            return_url=return_url,
            order_note=f"booking session {sess.id}",
        )
    except CashfreeAPIError as exc:
        logger.warning("cashfree order failed org=%s: %s", org.id, type(exc).__name__)
        raise HTTPException(status_code=502, detail="Failed to create Cashfree order")
    cf_order_id = str(order.get("order_id") or "")
    payment_session_id = str(order.get("payment_session_id") or "")
    if not cf_order_id or not payment_session_id:
        logger.warning("cashfree order missing ids org=%s", org.id)
        raise HTTPException(status_code=502, detail="Failed to create Cashfree order")

    expires_at = datetime.now(timezone.utc) + timedelta(minutes=PAYMENT_WINDOW_MINUTES)
    attempt = PaymentAttempt(
        org_id=org.id,
        session_id=sess.id,
        provider="cashfree",
        provider_order_id=cf_order_id,
        amount_paise=amount,
        currency=currency,
        status="created",
        verification_status="pending",
        expires_at=expires_at,
    )
    db.add(attempt)
    # Extend slot lock for the payment window if a slot is locked.
    try:
        from app.models.booking import TimeSlot

        if isinstance(state, dict):
            locked_id = state.get("locked_slot_id")
            if locked_id:
                slot_res = await db.execute(
                    select(TimeSlot).where(TimeSlot.id == locked_id, TimeSlot.org_id == org.id)
                )
                slot = slot_res.scalars().first()
                if slot and slot.locked_until:
                    new_until = datetime.now(timezone.utc) + timedelta(minutes=SLOT_HOLD_MINUTES)
                    if slot.locked_until < new_until:
                        slot.locked_until = new_until
    except Exception as e:
        logger.warning("cashfree extend lock failed: %s", e)
    await db.commit()
    return CashfreeOrderResponse(
        order_id=cf_order_id,
        payment_session_id=payment_session_id,
        amount_paise=amount,
        currency=currency,
        environment=_environment(getattr(client, "api_base", None)),
        mock=False,
    )


def _event_key(event_type: str, data: dict, order_id: str) -> str:
    payment = data.get("payment") if isinstance(data, dict) else None
    cf_payment_id = ""
    if isinstance(payment, dict):
        cf_payment_id = str(payment.get("cf_payment_id") or "")
    if cf_payment_id:
        return f"cfpay_{cf_payment_id}"
    if event_type == "PAYMENT_LINK_EVENT":
        link_id = str((data or {}).get("link_id") or "")
        link_status = str((data or {}).get("link_status") or "")
        return f"link_{link_id}_{link_status}"
    return f"{event_type}:{order_id}"


def _parse_cashfree_body(raw: bytes) -> dict:
    """Parse a Cashfree webhook body tolerantly.

    Order webhooks arrive as JSON. Payment-link webhooks may arrive as
    urlencoded form data instead — normalize those to the same shape so
    downstream handling stays uniform. Signature verification always runs
    on the RAW body regardless of encoding.
    """
    try:
        import json as _json

        payload = _json.loads(raw.decode() or "{}")
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    try:
        from urllib.parse import parse_qsl

        pairs = parse_qsl(raw.decode() or "", keep_blank_values=True)
        if not pairs:
            return {}
        flat = {k: v for k, v in pairs}
        if len(flat) == 1:
            only = next(iter(flat.values()))
            try:
                import json as _json2

                maybe = _json2.loads(only)
                if isinstance(maybe, dict):
                    return maybe
            except Exception:
                pass
        link_id = str(flat.get("link_id") or "")
        if link_id:
            return {
                "type": str(flat.get("type") or "PAYMENT_LINK_EVENT"),
                "data": {
                    "link_id": link_id,
                    "link_status": flat.get("link_status") or "",
                    "link_amount_paid": flat.get("link_amount_paid") or "",
                    "link_currency": flat.get("link_currency") or "INR",
                    "order": {
                        "order_id": flat.get("order_id") or "",
                        "transaction_status": flat.get("transaction_status") or "",
                        "transaction_id": flat.get("transaction_id") or "",
                    },
                },
            }
        return {"data": flat, "type": str(flat.get("type") or "")}
    except Exception:
        return {}


async def _nudge_whatsapp_payment_received(db: AsyncSession, attempt) -> None:
    """Best-effort WhatsApp nudge after a verified payment. Never raises.

    The web widget reconciles via polling, but WhatsApp has no polling —
    without this the customer would never know the payment landed. Sent
    exactly once per attempt (callers invoke only on the fresh paid flip).
    """
    try:
        sid = getattr(attempt, "session_id", None)
        if not sid:
            return
        sess = (
            await db.execute(select(ChatSession).where(ChatSession.id == sid))
        ).scalars().first()
        if sess is None:
            return
        channel = str(getattr(sess, "channel", "") or "").lower()
        if channel not in ("whatsapp", "whatsapp_cloud"):
            return
        try:
            _amt = int(getattr(attempt, "amount_paise", 0) or 0)
        except (TypeError, ValueError):
            _amt = 0
        body = (
            f"Payment of ₹{(_amt / 100):g} received ✅ "
            "Reply CONFIRM here to complete your booking."
        )
        try:
            from app.crud import chat as _crud

            await _crud.save_message(db, str(sess.id), "agent", body)
            await db.commit()
        except Exception:
            try:
                await db.rollback()
            except Exception:
                pass
        state = sess.state_data if isinstance(getattr(sess, "state_data", None), dict) else {}
        import re as _re

        raw_phone = (
            str(state.get("phone") or "")
            or "".join(_re.findall(r"\d", str(getattr(sess, "channel_user_id", "") or "")))
        )
        digits = "".join(_re.findall(r"\d", raw_phone or ""))
        if len(digits) == 10 and digits[0] in "6789":
            digits = "91" + digits
        if channel == "whatsapp_cloud":
            if not digits:
                return
            try:
                from app.core import whatsapp_cloud as _cloud

                await _cloud.send_booking_reply(digits, {"type": "text", "body": body})
            except Exception as exc:
                logger.warning("whatsapp cloud payment nudge failed: %s", type(exc).__name__)
            return
        # Baileys/QR transport: proactive send via the wa-gateway sidecar.
        try:
            from app.models.whatsapp_connection import WhatsAppConnection
            from app.core import wa_gateway as _gw

            conn = (
                await db.execute(
                    select(WhatsAppConnection).where(
                        WhatsAppConnection.org_id == getattr(attempt, "org_id", None)
                    )
                )
            ).scalars().first()
            if conn is None or not digits:
                return
            await _gw.send_session_message(
                str(conn.id), digits, body, idem_key=f"paypaid-{attempt.id}"
            )
        except Exception as exc:
            logger.warning("whatsapp qr payment nudge failed: %s", type(exc).__name__)
    except Exception as exc:
        logger.warning("payment nudge failed: %s", type(exc).__name__)


@router.post("/widget/payment/cashfree/webhook")
async def cashfree_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    raw = await request.body()
    signature = request.headers.get("x-webhook-signature", "")
    timestamp = request.headers.get("x-webhook-timestamp", "")
    payload = _parse_cashfree_body(raw)
    event_type = str(payload.get("type") or "")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    order = data.get("order") if isinstance(data.get("order"), dict) else {}
    order_id = str(order.get("order_id") or "")

    # Payment-link events carry the merchant link_id instead of an order id.
    link_id = ""
    link_status = ""
    link_paid_paise = -1
    link_currency = "INR"
    link_txn_status = ""
    link_txn_id = ""
    link_order_id = ""
    if event_type == "PAYMENT_LINK_EVENT":
        link_id = str(data.get("link_id") or "")
        link_status = str(data.get("link_status") or "").upper()
        try:
            link_paid_paise = int(round(float(data.get("link_amount_paid") or 0) * 100))
        except (TypeError, ValueError):
            link_paid_paise = -1
        link_currency = str(data.get("link_currency") or "INR").upper()
        link_txn_status = str(order.get("transaction_status") or "").upper()
        link_txn_id = str(order.get("transaction_id") or order.get("cf_payment_id") or "")
        link_order_id = str(order.get("order_id") or "")

    # Resolve the signing secret: the org's own keys first (orders created
    # on tenant accounts are signed with the tenant secret), platform
    # fallback otherwise. Verification itself stays fail-closed.
    from app.services.cashfree.org_creds import resolve_cashfree_secret

    secret = settings.CASHFREE_SECRET_KEY or ""
    lookup_key = order_id or link_id
    if lookup_key:
        att_lookup = await db.execute(
            select(PaymentAttempt).where(PaymentAttempt.provider_order_id == lookup_key)
        )
        _att = att_lookup.scalars().first()
        if _att is not None and getattr(_att, "org_id", None):
            try:
                secret = await resolve_cashfree_secret(db, _att.org_id) or secret
            except Exception:
                pass
    if not verify_webhook_signature(signature, raw, timestamp, secret):
        # Fail closed: never process unverified events. Always 200 so
        # Cashfree does not retry a request we will never accept.
        return {"ok": False, "error": "invalid_signature"}

    key = _event_key(event_type, data, order_id)
    exists = await db.execute(
        select(CashfreeWebhookEvent).where(CashfreeWebhookEvent.event_id == key)
    )
    if exists.scalars().first():
        return {"ok": True, "deduplicated": True}
    try:
        db.add(CashfreeWebhookEvent(event_id=key, event_type=event_type, payload=payload, status="processed"))
        await db.flush()
    except Exception:
        await db.rollback()
        return {"ok": True, "deduplicated": True}

    if event_type == "PAYMENT_SUCCESS_WEBHOOK" and order_id:
        payment = data.get("payment") if isinstance(data.get("payment"), dict) else {}
        payment_status = str(payment.get("payment_status") or "")
        paid_amount = payment.get("payment_amount")
        try:
            paid_paise = int(round(float(paid_amount) * 100))
        except (TypeError, ValueError):
            paid_paise = -1
        att_res = await db.execute(
            select(PaymentAttempt).where(PaymentAttempt.provider_order_id == order_id)
        )
        att = att_res.scalars().first()
        if att and att.status != "paid":
            # Amount match: a smaller real payment must never confirm a booking.
            if paid_paise == int(att.amount_paise or 0) and payment_status == "SUCCESS":
                att.status = "paid"
                att.provider_payment_id = str(payment.get("cf_payment_id") or "") or None
                att.verification_status = "verified"
                att.webhook_payload = payload
                await db.commit()
                await _nudge_whatsapp_payment_received(db, att)
            else:
                logger.warning(
                    "cashfree webhook amount/status mismatch order=%s status=%s",
                    order_id, payment_status,
                )
                await db.commit()
        else:
            await db.commit()
    elif event_type == "PAYMENT_LINK_EVENT" and link_id:
        att_res = await db.execute(
            select(PaymentAttempt).where(PaymentAttempt.provider_order_id == link_id)
        )
        att = att_res.scalars().first()
        if att and att.status != "paid":
            status_ok = link_status == "PAID" or (
                link_status in ("PARTIALLY_PAID", "SUCCESS") and link_txn_status == "SUCCESS"
            )
            amount_ok = link_paid_paise == int(att.amount_paise or 0)
            currency_ok = link_currency == str(att.currency or "INR").upper()
            if status_ok and amount_ok and currency_ok:
                att.status = "paid"
                att.provider_payment_id = link_txn_id or link_order_id or None
                att.verification_status = "verified"
                att.webhook_payload = payload
                await db.commit()
                await _nudge_whatsapp_payment_received(db, att)
            else:
                logger.warning(
                    "cashfree link webhook mismatch link=%s status=%s paid=%s",
                    link_id, link_status, link_paid_paise,
                )
                await db.commit()
        else:
            await db.commit()
    else:
        await db.commit()
    return {"ok": True}


@router.get("/widget/payment/cashfree/status")
async def cashfree_order_status(
    order_id: str,
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Reconcile fallback for widget polling when the webhook is delayed."""
    try:
        require_cashfree_enabled()
    except CashfreeAPIError:
        raise HTTPException(status_code=503, detail="Cashfree payments are disabled")
    att_res = await db.execute(
        select(PaymentAttempt).where(PaymentAttempt.provider_order_id == order_id)
    )
    att = att_res.scalars().first()
    if not att:
        raise HTTPException(status_code=404, detail="Order not found")
    if str(att.session_id or "") != str(session_id or ""):
        raise HTTPException(status_code=403, detail="Forbidden")
    if att.status == "paid":
        return {"order_id": order_id, "status": att.status, "verification_status": att.verification_status}
    try:
        from app.services.cashfree.org_creds import resolve_cashfree_client

        client, _owned = await resolve_cashfree_client(db, att.org_id)
        remote = await client.fetch_order(order_id)
    except CashfreeAPIError:
        raise HTTPException(status_code=502, detail="Failed to fetch order status")
    remote_status = str(remote.get("order_status") or "").upper()
    if remote_status == "PAID" and att.status != "paid":
        payments = []
        try:
            plist = await client.fetch_order_payments(order_id)
            if isinstance(plist, dict) and isinstance(plist.get("payments"), list):
                payments = plist["payments"]
            elif isinstance(plist, list):
                payments = plist
        except CashfreeAPIError:
            payments = []
        matched = None
        for p in payments:
            if not isinstance(p, dict):
                continue
            if str(p.get("payment_status") or "").upper() != "SUCCESS":
                continue
            try:
                if int(round(float(p.get("payment_amount", 0)) * 100)) == int(att.amount_paise or 0):
                    matched = p
                    break
            except (TypeError, ValueError):
                continue
        if matched is not None:
            att.status = "paid"
            att.provider_payment_id = str(matched.get("cf_payment_id") or "") or None
            att.verification_status = "verified"
            await db.commit()
            return {"order_id": order_id, "status": "paid", "verification_status": "verified"}
        # Fallback: Cashfree marks the ORDER itself PAID (server-to-server,
        # authenticated) but the payments list is not populated yet (sandbox
        # lag). Order-level amount+currency match is still verified proof —
        # never trust user-supplied success, only this API response.
        try:
            remote_paise = int(round(float(remote.get("order_amount", 0)) * 100))
        except (TypeError, ValueError):
            remote_paise = -1
        remote_currency = str(remote.get("order_currency") or "INR").upper()
        if (
            remote_paise == int(att.amount_paise or 0)
            and remote_currency == str(att.currency or "INR").upper()
        ):
            att.status = "paid"
            att.verification_status = "verified"
            await db.commit()
            return {"order_id": order_id, "status": "paid", "verification_status": "verified"}
        logger.warning("cashfree reconcile amount mismatch order=%s", order_id)
    return {"order_id": order_id, "status": att.status, "verification_status": att.verification_status}
