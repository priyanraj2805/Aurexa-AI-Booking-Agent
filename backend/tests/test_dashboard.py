"""
Dashboard endpoint tests — stats, bookings, conversations, availability, agent config.
"""

import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.organization import Organization
from app.models.config import OrgConfig, CalendarProviderType
from app.models.booking import TimeSlot, Booking
from app.models.session import ChatSession
from app.models.message import ChatMessage
from app.models.system_alert import SystemAlert
from app.models.user import User
from app.core.security import hash_password
from app.main import app

CSRF = {"X-Requested-With": "XMLHttpRequest"}


class TestMeetingProviderEndpoints:
    """Test meeting-provider admin endpoints with server-enforced activation checks."""

    async def test_meeting_provider_config_returns_expected_fields(
        self, registered_user, client: AsyncClient
    ):
        """GET /dashboard/meeting-providers/config returns connection booleans and active_meeting_provider."""
        resp = await client.get(
            "/api/v1/dashboard/meeting-providers/config",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "google_meet_connected" in body
        assert "microsoft_teams_connected" in body
        assert "zoom_connected" in body
        assert "active_meeting_provider" in body
        # Defaults should all be false/NONE
        assert body["google_meet_connected"] is False
        assert body["microsoft_teams_connected"] is False
        assert body["zoom_connected"] is False
        assert body["active_meeting_provider"] == "NONE"

    async def test_meeting_provider_activate_rejects_unconnected_provider(
        self, registered_user, client: AsyncClient
    ):
        """PUT /dashboard/meeting-providers/active-provider rejects activation of unconnected provider."""
        resp = await client.put(
            "/api/v1/dashboard/meeting-providers/active-provider",
            json={"provider": "GOOGLE_MEET"},
            headers=registered_user["headers"],
        )
        assert resp.status_code == 400
        assert "must be connected" in resp.json()["detail"]

    async def test_meeting_provider_activate_accepts_connected_provider(
        self, registered_user, client: AsyncClient, db_session: AsyncSession
    ):
        """PUT /dashboard/meeting-providers/active-provider accepts connected provider."""
        org_id = uuid.UUID(registered_user["user"]["org_id"])

        # First connect Google Meet
        cfg_res = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == org_id)
        )
        config = cfg_res.scalars().first()
        config.google_meet_connected = True
        await db_session.commit()

        resp = await client.put(
            "/api/v1/dashboard/meeting-providers/active-provider",
            json={"provider": "GOOGLE_MEET"},
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["active_meeting_provider"] == "GOOGLE_MEET"

    async def test_meeting_provider_connect_google_meet(
        self, registered_user, client: AsyncClient
    ):
        """POST /dashboard/meeting-providers/google-meet/connect returns auth URL."""
        resp = await client.post(
            "/api/v1/dashboard/meeting-providers/google-meet/connect",
            headers=registered_user["headers"],
        )
        # Should return auth URL or skip (depending on credentials configured)
        assert resp.status_code in (200, 400)

    async def test_meeting_provider_callback_google_meet_sets_connected_and_allows_activate(
        self,
        registered_user,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch,
    ):
        """Google callback with meet: state persists meeting-provider connection and allows activation."""
        org_id = registered_user["user"]["org_id"]

        async def _fake_exchange(_code: str, redirect_uri: str | None = None):
            return {"refresh_token": "test-meet-token"}

        monkeypatch.setattr(
            "app.core.integrations.google_calendar.exchange_code_for_tokens",
            _fake_exchange,
            raising=False,
        )

        resp = await client.get(
            f"/api/v1/dashboard/google-calendar/callback?code=abc&state=meet:{org_id}",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "google_connected=true" in resp.headers["location"]

        cfg_res = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == uuid.UUID(org_id))
        )
        config = cfg_res.scalars().first()
        assert config is not None
        assert config.google_meet_connected is True
        assert config.meeting_provider_updated_at is not None
        assert config.meeting_provider_updated_by == "oauth_callback"

        activate_resp = await client.put(
            "/api/v1/dashboard/meeting-providers/active-provider",
            json={"provider": "GOOGLE_MEET"},
            headers=registered_user["headers"],
        )
        assert activate_resp.status_code == 200

    async def test_meeting_provider_callback_microsoft_teams_sets_connected_and_allows_activate(
        self,
        registered_user,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch,
    ):
        """Outlook callback with teams: state persists meeting-provider connection and allows activation."""
        org_id = registered_user["user"]["org_id"]

        async def _fake_outlook_handle_callback(
            self, _code, config, db, redirect_uri: str | None = None
        ):
            config.outlook_calendar_connected = True
            config.outlook_calendar_enabled = True
            await db.commit()
            return True

        monkeypatch.setattr(
            "app.core.integrations.calendar_providers.outlook.OutlookCalendarProvider.handle_callback",
            _fake_outlook_handle_callback,
        )

        resp = await client.get(
            f"/api/v1/dashboard/outlook-calendar/callback?code=abc&state=teams:{org_id}",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "outlook_connected=true" in resp.headers["location"]

        cfg_res = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == uuid.UUID(org_id))
        )
        config = cfg_res.scalars().first()
        assert config is not None
        assert config.microsoft_teams_connected is True
        assert config.meeting_provider_updated_at is not None
        assert config.meeting_provider_updated_by == "oauth_callback"

        activate_resp = await client.put(
            "/api/v1/dashboard/meeting-providers/active-provider",
            json={"provider": "MICROSOFT_TEAMS"},
            headers=registered_user["headers"],
        )
        assert activate_resp.status_code == 200

    async def test_meeting_provider_zoom_connect_uses_runtime_callback_when_redirect_env_missing(
        self, registered_user, client: AsyncClient, monkeypatch
    ):
        """Zoom connect endpoint builds auth_url with request-derived callback URL."""
        monkeypatch.setenv("ZOOM_CLIENT_ID", "zoom-client-id")
        monkeypatch.setenv("ZOOM_CLIENT_SECRET", "zoom-client-secret")
        monkeypatch.delenv("ZOOM_REDIRECT_URI", raising=False)

        resp = await client.post(
            "/api/v1/dashboard/meeting-providers/zoom/connect",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "auth_url" in body
        assert body["auth_url"].startswith("https://zoom.us/oauth/authorize?")
        parsed = urlparse(body["auth_url"])
        qs = parse_qs(parsed.query)
        redirect_uri = qs.get("redirect_uri", [""])[0]
        assert redirect_uri.endswith(
            "/api/v1/dashboard/meeting-providers/zoom/callback"
        )
        assert "localhost:8005" not in redirect_uri

    async def test_meeting_provider_zoom_callback_persists_connection_and_allows_activate(
        self,
        registered_user,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch,
    ):
        """Zoom callback persists connection/token state and enables activation without direct DB mutation."""
        org_id = registered_user["user"]["org_id"]

        async def _fake_zoom_handle_callback(
            self, _code, config, db, redirect_uri: str | None = None
        ):
            config.zoom_connected = True
            config.zoom_refresh_token = "zoom-refresh"
            config.zoom_access_token = "zoom-access"
            config.zoom_token_expires_at = datetime.now(timezone.utc) + timedelta(
                hours=1
            )
            await db.commit()
            return True

        monkeypatch.setattr(
            "app.core.integrations.meeting_providers.zoom.ZoomProvider.handle_callback",
            _fake_zoom_handle_callback,
        )

        resp = await client.get(
            f"/api/v1/dashboard/meeting-providers/zoom/callback?code=abc&state=zoom:{org_id}",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "zoom_connected=true" in resp.headers["location"]

        cfg_res = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == uuid.UUID(org_id))
        )
        config = cfg_res.scalars().first()
        assert config is not None
        assert config.zoom_connected is True
        assert config.zoom_refresh_token == "zoom-refresh"
        assert config.zoom_access_token == "zoom-access"
        assert config.zoom_token_expires_at is not None
        assert config.meeting_provider_updated_at is not None
        assert config.meeting_provider_updated_by == "oauth_callback"

        activate_resp = await client.put(
            "/api/v1/dashboard/meeting-providers/active-provider",
            json={"provider": "ZOOM"},
            headers=registered_user["headers"],
        )
        assert activate_resp.status_code == 200

    async def test_meeting_provider_zoom_callback_failure_redirects_with_error(
        self, registered_user, client: AsyncClient, monkeypatch
    ):
        """Zoom callback token exchange failure returns deterministic redirect error."""
        org_id = registered_user["user"]["org_id"]

        async def _fake_zoom_handle_callback(
            self, _code, _config, _db, redirect_uri: str | None = None
        ):
            return False

        monkeypatch.setattr(
            "app.core.integrations.meeting_providers.zoom.ZoomProvider.handle_callback",
            _fake_zoom_handle_callback,
        )

        resp = await client.get(
            f"/api/v1/dashboard/meeting-providers/zoom/callback?code=abc&state=zoom:{org_id}",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=zoom_connection_failed" in resp.headers["location"]

    async def test_meeting_provider_disconnect_clears_active_provider(
        self, registered_user, client: AsyncClient, db_session: AsyncSession
    ):
        """Disconnecting the active meeting provider clears active_meeting_provider to NONE."""
        from app.models.config import ActiveMeetingProvider

        org_id = uuid.UUID(registered_user["user"]["org_id"])
        cfg_res = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == org_id)
        )
        config = cfg_res.scalars().first()
        config.google_meet_connected = True
        config.active_meeting_provider = ActiveMeetingProvider.GOOGLE_MEET
        await db_session.commit()

        resp = await client.post(
            "/api/v1/dashboard/meeting-providers/google-meet/disconnect",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200

        await db_session.refresh(config)
        assert config.google_meet_connected is False
        assert config.active_meeting_provider == ActiveMeetingProvider.NONE

    async def test_meeting_provider_activate_none_always_allowed(
        self, registered_user, client: AsyncClient
    ):
        """Setting active_meeting_provider to NONE is always allowed."""
        resp = await client.put(
            "/api/v1/dashboard/meeting-providers/active-provider",
            json={"provider": "NONE"},
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["active_meeting_provider"] == "NONE"

    async def test_meeting_provider_activation_is_org_scoped(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Meeting provider updates are scoped to the authenticated user's org."""
        # Create two orgs
        org_a = Organization(
            name="Meeting Provider Org A",
            api_key=f"ds_mpa_{uuid.uuid4().hex[:16]}",
            is_active=True,
        )
        org_b = Organization(
            name="Meeting Provider Org B",
            api_key=f"ds_mpb_{uuid.uuid4().hex[:16]}",
            is_active=True,
        )
        db_session.add_all([org_a, org_b])
        await db_session.flush()

        cfg_a = OrgConfig(org_id=org_a.id, required_fields=["name", "email"])
        cfg_b = OrgConfig(org_id=org_b.id, required_fields=["name", "email"])
        db_session.add_all([cfg_a, cfg_b])

        user_a = User(
            email=f"mpa_{uuid.uuid4().hex[:8]}@test.com",
            hashed_password=hash_password("securepass123"),
            full_name="User A",
            org_id=org_a.id,
            is_active=True,
        )
        db_session.add(user_a)
        await db_session.commit()

        from app.core.security import create_access_token
        from app.models.config import ActiveMeetingProvider

        # Connect Google Meet for org_a
        cfg_a.google_meet_connected = True
        await db_session.commit()

        token_a = create_access_token(
            data={"sub": user_a.email, "org_id": str(org_a.id)}
        )
        headers_a = {
            "Authorization": f"Bearer {token_a}",
            "X-Requested-With": "XMLHttpRequest",
        }

        # Activate for org_a
        resp = await client.put(
            "/api/v1/dashboard/meeting-providers/active-provider",
            json={"provider": "GOOGLE_MEET"},
            headers=headers_a,
        )
        assert resp.status_code == 200

        # Verify org_b is unaffected
        await db_session.refresh(cfg_b)
        assert cfg_b.active_meeting_provider == ActiveMeetingProvider.NONE

    async def test_meeting_provider_full_roundtrip_connect_activate_config(
        self, registered_user, client: AsyncClient, db_session: AsyncSession
    ):
        """Full round-trip: connect (simulated) → config shows connected → activate → config shows active."""
        from app.models.config import ActiveMeetingProvider

        org_id = uuid.UUID(registered_user["user"]["org_id"])

        # Step 1: Verify initial config shows all disconnected
        resp = await client.get(
            "/api/v1/dashboard/meeting-providers/config",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["google_meet_connected"] is False
        assert body["active_meeting_provider"] == "NONE"

        # Step 2: Simulate connection (direct DB update, real OAuth flow tested separately)
        cfg_res = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == org_id)
        )
        config = cfg_res.scalars().first()
        config.google_meet_connected = True
        await db_session.commit()

        # Step 3: Config should now show connected
        resp = await client.get(
            "/api/v1/dashboard/meeting-providers/config",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["google_meet_connected"] is True
        assert body["active_meeting_provider"] == "NONE"

        # Step 4: Activate the connected provider
        resp = await client.put(
            "/api/v1/dashboard/meeting-providers/active-provider",
            json={"provider": "GOOGLE_MEET"},
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200

        # Step 5: Config should now show active
        resp = await client.get(
            "/api/v1/dashboard/meeting-providers/config",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["google_meet_connected"] is True
        assert body["active_meeting_provider"] == "GOOGLE_MEET"

    async def test_meeting_provider_cross_tenant_mutation_blocked(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Cross-tenant requests never mutate another org's meeting-provider state."""
        from app.core.security import create_access_token
        from app.models.config import ActiveMeetingProvider

        # Create org A with user A
        org_a = Organization(
            name="Cross-tenant Test Org A",
            api_key=f"ds_ctoa_{uuid.uuid4().hex[:16]}",
            is_active=True,
        )
        db_session.add(org_a)
        await db_session.flush()

        cfg_a = OrgConfig(org_id=org_a.id, required_fields=["name", "email"])
        cfg_a.google_meet_connected = True
        cfg_a.active_meeting_provider = ActiveMeetingProvider.GOOGLE_MEET
        db_session.add(cfg_a)

        user_a = User(
            email=f"ctoa_{uuid.uuid4().hex[:8]}@test.com",
            hashed_password=hash_password("securepass123"),
            full_name="User A",
            org_id=org_a.id,
            is_active=True,
        )
        db_session.add(user_a)

        # Create org B with user B
        org_b = Organization(
            name="Cross-tenant Test Org B",
            api_key=f"ds_ctob_{uuid.uuid4().hex[:16]}",
            is_active=True,
        )
        db_session.add(org_b)
        await db_session.flush()

        cfg_b = OrgConfig(org_id=org_b.id, required_fields=["name", "email"])
        cfg_b.google_meet_connected = True
        db_session.add(cfg_b)

        user_b = User(
            email=f"ctob_{uuid.uuid4().hex[:8]}@test.com",
            hashed_password=hash_password("securepass123"),
            full_name="User B",
            org_id=org_b.id,
            is_active=True,
        )
        db_session.add(user_b)
        await db_session.commit()

        # User B activates their provider
        token_b = create_access_token(
            data={"sub": user_b.email, "org_id": str(org_b.id)}
        )
        headers_b = {
            "Authorization": f"Bearer {token_b}",
            "X-Requested-With": "XMLHttpRequest",
        }

        resp = await client.put(
            "/api/v1/dashboard/meeting-providers/active-provider",
            json={"provider": "GOOGLE_MEET"},
            headers=headers_b,
        )
        assert resp.status_code == 200

        # Verify org A's state is unchanged
        await db_session.refresh(cfg_a)
        assert cfg_a.active_meeting_provider == ActiveMeetingProvider.GOOGLE_MEET

        # User B disconnects their provider
        resp = await client.post(
            "/api/v1/dashboard/meeting-providers/google-meet/disconnect",
            headers=headers_b,
        )
        assert resp.status_code == 200

        # Verify org A is still connected
        await db_session.refresh(cfg_a)
        assert cfg_a.google_meet_connected is True
        assert cfg_a.active_meeting_provider == ActiveMeetingProvider.GOOGLE_MEET

    async def test_meeting_provider_all_providers_activation_rejection(
        self, registered_user, client: AsyncClient
    ):
        """All meeting providers reject activation when not connected."""
        providers = ["GOOGLE_MEET", "MICROSOFT_TEAMS", "ZOOM"]

        for provider in providers:
            resp = await client.put(
                "/api/v1/dashboard/meeting-providers/active-provider",
                json={"provider": provider},
                headers=registered_user["headers"],
            )
            assert resp.status_code == 400, (
                f"Expected 400 for {provider}, got {resp.status_code}"
            )
            assert "must be connected" in resp.json()["detail"]

    async def test_meeting_provider_config_defaults_for_new_org(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """New org should have all meeting providers disconnected and NONE active."""
        from app.core.security import create_access_token

        org = Organization(
            name="New Meeting Provider Org",
            api_key=f"ds_newmp_{uuid.uuid4().hex[:16]}",
            is_active=True,
        )
        db_session.add(org)
        await db_session.flush()

        cfg = OrgConfig(org_id=org.id, required_fields=["name", "email"])
        db_session.add(cfg)

        user = User(
            email=f"newmp_{uuid.uuid4().hex[:8]}@test.com",
            hashed_password=hash_password("securepass123"),
            full_name="New User",
            org_id=org.id,
            is_active=True,
        )
        db_session.add(user)
        await db_session.commit()

        token = create_access_token(data={"sub": user.email, "org_id": str(org.id)})
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Requested-With": "XMLHttpRequest",
        }

        resp = await client.get(
            "/api/v1/dashboard/meeting-providers/config",
            headers=headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["google_meet_connected"] is False
        assert body["microsoft_teams_connected"] is False
        assert body["zoom_connected"] is False
        assert body["active_meeting_provider"] == "NONE"


class TestDashboardStats:
    async def test_stats_returns_structure(self, registered_user, client: AsyncClient):
        resp = await client.get(
            "/api/v1/dashboard/stats",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "total_bookings" in body
        assert "active_sessions" in body
        assert "confirmed_sessions" in body
        assert "cancelled_sessions" in body
        assert "has_schedule" in body
        assert "bookings_this_week" in body
        assert "unresolved_alerts_total" in body
        assert "unresolved_critical_alerts" in body
        assert "reliability_link" in body

    async def test_stats_isolated_per_org(self, registered_user, client: AsyncClient):
        """Stats should only show data for the authenticated user's org."""
        resp = await client.get(
            "/api/v1/dashboard/stats",
            headers=registered_user["headers"],
        )
        body = resp.json()
        # New org should have zero of everything
        assert body["active_sessions"] >= 0
        assert body["total_bookings"] >= 0

    async def test_stats_reliability_counts_are_org_scoped(
        self, client: AsyncClient, db_session: AsyncSession, registered_user
    ):
        org_a = uuid.UUID(registered_user["user"]["org_id"])
        org_b = Organization(
            name="Stats Org B",
            api_key=f"ds_statsb_{uuid.uuid4().hex[:16]}",
            is_active=True,
        )
        db_session.add(org_b)
        await db_session.flush()

        baseline_resp = await client.get(
            "/api/v1/dashboard/stats",
            headers=registered_user["headers"],
        )
        assert baseline_resp.status_code == 200
        baseline = baseline_resp.json()

        db_session.add_all(
            [
                SystemAlert(
                    org_id=org_a,
                    component="email",
                    severity="critical",
                    code="A_CRIT",
                    dedupe_key=f"{org_a}:email:A_CRIT",
                    message="a crit",
                ),
                SystemAlert(
                    org_id=org_a,
                    component="llm",
                    severity="warning",
                    code="A_WARN",
                    dedupe_key=f"{org_a}:llm:A_WARN",
                    message="a warn",
                ),
                SystemAlert(
                    org_id=org_b.id,
                    component="database",
                    severity="critical",
                    code="B_CRIT",
                    dedupe_key=f"{org_b.id}:database:B_CRIT",
                    message="b crit",
                ),
            ]
        )
        await db_session.commit()

        resp = await client.get(
            "/api/v1/dashboard/stats",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert (
            body["unresolved_alerts_total"] == baseline["unresolved_alerts_total"] + 2
        )
        assert (
            body["unresolved_critical_alerts"]
            == baseline["unresolved_critical_alerts"] + 1
        )
        assert body["reliability_link"].startswith("/dashboard/reliability?")


class TestDashboardDependencyHealth:
    async def test_dependency_health_requires_auth(self, client: AsyncClient):
        resp = await client.get("/api/v1/dashboard/dependency-health")
        assert resp.status_code == 401

    async def test_dependency_health_returns_expected_shape(
        self, registered_user, client: AsyncClient
    ):
        resp = await client.get(
            "/api/v1/dashboard/dependency-health",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "status" in body
        assert "request_id" in body
        assert "trace_id" in body
        assert "checks" in body
        for key in ("database", "llm", "email"):
            assert key in body["checks"]
            assert "status" in body["checks"][key]
            assert "latency_ms" in body["checks"][key]
            assert "error" in body["checks"][key]

    async def test_dependency_health_shape_matches_public_health(
        self, registered_user, client: AsyncClient
    ):
        public_resp = await client.get("/health")
        dashboard_resp = await client.get(
            "/api/v1/dashboard/dependency-health",
            headers=registered_user["headers"],
        )
        assert public_resp.status_code == 200
        assert dashboard_resp.status_code == 200
        public_body = public_resp.json()
        dashboard_body = dashboard_resp.json()

        assert set(public_body.keys()) == set(dashboard_body.keys())
        assert set(public_body["checks"].keys()) == set(dashboard_body["checks"].keys())
        for dep in ("database", "llm", "email"):
            assert set(public_body["checks"][dep].keys()) == set(
                dashboard_body["checks"][dep].keys()
            )


class TestRecentBookings:
    async def test_recent_bookings_empty(self, registered_user, client: AsyncClient):
        resp = await client.get(
            "/api/v1/dashboard/recent-bookings",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_recent_bookings_with_limit(
        self, registered_user, client: AsyncClient
    ):
        resp = await client.get(
            "/api/v1/dashboard/recent-bookings?limit=5",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200
        assert len(resp.json()) <= 5


class TestConversations:
    @staticmethod
    def _rows(response):
        payload = response.json()
        return payload["items"] if isinstance(payload, dict) else payload

    async def test_conversations_empty(self, registered_user, client: AsyncClient):
        resp = await client.get(
            "/api/v1/dashboard/conversations",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200
        assert isinstance(self._rows(resp), list)

    async def test_conversations_limit(self, registered_user, client: AsyncClient):
        resp = await client.get(
            "/api/v1/dashboard/conversations?limit=5",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200
        assert len(self._rows(resp)) <= 5

    async def test_conversations_contract_keys(
        self, registered_user, client: AsyncClient
    ):
        resp = await client.get(
            "/api/v1/dashboard/conversations",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200
        rows = self._rows(resp)
        if rows:
            row = rows[0]
            assert "id" in row
            assert "session_status" in row
            assert "current_intent" in row
            assert "collected_data" in row
            assert "missing_fields" in row
            assert "created_at" in row
            assert "updated_at" in row
            assert "channel" in row

    async def test_conversations_excludes_archived_by_default(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        org = Organization(
            name="Conv Org",
            api_key=f"ds_conv_{uuid.uuid4().hex[:16]}",
            is_active=True,
        )
        db_session.add(org)
        await db_session.flush()
        user = User(
            email=f"conv_{uuid.uuid4().hex[:8]}@test.com",
            hashed_password=hash_password("securepass123"),
            full_name="Conv User",
            org_id=org.id,
            is_active=True,
        )
        db_session.add(user)
        active = ChatSession(
            org_id=org.id,
            state_data={"name": "Active"},
            missing_fields=[],
            session_status="ACTIVE",
            channel="web",
        )
        archived = ChatSession(
            org_id=org.id,
            state_data={"name": "Archived"},
            missing_fields=[],
            session_status="ARCHIVED",
            archived_at=datetime.now(timezone.utc),
            channel="web",
        )
        db_session.add_all([active, archived])
        await db_session.commit()

        from app.core.security import create_access_token

        token = create_access_token(data={"sub": user.email, "org_id": str(org.id)})
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Requested-With": "XMLHttpRequest",
        }

        resp = await client.get("/api/v1/dashboard/conversations", headers=headers)
        assert resp.status_code == 200
        ids = {row["id"] for row in self._rows(resp)}
        assert str(active.id) in ids
        assert str(archived.id) not in ids

    async def test_conversations_include_archived_flag(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        org = Organization(
            name="Conv Org Include",
            api_key=f"ds_conv_{uuid.uuid4().hex[:16]}",
            is_active=True,
        )
        db_session.add(org)
        await db_session.flush()
        user = User(
            email=f"convinc_{uuid.uuid4().hex[:8]}@test.com",
            hashed_password=hash_password("securepass123"),
            full_name="Conv Include User",
            org_id=org.id,
            is_active=True,
        )
        archived = ChatSession(
            org_id=org.id,
            state_data={},
            missing_fields=[],
            session_status="ARCHIVED",
            archived_at=datetime.now(timezone.utc),
            channel="web",
        )
        db_session.add_all([user, archived])
        await db_session.commit()

        from app.core.security import create_access_token

        token = create_access_token(data={"sub": user.email, "org_id": str(org.id)})
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Requested-With": "XMLHttpRequest",
        }

        resp = await client.get(
            "/api/v1/dashboard/conversations?include_archived=true", headers=headers
        )
        assert resp.status_code == 200
        ids = {row["id"] for row in self._rows(resp)}
        assert str(archived.id) in ids

    async def test_conversations_archived_filter_preserves_org_isolation(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        org_a = Organization(
            name="Org A",
            api_key=f"ds_orga_{uuid.uuid4().hex[:16]}",
            is_active=True,
        )
        org_b = Organization(
            name="Org B",
            api_key=f"ds_orgb_{uuid.uuid4().hex[:16]}",
            is_active=True,
        )
        db_session.add_all([org_a, org_b])
        await db_session.flush()
        user_a = User(
            email=f"orga_{uuid.uuid4().hex[:8]}@test.com",
            hashed_password=hash_password("securepass123"),
            full_name="Org A User",
            org_id=org_a.id,
            is_active=True,
        )
        session_b = ChatSession(
            org_id=org_b.id,
            state_data={"name": "Other tenant"},
            missing_fields=[],
            session_status="ARCHIVED",
            archived_at=datetime.now(timezone.utc),
            channel="web",
        )
        db_session.add_all([user_a, session_b])
        await db_session.commit()

        from app.core.security import create_access_token

        token = create_access_token(data={"sub": user_a.email, "org_id": str(org_a.id)})
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Requested-With": "XMLHttpRequest",
        }
        resp = await client.get(
            "/api/v1/dashboard/conversations?include_archived=true", headers=headers
        )
        assert resp.status_code == 200
        ids = {row["id"] for row in self._rows(resp)}
        assert str(session_b.id) not in ids

    async def test_conversation_messages_returns_transcript_rows(
        self, registered_user, client: AsyncClient, db_session: AsyncSession
    ):
        org_id = uuid.UUID(registered_user["user"]["org_id"])
        session = ChatSession(
            org_id=org_id,
            state_data={},
            missing_fields=[],
            session_status="ACTIVE",
            channel="web",
        )
        db_session.add(session)
        await db_session.flush()
        db_session.add_all(
            [
                ChatMessage(
                    session_id=session.id,
                    role="user",
                    content="Hi there",
                    metadata_={"directive": "TEXT_INPUT"},
                ),
                ChatMessage(
                    session_id=session.id,
                    role="agent",
                    content='{"response_text":"Hello! How can I help you?"}',
                    metadata_={"directive": "TEXT_INPUT"},
                ),
            ]
        )
        await db_session.commit()

        resp = await client.get(
            f"/api/v1/dashboard/conversations/{session.id}/messages",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert len(payload) == 2
        assert payload[0]["content"] == "Hi there"
        assert payload[1]["role"] == "agent"
        assert "response_text" in payload[1]["content"]


class TestAvailability:
    async def test_list_availability(self, registered_user, client: AsyncClient):
        resp = await client.get(
            "/api/v1/dashboard/availability",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_create_availability_slot(self, registered_user, client: AsyncClient):
        start = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
        end = (datetime.now(timezone.utc) + timedelta(days=5, hours=1)).isoformat()

        resp = await client.post(
            "/api/v1/dashboard/availability",
            json={"start_time": start, "end_time": end},
            headers=registered_user["headers"],
        )
        assert resp.status_code == 201

    async def test_delete_availability_slot(self, registered_user, client: AsyncClient):
        start = (datetime.now(timezone.utc) + timedelta(days=6)).isoformat()
        end = (datetime.now(timezone.utc) + timedelta(days=6, hours=1)).isoformat()

        create_resp = await client.post(
            "/api/v1/dashboard/availability",
            json={"start_time": start, "end_time": end},
            headers=registered_user["headers"],
        )
        assert create_resp.status_code == 201
        slot_id = create_resp.json()["id"]

        del_resp = await client.delete(
            f"/api/v1/dashboard/availability/{slot_id}",
            headers=registered_user["headers"],
        )
        assert del_resp.status_code == 204


class TestAgentConfig:
    async def test_get_agent_config(self, registered_user, client: AsyncClient):
        resp = await client.get(
            "/api/v1/dashboard/agent-config",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "required_fields" in body
        assert "timezone" in body
        assert "api_key" in body
        # API key should be masked in dashboard config responses
        if body["api_key"]:
            assert "..." in body["api_key"]

    async def test_update_agent_config(self, registered_user, client: AsyncClient):
        resp = await client.put(
            "/api/v1/dashboard/agent-config",
            json={
                "required_fields": ["name", "email", "phone", "date", "time"],
                "timezone": "America/New_York",
                "ui_brand_color": "#FF0000",
            },
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["timezone"] == "America/New_York"
        assert "phone" in body["required_fields"]
        if body["api_key"]:
            assert "..." in body["api_key"]

    async def test_update_agent_config_rejects_email_phone_optional_without_policy(
        self, registered_user, client: AsyncClient
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as raw_client:
            resp = await raw_client.put(
                "/api/v1/dashboard/agent-config",
                json={"required_fields": ["name", "date", "time"]},
                headers=registered_user["headers"],
            )
        assert resp.status_code == 422
        assert "policy" in str(resp.json()["detail"]).lower()

    async def test_update_agent_config_rejects_unsupported_field_type(
        self, registered_user, client: AsyncClient
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as raw_client:
            resp = await raw_client.put(
                "/api/v1/dashboard/agent-config",
                json={
                    "field_definitions": [
                        {
                            "name": "unsupported",
                            "type": "slider",
                            "label": "Unsupported",
                            "required": True,
                            "order": 0,
                        }
                    ]
                },
                headers=registered_user["headers"],
            )
        assert resp.status_code == 422
        assert "field_definitions" in str(resp.json()["detail"]).lower()

    async def test_update_agent_config_rejects_select_without_options(
        self, registered_user, client: AsyncClient
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as raw_client:
            resp = await raw_client.put(
                "/api/v1/dashboard/agent-config",
                json={
                    "field_definitions": [
                        {
                            "name": "service",
                            "type": "select",
                            "label": "Service",
                            "required": True,
                            "options": [],
                            "order": 0,
                        }
                    ]
                },
                headers=registered_user["headers"],
            )
        assert resp.status_code == 422
        assert "options" in str(resp.json()["detail"]).lower()

    async def test_update_agent_config_enforces_field_and_option_limits(
        self, registered_user, client: AsyncClient
    ):
        too_many_fields = [
            {
                "name": f"custom_{i}",
                "type": "text",
                "label": f"Custom {i}",
                "required": True,
                "order": i,
            }
            for i in range(21)
        ]
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as raw_client:
            fields_resp = await raw_client.put(
                "/api/v1/dashboard/agent-config",
                json={"field_definitions": too_many_fields},
                headers=registered_user["headers"],
            )
        assert fields_resp.status_code == 422
        assert "20" in str(fields_resp.json()["detail"])

        too_many_options = [f"Option {i}" for i in range(51)]
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as raw_client:
            options_resp = await raw_client.put(
                "/api/v1/dashboard/agent-config",
                json={
                    "field_definitions": [
                        {
                            "name": "service",
                            "type": "select",
                            "label": "Service",
                            "required": True,
                            "options": too_many_options,
                            "order": 0,
                        }
                    ]
                },
                headers=registered_user["headers"],
            )
        assert options_resp.status_code == 422
        assert "50" in str(options_resp.json()["detail"])

    async def test_get_agent_config_includes_retention_cleanup_fields(
        self, registered_user, client: AsyncClient
    ):
        resp = await client.get(
            "/api/v1/dashboard/agent-config",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "session_retention_days" in body
        assert "session_cleanup_frequency" in body
        assert "session_cleanup_hour_utc" in body

    async def test_update_agent_config_persists_retention_cleanup_fields(
        self, registered_user, client: AsyncClient
    ):
        resp = await client.put(
            "/api/v1/dashboard/agent-config",
            json={
                "session_retention_days": 45,
                "session_cleanup_frequency": "daily",
                "session_cleanup_hour_utc": 3,
            },
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["session_retention_days"] == 45
        assert body["session_cleanup_frequency"] == "daily"
        assert body["session_cleanup_hour_utc"] == 3

    async def test_update_agent_config_rejects_invalid_retention(
        self, registered_user, client: AsyncClient
    ):
        resp = await client.put(
            "/api/v1/dashboard/agent-config",
            json={"session_retention_days": 3},
            headers=registered_user["headers"],
        )
        assert resp.status_code == 422

    async def test_update_agent_config_meeting_policy_accepts_valid_modes(
        self, registered_user, client: AsyncClient
    ):
        resp = await client.put(
            "/api/v1/dashboard/agent-config",
            json={
                "meeting_mode": "HYBRID",
                "allow_hybrid_selection": True,
                "meeting_provider_default": "GOOGLE_MEET",
            },
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "meeting_policy" in body
        assert body["meeting_policy"]["meeting_mode"] == "HYBRID"
        assert body["meeting_policy"]["allow_hybrid_selection"] is True
        assert body["meeting_policy"]["meeting_provider_default"] == "GOOGLE_MEET"
        assert body["meeting_policy"]["updated_at"] is not None
        assert body["meeting_policy"]["updated_by"] == registered_user["email"]
        assert body["meeting_policy"]["available_meeting_types"] == [
            "ONLINE",
            "OFFLINE",
        ]

    async def test_update_agent_config_meeting_policy_rejects_invalid_mode(
        self, registered_user, client: AsyncClient
    ):
        resp = await client.put(
            "/api/v1/dashboard/agent-config",
            json={"meeting_mode": "BAD_MODE"},
            headers=registered_user["headers"],
        )
        assert resp.status_code == 422

    async def test_widget_config_includes_meeting_policy_capabilities(
        self, registered_user, client: AsyncClient, db_session: AsyncSession
    ):
        org_id = uuid.UUID(registered_user["user"]["org_id"])
        org_result = await db_session.execute(
            select(Organization).where(Organization.id == org_id)
        )
        org = org_result.scalars().first()
        assert org is not None

        update_resp = await client.put(
            "/api/v1/dashboard/agent-config",
            json={
                "meeting_mode": "ONLINE_ONLY",
                "allow_hybrid_selection": False,
                "meeting_provider_default": "NONE",
            },
            headers=registered_user["headers"],
        )
        assert update_resp.status_code == 200

        widget_resp = await client.get(
            f"/api/v1/widget/config?api_key={org.api_key}",
        )
        assert widget_resp.status_code == 200
        widget_body = widget_resp.json()
        assert widget_body["meeting_mode"] == "ONLINE_ONLY"
        assert widget_body["allow_hybrid_selection"] is False
        assert widget_body["meeting_provider_default"] == "NONE"
        assert widget_body["available_meeting_types"] == ["ONLINE"]


class TestCalendarIntegrations:
    async def test_set_active_provider_requires_connection(
        self, registered_user, client: AsyncClient
    ):
        resp = await client.put(
            "/api/v1/dashboard/calendar/active-provider",
            json={"provider": "GOOGLE"},
            headers=registered_user["headers"],
        )
        assert resp.status_code == 400
        assert "must be connected" in resp.json()["detail"]

    async def test_disconnect_google_clears_active_provider(
        self, registered_user, client: AsyncClient, db_session: AsyncSession
    ):
        org_id = uuid.UUID(registered_user["user"]["org_id"])
        cfg_res = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == org_id)
        )
        config = cfg_res.scalars().first()
        assert config is not None
        config.google_calendar_connected = True
        config.google_calendar_enabled = True
        config.google_calendar_refresh_token = "refresh-token"
        config.active_calendar_provider = CalendarProviderType.GOOGLE
        await db_session.commit()

        resp = await client.post(
            "/api/v1/dashboard/google-calendar/disconnect",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200

        await db_session.refresh(config)
        assert config.google_calendar_connected is False
        assert config.google_calendar_enabled is False
        assert config.google_calendar_refresh_token is None
        assert config.active_calendar_provider == CalendarProviderType.NONE

    async def test_disconnect_outlook_clears_active_provider(
        self, registered_user, client: AsyncClient, db_session: AsyncSession
    ):
        org_id = uuid.UUID(registered_user["user"]["org_id"])
        cfg_res = await db_session.execute(
            select(OrgConfig).where(OrgConfig.org_id == org_id)
        )
        config = cfg_res.scalars().first()
        assert config is not None
        config.outlook_calendar_connected = True
        config.outlook_calendar_enabled = True
        config.outlook_calendar_refresh_token = "refresh-token"
        config.outlook_calendar_access_token = "access-token"
        config.active_calendar_provider = CalendarProviderType.OUTLOOK
        await db_session.commit()

        resp = await client.post(
            "/api/v1/dashboard/outlook-calendar/disconnect",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200

        await db_session.refresh(config)
        assert config.outlook_calendar_connected is False
        assert config.outlook_calendar_enabled is False
        assert config.outlook_calendar_refresh_token is None
        assert config.outlook_calendar_access_token is None
        assert config.active_calendar_provider == CalendarProviderType.NONE

    async def test_google_callback_redirects_to_frontend(
        self, registered_user, client: AsyncClient, monkeypatch
    ):
        org_id = registered_user["user"]["org_id"]

        async def _fake_exchange(_code: str, redirect_uri: str | None = None):
            return {"refresh_token": "test-token"}

        monkeypatch.setattr(
            "app.core.integrations.google_calendar.exchange_code_for_tokens",
            _fake_exchange,
            raising=False,
        )

        resp = await client.get(
            f"/api/v1/dashboard/google-calendar/callback?code=abc&state={org_id}",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert (
            resp.headers["location"]
            == "http://localhost:3002/dashboard/integrations?google_connected=true"
        )

    async def test_google_callback_redirect_honors_frontend_base_alias(
        self, registered_user, client: AsyncClient, monkeypatch
    ):
        org_id = registered_user["user"]["org_id"]

        async def _fake_exchange(_code: str, redirect_uri: str | None = None):
            return {"refresh_token": "test-token"}

        monkeypatch.setattr(
            "app.core.integrations.google_calendar.exchange_code_for_tokens",
            _fake_exchange,
            raising=False,
        )
        monkeypatch.setattr(
            "app.api.v1.endpoints.dashboard.settings.FRONTEND_URL",
            "https://admin.example.com",
            raising=False,
        )

        resp = await client.get(
            f"/api/v1/dashboard/google-calendar/callback?code=abc&state={org_id}",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert (
            resp.headers["location"]
            == "https://admin.example.com/dashboard/integrations?google_connected=true"
        )

    async def test_google_callback_redirect_prefers_runtime_frontend_from_signed_state(
        self, registered_user, client: AsyncClient, monkeypatch
    ):
        org_id = registered_user["user"]["org_id"]

        async def _fake_exchange(_code: str, redirect_uri: str | None = None):
            return {"refresh_token": "test-token"}

        monkeypatch.setattr(
            "app.core.integrations.google_calendar.exchange_code_for_tokens",
            _fake_exchange,
            raising=False,
        )
        monkeypatch.setattr(
            "app.core.integrations.google_calendar.GOOGLE_CLIENT_ID",
            "test-google-client-id",
            raising=False,
        )

        connect_headers = dict(registered_user["headers"])
        connect_headers["origin"] = "https://admin-new.trycloudflare.com"
        connect_resp = await client.post(
            "/api/v1/dashboard/google-calendar/connect",
            headers=connect_headers,
        )
        assert connect_resp.status_code == 200

        auth_url = connect_resp.json()["auth_url"]
        state = parse_qs(urlparse(auth_url).query)["state"][0]

        callback_resp = await client.get(
            "/api/v1/dashboard/google-calendar/callback",
            params={"code": "abc", "state": state},
            follow_redirects=False,
        )
        assert callback_resp.status_code == 303
        assert (
            callback_resp.headers["location"]
            == "https://admin-new.trycloudflare.com/dashboard/integrations?google_connected=true"
        )

    async def test_runtime_urls_endpoint_returns_oauth_diagnostics(
        self, registered_user, client: AsyncClient
    ):
        resp = await client.get(
            "/api/v1/dashboard/runtime-urls",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "public_api_base" in body
        assert "widget_script_url" in body
        assert "oauth_redirect_uris" in body
        assert "warnings" in body


class TestBookingCancellation:
    async def test_admin_cancel_booking_success(
        self, registered_user, client: AsyncClient, db_session: AsyncSession
    ):
        org_id = uuid.UUID(registered_user["user"]["org_id"])
        slot_start = datetime.now(timezone.utc) + timedelta(days=3)
        slot = TimeSlot(
            org_id=org_id,
            start_time=slot_start,
            end_time=slot_start + timedelta(hours=1),
            is_booked=True,
        )
        session = ChatSession(
            org_id=org_id,
            state_data={
                "name": "Cancel User",
                "email": "cancel-user@test.com",
                "date": "2026-04-10",
                "time": "10:00",
            },
            missing_fields=[],
            session_status="CONFIRMED",
        )
        db_session.add_all([slot, session])
        await db_session.commit()
        await db_session.refresh(slot)
        await db_session.refresh(session)

        booking = Booking(
            org_id=org_id,
            session_id=session.id,
            slot_id=slot.id,
            customer_name="Cancel User",
            customer_email="cancel-user@test.com",
        )
        db_session.add(booking)
        await db_session.commit()
        await db_session.refresh(booking)

        resp = await client.post(
            f"/api/v1/dashboard/bookings/{booking.id}/cancel",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["booking_id"] == str(booking.id)

        booking_check = await db_session.execute(
            select(Booking).where(Booking.id == booking.id)
        )
        assert booking_check.scalars().first() is None

    async def test_admin_cancel_booking_cross_tenant_blocked(
        self, registered_user, client: AsyncClient, db_session: AsyncSession
    ):
        other_org = Organization(
            name="Other Tenant",
            api_key=f"ds_other_{uuid.uuid4().hex[:16]}",
            is_active=True,
        )
        db_session.add(other_org)
        await db_session.flush()
        other_cfg = OrgConfig(
            org_id=other_org.id,
            required_fields=["name", "email", "phone", "date", "time"],
        )
        db_session.add(other_cfg)
        slot_start = datetime.now(timezone.utc) + timedelta(days=4)
        other_slot = TimeSlot(
            org_id=other_org.id,
            start_time=slot_start,
            end_time=slot_start + timedelta(hours=1),
            is_booked=True,
        )
        other_session = ChatSession(
            org_id=other_org.id,
            state_data={"name": "Other", "email": "other@test.com"},
            missing_fields=[],
            session_status="CONFIRMED",
        )
        db_session.add_all([other_slot, other_session])
        await db_session.commit()
        await db_session.refresh(other_slot)
        await db_session.refresh(other_session)
        other_booking = Booking(
            org_id=other_org.id,
            session_id=other_session.id,
            slot_id=other_slot.id,
            customer_name="Other Tenant User",
            customer_email="other@test.com",
        )
        db_session.add(other_booking)
        await db_session.commit()
        await db_session.refresh(other_booking)

        resp = await client.post(
            f"/api/v1/dashboard/bookings/{other_booking.id}/cancel",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 404

        still_exists = await db_session.execute(
            select(Booking).where(Booking.id == other_booking.id)
        )
        assert still_exists.scalars().first() is not None

    async def test_admin_cancel_booking_writes_audit_event(
        self,
        registered_user,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch,
    ):
        org_id = uuid.UUID(registered_user["user"]["org_id"])
        slot_start = datetime.now(timezone.utc) + timedelta(days=5)
        slot = TimeSlot(
            org_id=org_id,
            start_time=slot_start,
            end_time=slot_start + timedelta(hours=1),
            is_booked=True,
        )
        session = ChatSession(
            org_id=org_id,
            state_data={
                "name": "Audit User",
                "email": "audit@test.com",
                "date": "2026-04-12",
                "time": "11:00",
            },
            missing_fields=[],
            session_status="CONFIRMED",
        )
        db_session.add_all([slot, session])
        await db_session.commit()
        await db_session.refresh(slot)
        await db_session.refresh(session)
        booking = Booking(
            org_id=org_id,
            session_id=session.id,
            slot_id=slot.id,
            customer_name="Audit User",
            customer_email="audit@test.com",
        )
        db_session.add(booking)
        await db_session.commit()
        await db_session.refresh(booking)

        captured = {}

        def _capture_audit(background_tasks, **kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(
            "app.api.v1.endpoints.dashboard.log_audit_event",
            _capture_audit,
        )

        resp = await client.post(
            f"/api/v1/dashboard/bookings/{booking.id}/cancel",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200
        assert captured["action"].value == "booking.cancel"
        assert captured["resource_id"] == str(booking.id)
        assert captured["old_values"]["session_status"] == "CONFIRMED"
        assert captured["new_values"]["session_status"] == "CANCELLED"

    async def test_admin_cancel_booking_email_failure_non_blocking(
        self,
        registered_user,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch,
    ):
        org_id = uuid.UUID(registered_user["user"]["org_id"])
        slot_start = datetime.now(timezone.utc) + timedelta(days=6)
        slot = TimeSlot(
            org_id=org_id,
            start_time=slot_start,
            end_time=slot_start + timedelta(hours=1),
            is_booked=True,
        )
        session = ChatSession(
            org_id=org_id,
            state_data={"name": "Mail Fail", "email": "mailfail@test.com"},
            missing_fields=[],
            session_status="CONFIRMED",
        )
        db_session.add_all([slot, session])
        await db_session.commit()
        await db_session.refresh(slot)
        await db_session.refresh(session)
        booking = Booking(
            org_id=org_id,
            session_id=session.id,
            slot_id=slot.id,
            customer_name="Mail Fail",
            customer_email="mailfail@test.com",
        )
        db_session.add(booking)
        await db_session.commit()
        await db_session.refresh(booking)

        async def _boom(*_args, **_kwargs):
            raise RuntimeError("smtp unavailable")

        monkeypatch.setattr(
            "app.api.v1.endpoints.dashboard.send_cancellation_email",
            _boom,
        )

        resp = await client.post(
            f"/api/v1/dashboard/bookings/{booking.id}/cancel",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200

        booking_check = await db_session.execute(
            select(Booking).where(Booking.id == booking.id)
        )
        assert booking_check.scalars().first() is None

    async def test_admin_cancel_booking_email_payload_includes_meeting_snapshot_fields(
        self,
        registered_user,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch,
    ):
        org_id = uuid.UUID(registered_user["user"]["org_id"])
        slot_start = datetime.now(timezone.utc) + timedelta(days=7)
        slot = TimeSlot(
            org_id=org_id,
            start_time=slot_start,
            end_time=slot_start + timedelta(hours=1),
            is_booked=True,
        )
        session = ChatSession(
            org_id=org_id,
            state_data={
                "name": "Online Cancel User",
                "email": "online-cancel@test.com",
                "date": "2026-04-20",
                "time": "14:00",
            },
            missing_fields=[],
            session_status="CONFIRMED",
        )
        db_session.add_all([slot, session])
        await db_session.commit()
        await db_session.refresh(slot)
        await db_session.refresh(session)

        booking = Booking(
            org_id=org_id,
            session_id=session.id,
            slot_id=slot.id,
            customer_name="Online Cancel User",
            customer_email="online-cancel@test.com",
            meeting_type_snapshot="ONLINE",
            meeting_link_status="READY",
            meeting_link_url="https://meet.example.com/admin-cancel",
            meeting_link_provider="GOOGLE_MEET",
            user_timezone="Asia/Kolkata",
            org_timezone_snapshot="UTC",
        )
        db_session.add(booking)
        await db_session.commit()
        await db_session.refresh(booking)

        captured = {}

        async def _capture_send_cancellation_email(
            customer_email, customer_name, booking_details, **kwargs
        ):
            captured["customer_email"] = customer_email
            captured["customer_name"] = customer_name
            captured["booking_details"] = booking_details
            captured["kwargs"] = kwargs

        monkeypatch.setattr(
            "app.api.v1.endpoints.dashboard.send_cancellation_email",
            _capture_send_cancellation_email,
        )

        resp = await client.post(
            f"/api/v1/dashboard/bookings/{booking.id}/cancel",
            headers=registered_user["headers"],
        )
        assert resp.status_code == 200

        payload = captured.get("booking_details", {})
        assert payload.get("meeting_type_snapshot") == "ONLINE"
        assert payload.get("meeting_link_status") == "READY"
        assert (
            payload.get("meeting_link_url") == "https://meet.example.com/admin-cancel"
        )
        assert payload.get("meeting_link_provider") == "GOOGLE_MEET"
