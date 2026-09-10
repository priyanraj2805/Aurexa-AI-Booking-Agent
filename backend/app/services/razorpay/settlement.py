"""Idempotent settlement orchestration: settle_payment() -> ensure_transfer().

Single convergence point for BOTH callers:
- POST /widget/payment/verify (after existing amount/order verification)
- payment.captured webhook (after existing amount verification)

Safety properties:
- verify twice / webhook twice / webhook-before-verify / verify-before-webhook /
  concurrent callers: all converge on the same PaymentAttempt row (UNIQUE
  razorpay_order_id) and the same RazorpayTransfer row (UNIQUE
  razorpay_payment_id + UNIQUE idempotency_key). Re-runs are read-only no-ops.
- Timeout after Razorpay accepted: reconcile via GET /v1/payments/{id}/transfers
  BEFORE any retry. Never blindly re-POST.
- Money math (gross/commission/provider paise + idempotency_key) is persisted
  BEFORE the first Razorpay transfer call. Retries recompute and abort on
  mismatch instead of re-sending.
- Transfer failures never fail the customer payment response: the payment stays
  paid; the transfer row records the failure for retry/alerting.

This module never reads OrgConfig OAuth tokens and never logs secrets, bank
numbers, or KYC data. Correlation IDs only.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.config import settings
from app.models.payment import PaymentAttempt
from app.models.razorpay_route import RazorpayTransfer, RouteTransferStatus
from app.services.razorpay import route as route_svc
from app.services.razorpay.client import RouteAPIError, RouteClient, is_route_enabled
from app.services.razorpay.commission import build_idempotency_key, calculate_split
from app.services.razorpay.routing import ROUTE, get_org_route_context

logger = logging.getLogger(__name__)

# Razorpay transfer entity statuses (docs: fetch-transfers-payment).
_RZP_FINAL_PROCESSED = "processed"
_RZP_FAILED = "failed"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _map_rzp_status(rzp_status: Optional[str]) -> str:
    if rzp_status == _RZP_FINAL_PROCESSED:
        return RouteTransferStatus.PROCESSED
    if rzp_status == _RZP_FAILED:
        return RouteTransferStatus.FAILED
    return RouteTransferStatus.PROCESSING


def _snapshot_transfer(row: RazorpayTransfer) -> Dict[str, Any]:
    return {
        "id": str(row.id),
        "status": row.status,
        "gross_paise": row.gross_paise,
        "commission_paise": row.commission_paise,
        "provider_paise": row.provider_paise,
        "currency": row.currency,
        "razorpay_transfer_id": row.razorpay_transfer_id,
        "failure_reason": row.failure_reason,
    }


async def _adopt_rzp_transfer(
    db: AsyncSession, row: RazorpayTransfer, item: Dict[str, Any]
) -> RazorpayTransfer:
    """Adopt a Razorpay-side transfer found during reconciliation."""
    trf_id = item.get("id")
    if trf_id and not row.razorpay_transfer_id:
        row.razorpay_transfer_id = str(trf_id)[:32]
    row.status = _map_rzp_status(item.get("status"))
    if row.status == RouteTransferStatus.PROCESSED:
        row.processed_at = row.processed_at or _now()
    if row.status == RouteTransferStatus.FAILED:
        err = item.get("error") or {}
        row.failure_reason = str(err.get("description") or err.get("reason") or "transfer failed")[:500]
    await db.commit()
    await db.refresh(row)
    logger.info(
        "route settle: reconciled transfer payment=%s status=%s",
        str(row.razorpay_payment_id)[:16],
        row.status,
    )
    return row


async def ensure_transfer(
    db: AsyncSession,
    attempt: PaymentAttempt,
    *,
    client: Optional[RouteClient] = None,
) -> Dict[str, Any]:
    """Ensure exactly one provider transfer for a paid ROUTE attempt.

    Returns {"outcome": ...} where outcome is one of:
    not_route | awaiting_readiness | existing | created | reconciled |
    failed | error
    """
    org_id = attempt.org_id
    payment_id = attempt.razorpay_payment_id or ""

    if (attempt.route_mode or "LEGACY") != ROUTE:
        return {"outcome": "not_route"}
    if not payment_id:
        return {"outcome": "error", "reason": "attempt has no razorpay_payment_id"}
    if (attempt.currency or "INR") != "INR":
        return {"outcome": "error", "reason": "Route transfers support INR only"}

    ctx = await get_org_route_context(db, org_id)
    if not ctx.transfer_ready or not ctx.razorpay_account_id:
        # No gate change: customer payment stands; funds wait on the platform
        # until the provider completes onboarding (explicit business-model
        # choice deferred — see architecture doc).
        logger.info("route settle: org not transfer-ready, parking payment=%s", payment_id[:16])
        return {"outcome": "awaiting_readiness", "onboarding_status": ctx.onboarding_status}

    # 1. Existing transfer? Return it — never create another.
    res = await db.execute(
        select(RazorpayTransfer).where(RazorpayTransfer.razorpay_payment_id == payment_id)
    )
    row = res.scalars().first()
    if row is not None and row.razorpay_transfer_id:
        return {"outcome": "existing", "transfer": _snapshot_transfer(row)}

    # 2. Money math first (persisted before any Razorpay call).
    bps = ctx.commission_bps if ctx.commission_bps is not None else settings.RAZORPAY_ROUTE_COMMISSION_BPS
    try:
        commission, provider = calculate_split(int(attempt.amount_paise), int(bps))
    except ValueError as exc:
        return {"outcome": "error", "reason": str(exc)}
    idem_key = build_idempotency_key(str(org_id), payment_id, provider)

    if row is None:
        row = RazorpayTransfer(
            org_id=org_id,
            booking_id=attempt.booking_id,
            payment_attempt_id=attempt.id,
            razorpay_order_id=attempt.razorpay_order_id,
            razorpay_payment_id=payment_id,
            razorpay_account_id=ctx.razorpay_account_id,
            gross_paise=int(attempt.amount_paise),
            commission_paise=commission,
            provider_paise=provider,
            currency="INR",
            status=RouteTransferStatus.PENDING,
            idempotency_key=idem_key,
        )
        db.add(row)
        try:
            await db.flush()
        except IntegrityError:
            # Concurrent caller won the insert — use the winner's row.
            await db.rollback()
            res = await db.execute(
                select(RazorpayTransfer).where(RazorpayTransfer.razorpay_payment_id == payment_id)
            )
            row = res.scalars().first()
            if row is not None and row.razorpay_transfer_id:
                logger.info("route settle: duplicate prevented payment=%s", payment_id[:16])
                return {"outcome": "existing", "transfer": _snapshot_transfer(row)}
            if row is None:  # pragma: no cover — defensive
                return {"outcome": "error", "reason": "transfer insert conflict without winner"}
    else:
        # Retry of a row that never got a trf id: recompute must match exactly.
        if (row.gross_paise, row.commission_paise, row.provider_paise) != (
            int(attempt.amount_paise),
            commission,
            provider,
        ):
            logger.warning("route settle: money mismatch, aborting payment=%s", payment_id[:16])
            return {"outcome": "error", "reason": "persisted money snapshot mismatch — aborting"}

    rzp = client or RouteClient.from_settings()

    # 3. Reconcile BEFORE sending (covers timeout-retry + webhook races).
    try:
        existing = await route_svc.fetch_transfers_for_payment(rzp, payment_id)
    except RouteAPIError as exc:
        await db.commit()  # persist the pending row for a later retry
        return {"outcome": "failed", "reason": f"reconcile fetch failed: {exc}"}
    found = route_svc.find_transfer_for_account(existing, ctx.razorpay_account_id)
    if found is not None:
        row = await _adopt_rzp_transfer(db, row, found)
        return {"outcome": "reconciled", "transfer": _snapshot_transfer(row)}

    # 4. Send exactly once per (payment, account, amount).
    try:
        created = await route_svc.create_transfer_from_payment(
            rzp,
            payment_id,
            account_id=ctx.razorpay_account_id,
            amount_paise=provider,
            currency="INR",
            notes={"org_id": str(org_id), "booking_id": str(attempt.booking_id or "")},
        )
    except RouteAPIError as exc:
        # Ambiguous (possible timeout): do NOT mark terminal. Reconcile on retry.
        row.failure_reason = f"transfer request failed, reconcile on retry: {exc}"[:500]
        await db.commit()
        logger.warning("route settle: transfer send failed payment=%s", payment_id[:16])
        return {"outcome": "failed", "reason": str(exc)[:200], "transfer": _snapshot_transfer(row)}

    items = created.get("items") or []
    item = route_svc.find_transfer_for_account(items, ctx.razorpay_account_id) or (items[0] if items else None)
    if item is None:
        row.failure_reason = "Razorpay returned no transfer items"
        await db.commit()
        return {"outcome": "failed", "reason": row.failure_reason, "transfer": _snapshot_transfer(row)}
    if item.get("recipient") and item.get("recipient") != ctx.razorpay_account_id:
        row.failure_reason = "Razorpay transfer recipient mismatch — aborting"
        await db.commit()
        logger.warning("route settle: recipient mismatch payment=%s", payment_id[:16])
        return {"outcome": "failed", "reason": row.failure_reason, "transfer": _snapshot_transfer(row)}

    row = await _adopt_rzp_transfer(db, row, item)
    logger.info(
        "route settle: transfer created payment=%s provider_paise=%s",
        payment_id[:16],
        provider,
    )
    return {"outcome": "created", "transfer": _snapshot_transfer(row)}


async def settle_payment(
    db: AsyncSession,
    *,
    org_id: object,
    razorpay_order_id: str,
    razorpay_payment_id: str,
    client: Optional[RouteClient] = None,
) -> Dict[str, Any]:
    """Converged settlement entry point (verify endpoint + webhook).

    Idempotently marks the attempt paid, then delegates to ensure_transfer().
    Never raises for transfer problems — the customer payment outcome is
    independent of payout progress.
    """
    res = await db.execute(
        select(PaymentAttempt).where(
            PaymentAttempt.razorpay_order_id == razorpay_order_id,
            PaymentAttempt.org_id == org_id,  # type: ignore[arg-type]
        )
    )
    attempt = res.scalars().first()
    if attempt is None:
        return {"outcome": "unknown_order"}

    if attempt.status != "paid":
        attempt.status = "paid"
        attempt.verification_status = "verified"
        attempt.razorpay_payment_id = razorpay_payment_id
        await db.commit()
        await db.refresh(attempt)
    elif not attempt.razorpay_payment_id:
        attempt.razorpay_payment_id = razorpay_payment_id
        await db.commit()
        await db.refresh(attempt)

    if (attempt.route_mode or "LEGACY") != ROUTE or not is_route_enabled():
        return {"outcome": "not_route", "payment": "paid"}

    try:
        return await ensure_transfer(db, attempt, client=client)
    except RouteAPIError as exc:
        logger.warning("route settle: %s", type(exc).__name__)
        return {"outcome": "failed", "reason": str(exc)[:200]}
    except Exception:  # pragma: no cover — defensive; payout must not break payment
        logger.exception("route settle: unexpected error")
        await db.rollback()
        return {"outcome": "failed", "reason": "internal settlement error"}


async def apply_transfer_webhook(db: AsyncSession, transfer: Dict[str, Any]) -> Dict[str, Any]:
    """Apply a verified transfer.* webhook entity to the local transfer row."""
    trf_id = transfer.get("id")
    source_payment = transfer.get("source")
    recipient = transfer.get("recipient")
    if not trf_id:
        return {"outcome": "ignored", "reason": "no transfer id"}
    res = await db.execute(
        select(RazorpayTransfer).where(RazorpayTransfer.razorpay_transfer_id == trf_id)
    )
    row = res.scalars().first()
    if row is None and source_payment and recipient:
        # Adopt late: transfer exists at Razorpay but our send path never stored it
        # (e.g. timeout + missed reconcile). Match by (payment, account).
        res = await db.execute(
            select(RazorpayTransfer).where(
                RazorpayTransfer.razorpay_payment_id == source_payment,
                RazorpayTransfer.razorpay_account_id == recipient,
            )
        )
        row = res.scalars().first()
        if row is not None and not row.razorpay_transfer_id:
            row.razorpay_transfer_id = str(trf_id)[:32]
    if row is None:
        return {"outcome": "ignored", "reason": "unknown transfer"}
    row.status = _map_rzp_status(transfer.get("status"))
    if row.status == RouteTransferStatus.PROCESSED:
        row.processed_at = row.processed_at or _now()
    if row.status == RouteTransferStatus.FAILED:
        err = transfer.get("error") or {}
        row.failure_reason = str(err.get("description") or err.get("reason") or "transfer failed")[:500]
    await db.commit()
    logger.info("route webhook: transfer %s -> %s", str(trf_id)[:16], row.status)
    return {"outcome": "updated", "status": row.status}


async def request_route_refund(
    db: AsyncSession,
    transfer_row_id: object,
    *,
    amount_paise: int,
    client: Optional[RouteClient] = None,
) -> Dict[str, Any]:
    """Full refund with automatic reversal (single-transfer payments only).

    Uses the documented POST /v1/payments/{id}/refund {amount, reverse_all}
    shape. Partial refunds across multiple linked accounts are NOT supported
    here (per docs, reverse_all cannot split those) — raises instead.
    """
    res = await db.execute(select(RazorpayTransfer).where(RazorpayTransfer.id == transfer_row_id))  # type: ignore[arg-type]
    row = res.scalars().first()
    if row is None:
        return {"outcome": "error", "reason": "transfer not found"}
    if row.status not in (RouteTransferStatus.PROCESSED, RouteTransferStatus.PROCESSING):
        return {"outcome": "error", "reason": f"refund requires a sent transfer (status={row.status})"}
    if amount_paise != row.provider_paise:
        # Single-transfer partial refunds ARE covered by reverse_all per docs,
        # but to stay conservative we only auto-handle full provider reversals
        # here; anything else needs an explicit reversal first (operator flow).
        return {"outcome": "error", "reason": "only full provider-amount refunds are automated"}
    rzp = client or RouteClient.from_settings()
    try:
        refund = await route_svc.refund_payment_with_reversal(
            rzp, row.razorpay_payment_id, amount_paise=row.gross_paise, reverse_all=True
        )
    except RouteAPIError as exc:
        return {"outcome": "failed", "reason": str(exc)[:200]}
    row.razorpay_refund_id = str(refund.get("id", ""))[:32] or None
    row.status = RouteTransferStatus.REVERSED
    row.reversed_at = _now()
    await db.commit()
    logger.info("route refund: payment=%s reversed", str(row.razorpay_payment_id)[:16])
    return {"outcome": "reversed", "refund_id": row.razorpay_refund_id}
