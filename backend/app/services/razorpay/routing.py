"""Payment route classification: LEGACY vs ROUTE (pure logic + DB loader).

Rules (no silent fallback):
- Global kill switch off ........................... LEGACY
- payment_mode == LEGACY .......................... LEGACY (always)
- payment_mode == ROUTE + TRANSFER_READY .......... ROUTE
- payment_mode == ROUTE + not ready ............... raise ProviderPayoutNotReady
- payment_mode == AUTO + opted-in AND ready ....... ROUTE
- payment_mode == AUTO + otherwise ................ LEGACY

"AUTO opted in" means the org has a linked-account row that reached
TRANSFER_READY (explicit onboarding completion). A missing row, or any
non-ready state, stays on the legacy sub-merchant flow.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.razorpay_route import (
    PaymentRouteMode,
    RazorpayLinkedAccount,
    RouteOnboardingStatus,
)
from app.services.razorpay.onboarding import is_transfer_ready

logger = logging.getLogger(__name__)

LEGACY = "LEGACY"
ROUTE = "ROUTE"

PROVIDER_PAYOUT_NOT_READY = "provider_payout_not_ready"


class ProviderPayoutNotReady(Exception):
    """Explicit ROUTE org is not TRANSFER_READY. Caller must reject the order."""

    code = PROVIDER_PAYOUT_NOT_READY

    def __init__(self, message: str = "Provider payout onboarding is not complete") -> None:
        super().__init__(message)


@dataclass(frozen=True)
class OrgRouteContext:
    org_id: object
    payment_mode: str
    onboarding_status: str
    route_product_status: Optional[str]
    razorpay_account_id: Optional[str]
    commission_bps: Optional[int]

    @property
    def transfer_ready(self) -> bool:
        return is_transfer_ready(self.onboarding_status, self.route_product_status)


def resolve_payment_route(
    *,
    route_enabled: bool,
    payment_mode: str,
    onboarding_status: str,
    route_product_status: Optional[str],
) -> str:
    """Pure classification. Raises ProviderPayoutNotReady for explicit ROUTE gaps."""
    if not route_enabled:
        return LEGACY
    mode = (payment_mode or PaymentRouteMode.AUTO).upper()
    ready = is_transfer_ready(onboarding_status, route_product_status)
    if mode == PaymentRouteMode.LEGACY:
        return LEGACY
    if mode == PaymentRouteMode.ROUTE:
        if not ready:
            raise ProviderPayoutNotReady(
                f"Organization selected ROUTE payouts but onboarding is {onboarding_status} "
                "(transfer requires TRANSFER_READY)"
            )
        return ROUTE
    # AUTO: Route only when explicitly onboarded AND ready; else legacy.
    if ready and onboarding_status == RouteOnboardingStatus.TRANSFER_READY:
        return ROUTE
    return LEGACY


async def get_org_route_context(db: AsyncSession, org_id: object) -> OrgRouteContext:
    """Load the org's routing context (linked row or NOT_STARTED default)."""
    res = await db.execute(select(RazorpayLinkedAccount).where(RazorpayLinkedAccount.org_id == org_id))  # type: ignore[arg-type]
    row = res.scalars().first()
    if row is None:
        return OrgRouteContext(
            org_id=org_id,
            payment_mode=PaymentRouteMode.AUTO,
            onboarding_status=RouteOnboardingStatus.NOT_STARTED,
            route_product_status=None,
            razorpay_account_id=None,
            commission_bps=None,
        )
    return OrgRouteContext(
        org_id=org_id,
        payment_mode=row.payment_mode or PaymentRouteMode.AUTO,
        onboarding_status=row.onboarding_status or RouteOnboardingStatus.NOT_STARTED,
        route_product_status=row.route_product_status,
        razorpay_account_id=row.razorpay_account_id,
        commission_bps=row.commission_bps,
    )
