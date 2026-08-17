"""
Auth endpoint tests — register (email-OTP gated), plain login, /me, logout, edge cases.
"""
import pytest
from httpx import AsyncClient

CSRF = {"X-Requested-With": "XMLHttpRequest"}


class TestOTP:
    async def test_send_register_otp(self, client: AsyncClient, auth_helpers):
        resp = await client.post(
            "/api/v1/auth/otp/send",
            json={"email": "otp_register@example.com", "purpose": "register"},
            headers=CSRF,
        )
        assert resp.status_code == 200
        assert resp.json()["detail"] == "Verification code sent"
        assert len(auth_helpers["latest_code"]("otp_register@example.com", "register")) == 6

    async def test_send_register_otp_rejects_existing_email(
        self, client: AsyncClient, auth_helpers, registered_user
    ):
        resp = await client.post(
            "/api/v1/auth/otp/send",
            json={"email": registered_user["email"], "purpose": "register"},
            headers=CSRF,
        )
        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"].lower()

    async def test_send_login_purpose_is_rejected(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/auth/otp/send",
            json={"email": "nobody@nowhere.com", "purpose": "login"},
            headers=CSRF,
        )
        # OTPs exist only for registration — login is a plain password flow.
        assert resp.status_code == 422

    async def test_send_otp_invalid_purpose(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/auth/otp/send",
            json={"email": "x@example.com", "purpose": "hack"},
            headers=CSRF,
        )
        assert resp.status_code == 422


class TestRegister:
    async def test_register_success(self, client: AsyncClient, auth_helpers):
        resp = await auth_helpers["register"](
            {
                "full_name": "Alice Smith",
                "org_name": "Alice Corp",
                "email": "alice@example.com",
                "password": "strongpass1",
            }
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"]
        assert body["user"]["email"] == "alice@example.com"
        assert body["user"]["org_id"]

    async def test_register_requires_otp(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/auth/register",
            json={
                "full_name": "No OTP",
                "org_name": "No OTP Org",
                "email": "nootp@example.com",
                "password": "pass123456",
                "otp_code": "000000",
            },
            headers=CSRF,
        )
        assert resp.status_code == 400
        assert "verification code" in resp.json()["detail"].lower()

    async def test_register_duplicate_email(self, client: AsyncClient, auth_helpers):
        payload = {
            "full_name": "Bob",
            "org_name": "Bob Inc",
            "email": "duplicate@example.com",
            "password": "pass123456",
        }
        resp1 = await auth_helpers["register"](payload)
        assert resp1.status_code == 200

        resp2 = await client.post(
            "/api/v1/auth/otp/send",
            json={"email": payload["email"], "purpose": "register"},
            headers=CSRF,
        )
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
                "otp_code": "123456",
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

    async def test_register_does_not_set_cookie(self, client: AsyncClient, auth_helpers):
        resp = await auth_helpers["register"](
            {
                "full_name": "Cookie Test",
                "org_name": "Cookie Org",
                "email": "cookie_test@example.com",
                "password": "pass123456",
            }
        )
        assert resp.status_code == 200
        assert "ds_token" not in resp.cookies


class TestLogin:
    async def test_login_success(self, registered_user, client: AsyncClient, auth_helpers):
        resp = await auth_helpers["login"](
            registered_user["email"], registered_user["password"]
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"]
        assert body["user"]["email"] == registered_user["email"]
        assert "ds_token" in resp.cookies

    async def test_login_does_not_require_otp(
        self, registered_user, client: AsyncClient, auth_helpers
    ):
        # Sign-in is a plain email + password flow: valid credentials must
        # yield a session immediately, without any emailed code.
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
        assert "otp_required" not in body
        assert body["access_token"]
        assert "ds_token" in resp.cookies
        assert all(
            item["purpose"] != "login" for item in auth_helpers["sent"]
        ), "No login OTP may be sent"

    async def test_login_wrong_password(self, registered_user, client: AsyncClient, auth_helpers):
        resp = await client.post(
            "/api/v1/auth/login",
            json={
                "email": registered_user["email"],
                "password": "wrongpassword",
            },
            headers=CSRF,
        )
        assert resp.status_code == 401
        assert all(
            item["purpose"] != "login" for item in auth_helpers["sent"]
        ), "An OTP must not be sent when the password is wrong"

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


class TestPasswordReset:
    async def test_forgot_sends_reset_otp(
        self, client: AsyncClient, auth_helpers, registered_user
    ):
        resp = await client.post(
            "/api/v1/auth/password/forgot",
            json={"email": registered_user["email"]},
            headers=CSRF,
        )
        assert resp.status_code == 200
        assert "if an account exists" in resp.json()["detail"].lower()
        code = auth_helpers["latest_code"](registered_user["email"], "password_reset")
        assert len(code) == 6

    async def test_forgot_unknown_email_is_generic(self, client: AsyncClient, auth_helpers):
        resp = await client.post(
            "/api/v1/auth/password/forgot",
            json={"email": "ghost@nowhere.com"},
            headers=CSRF,
        )
        assert resp.status_code == 200
        assert "if an account exists" in resp.json()["detail"].lower()
        assert all(
            item["purpose"] != "password_reset" for item in auth_helpers["sent"]
        ), "No reset code may be sent for an unknown email"

    async def test_forgot_reissues_code_on_repeat(
        self, client: AsyncClient, auth_helpers, registered_user
    ):
        # A repeat forgot request reissues the code (the resend budget is
        # enforced by the rate limiter, not per-request cooldown).
        resp1 = await client.post(
            "/api/v1/auth/password/forgot",
            json={"email": registered_user["email"]},
            headers=CSRF,
        )
        assert resp1.status_code == 200
        resp2 = await client.post(
            "/api/v1/auth/password/forgot",
            json={"email": registered_user["email"]},
            headers=CSRF,
        )
        assert resp2.status_code == 200
        resets = [
            item
            for item in auth_helpers["sent"]
            if item["to_email"] == registered_user["email"]
            and item["purpose"] == "password_reset"
        ]
        assert len(resets) == 2

    async def test_reset_with_correct_code(
        self, client: AsyncClient, auth_helpers, registered_user
    ):
        email = registered_user["email"]
        await client.post(
            "/api/v1/auth/password/forgot",
            json={"email": email},
            headers=CSRF,
        )
        code = auth_helpers["latest_code"](email, "password_reset")

        resp = await client.post(
            "/api/v1/auth/password/reset",
            json={"email": email, "otp_code": code, "new_password": "brandnewpass1"},
            headers=CSRF,
        )
        assert resp.status_code == 200

        # Old password must no longer work, new one must.
        old_login = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": registered_user["password"]},
            headers=CSRF,
        )
        assert old_login.status_code == 401

        new_login = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "brandnewpass1"},
            headers=CSRF,
        )
        assert new_login.status_code == 200
        assert "ds_token" in new_login.cookies

    async def test_reset_wrong_code(self, client: AsyncClient, auth_helpers, registered_user):
        email = registered_user["email"]
        await client.post(
            "/api/v1/auth/password/forgot",
            json={"email": email},
            headers=CSRF,
        )
        resp = await client.post(
            "/api/v1/auth/password/reset",
            json={"email": email, "otp_code": "000000", "new_password": "brandnewpass1"},
            headers=CSRF,
        )
        assert resp.status_code == 400
        assert "invalid or expired" in resp.json()["detail"].lower()

    async def test_reset_unknown_email(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/auth/password/reset",
            json={
                "email": "ghost@nowhere.com",
                "otp_code": "123456",
                "new_password": "brandnewpass1",
            },
            headers=CSRF,
        )
        assert resp.status_code == 404

    async def test_reset_requires_password_policy(
        self, client: AsyncClient, auth_helpers, registered_user
    ):
        email = registered_user["email"]
        await client.post(
            "/api/v1/auth/password/forgot",
            json={"email": email},
            headers=CSRF,
        )
        code = auth_helpers["latest_code"](email, "password_reset")
        resp = await client.post(
            "/api/v1/auth/password/reset",
            json={"email": email, "otp_code": code, "new_password": "short"},
            headers=CSRF,
        )
        assert resp.status_code in (400, 422)


class TestLogout:
    async def test_logout_clears_cookie(self, registered_user, client: AsyncClient):
        resp = await client.post(
            "/api/v1/auth/logout",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200
        assert resp.json()["detail"] == "Logged out"
