"""Route onboarding state machine (pure functions — no DB, no HTTP).

Single source of truth for deriving ``RazorpayLinkedAccount.onboarding_status``
from persisted fields + live Razorpay product/account state. Reused by the
onboarding endpoints, payment routing, settlement orchestration, and webhooks
so every layer agrees on what TRANSFER_READY means.
"""

from typing import Optional

from app.models.razorpay_route import RouteOnboardingStatus as S
from app.models.razorpay_route import RouteProductStatus as P


def is_transfer_ready(onboarding_status: Optional[str], route_product_status: Optional[str]) -> bool:
    """Transfer-ready = explicit state AND Razorpay product activated."""
    return onboarding_status == S.TRANSFER_READY and route_product_status == P.ACTIVATED


def derive_onboarding_status(
    *,
    razorpay_account_id: Optional[str] = None,
    razorpay_account_status: Optional[str] = None,
    stakeholder_id: Optional[str] = None,
    route_product_id: Optional[str] = None,
    route_product_status: Optional[str] = None,
    bank_last4: Optional[str] = None,
    failure_reason: Optional[str] = None,
) -> str:
    """Derive the next onboarding status. Suspended/failure short-circuit first."""
    if (razorpay_account_status or "").lower() == "suspended" or route_product_status == P.SUSPENDED:
        return S.SUSPENDED
    if failure_reason and not razorpay_account_id:
        return S.FAILED
    if not razorpay_account_id:
        return S.FAILED if failure_reason else S.NOT_STARTED
    if route_product_status == P.ACTIVATED:
        return S.TRANSFER_READY
    if route_product_status in (P.UNDER_REVIEW, P.REQUESTED):
        return S.UNDER_REVIEW
    if route_product_status == P.NEEDS_CLARIFICATION:
        # Clarification needed: bank details missing/flagged until submitted.
        return S.BANK_DETAILS_PENDING if not bank_last4 else S.UNDER_REVIEW
    if route_product_id:
        return S.BANK_DETAILS_PENDING
    if stakeholder_id:
        return S.PRODUCT_PENDING
    return S.STAKEHOLDER_PENDING


def build_reference_id(org_id: object) -> str:
    """Stable per-org Razorpay reference_id (<=20 chars per API constraints)."""
    hexpart = str(org_id).replace("-", "")[:13]
    return f"aurexa_{hexpart}"


def frontend_message(onboarding_status: str) -> str:
    """Human-readable payout status for the provider UI (backend-driven)."""
    return {
        S.NOT_STARTED: "Connect bank account",
        S.ACCOUNT_CREATED: "Account created — completing setup",
        S.STAKEHOLDER_PENDING: "Business details pending",
        S.PRODUCT_PENDING: "Payout product pending",
        S.BANK_DETAILS_PENDING: "Connect bank account",
        S.UNDER_REVIEW: "Bank account verification in progress",
        S.ACTIVE: "Bank account connected",
        S.TRANSFER_READY: "Ready to receive payments",
        S.SUSPENDED: "Payouts suspended — action required",
        S.FAILED: "Verification failed — action required",
    }.get(onboarding_status, "Payout status unavailable")
