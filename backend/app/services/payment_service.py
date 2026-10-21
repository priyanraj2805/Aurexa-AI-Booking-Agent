"""
Centralized payment ruleset — single source of truth for ALL payment providers.

Guiding rule: no provider-specific logic should live in endpoint files or
cancel_flow. Everything provider-agnostic (charge resolution, refund
eligibility, refund execution, readiness gating) lives here. Provider
clients (Cashfree/Razorpay) are only used as thin HTTP adapters behind
this service.

Usage:
    from app.services.payment_service import payment_service

    amount, currency = await payment_service.resolve_charge(db, org, cfg, unit_id, provider_id)
    eligible, days = payment_service.is_refund_eligible(local_date, org_config)
    result = await payment_service.process_refund(db, org, attempt, booking_id)
    ready, provider = await payment_service.is_usable(db, org, org_config)
"""
import logging
from datetime import datetime, timezone
from typing import Any, Optional, Tuple

from sqlalchemy import delete as _delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select as _sel

from app.core.config import settings
from app.models.config import OrgConfig
from app.models.organization import Organization
from app.models.payment import PaymentAttempt

logger = logging.getLogger(__name__)


class PaymentService:
    """Centralized payment rules — provider-agnostic."""

    # ── Charge resolution ─────────────────────────────────────────────
    async def resolve_charge(
        self,
        db: AsyncSession,
        org_id: Any,
        cfg: OrgConfig,
        unit_id: Any = None,
        provider_id: Any = None,
    ) -> Tuple[int, str]:
        """
        Return (amount_paise, currency) for a booking.

        Priority:
          1. Catalog fee for (unit + provider) — tenant sets per dept/member
          2. Catalog fee for provider-only
          3. Catalog fee for unit-only
          4. Flat org price (appointment_price_paise) — legacy fallback
        """
        from app.services.catalog_service import resolve_effective_fee

        currency = (getattr(cfg, "appointment_currency", "INR") or "INR").upper()
        try:
            fee_paise, fee_cur = await resolve_effective_fee(db, org_id, unit_id, provider_id)
        except Exception:
            fee_paise, fee_cur = None, None
        if fee_paise:
            return int(fee_paise), (fee_cur or currency).upper()
        flat = int(getattr(cfg, "appointment_price_paise", 0) or 0)
        return flat, currency

    # ── Refund eligibility ────────────────────────────────────────────
    def is_refund_eligible(self, local_date: str, org_config: OrgConfig) -> Tuple[bool, int]:
        """
        (eligible, days_before).

        Full refund only when cancelled >= 1 day before appointment date
        (org timezone). Same-day (0 days) or past dates -> not eligible.
        """
        try:
            from zoneinfo import ZoneInfo

            tz_name = getattr(org_config, "timezone", None) or None
            tz = ZoneInfo(tz_name) if tz_name else None
            today = datetime.now(tz).date() if tz is not None else datetime.now(timezone.utc).date()
            appt = datetime.strptime(str(local_date or "").strip(), "%Y-%m-%d").date()
            days = (appt - today).days
            return days >= 1, days
        except Exception:
            return False, -999

    # ── Refund execution (provider-agnostic) ──────────────────────────
    async def process_refund(
        self,
        db: AsyncSession,
        org: Organization,
        attempt: PaymentAttempt,
        booking_id: str,
        session_id: str,
    ) -> dict:
        """
        Refund a paid booking attempt. Provider-agnostic — only Cashfree
        is currently supported for auto-refund; Razorpay returns a
        manual-refund reason.

        Returns dict with keys: refunded (bool), amount_display (str),
        eligible (bool), reason (str).
        """
        out: dict = {
            "refunded": False,
            "amount_display": "",
            "eligible": False,
            "reason": "",
        }
        provider = str(getattr(attempt, "provider", "razorpay") or "razorpay").lower()

        try:
            amount_paise = int(getattr(attempt, "amount_paise", 0) or 0)
            currency = str(getattr(attempt, "currency", "INR") or "INR").upper()
            out["amount_display"] = (
                f"₹{amount_paise / 100:g}" if currency == "INR" else f"{currency} {amount_paise / 100:,.2f}"
            )
        except Exception:
            pass

        # Resolve order id (provider-agnostic column names)
        order_id = (
            getattr(attempt, "provider_order_id", None)
            or getattr(attempt, "razorpay_order_id", None)
            or ""
        )
        if not order_id:
            out["reason"] = "no provider order id on attempt"
            return out

        if provider != "cashfree":
            out["reason"] = "non-cashfree payment — manual refund"
            logger.info("auto-refund skipped (non-cashfree provider) booking=%s", booking_id)
            return out

        # Idempotency: skip when Cashfree already lists a successful refund.
        from app.services.cashfree.client import CashfreeAPIError, CashfreeClient
        from app.services.cashfree.org_creds import resolve_cashfree_client

        try:
            client, _owned = await resolve_cashfree_client(db, org.id)
        except CashfreeAPIError as exc:
            out["reason"] = f"cashfree unavailable: {exc}"
            return out

        try:
            existing = await client.fetch_refunds(order_id)
            rlist = []
            if isinstance(existing, dict) and isinstance(existing.get("refunds"), list):
                rlist = existing["refunds"]
            elif isinstance(existing, list):
                rlist = existing
            for r in rlist:
                if isinstance(r, dict) and str(r.get("refund_status") or "").upper() == "SUCCESS":
                    attempt.refund_id = str(r.get("cf_refund_id") or r.get("refund_id") or attempt.refund_id or "")
                    attempt.refund_status = "succeeded"
                    try:
                        attempt.refunded_at = datetime.now(timezone.utc)
                    except Exception:
                        pass
                    await db.commit()
                    out["refunded"] = True
                    out["reason"] = "already refunded on cashfree"
                    return out
        except Exception as exc:
            logger.warning("refund pre-check failed booking=%s: %s", booking_id, exc)

        refund_id = f"rf_{str(booking_id).replace('-', '')[:8]}_{int(__import__('time').time())}"
        try:
            resp = await client.refund(
                order_id=order_id,
                refund_amount=round(amount_paise / 100.0, 2),
                refund_id=refund_id,
                refund_note=f"auto refund on cancellation {booking_id}",
            )
        except Exception as exc:
            logger.warning("cashfree refund call failed booking=%s: %s", booking_id, exc)
            try:
                attempt.refund_id = refund_id
                attempt.refund_status = "failed"
                await db.commit()
            except Exception:
                pass
            out["reason"] = "refund call failed"
            return out

        try:
            cf_id = ""
            if isinstance(resp, dict):
                cf_id = str(resp.get("cf_refund_id") or resp.get("refund_id") or "")
            attempt.refund_id = cf_id or refund_id
            attempt.refund_status = "succeeded"
            try:
                attempt.refunded_at = datetime.now(timezone.utc)
            except Exception:
                pass
            await db.commit()
        except Exception:
            pass
        out["refunded"] = True
        out["reason"] = "refunded"
        return out

    # ── WhatsApp payment links ────────────────────────────────────────
    @staticmethod
    def _session_state(session) -> dict:
        """Read-only view of collected customer state (ORM or state object)."""
        state = getattr(session, "state_data", None)
        if not isinstance(state, dict):
            state = getattr(session, "collected_data", None)
        return state if isinstance(state, dict) else {}

    @staticmethod
    def _digits(raw: Any) -> str:
        return "".join(c for c in str(raw or "") if c.isdigit())

    def _notify_url(self) -> str:
        base = (settings.PUBLIC_API_BASE or "").rstrip("/")
        if base.endswith("/api/v1"):
            base = base[: -len("/api/v1")]
        return base + "/api/v1/widget/payment/cashfree/webhook" if base else ""

    async def ensure_whatsapp_payment_link(
        self,
        db: AsyncSession,
        org: Organization,
        cfg: OrgConfig,
        session,
        amount_paise: int,
        currency: str,
    ) -> Optional[str]:
        """Get-or-create a Cashfree payment link for a WhatsApp checkout.

        Only for cashfree-provider orgs on WhatsApp channels. Reuses the
        live link of an existing unexpired `created` attempt for the same
        session+amount so re-surfaced payment prompts don't mint new links.
        Returns the link_url, or None when unavailable. Never raises.
        """
        try:
            from app.services.cashfree.org_creds import resolve_cashfree_client

            ready, provider = await self.is_usable(db, org.id, cfg)
            if not ready or provider != "cashfree":
                return None
            import uuid as _uuid_mod

            try:
                sid_raw = str(getattr(session, "session_id", None) or getattr(session, "id", ""))
                sid = _uuid_mod.UUID(sid_raw)
            except Exception:
                return None
            if not amount_paise or amount_paise <= 0:
                return None
            now = datetime.now(timezone.utc)
            try:
                existing = (
                    await db.execute(
                        _sel(PaymentAttempt).where(
                            PaymentAttempt.session_id == sid,
                            PaymentAttempt.org_id == org.id,
                            PaymentAttempt.status == "created",
                            PaymentAttempt.provider == "cashfree",
                            PaymentAttempt.amount_paise == int(amount_paise),
                        )
                    )
                ).scalars().first()
            except Exception:
                existing = None
            _exp = getattr(existing, "expires_at", None) if existing is not None else None
            if _exp is not None and _exp.tzinfo is None:
                # SQLite returns naive datetimes; treat as UTC for comparison.
                _exp = _exp.replace(tzinfo=timezone.utc)
            if (
                existing is not None
                and getattr(existing, "checkout_url", None)
                and _exp is not None
                and _exp > now
            ):
                return str(existing.checkout_url)

            state = self._session_state(session)
            phone = self._digits(state.get("phone") or "")
            email = str(state.get("email") or "").strip()
            name = str(state.get("name") or "").strip()
            link_id = (
                f"cfl_{str(org.id).replace('-', '')[:8]}"
                f"_{str(sid).replace('-', '')[:8]}"
                f"_{int(now.timestamp())}"
            )[:50]
            expiry = now + __import__("datetime").timedelta(minutes=30)
            client, _owned = await resolve_cashfree_client(db, org.id)
            resp = await client.create_payment_link(
                link_id=link_id,
                link_amount=round(int(amount_paise) / 100.0, 2),
                link_currency=(currency or "INR").upper(),
                link_purpose="Appointment booking payment",
                customer_phone=phone,
                customer_email=email,
                customer_name=name,
                notify_url=self._notify_url(),
                link_expiry_time=expiry.isoformat(),
                upi_intent=True,
                link_notes={"org_id": str(org.id)[:36], "session_id": str(sid)[:36]},
            )
            link_url = str(resp.get("link_url") or "")
            if not link_url:
                return None
            attempt = PaymentAttempt(
                org_id=org.id,
                session_id=sid,
                provider="cashfree",
                provider_order_id=link_id,
                checkout_url=link_url,
                amount_paise=int(amount_paise),
                currency=(currency or "INR").upper(),
                status="created",
                verification_status="pending",
                expires_at=expiry,
            )
            db.add(attempt)
            await db.commit()
            return link_url
        except Exception as exc:
            logger.warning("whatsapp payment link failed org=%s: %s", getattr(org, "id", "?"), type(exc).__name__)
            try:
                await db.rollback()
            except Exception:
                pass
            return None

    async def attach_whatsapp_payment_link(
        self,
        db: AsyncSession,
        org: Organization,
        cfg: OrgConfig,
        chat_session,
        action,
    ):
        """Append a Cashfree payment link to a REQUIRE_PAYMENT reply on WhatsApp.

        No-op for non-payment directives, non-WhatsApp channels, and
        non-cashfree providers — the web widget keeps its JS checkout.
        Never raises; returns the (possibly modified) action.
        """
        try:
            if (getattr(action, "widget_directive", "") or "") != "REQUIRE_PAYMENT":
                return action
            channel = str(getattr(chat_session, "channel", "") or "").lower()
            if channel not in ("whatsapp", "whatsapp_cloud"):
                return action
            meta = getattr(action, "field_meta", None) or {}
            try:
                amount = int(meta.get("amount_paise") or 0)
            except (TypeError, ValueError):
                amount = 0
            currency = str(meta.get("currency") or "INR")
            if amount <= 0:
                return action
            link_url = await self.ensure_whatsapp_payment_link(
                db, org, cfg, chat_session, amount, currency
            )
            if not link_url:
                return action
            body = str(getattr(action, "message", "") or "")
            link_block = (
                f"\n\nPay securely here:\n{link_url}\n"
                "Link valid for 30 minutes. After paying, reply DONE here."
            )
            if link_url not in body:
                action.message = (body + link_block) if body else link_block.strip()
            try:
                fm = dict(meta) if isinstance(meta, dict) else {}
                fm["payment_link_url"] = link_url
                action.field_meta = fm
            except Exception:
                pass
            return action
        except Exception as exc:
            logger.warning("attach whatsapp payment link failed: %s", type(exc).__name__)
            return action

    # ── Readiness gate ────────────────────────────────────────────────
    async def is_usable(
        self,
        db: AsyncSession,
        org_id: Any,
        org_config: OrgConfig,
    ) -> Tuple[bool, str]:
        """
        (payments_ready, provider).

        Cashfree orgs: gated on own credentials (kill switch + own keys or
        platform fallback). Razorpay orgs: gated on Razorpay Route
        TRANSFER_READY. Fail closed either way.
        """
        provider = (getattr(org_config, "payment_provider", "razorpay") or "razorpay").lower()
        try:
            if provider == "cashfree":
                from app.services.cashfree.org_creds import is_cashfree_usable

                return bool(await is_cashfree_usable(db, org_id)), provider
            from app.services.razorpay.routing import get_org_route_context

            return bool((await get_org_route_context(db, org_id)).transfer_ready), provider
        except Exception:
            return False, provider


# Singleton instance — import this everywhere instead of inline logic.
payment_service = PaymentService()
