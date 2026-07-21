"""
Auth endpoint tests — register, login, /me, logout, edge cases.
"""
import pytest
from httpx import AsyncClient

CSRF = {"X-Requested-With": "XMLHttpRequest"}


class TestRegister:
    async def test_register_success(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/auth/register",
            json={
                "full_name": "Alice Smith",
                "org_name": "Alice Corp",
                "email": "alice@example.com",
                "password": "strongpass1",
            },
            headers=CSRF,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"]
        assert body["user"]["email"] == "alice@example.com"
        assert body["user"]["org_id"]

    async def test_register_duplicate_email(self, client: AsyncClient):
        payload = {
            "full_name": "Bob",
            "org_name": "Bob Inc",
            "email": "duplicate@example.com",
            "password": "pass123456",
        }
        resp1 = await client.post("/api/v1/auth/register", json=payload, headers=CSRF)
        assert resp1.status_code == 200

        resp2 = await client.post("/api/v1/auth/register", json=payload, headers=CSRF)
        assert resp2.status_code == 409
        assert "already exists" in resp2.json()["detail"].lower()

    async def test_register_short_password(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/auth/register",
            json={
                "full_name": "Eve",
                "org_name": "Eve Corp",
                "email": "eve@example.com",
                "password": "abc",  # too short (min 6)
            },
            headers=CSRF,
        )
        assert resp.status_code == 422  # Pydantic validation

    async def test_register_missing_fields(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/auth/register",
            json={"email": "missing@example.com"},
            headers=CSRF,
        )
        assert resp.status_code == 422

    async def test_register_sets_cookie(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/auth/register",
            json={
                "full_name": "Cookie Test",
                "org_name": "Cookie Org",
                "email": "cookie_test@example.com",
                "password": "pass123456",
            },
            headers=CSRF,
        )
        assert resp.status_code == 200
        assert "ds_token" in resp.cookies


class TestLogin:
    async def test_login_success(self, registered_user, client: AsyncClient):
        resp = await client.post(
            "/api/v1/auth/login",
            json={
                "email": registered_user["email"],
                "password": registered_user["password"],
            },
            headers=CSRF,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"]
        assert body["user"]["email"] == registered_user["email"]

    async def test_login_wrong_password(self, registered_user, client: AsyncClient):
        resp = await client.post(
            "/api/v1/auth/login",
            json={
                "email": registered_user["email"],
                "password": "wrongpassword",
            },
            headers=CSRF,
        )
        assert resp.status_code == 401

    async def test_login_nonexistent_email(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "noone@nowhere.com", "password": "whatever"},
            headers=CSRF,
        )
        assert resp.status_code == 401


class TestMe:
    async def test_me_with_bearer(self, registered_user, client: AsyncClient):
        resp = await client.get(
            "/api/v1/auth/me",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200
        assert resp.json()["email"] == registered_user["email"]

    async def test_me_unauthenticated(self, client: AsyncClient):
        resp = await client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    async def test_me_invalid_token(self, client: AsyncClient):
        resp = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert resp.status_code == 401


class TestLogout:
    async def test_logout_clears_cookie(self, registered_user, client: AsyncClient):
        resp = await client.post(
            "/api/v1/auth/logout",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200
        assert resp.json()["detail"] == "Logged out"
