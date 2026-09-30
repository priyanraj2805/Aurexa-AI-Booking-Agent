"""Regression: the payment status recorded on a booking.

`_execute_booking` does not take `org_config` as a parameter, but it assigned
`org_config` partway down (at the Google Calendar step). Python therefore
treated the name as a LOCAL for the whole function, so the earlier read in the
payment block raised `UnboundLocalError` -- and the block's
`except Exception:` swallowed it into `_pay_status = "not_required"`.

Result: for an org with `appointment_price_enabled = True` and a route-ready
Razorpay account, EVERY booking was stamped `payment_status = "not_required"`,
whatever had actually been paid. A payment requirement was silently waived.

These tests call the REAL `resolve_payment_snapshot_status`, which is the
function `_execute_booking` uses. An earlier version of this file re-created
the logic locally and so would have passed even if the production code were
deleted.
"""

import pytest

from app.engine.message_processor import resolve_payment_snapshot_status


# ── the happy paths ────────────────────────────────────────────────────────
def test_paid_attempt_is_recorded_as_paid() -> None:
    assert (
        resolve_payment_snapshot_status(
            price_expected=True, route_ready=True, paid_found=True
        )
        == "paid"
    )


def test_priced_but_unpaid_is_pending() -> None:
    assert (
        resolve_payment_snapshot_status(
            price_expected=True, route_ready=True, paid_found=False
        )
        == "pending"
    )


# ── payment is not required ────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("price_expected", "route_ready", "paid_found"),
    [
        (False, False, False),
        (False, True, False),
    ],
)
def test_unpriced_org_with_no_payment_never_owes_anything(
    price_expected, route_ready, paid_found
) -> None:
    assert (
        resolve_payment_snapshot_status(
            price_expected=price_expected,
            route_ready=route_ready,
            paid_found=paid_found,
        )
        == "not_required"
    )


def test_a_captured_payment_is_recorded_as_paid_even_if_not_priced() -> None:
    """A real paid attempt is the strongest signal and is never downgraded.

    (In practice the caller only looks for an attempt when the org is priced
    and route-ready, so this is defensive -- but the resolver must not report
    "nothing owed" when money demonstrably arrived.)
    """
    assert (
        resolve_payment_snapshot_status(
            price_expected=False, route_ready=True, paid_found=True
        )
        == "paid"
    )


# ── the rule that matters: an UNKNOWN state is never "nothing owed" ────────
def test_a_failed_lookup_at_a_priced_org_is_pending_not_free() -> None:
    """`lookup_failed` is what distinguishes "we could not find out" from the
    legitimate business state "this org is priced but has no Route account
    yet". This is the regression: a crash used to resolve to "not_required",
    which is a silent payment waiver."""
    assert (
        resolve_payment_snapshot_status(
            price_expected=True,
            route_ready=False,
            paid_found=False,
            lookup_failed=True,
        )
        == "pending"
    )


def test_a_failed_lookup_at_an_unpriced_org_stays_not_required() -> None:
    assert (
        resolve_payment_snapshot_status(
            price_expected=False,
            route_ready=False,
            paid_found=False,
            lookup_failed=True,
        )
        == "not_required"
    )


def test_priced_without_a_route_account_is_a_real_not_required_state() -> None:
    """The org is priced but genuinely cannot collect payment -- the widget
    rejects the order with 409 provider_payout_not_ready. That is NOT a
    failure, so it stays not_required."""
    assert (
        resolve_payment_snapshot_status(
            price_expected=True, route_ready=False, paid_found=False
        )
        == "not_required"
    )


def test_a_found_payment_wins_even_if_route_readiness_is_false() -> None:
    """A real captured payment is 'paid' no matter what the route flag says."""
    assert (
        resolve_payment_snapshot_status(
            price_expected=True, route_ready=False, paid_found=True
        )
        == "paid"
    )


# ── no FAILURE can silently waive a payment ───────────────────────────────
def test_no_failed_lookup_at_a_priced_org_ever_returns_not_required() -> None:
    """Exhaustive over the boolean space, with the lookup-failed flag set.

    This is the invariant worth protecting: when we genuinely do not know,
    we must not record that nothing is owed.
    """
    seen = []
    for route_ready in (True, False):
        for paid_found in (True, False):
            status = resolve_payment_snapshot_status(
                price_expected=True,
                route_ready=route_ready,
                paid_found=paid_found,
                lookup_failed=True,
            )
            if status == "not_required":
                seen.append((route_ready, paid_found))
    assert seen == []


def test_every_combination_returns_a_known_status() -> None:
    known = {"paid", "pending", "not_required"}
    for price_expected in (True, False):
        for route_ready in (True, False):
            for paid_found in (True, False):
                for lookup_failed in (True, False):
                    status = resolve_payment_snapshot_status(
                        price_expected=price_expected,
                        route_ready=route_ready,
                        paid_found=paid_found,
                        lookup_failed=lookup_failed,
                    )
                    assert status in known, (
                        price_expected, route_ready, paid_found, lookup_failed, status
                    )
