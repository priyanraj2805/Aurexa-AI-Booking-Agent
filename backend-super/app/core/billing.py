"""Billing provider abstraction (Phase 5).

Control plane never talks to a PSP directly from endpoints: every external
side-effect goes through a BillingProvider. Two implementations:

  - MockProvider   — deterministic dev/test provider; no external calls.
  - StripeProvider — real Stripe SDK calls, only used when a
                     STRIPE_SECRET_KEY is configured.

get_provider() picks Stripe when a key is present, otherwise Mock.

execute_refund() is the single refund path shared by the /billing endpoints
and the two-person approval executor (approvals.py): it marks the invoice
refunded in the DB and records the provider result.
"""
import logging
import uuid as uuid_mod
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

logger = logging.getLogger(__name__)


class BillingProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def create_subscription(self, org_id: str, plan_code: str) -> dict:
        """Provision the subscription externally; returns provider IDs."""

    @abstractmethod
    async def retry_invoice(self, provider_invoice_id: str) -> dict:
        """Attempt payment for an open invoice; returns {'paid': bool}."""

    @abstractmethod
    async def refund_invoice(
        self, provider_invoice_id: str, amount_cents: int, reason: str
    ) -> dict:
        """Refund a paid invoice; returns {'refund_id': str}."""


class MockProvider(BillingProvider):
    name = "mock"

    async def create_subscription(self, org_id: str, plan_code: str) -> dict:
        return {
            "provider_customer_id": f"cus_mock_{org_id[:8]}",
            "provider_sub_id": f"sub_mock_{uuid_mod.uuid4().hex[:12]}",
        }

    async def retry_invoice(self, provider_invoice_id: str) -> dict:
        return {"paid": True, "payment_intent": f"pi_mock_{provider_invoice_id[:8]}"}

    async def refund_invoice(
        self, provider_invoice_id: str, amount_cents: int, reason: str
    ) -> dict:
        return {"refund_id": f"re_mock_{provider_invoice_id[:8]}"}


class StripeProvider(BillingProvider):
    name = "stripe"

    def _stripe(self):
        import stripe

        stripe.api_key = settings.STRIPE_SECRET_KEY
        return stripe

    async def create_subscription(self, org_id: str, plan_code: str) -> dict:
        stripe = self._stripe()
        sub = stripe.Subscription.create(
            customer=stripe.Customer.create(metadata={"org_id": org_id}).id,
            items=[{"price": plan_code}],
        )
        return {"provider_customer_id": sub.customer, "provider_sub_id": sub.id}

    async def retry_invoice(self, provider_invoice_id: str) -> dict:
        stripe = self._stripe()
        invoice = stripe.Invoice.pay(provider_invoice_id)
        return {"paid": invoice.status == "paid"}

    async def refund_invoice(
        self, provider_invoice_id: str, amount_cents: int, reason: str
    ) -> dict:
        stripe = self._stripe()
        refund = stripe.Refund.create(
            payment_intent=provider_invoice_id,
            amount=amount_cents,
            reason=reason,
        )
        return {"refund_id": refund.id}


def get_provider() -> BillingProvider:
    if settings.STRIPE_SECRET_KEY:
        return StripeProvider()
    return MockProvider()


async def execute_refund(
    db: AsyncSession,
    invoice_id,
    amount_cents: int,
    reason: str,
) -> bool:
    """Refund a paid invoice. Returns True on success (row updated to
    refunded). Shared by /billing endpoints and the approval executor."""
    row = (
        await db.execute(
            text(
                """
                SELECT id, provider, provider_invoice_id, amount_cents, status
                FROM super_admin.invoices WHERE id = :invoice_id
                """
            ),
            {"invoice_id": invoice_id},
        )
    ).mappings().first()
    if not row or row["status"] != "paid":
        return False

    try:
        provider = get_provider()
        result = await provider.refund_invoice(
            row["provider_invoice_id"], amount_cents, reason
        )
    except Exception:
        logger.exception("provider refund failed invoice=%s", invoice_id)
        return False

    await db.execute(
        text(
            """
            UPDATE super_admin.invoices
            SET status = 'refunded', refunded_at = now(),
                refund_amount_cents = :amount
            WHERE id = :invoice_id
            """
        ),
        {"invoice_id": invoice_id, "amount": amount_cents},
    )
    await db.commit()
    logger.info(
        "refund executed invoice=%s amount=%s refund_id=%s",
        invoice_id, amount_cents, result.get("refund_id"),
    )
    return True
