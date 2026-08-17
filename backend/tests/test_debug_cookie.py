import pytest
from httpx import AsyncClient

CSRF = {"X-Requested-With": "XMLHttpRequest"}


class TestDebugCSRF:
    async def test_debug_cookie(
        self, registered_user, client: AsyncClient, auth_helpers
    ):
        login_resp = await auth_helpers["login"](
            registered_user["email"], registered_user["password"]
        )
        print("LOGIN-RESP", login_resp.status_code)
        print("COOKIES", dict(login_resp.cookies))
        resp = await client.post(
            "/api/v1/auth/logout",
            cookies=dict(login_resp.cookies),
        )
        print("LOGOUT", resp.status_code, resp.text)
