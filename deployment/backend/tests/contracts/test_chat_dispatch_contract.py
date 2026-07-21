"""
Phase 1 contract freeze coverage for web dispatch only (D-04).

This file intentionally excludes WhatsApp contract checks in this phase.
Full LangGraph runtime cutover behavior is out of scope here (D-07, D-08).
"""

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config import OrgConfig
from app.models.organization import Organization
from app.core.config import settings
from app.schemas.agent import ActionPayload


SNAPSHOT_DIR = Path(__file__).parent / "snapshots"


def _load_snapshot(name: str) -> dict:
    return json.loads((SNAPSHOT_DIR / name).read_text(encoding="utf-8"))


def _normalize_dispatch_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = json.loads(json.dumps(payload))
    body = normalized.get("body", {})
    session_id = body.get("session_id")
    if isinstance(session_id, str):
        try:
            uuid.UUID(session_id)
            body["session_id"] = "<uuid>"
        except ValueError:
            pass

    action = body.get("action")
    if isinstance(action, dict) and isinstance(action.get("message"), str):
        action["message"] = "<string>"
    return normalized


async def _create_org_with_config(db: AsyncSession) -> Organization:
    org = Organization(
        name="Contract Test Org",
        api_key=f"ds_contract_{uuid.uuid4().hex[:16]}",
        is_active=True,
    )
    db.add(org)
    await db.flush()
    db.add(OrgConfig(org_id=org.id, required_fields=["name", "email", "phone", "date", "time"]))
    await db.commit()
    await db.refresh(org)
    return org


@pytest.mark.asyncio
async def test_snapshot_success_response_contract_snapshot(client: AsyncClient, db_session: AsyncSession, monkeypatch):
    async def _fake_process_message_with_graph(**_kwargs):
        return ActionPayload(
            message="Contract baseline response",
            widget_directive="TEXT_INPUT",
            options=None,
            field_meta=None,
            is_terminal=False,
        )

    monkeypatch.setattr(
        "app.engine.langgraph_orchestrator.process_message_with_graph",
        _fake_process_message_with_graph,
    )

    org = await _create_org_with_config(db_session)
    response = await client.post(
        "/api/v1/chat/message/dispatch",
        json={"message": "Hello"},
        headers={"X-Org-API-Key": org.api_key},
    )
    actual = _normalize_dispatch_payload({"status_code": response.status_code, "body": response.json()})
    expected = _load_snapshot("chat_dispatch_success.json")
    assert actual == expected


@pytest.mark.asyncio
async def test_snapshot_auth_error_response_contract_snapshot(client: AsyncClient):
    response = await client.post("/api/v1/chat/message/dispatch", json={"message": "Hello"})
    actual = {"status_code": response.status_code, "body": response.json()}
    expected = _load_snapshot("chat_dispatch_auth_error.json")
    assert actual == expected


@pytest.mark.asyncio
async def test_snapshot_session_not_found_response_contract_snapshot(client: AsyncClient, db_session: AsyncSession):
    org = await _create_org_with_config(db_session)
    response = await client.post(
        "/api/v1/chat/message/dispatch",
        json={"message": "Hello", "session_id": str(uuid.uuid4())},
        headers={"X-Org-API-Key": org.api_key},
    )
    actual = {"status_code": response.status_code, "body": response.json()}
    expected = _load_snapshot("chat_dispatch_session_error.json")
    assert actual == expected


@pytest.mark.asyncio
async def test_contract_shape_stays_frozen_across_canary_toggle(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch,
):
    calls: list[bool] = []

    async def _fake_route_web_message(**_kwargs):
        calls.append(bool(getattr(settings, "RUNTIME_CANARY_FALLBACK_ENABLED", False)))
        return ActionPayload(
            message="Canary-safe response",
            widget_directive="TEXT_INPUT",
            options=None,
            field_meta=None,
            is_terminal=False,
        )

    monkeypatch.setattr(
        "app.engine.runtime_router.route_web_message",
        _fake_route_web_message,
    )

    org = await _create_org_with_config(db_session)

    monkeypatch.setattr(settings, "RUNTIME_CANARY_FALLBACK_ENABLED", False, raising=False)
    response_default = await client.post(
        "/api/v1/chat/message/dispatch",
        json={"message": "Hello"},
        headers={"X-Org-API-Key": org.api_key},
    )

    monkeypatch.setattr(settings, "RUNTIME_CANARY_FALLBACK_ENABLED", True, raising=False)
    response_canary = await client.post(
        "/api/v1/chat/message/dispatch",
        json={"message": "Hello"},
        headers={"X-Org-API-Key": org.api_key},
    )

    normalized_default = _normalize_dispatch_payload(
        {"status_code": response_default.status_code, "body": response_default.json()}
    )
    normalized_canary = _normalize_dispatch_payload(
        {"status_code": response_canary.status_code, "body": response_canary.json()}
    )

    assert normalized_default == normalized_canary
    assert calls == [False, True]
