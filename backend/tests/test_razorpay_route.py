"""Razorpay Route tests (additive payout path; legacy flow untouched).

Covers: commission math, routing matrix, onboarding states, service payloads
and docs-verified API shapes (mock transport), onboarding endpoint idempotency,
order routing incl. explicit-ROUTE 409, settlement convergence (duplicate
verify, duplicate webhook, webhook-before/after-verify, timeout reconcile,
duplicate prevention, failed transfer, suspended account), refunds, and the
production webhook-secret guard.
"""

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio

from app.core.config import settings
from app.services.razorpay import route as route_svc
from app.services.razorpay.client import (
    RouteAPIError,
    RouteClient,
    is_route_enabled,
    set_http_client_factory,
)
from app.services.razorpay.commission import build_idempotency_key, calculate_split
from app.services.razorpay.onboarding import (
    build_reference_id,
    derive_onboarding_status,
    frontend_message,
    is_transfer_ready,
)
from app.services.razorpay.routing import (
    ProviderPayoutNotReady,
    get_org_route_context,
    resolve_payment_route,
)


# ── Helpers ──────────────────────────────────────────────────────────────

def _mock_client(handler, key_id="rzp_test_platform", key_secret="test_secret"):
    set_http_client_factory(
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://api.razorpay.com",
        )
    )
    return RouteClient(key_id=key_id, key_secret=key_secret)


@pytest_asyncio.fixture
async def route_enabled(monkeypatch):
    monkeypatch.setattr(settings, "RAZORPAY_ROUTE_ENABLED", True)
    yield
    set_http_client_factory(None)


def _ok_json(payload, status=200):
    return httpx.Response(status, json=payload)


# ── Commission ───────────────────────────────────────────────────────────

class TestCommission:
    def test_exact_split(self):
        assert calculate_split(100000, 1000) == (10000, 90000)

    def test_zero_commission(self):
        assert calculate_split(50000, 0) == (0, 50000)

    def test_remainder_stays_with_platform(self):
        commission, provider = calculate_split(101, 1000)
        assert commission + provider == 101
        assert commission == 10  # floor(101*0.1)

    def test_invalid_inputs(self):
        with pytest.raises(ValueError):
            calculate_split(100.5, 1000)  # floats forbidden
        with pytest.raises(ValueError):
            calculate_split(0, 1000)
        with pytest.raises(ValueError):
            calculate_split(1000, 10001)
        with pytest.raises(ValueError):
            calculate_split(100, 10000)  # provider would be 0

    def test_idempotency_key_stable_and_specific(self):
        k1 = build_idempotency_key("org1", "pay_1", 90000)
        assert k1 == build_idempotency_key("org1", "pay_1", 90000)
        assert k1 != build_idempotency_key("org1", "pay_1", 90001)
        assert k1 != build_idempotency_key("org2", "pay_1", 90000)


# ── Routing matrix ───────────────────────────────────────────────────────

class TestRouting:
    def test_kill_switch_forces_legacy(self):
        assert (
            resolve_payment_route(
                route_enabled=False, payment_mode="ROUTE",
                onboarding_status="TRANSFER_READY", route_product_status="activated",
            )
            == "LEGACY"
        )

    def test_legacy_mode_always_legacy(self):
        assert (
            resolve_payment_route(
                route_enabled=True, payment_mode="LEGACY",
                onboarding_status="TRANSFER_READY", route_product_status="activated",
            )
            == "LEGACY"
        )

    def test_route_ready(self):
        assert (
            resolve_payment_route(
                route_enabled=True, payment_mode="ROUTE",
                onboarding_status="TRANSFER_READY", route_product_status="activated",
            )
            == "ROUTE"
        )

    def test_route_not_ready_rejects_without_fallback(self):
        with pytest.raises(ProviderPayoutNotReady):
            resolve_payment_route(
                route_enabled=True, payment_mode="ROUTE",
                onboarding_status="BANK_DETAILS_PENDING",
                route_product_status="needs_clarification",
            )

    def test_auto_ready_routes(self):
        assert (
            resolve_payment_route(
                route_enabled=True, payment_mode="AUTO",
                onboarding_status="TRANSFER_READY", route_product_status="activated",
            )
            == "ROUTE"
        )

    def test_auto_pending_stays_legacy(self):
        for status, prod in [
            ("NOT_STARTED", None),
            ("UNDER_REVIEW", "under_review"),
            ("BANK_DETAILS_PENDING", "needs_clarification"),
        ]:
            assert (
                resolve_payment_route(
                    route_enabled=True, payment_mode="AUTO",
                    onboarding_status=status, route_product_status=prod,
                )
                == "LEGACY"
            )

    def test_auto_requires_activated_product(self):
        # TRANSFER_READY state without activated product must not route.
        assert (
            resolve_payment_route(
                route_enabled=True, payment_mode="AUTO",
                onboarding_status="TRANSFER_READY", route_product_status="under_review",
            )
            == "LEGACY"
        )


# ── Onboarding state machine ─────────────────────────────────────────────

class TestOnboardingStates:
    def test_full_lifecycle(self):
        assert derive_onboarding_status() == "NOT_STARTED"
        assert derive_onboarding_status(razorpay_account_id="acc_1") == "STAKEHOLDER_PENDING"
        assert derive_onboarding_status(razorpay_account_id="acc_1", stakeholder_id="sth_1") == "PRODUCT_PENDING"
        assert (
            derive_onboarding_status(
                razorpay_account_id="acc_1", stakeholder_id="sth_1",
                route_product_id="acc_prd_1", route_product_status="needs_clarification",
            )
            == "BANK_DETAILS_PENDING"
        )
        assert (
            derive_onboarding_status(
                razorpay_account_id="acc_1", stakeholder_id="sth_1",
                route_product_id="acc_prd_1", route_product_status="under_review",
                bank_last4="4821",
            )
            == "UNDER_REVIEW"
        )
        assert (
            derive_onboarding_status(
                razorpay_account_id="acc_1", route_product_status="activated",
            )
            == "TRANSFER_READY"
        )

    def test_suspended_short_circuits(self):
        assert (
            derive_onboarding_status(razorpay_account_id="acc_1", razorpay_account_status="suspended")
            == "SUSPENDED"
        )
        assert (
            derive_onboarding_status(razorpay_account_id="acc_1", route_product_status="suspended")
            == "SUSPENDED"
        )

    def test_transfer_ready_gate(self):
        assert is_transfer_ready("TRANSFER_READY", "activated") is True
        assert is_transfer_ready("TRANSFER_READY", "under_review") is False
        assert is_transfer_ready("ACTIVE", "activated") is False

    def test_reference_id_stable_and_short(self):
        org = uuid.uuid4()
        assert build_reference_id(org) == build_reference_id(org)
        assert len(build_reference_id(org)) <= 20

    def test_frontend_messages_cover_all_states(self):
        for s in ["NOT_STARTED", "BANK_DETAILS_PENDING", "UNDER_REVIEW",
                  "TRANSFER_READY", "FAILED", "SUSPENDED"]:
            assert frontend_message(s)


# ── Service payloads + docs-verified API shapes ──────────────────────────

class TestRouteService:
    async def test_linked_account_create_shape(self, route_enabled):
        seen = {}

        def handler(request: httpx.Request):
            seen["method"] = request.method
            seen["path"] = request.url.path
            body = httpx.Response(200, json={"id": "acc_test123", "status": "created",
                                             "reference_id": "aurexa_abc", "type": "route"})
            return body

        client = _mock_client(handler)
        payload = route_svc.build_linked_account_payload(
            email="biz@example.com", phone="9000090000", reference_id="aurexa_abc123",
            legal_business_name="Test Clinic",
        )
        assert payload["type"] == "route"
        assert payload["profile"]["category"] == "healthcare"
        data = await route_svc.create_linked_account(client, payload)
        assert seen == {"method": "POST", "path": "/v2/accounts"}
        assert data["id"] == "acc_test123"

    async def test_stakeholder_create_shape(self, route_enabled):
        seen = {}

        def handler(request: httpx.Request):
            seen["path"] = request.url.path
            return _ok_json({"id": "sth_test123", "entity": "stakeholder"})

        client = _mock_client(handler)
        payload = route_svc.build_stakeholder_payload(name="Rahul Sharma", email="r@example.com")
        assert payload["email"] == "r@example.com"
        # Identity email cannot be omitted (bound to authenticated provider upstream).
        with pytest.raises(ValueError):
            route_svc.build_stakeholder_payload(name="X", email="")
        data = await route_svc.create_stakeholder(client, "acc_test123", payload)
        assert seen["path"] == "/v2/accounts/acc_test123/stakeholders"
        assert data["id"] == "sth_test123"

    async def test_product_request_requires_tnc(self, route_enabled):
        def handler(request: httpx.Request):
            return _ok_json({"id": "acc_prd_1", "product_name": "route",
                             "activation_status": "needs_clarification"})

        client = _mock_client(handler)
        with pytest.raises(ValueError):
            await route_svc.request_route_product(client, "acc_1", tnc_accepted=False)
        data = await route_svc.request_route_product(client, "acc_1", tnc_accepted=True)
        assert data["activation_status"] == "needs_clarification"

    async def test_settlement_update_shape_and_last4(self, route_enabled):
        seen = {}

        def handler(request: httpx.Request):
            import json as _j
            seen["body"] = _j.loads(request.content.decode())
            return _ok_json({"id": "acc_prd_1", "activation_status": "activated"})

        client = _mock_client(handler)
        data = await route_svc.update_product_settlement(
            client, "acc_1", "acc_prd_1",
            account_number="1234567890123456", ifsc_code="HDFC0000317",
            beneficiary_name="Rahul Sharma",
        )
        assert seen["body"]["settlements"] == {
            "account_number": "1234567890123456",
            "ifsc_code": "HDFC0000317",
            "beneficiary_name": "Rahul Sharma",
        }
        assert seen["body"]["tnc_accepted"] is True
        assert data["activation_status"] == "activated"
        safe = route_svc.summarize_settlement_for_storage("Rahul Sharma", "1234567890123456")
        assert safe == {"bank_last4": "3456", "beneficiary_name": "Rahul Sharma"}

    async def test_settlement_validation(self, route_enabled):
        client = _mock_client(lambda r: _ok_json({}))
        with pytest.raises(ValueError):
            await route_svc.update_product_settlement(
                client, "acc_1", "acc_prd_1",
                account_number="123", ifsc_code="HDFC0000317", beneficiary_name="R",
            )
        with pytest.raises(ValueError):
            await route_svc.update_product_settlement(
                client, "acc_1", "acc_prd_1",
                account_number="1234567890", ifsc_code="BOGUS", beneficiary_name="Rahul",
            )

    async def test_transfer_create_and_fetch_shapes(self, route_enabled):
        calls = []

        def handler(request: httpx.Request):
            calls.append((request.method, request.url.path))
            if request.method == "POST":
                return _ok_json({"entity": "collection", "count": 1, "items": [{
                    "id": "trf_test1", "status": "pending", "source": "pay_1",
                    "recipient": "acc_1", "amount": 90000, "currency": "INR"}]})
            return _ok_json({"entity": "collection", "count": 0, "items": []})

        client = _mock_client(handler)
        created = await route_svc.create_transfer_from_payment(
            client, "pay_1", account_id="acc_1", amount_paise=90000)
        assert calls[0] == ("POST", "/v1/payments/pay_1/transfers")
        assert created["items"][0]["id"] == "trf_test1"
        with pytest.raises(ValueError):
            await route_svc.create_transfer_from_payment(
                client, "pay_1", account_id="acc_1", amount_paise=90000, currency="USD")
        items = await route_svc.fetch_transfers_for_payment(client, "pay_1")
        assert items == []
        assert ("GET", "/v1/payments/pay_1/transfers") in calls

    async def test_refund_with_reversal_shape(self, route_enabled):
        seen = {}

        def handler(request: httpx.Request):
            import json as _j
            seen["path"] = request.url.path
            seen["body"] = _j.loads(request.content.decode())
            return _ok_json({"id": "rfnd_1", "status": "processed"})

        client = _mock_client(handler)
        data = await route_svc.refund_payment_with_reversal(client, "pay_1", amount_paise=100000)
        assert seen["path"] == "/v1/payments/pay_1/refund"
        assert seen["body"] == {"amount": 100000, "reverse_all": True}
        assert data["id"] == "rfnd_1"

    async def test_api_error_mapping_and_kill_switch(self, route_enabled, monkeypatch):
        def handler(request: httpx.Request):
            return httpx.Response(400, json={"error": {"code": "BAD_REQUEST_ERROR",
                                                       "description": "Invalid IFSC Code"}})

        client = _mock_client(handler)
        with pytest.raises(RouteAPIError) as exc:
            await route_svc.fetch_linked_account(client, "acc_nope")
        assert exc.value.status_code == 400
        assert "secret" not in str(exc.value).lower()

        monkeypatch.setattr(settings, "RAZORPAY_ROUTE_ENABLED", False)
        assert is_route_enabled() is False
        with pytest.raises(RouteAPIError):
            await route_svc.fetch_linked_account(client, "acc_1")

    async def test_timeout_is_ambiguous(self, route_enabled):
        def handler(request: httpx.Request):
            raise httpx.TimeoutException("slow")

        client = _mock_client(handler)
        with pytest.raises(RouteAPIError) as exc:
            await route_svc.fetch_transfer(client, "trf_1")
        assert "timed out" in str(exc.value)


# ── DB-backed settlement tests ───────────────────────────────────────────

def _org_and_attempt(db_session, user, **attempt_kw):
    """Build a paid attempt for the fixture-created org (register already made it)."""
    from app.models.payment import PaymentAttempt

    org_id = user["user"]["org_id"]
    route_mode = attempt_kw.pop("route_mode", "ROUTE")
    status = attempt_kw.pop("status", "paid")
    verification_status = attempt_kw.pop("verification_status", "verified")
    attempt = PaymentAttempt(
        org_id=org_id,
        razorpay_order_id=f"order_route_{uuid.uuid4().hex[:8]}",
        razorpay_payment_id=f"pay_route_{uuid.uuid4().hex[:8]}",
        amount_paise=100000,
        currency="INR",
        status=status,
        verification_status=verification_status,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        route_mode=route_mode,
        **attempt_kw,
    )
    db_session.add(attempt)
    return org_id, attempt


def _linked(db_session, org_id, status="TRANSFER_READY", product="activated"):
    from app.models.razorpay_route import RazorpayLinkedAccount

    row = RazorpayLinkedAccount(
        org_id=org_id,
        payment_mode="ROUTE",
        razorpay_account_id=f"acc_{uuid.uuid4().hex[:10]}",
        razorpay_reference_id=f"aurexa_{uuid.uuid4().hex[:13]}",
        razorpay_status="created",
        stakeholder_id="sth_test",
        route_product_id="acc_prd_test",
        route_product_status=product,
        onboarding_status=status,
        bank_verified=(status == "TRANSFER_READY"),
        bank_last4="4821",
        beneficiary_name="Rahul Sharma",
    )
    db_session.add(row)
    return row


def _transfer_http(state):
    """Stateful mock: reconcile GET reflects transfers 'accepted' by POST."""

    def handler(request: httpx.Request):
        import json as _j
        path = request.url.path
        if request.method == "GET" and path.endswith("/transfers"):
            items = [t for t in state["accepted"]
                     if t["source"] == path.split("/")[3]]
            return _ok_json({"entity": "collection", "count": len(items), "items": items})
        if request.method == "POST" and path.endswith("/transfers"):
            if state.get("timeout_once"):
                state["timeout_once"] = False
                raise httpx.TimeoutException("slow")
            body = _j.loads(request.content.decode())
            entry = body["transfers"][0]
            item = {"id": f"trf_{uuid.uuid4().hex[:10]}", "entity": "transfer",
                    "status": state.get("create_status", "pending"),
                    "source": path.split("/")[3], "recipient": entry["account"],
                    "amount": entry["amount"], "currency": entry["currency"],
                    "amount_reversed": 0, "error": {"description": state.get("fail_reason")}}
            state["accepted"].append(item)
            state["posts"] += 1
            return _ok_json({"entity": "collection", "count": 1, "items": [item]})
        return _ok_json({})
    return handler


class TestSettlement:
    async def test_create_persists_money_before_send(self, db_session, registered_user, route_enabled):
        from app.models.razorpay_route import RazorpayTransfer
        from app.services.razorpay import settlement as st
        from sqlalchemy.future import select

        org_id, attempt = _org_and_attempt(db_session, registered_user)
        linked = _linked(db_session, org_id)
        await db_session.commit()
        state = {"accepted": [], "posts": 0}
        out = await st.ensure_transfer(
            db_session, attempt, client=_mock_client(_transfer_http(state)))
        assert out["outcome"] == "created"
        t = out["transfer"]
        assert (t["gross_paise"], t["commission_paise"], t["provider_paise"]) == (100000, 10000, 90000)
        assert t["currency"] == "INR"
        rows = (await db_session.execute(
            select(RazorpayTransfer).where(
                RazorpayTransfer.razorpay_payment_id == attempt.razorpay_payment_id)
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].idempotency_key.startswith("route_")

    async def test_duplicate_ensure_single_transfer(self, db_session, registered_user, route_enabled):
        from app.models.razorpay_route import RazorpayTransfer
        from app.services.razorpay import settlement as st
        from sqlalchemy.future import select

        org_id, attempt = _org_and_attempt(db_session, registered_user)
        _linked(db_session, org_id)
        await db_session.commit()
        state = {"accepted": [], "posts": 0}
        _svc_client = _mock_client(_transfer_http(state))
        first = await st.ensure_transfer(db_session, attempt, client=_svc_client)
        second = await st.ensure_transfer(db_session, attempt, client=_svc_client)
        assert first["outcome"] == "created"
        assert second["outcome"] == "existing"
        assert state["posts"] == 1
        rows = (await db_session.execute(
            select(RazorpayTransfer).where(
                RazorpayTransfer.razorpay_payment_id == attempt.razorpay_payment_id)
        )).scalars().all()
        assert len(rows) == 1

    async def test_timeout_reconciles_before_retry(self, db_session, registered_user, route_enabled):
        from app.services.razorpay import settlement as st

        org_id, attempt = _org_and_attempt(db_session, registered_user)
        linked = _linked(db_session, org_id)
        await db_session.commit()
        # Timeout on POST but Razorpay actually accepted: seed accepted list manually.
        state = {"accepted": [{"id": "trf_accepted1", "entity": "transfer", "status": "pending",
                               "source": attempt.razorpay_payment_id, "recipient": linked.razorpay_account_id,
                               "amount": 90000, "currency": "INR", "amount_reversed": 0,
                               "error": {}}],
                  "posts": 0, "timeout_once": True}
        out = await st.ensure_transfer(
            db_session, attempt, client=_mock_client(_transfer_http(state)))
        # First call: reconcile GET finds the accepted transfer -> adopted, no POST.
        assert out["outcome"] == "reconciled"
        assert state["posts"] == 0
        assert out["transfer"]["razorpay_transfer_id"] == "trf_accepted1"

    async def test_failed_transfer_recorded(self, db_session, registered_user, route_enabled):
        from app.services.razorpay import settlement as st

        org_id, attempt = _org_and_attempt(db_session, registered_user)
        _linked(db_session, org_id)
        await db_session.commit()
        state = {"accepted": [], "posts": 0, "create_status": "failed",
                 "fail_reason": "insufficient funds"}
        out = await st.ensure_transfer(
            db_session, attempt, client=_mock_client(_transfer_http(state)))
        assert out["transfer"]["status"] == "failed"
        assert "insufficient funds" in (out["transfer"]["failure_reason"] or "")

    async def test_suspended_account_parks_payment(self, db_session, registered_user, route_enabled):
        from app.services.razorpay import settlement as st

        org_id, attempt = _org_and_attempt(db_session, registered_user)
        _linked(db_session, org_id, status="SUSPENDED", product="suspended")
        await db_session.commit()
        state = {"accepted": [], "posts": 0}
        _mock_client(_transfer_http(state))
        out = await st.ensure_transfer(db_session, attempt)
        assert out["outcome"] == "awaiting_readiness"
        assert state["posts"] == 0

    async def test_legacy_attempt_never_transfers(self, db_session, registered_user, route_enabled):
        from app.services.razorpay import settlement as st

        org_id, attempt = _org_and_attempt(db_session, registered_user, route_mode=None)
        await db_session.commit()
        state = {"accepted": [], "posts": 0}
        _mock_client(_transfer_http(state))
        out = await st.settle_payment(
            db_session, org_id=org_id,
            razorpay_order_id=attempt.razorpay_order_id,
            razorpay_payment_id=attempt.razorpay_payment_id,
        )
        assert out["outcome"] == "not_route"
        assert state["posts"] == 0

    async def test_settle_converges_verify_and_webhook(self, db_session, registered_user, route_enabled):
        from app.services.razorpay import settlement as st

        org_id, attempt = _org_and_attempt(db_session, registered_user, status="created",
                                        verification_status="pending")
        attempt.razorpay_payment_id = None
        _linked(db_session, org_id)
        await db_session.commit()
        state = {"accepted": [], "posts": 0}
        _settle_client = _mock_client(_transfer_http(state))
        pay_id = f"pay_conv_{uuid.uuid4().hex[:8]}"
        first = await st.settle_payment(
            db_session, org_id=org_id,
            razorpay_order_id=attempt.razorpay_order_id, razorpay_payment_id=pay_id,
            client=_settle_client)
        second = await st.settle_payment(
            db_session, org_id=org_id,
            razorpay_order_id=attempt.razorpay_order_id, razorpay_payment_id=pay_id,
            client=_settle_client)
        assert first["outcome"] == "created"
        assert second["outcome"] == "existing"  # webhook-after-verify: no duplicate
        assert state["posts"] == 1

    async def test_full_refund_reverses(self, db_session, registered_user, route_enabled):
        from app.services.razorpay import settlement as st

        org_id, attempt = _org_and_attempt(db_session, registered_user)
        _linked(db_session, org_id)
        await db_session.commit()
        state = {"accepted": [], "posts": 0}
        created = await st.ensure_transfer(
            db_session, attempt,
            client=_mock_client(_transfer_http(state)))
        assert created["outcome"] == "created"

        from app.models.razorpay_route import RazorpayTransfer
        from sqlalchemy.future import select
        row = (await db_session.execute(
            select(RazorpayTransfer).where(
                RazorpayTransfer.razorpay_payment_id == attempt.razorpay_payment_id))).scalars().first()
        # Webhook marks it processed first.
        applied = await st.apply_transfer_webhook(db_session, {
            "id": row.razorpay_transfer_id, "status": "processed",
            "source": attempt.razorpay_payment_id, "recipient": row.razorpay_account_id})
        assert applied == {"outcome": "updated", "status": "processed"}

        def refund_handler(request: httpx.Request):
            return _ok_json({"id": "rfnd_test1", "status": "processed"})

        out = await st.request_route_refund(
            db_session, row.id, amount_paise=row.provider_paise,
            client=_mock_client(refund_handler))
        assert out == {"outcome": "reversed", "refund_id": "rfnd_test1"}

        # Partial (non-full) refunds are not automated.
        out2 = await st.request_route_refund(
            db_session, row.id, amount_paise=100,
            client=_mock_client(refund_handler))
        assert out2["outcome"] == "error"


# ── Onboarding endpoint idempotency ──────────────────────────────────────

class TestOnboardingEndpoints:
    async def _enable(self, monkeypatch):
        monkeypatch.setattr(settings, "RAZORPAY_ROUTE_ENABLED", True)
        # Endpoints build RouteClient.from_settings(): fail-closed without
        # platform credentials, so tests inject fakes (no real HTTP — all
        # Route calls are mocked per test).
        monkeypatch.setattr(settings, "RAZORPAY_KEY_ID", "rzp_test_platform")
        monkeypatch.setattr(settings, "RAZORPAY_KEY_SECRET", "test_secret")

    async def test_linked_account_idempotent(self, client, registered_user, monkeypatch):
        await self._enable(monkeypatch)
        import app.api.v1.endpoints.razorpay_route as ep

        calls = []

        async def fake_create(client_, payload):
            calls.append(payload)
            return {"id": "acc_ep1", "status": "created", "reference_id": payload["reference_id"]}

        monkeypatch.setattr(ep.route_svc, "create_linked_account", fake_create)
        h = registered_user["headers"]
        r1 = await client.post("/api/v1/dashboard/razorpay/route/linked-account",
                               json={"phone": "9000090000"}, headers=h)
        assert r1.status_code == 200, r1.text
        assert r1.json()["razorpay_account_id"] == "acc_ep1"
        r2 = await client.post("/api/v1/dashboard/razorpay/route/linked-account",
                               json={"phone": "9000090000"}, headers=h)
        assert r2.status_code == 200
        assert len(calls) == 1  # second call returns existing, no Razorpay call

    async def test_stakeholder_idempotent_and_bound(self, client, registered_user, monkeypatch, db_session):
        await self._enable(monkeypatch)
        import app.api.v1.endpoints.razorpay_route as ep
        from app.models.razorpay_route import RazorpayLinkedAccount

        org_id = registered_user["user"]["org_id"]
        db_session.add(RazorpayLinkedAccount(
            org_id=org_id, razorpay_account_id="acc_ep2",
            razorpay_reference_id="aurexa_testref",
            onboarding_status="STAKEHOLDER_PENDING"))
        await db_session.commit()

        seen = {}

        async def fake_stakeholder(client_, account_id, payload):
            seen.update(payload)
            return {"id": "sth_ep2"}

        monkeypatch.setattr(ep.route_svc, "create_stakeholder", fake_stakeholder)
        h = registered_user["headers"]
        # Attempt to substitute an unrelated identity email: must be ignored.
        r1 = await client.post("/api/v1/dashboard/razorpay/route/stakeholder",
                               json={"name": "Someone Else"}, headers=h)
        assert r1.status_code == 200, r1.text
        assert seen["email"] == registered_user["email"]  # bound to authenticated provider
        r2 = await client.post("/api/v1/dashboard/razorpay/route/stakeholder",
                               json={"name": "Someone Else"}, headers=h)
        assert r2.status_code == 200
        assert r2.json()["stakeholder_id"] == "sth_ep2"

    async def test_product_requires_tnc(self, client, registered_user, monkeypatch, db_session):
        await self._enable(monkeypatch)
        from app.models.razorpay_route import RazorpayLinkedAccount

        org_id = registered_user["user"]["org_id"]
        db_session.add(RazorpayLinkedAccount(
            org_id=org_id, razorpay_account_id="acc_ep3",
            razorpay_reference_id="aurexa_testref3", stakeholder_id="sth_x",
            onboarding_status="PRODUCT_PENDING"))
        await db_session.commit()
        h = registered_user["headers"]
        r = await client.post("/api/v1/dashboard/razorpay/route/product",
                              json={"tnc_accepted": False}, headers=h)
        assert r.status_code == 422  # terms cannot be silently accepted

    async def test_payment_mode_validation(self, client, registered_user, monkeypatch):
        await self._enable(monkeypatch)
        h = registered_user["headers"]
        r = await client.patch("/api/v1/dashboard/razorpay/route/payment-mode",
                               json={"mode": "SOMETHING"}, headers=h)
        assert r.status_code == 422
        r2 = await client.patch("/api/v1/dashboard/razorpay/route/payment-mode",
                                json={"mode": "ROUTE"}, headers=h)
        assert r2.status_code == 200
        assert r2.json()["payment_mode"] == "ROUTE"

    async def test_status_hidden_when_disabled(self, client, registered_user, monkeypatch):
        monkeypatch.setattr(settings, "RAZORPAY_ROUTE_ENABLED", False)
        r = await client.get("/api/v1/dashboard/razorpay/route/status",
                             headers=registered_user["headers"])
        assert r.status_code == 404


# ── Order routing + webhooks ─────────────────────────────────────────────

class TestOrderRoutingAndWebhooks:
    async def _org_with_price(self, db_session, registered_user, widget_token="wt_route_test"):
        from app.models.config import OrgConfig
        from sqlalchemy.future import select

        org_id = registered_user["user"]["org_id"]
        res = await db_session.execute(select(OrgConfig).where(OrgConfig.org_id == org_id))
        cfg = res.scalars().first()
        if cfg is None:
            cfg = OrgConfig(org_id=org_id, required_fields=["name"])
            db_session.add(cfg)
        cfg.appointment_price_enabled = True
        cfg.appointment_price_paise = 100000
        cfg.appointment_currency = "INR"
        cfg.widget_token = widget_token
        await db_session.commit()
        return cfg

    async def _session(self, db_session, org_id):
        from app.models.session import ChatSession

        sess = ChatSession(org_id=org_id, channel="web")
        db_session.add(sess)
        await db_session.commit()
        await db_session.refresh(sess)
        return sess

    async def test_explicit_route_not_ready_rejected(self, client, registered_user,
                                                     monkeypatch, db_session):
        monkeypatch.setattr(settings, "RAZORPAY_ROUTE_ENABLED", True)
        from app.models.razorpay_route import RazorpayLinkedAccount

        org_id = registered_user["user"]["org_id"]
        await self._org_with_price(db_session, registered_user)
        sess = await self._session(db_session, org_id)
        db_session.add(RazorpayLinkedAccount(
            org_id=org_id, payment_mode="ROUTE",
            razorpay_reference_id="aurexa_route409",
            onboarding_status="BANK_DETAILS_PENDING",
            route_product_status="needs_clarification"))
        await db_session.commit()
        r = await client.post("/api/v1/widget/payment/order",
                              json={"session_id": str(sess.id), "widget_token": "wt_route_test"})
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "provider_payout_not_ready"

    async def test_non_ready_org_rejected_no_legacy_fallback(self, client, registered_user, monkeypatch, db_session):
        monkeypatch.setattr(settings, "RAZORPAY_ROUTE_ENABLED", True)
        await self._org_with_price(db_session, registered_user, widget_token="wt_leg")
        org_id = registered_user["user"]["org_id"]
        sess = await self._session(db_session, org_id)
        r = await client.post("/api/v1/widget/payment/order",
                              json={"session_id": str(sess.id), "widget_token": "wt_leg"})
        # No linked row -> AUTO not ready -> 409 (legacy flow removed, no fallback).
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "provider_payout_not_ready"

    async def test_route_ready_creates_platform_order(self, client, registered_user,
                                                      monkeypatch, db_session):
        monkeypatch.setattr(settings, "RAZORPAY_ROUTE_ENABLED", True)
        from app.services.razorpay import route as route_module
        from app.models.razorpay_route import RazorpayLinkedAccount

        await self._org_with_price(db_session, registered_user, widget_token="wt_rdy")
        org_id = registered_user["user"]["org_id"]
        sess = await self._session(db_session, org_id)
        db_session.add(RazorpayLinkedAccount(
            org_id=org_id, payment_mode="AUTO",
            razorpay_account_id="acc_ready1", razorpay_reference_id="aurexa_rdy",
            stakeholder_id="sth_rdy", route_product_id="acc_prd_rdy",
            route_product_status="activated", onboarding_status="TRANSFER_READY",
            bank_verified=True, bank_last4="4821"))
        await db_session.commit()

        async def fake_platform_order(client_, amount_paise, receipt, currency="INR", notes=None):
            assert receipt.startswith("route_")
            assert currency == "INR"
            return {"id": "order_plat1", "amount": amount_paise, "currency": currency,
                    "receipt": receipt, "status": "created"}

        monkeypatch.setattr(route_module, "create_platform_order", fake_platform_order)
        monkeypatch.setattr(settings, "RAZORPAY_KEY_ID", "rzp_test_platform")
        monkeypatch.setattr(settings, "RAZORPAY_KEY_SECRET", "test_secret")
        r = await client.post("/api/v1/widget/payment/order",
                              json={"session_id": str(sess.id), "widget_token": "wt_rdy"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["order_id"] == "order_plat1"
        assert body["key"] == "rzp_test_platform"  # platform key, never sub-merchant token
        assert body["mock"] is False

        from app.models.payment import PaymentAttempt
        from sqlalchemy.future import select
        att = (await db_session.execute(
            select(PaymentAttempt).where(PaymentAttempt.razorpay_order_id == "order_plat1")
        )).scalars().first()
        assert att is not None and att.route_mode == "ROUTE"

    async def _verify_attempt(self, db_session, org_id, sess, route_mode="ROUTE"):
        from app.models.payment import PaymentAttempt

        order_id = f"order_vfy_{uuid.uuid4().hex[:8]}"
        pay_id = f"pay_vfy_{uuid.uuid4().hex[:8]}"
        db_session.add(PaymentAttempt(
            org_id=org_id, session_id=sess.id, razorpay_order_id=order_id,
            amount_paise=100000, currency="INR", status="created",
            verification_status="pending",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            route_mode=route_mode))
        await db_session.commit()
        return order_id, pay_id

    async def test_platform_verify_accepts_valid_signature(self, client, registered_user,
                                                           monkeypatch, db_session):
        import hashlib
        import hmac as _hmac_mod
        monkeypatch.setattr(settings, "RAZORPAY_ROUTE_ENABLED", True)
        monkeypatch.setattr(settings, "RAZORPAY_KEY_SECRET", "test_platform_secret")
        monkeypatch.setattr(settings, "RAZORPAY_KEY_ID", "rzp_test_platform")
        from app.models.razorpay_route import RazorpayLinkedAccount
        from app.services.razorpay.client import RouteClient

        await self._org_with_price(db_session, registered_user, widget_token="wt_vfy")
        org_id = registered_user["user"]["org_id"]
        sess = await self._session(db_session, org_id)
        # Not transfer-ready: settle parks the payout (no HTTP) after paid-marking.
        db_session.add(RazorpayLinkedAccount(
            org_id=org_id, payment_mode="AUTO",
            razorpay_reference_id="aurexa_vfy",
            onboarding_status="BANK_DETAILS_PENDING",
            route_product_status="needs_clarification"))
        order_id, pay_id = await self._verify_attempt(db_session, org_id, sess)

        async def fake_fetch(self, payment_id):
            assert payment_id == pay_id
            return {"id": pay_id, "order_id": order_id, "amount": 100000,
                    "currency": "INR", "status": "captured"}

        monkeypatch.setattr(RouteClient, "fetch_payment", fake_fetch)
        sig = _hmac_mod.new(b"test_platform_secret", f"{order_id}|{pay_id}".encode(),
                            hashlib.sha256).hexdigest()
        r = await client.post("/api/v1/widget/payment/verify", json={
            "session_id": str(sess.id), "razorpay_order_id": order_id,
            "razorpay_payment_id": pay_id, "razorpay_signature": sig,
            "widget_token": "wt_vfy"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "paid"

    async def test_platform_verify_rejects_bad_signature(self, client, registered_user,
                                                         monkeypatch, db_session):
        monkeypatch.setattr(settings, "RAZORPAY_ROUTE_ENABLED", True)
        monkeypatch.setattr(settings, "RAZORPAY_KEY_SECRET", "test_platform_secret")

        await self._org_with_price(db_session, registered_user, widget_token="wt_vfy2")
        org_id = registered_user["user"]["org_id"]
        sess = await self._session(db_session, org_id)
        order_id, pay_id = await self._verify_attempt(db_session, org_id, sess)
        r = await client.post("/api/v1/widget/payment/verify", json={
            "session_id": str(sess.id), "razorpay_order_id": order_id,
            "razorpay_payment_id": pay_id, "razorpay_signature": "deadbeef",
            "widget_token": "wt_vfy2"})
        assert r.status_code == 400

    async def test_payment_webhook_duplicate_safe(self, client, registered_user, db_session):
        from app.models.payment import PaymentAttempt

        org_id = registered_user["user"]["org_id"]
        order_id = f"order_wh_{uuid.uuid4().hex[:8]}"
        db_session.add(PaymentAttempt(
            org_id=org_id, razorpay_order_id=order_id, amount_paise=100000,
            currency="INR", status="created", verification_status="pending",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)))
        await db_session.commit()
        payload = {"id": f"evt_wh_{uuid.uuid4().hex[:8]}", "event": "payment.captured",
                   "payload": {"payment": {"entity": {"id": "pay_wh1", "order_id": order_id,
                                                      "amount": 100000, "currency": "INR",
                                                      "status": "captured"}}}}
        r1 = await client.post("/api/v1/webhooks/razorpay", json=payload)
        assert r1.status_code == 200 and r1.json()["ok"] is True
        r2 = await client.post("/api/v1/webhooks/razorpay", json=payload)
        assert r2.json().get("deduplicated") is True

        from sqlalchemy.future import select
        att = (await db_session.execute(
            select(PaymentAttempt).where(PaymentAttempt.razorpay_order_id == order_id)
        )).scalars().first()
        assert att.status == "paid"  # legacy NULL route_mode: paid, no transfer

    async def test_transfer_webhook_updates_and_dedups(self, client, registered_user,
                                                       monkeypatch, db_session):
        monkeypatch.setattr(settings, "RAZORPAY_ROUTE_ENABLED", True)
        from app.models.razorpay_route import RazorpayTransfer

        org_id = registered_user["user"]["org_id"]
        pay_id = f"pay_wh2_{uuid.uuid4().hex[:6]}"
        db_session.add(RazorpayTransfer(
            org_id=org_id, razorpay_payment_id=pay_id,
            razorpay_account_id="acc_wh2", razorpay_transfer_id="trf_wh2",
            gross_paise=100000, commission_paise=10000, provider_paise=90000,
            currency="INR", status="processing", idempotency_key=f"route_{uuid.uuid4().hex}"))
        await db_session.commit()
        payload = {"id": f"evt_wh2_{uuid.uuid4().hex[:8]}", "event": "transfer.processed",
                   "payload": {"transfer": {"entity": {"id": "trf_wh2", "status": "processed",
                                                       "source": pay_id, "recipient": "acc_wh2"}}}}
        r1 = await client.post("/api/v1/webhooks/razorpay", json=payload)
        assert r1.status_code == 200
        r2 = await client.post("/api/v1/webhooks/razorpay", json=payload)
        assert r2.json().get("deduplicated") is True

        from sqlalchemy.future import select
        row = (await db_session.execute(
            select(RazorpayTransfer).where(RazorpayTransfer.razorpay_transfer_id == "trf_wh2")
        )).scalars().first()
        assert row.status == "processed"
        assert row.processed_at is not None

    async def test_production_secret_guard(self, client, monkeypatch):
        # Route enabled + live mode + no webhook secret -> reject unsigned webhook.
        monkeypatch.setattr(settings, "RAZORPAY_ROUTE_ENABLED", True)
        monkeypatch.setattr(settings, "RAZORPAY_PARTNER_MODE", "live")
        monkeypatch.setattr(settings, "RAZORPAY_WEBHOOK_SECRET", "")
        r = await client.post("/api/v1/webhooks/razorpay", json={"event": "ping"})
        assert r.status_code == 400
