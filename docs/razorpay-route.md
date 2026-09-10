# Razorpay Route — Provider Payouts (Developer Guide)

The legacy Technology Partner sub-merchant flow has been **removed**.
Booking payments are created on the platform account and split to providers
via Razorpay Route. There is no sub-merchant OAuth, no per-org token, and no
silent fallback: organizations must reach `TRANSFER_READY` before payments
are accepted (explicit `ROUTE` mode, or `AUTO` once ready).

All Route request/response shapes below were verified against the current
official docs (TEST mode) at implementation time:

- Route API index: https://razorpay.com/docs/api/payments/route/
- Integration guide: https://razorpay.com/docs/payments/route/integration-guide
- Webhooks: https://razorpay.com/docs/payments/route/subscribe-to-webhooks

---

## 1. Required Razorpay dashboard setup

1. Create / log in to the Razorpay account that will be the **platform** account.
2. Enable **Route** (Marketplace/Route feature) for the account. Without it,
   `POST /v2/accounts` returns `400` ("Marketplace feature is not enabled").
3. If you use `reference_id` on linked accounts, have Razorpay enable
   `route_code_support` for the merchant (else `400` on that field).
4. Generate API keys (**Test Mode** first): `RAZORPAY_KEY_ID` (`rzp_test_*`),
   `RAZORPAY_KEY_SECRET`. These are the *platform* credentials.
5. Add the webhook URL (see §7) and subscribe to Route events (§7).

## 2. Route activation prerequisites

- Route feature enabled (§1.2) — otherwise account/product/transfer calls fail.
- `RAZORPAY_WEBHOOK_SECRET` configured (mandatory for Route in live mode;
  the code refuses unsigned webhooks when Route is enabled in live mode).
- Provider completes onboarding to `TRANSFER_READY` before any transfer
  (§5). Transfers to non-activated linked accounts fail at Razorpay.

## 3. Test mode setup

```bash
RAZORPAY_PARTNER_MODE=test
RAZORPAY_KEY_ID=rzp_test_...
RAZORPAY_KEY_SECRET=...
RAZORPAY_WEBHOOK_SECRET=...        # test webhook secret from dashboard
RAZORPAY_ROUTE_ENABLED=true        # kill switch ON for testing
RAZORPAY_ROUTE_COMMISSION_BPS=1000 # 10% TEST default
```

Simulate end-to-end in Test Mode per the integration guide: create linked
account → stakeholder → request product → update settlement (use Razorpay's
test bank details) → test payment → transfer via payment → observe
`transfer.processed`. `backend/tests/test_razorpay_route.py` (45 tests) covers
the same flow against mocked HTTP.

## 4. Environment variables

| Var | Purpose | Notes |
|---|---|---|
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | Platform account Basic Auth (Route orders, transfers, refunds) | Reused; no new secret. Must be `rzp_*` in live+Route mode (validated). |
| `RAZORPAY_ROUTE_ENABLED` | Global kill switch | `false` → all Route endpoints `404`, no Route API calls. Data preserved. |
| `RAZORPAY_ROUTE_API_BASE` | API base (default `https://api.razorpay.com`) | Override for proxies/mocks only. |
| `RAZORPAY_ROUTE_COMMISSION_BPS` | Default commission (bps) | Fallback when org has no override. Persisted per transfer. |
| `RAZORPAY_WEBHOOK_SECRET` | Webhook HMAC verification | Required for Route in live mode. |
| `RAZORPAY_PARTNER_*` | Legacy sub-merchant OAuth (untouched) | Route never reads these per-org tokens. |

## 5. Provider onboarding flow

```
POST linked-account  (phone + legal name; reference_id = stable aurexa_<org>)
  → ACCOUNT_CREATED → STAKEHOLDER_PENDING
POST stakeholder     (identity BOUND to the authenticated provider's verified
                      email; frontend cannot substitute another identity)
  → PRODUCT_PENDING
POST product         {product_name: "route", tnc_accepted: true}
  ONLY after real UI checkbox acceptance (422 otherwise)
  → BANK_DETAILS_PENDING (activation needs_clarification + settlements.* requirements)
PATCH product        {settlements: {account_number, ifsc_code, beneficiary_name}}
  raw number travels to Razorpay over HTTPS; only last4 + name persist
  → UNDER_REVIEW → (Razorpay activates) → TRANSFER_READY
PATCH payment-mode   {mode: AUTO|LEGACY|ROUTE}  (explicit routing choice)
GET  status          backend-driven message for the UI
```

State machine lives in `app/services/razorpay/onboarding.py` (pure functions,
single source of truth for endpoints, routing, settlement, webhooks).
Frontend: admin-panel → Integrations → Payments → **Provider Payouts** card
(hidden entirely when the backend 404s = Route disabled).

## 6. Payment → transfer flow

```
Widget order (ROUTE org) → POST /v1/orders on PLATFORM account
  receipt "route_*", attempt.route_mode = "ROUTE"
Customer pays via Checkout (key = platform RAZORPAY_KEY_ID, public-safe)
Verify + payment.captured webhook → settle_payment()  (converged, idempotent)
  → ensure_transfer():
     1. existing transfer? → return it (no second send)
     2. persist gross/commission/provider paise + idempotency_key (BEFORE send)
     3. GET /v1/payments/{id}/transfers reconcile (timeout-safe)
     4. POST /v1/payments/{id}/transfers {transfers:[{account, amount, currency}]}
transfer.processed / transfers.failed webhook → apply_transfer_webhook()
```

Routing (`app/services/razorpay/routing.py`):

| payment_mode | TRANSFER_READY? | Result |
|---|---|---|
| LEGACY | — | legacy sub-merchant flow |
| ROUTE | yes | platform order + transfer |
| ROUTE | no | **409 `provider_payout_not_ready`** (no silent fallback) |
| AUTO | yes (opted-in + activated) | platform order + transfer |
| AUTO | otherwise | legacy flow |

No `message_processor.py` gate exists in this phase (deferred business-model
choice): a provider that loses readiness after payment parks funds on the
platform (`awaiting_readiness`) instead of blocking the customer payment.

## 7. Webhook setup

Existing endpoint extended (no second endpoint): `POST /api/v1/webhooks/razorpay`.
Subscribe in the dashboard to: `payment.captured` (existing),
`transfer.processed`, `transfers.failed` (note plural — documented name; the
singular `transfer.failed` is also accepted), `settlement.processed`
(informational). Idempotency reuses `razorpay_webhook_events` (event PK);
duplicate deliveries return `deduplicated:true`.

## 8. Refund / reversal behavior

Implemented with documented shapes only
(`/v1/payments/{id}/refund {amount, reverse_all}`,
`/v1/transfers/{id}/reversals {amount?, notes?}`):

- `request_route_refund()` automates **full provider-amount refunds** with
  `reverse_all=true` (covers full refunds and single-account partials per docs).
- Partial refunds across **multiple** linked accounts are **not** automated
  (docs: `reverse_all` cannot split those) — use `reverse_transfer()`
  explicitly (operator flow). No public booking-refund endpoint is wired yet;
  wire one to `request_route_refund()` when the product refund flow is defined.

Invariant: never refund the customer while leaving the provider transfer
untouched — reverse first, then refund, persist both IDs.

## 9. Production go-live checklist

- [ ] Live platform keys (`rzp_live_*`) in `RAZORPAY_KEY_ID/SECRET`.
- [ ] Live `RAZORPAY_WEBHOOK_SECRET` set; endpoint subscribed to §7 events.
- [ ] `RAZORPAY_PARTNER_MODE=live`, HTTPS callback/frontend URLs
      (`validate_production_razorpay_config()` passes).
- [ ] `RAZORPAY_ALLOW_MOCK_COMPLETE` unset; no `acc_mock_*` orgs in Route mode.
- [ ] Commission BPS signed off; per-org overrides reviewed.
- [ ] Full TEST-mode matrix green: onboarding, transfer, duplicate
      webhook/verify, timeout reconcile, failed transfer, refund+reversal.
- [ ] `RAZORPAY_ROUTE_ENABLED=true` only after the above; kill switch verified
      (set `false` → Route endpoints 404, legacy unaffected).

## 10. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `400 Marketplace feature not enabled` | Enable Route on platform account (§1.2). |
| `400 route_code_support` / `reference_id` errors | Ask Razorpay to enable the feature, or drop `reference_id`. |
| `409 provider_payout_not_ready` on order | Org chose ROUTE but isn't `TRANSFER_READY` — finish onboarding or switch to AUTO/LEGACY. |
| Transfer stuck `processing` | Check `transfer.processed`/`transfers.failed` webhook delivery + secret; reconcile via `GET /v1/payments/{id}/transfers`. |
| `awaiting_readiness` settle outcome | Org lost readiness after payment; funds parked on platform until re-activated. |
| Duplicate transfer suspected | Impossible via this code (UNIQUE payment + idempotency key + reconcile-first); check Razorpay dashboard for out-of-band sends. |
| `Failed to write audit log` in test output | Pre-existing test-env noise, unrelated to Route. |

## 11. Files changed / added (this feature)

Added: `app/services/razorpay/` (`client`, `route`, `commission`,
`onboarding`, `routing`, `settlement`), `app/models/razorpay_route.py`,
`app/api/v1/endpoints/razorpay_route.py`, migrations `b7c2d9e4`, `c8d3e1f5`,
`d9e4f2a8` (drops legacy sub-merchant columns),
`tests/test_razorpay_route.py`, this doc, frontend Provider Payouts card
(incl. appointment-price controls).
Removed: `app/core/razorpay_partner.py`, `app/core/razorpay_crypto.py`,
legacy `razorpay.py` endpoints (`status/connect/mock-complete/disconnect/
cancel/callback`), `OrgConfig` sub-merchant columns, legacy dashboard card.
Kept: `PUT /dashboard/razorpay/price` (pricing now managed from the Route
card), `POST /widget/payment/{order,verify}`, `POST /webhooks/razorpay`
(payment + transfer events only), SaaS plan billing (`account.py` →
`backend-super`), `app/models/payment.py` (`route_mode` always `ROUTE`).
