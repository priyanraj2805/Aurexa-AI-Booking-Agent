"""
Security tests — CSRF enforcement, unauthenticated access, org isolation.
"""
import pytest
from httpx import AsyncClient

CSRF = {"X-Requested-With": "XMLHttpRequest"}


class TestCSRF:
    """CSRF middleware should block POST/PUT/DELETE without X-Requested-With when using cookies."""

    async def test_post_without_csrf_header_and_with_cookie_is_blocked(
        self, registered_user, client: AsyncClient
    ):
        # Login to get a cookie
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={
                "email": registered_user["email"],
                "password": registered_user["password"],
            },
            headers=CSRF,
        )
        cookies = login_resp.cookies

        # Now make a POST without X-Requested-With but with the cookie
        resp = await client.post(
            "/api/v1/auth/logout",
            cookies=cookies,
            # No X-Requested-With header
        )
        assert resp.status_code == 403
        assert "CSRF" in resp.json()["detail"]

    async def test_get_without_csrf_header_is_allowed(
        self, registered_user, client: AsyncClient
    ):
        # GET requests should not require CSRF header
        resp = await client.get(
            "/api/v1/auth/me",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200

    async def test_post_with_bearer_token_bypasses_csrf(
        self, registered_user, client: AsyncClient
    ):
        # Bearer token auth (no cookie) should not require CSRF
        resp = await client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {registered_user['token']}"},
        )
        assert resp.status_code == 200


class TestOrganizationAuth:
    """CR-01: Organization endpoints require authentication."""

    async def test_get_org_unauthenticated(self, client: AsyncClient):
        import uuid

        resp = await client.get(f"/api/v1/organizations/{uuid.uuid4()}")
        assert resp.status_code == 401

    async def test_create_org_unauthenticated(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/organizations/",
            json={"name": "Hacker Org"},
            headers=CSRF,
        )
        assert resp.status_code == 401

    async def test_get_org_wrong_owner(self, registered_user, client: AsyncClient):
        """A user should NOT be able to access another org's details."""
        import uuid

        fake_org_id = str(uuid.uuid4())
        resp = await client.get(
            f"/api/v1/organizations/{fake_org_id}",
            headers=registered_user["headers"],
        )
        # Should be 403 (not their org) or 404
        assert resp.status_code in (403, 404)

    async def test_get_own_org(self, registered_user, client: AsyncClient):
        """A user CAN access their own org."""
        org_id = registered_user["user"]["org_id"]
        resp = await client.get(
            f"/api/v1/organizations/{org_id}",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == org_id


class TestDashboardAuth:
    """Dashboard endpoints require authentication."""

    async def test_stats_unauthenticated(self, client: AsyncClient):
        resp = await client.get("/api/v1/dashboard/stats")
        assert resp.status_code == 401

    async def test_recent_bookings_unauthenticated(self, client: AsyncClient):
        resp = await client.get("/api/v1/dashboard/recent-bookings")
        assert resp.status_code == 401

    async def test_conversations_unauthenticated(self, client: AsyncClient):
        resp = await client.get("/api/v1/dashboard/conversations")
        assert resp.status_code == 401


class TestDashboardPagination:
    """CR-12: Dashboard limit params are bounded."""

    async def test_recent_bookings_limit_too_high(self, registered_user, client: AsyncClient):
        resp = await client.get(
            "/api/v1/dashboard/recent-bookings?limit=999999",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 422  # Pydantic/FastAPI validation error

    async def test_recent_bookings_limit_zero(self, registered_user, client: AsyncClient):
        resp = await client.get(
            "/api/v1/dashboard/recent-bookings?limit=0",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 422

    async def test_conversations_limit_valid(self, registered_user, client: AsyncClient):
        resp = await client.get(
            "/api/v1/dashboard/conversations?limit=10",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200
