"""Regression: a priced tenant with no collectable payment provider must
still get their appointment.

The web widget used to answer "Online payments are temporarily unavailable
for this business" and BLOCK the booking whenever the tenant had a price
configured but their payment account was not connected/live yet. The
customer saw the business's payment misconfiguration and the booking was
lost. The WhatsApp path never had that gate, so the two channels
disagreed about the same tenant.

The gate decision is `resolve_payment_gate` (extracted so it is testable on
its own, like `resolve_payment_snapshot_status`).
"""

from app.engine.message_processor import resolve_payment_gate


# ── no price -> no payment, no notice ─────────────────────────────────────
def test_unpriced_org_never_asks_for_payment() -> None:
    required, notice = resolve_payment_gate(
        price_enabled=False, price_paise=0, payments_ready=False
    )
    assert required is False
    assert notice == ""


# ── priced + a provider that can collect -> payment is required ──────────
def test_priced_org_with_a_ready_provider_must_pay() -> None:
    required, notice = resolve_payment_gate(
        price_enabled=True, price_paise=50000, payments_ready=True
    )
    assert required is True
    assert notice == ""


# ── priced + NO provider that can collect -> book it, say nothing ────────
def test_priced_org_with_an_unready_provider_still_books() -> None:
    """The customer must not be blocked, and must not be shown the
    business's payment misconfiguration."""
    required, notice = resolve_payment_gate(
        price_enabled=True, price_paise=50000, payments_ready=False
    )
    assert required is False  # the booking goes through
    assert notice == ""  # and nothing is said to the customer


# ── the scary message must not come back ─────────────────────────────────
def test_no_state_ever_produces_a_user_facing_payment_notice() -> None:
    """Every combination resolves to an empty notice. The old text
    ("Online payments are temporarily unavailable...") must never be
    returned to a customer again."""
    for price_enabled in (False, True):
        for price_paise in (0, 50000):
            for payments_ready in (False, True):
                _required, notice = resolve_payment_gate(
                    price_enabled=price_enabled,
                    price_paise=price_paise,
                    payments_ready=payments_ready,
                )
                assert notice == ""
