from types import SimpleNamespace

import pytest

from app.schemas.agent import ActionPayload


def _build_context() -> dict:
    return {
        "db": SimpleNamespace(),
        "org": SimpleNamespace(id="org-1"),
        "org_config": SimpleNamespace(),
        "chat_session": SimpleNamespace(session_id="session-1"),
        "message_text": "hello",
        "background_tasks": None,
    }


@pytest.mark.asyncio
async def test_web_channel_prefers_langgraph_by_default(monkeypatch):
    from app.engine import runtime_router

    calls = {"graph": 0, "legacy": 0}

    async def _graph(**_kwargs):
        calls["graph"] += 1
        return ActionPayload(message="graph", widget_directive="TEXT_INPUT")

    async def _legacy(**_kwargs):
        calls["legacy"] += 1
        return ActionPayload(message="legacy", widget_directive="TEXT_INPUT")

    monkeypatch.setattr(runtime_router, "process_message_with_graph", _graph)
    monkeypatch.setattr(runtime_router, "process_message", _legacy)

    result = await runtime_router.route_web_message(**_build_context())

    assert result.message == "graph"
    assert calls == {"graph": 1, "legacy": 0}


@pytest.mark.asyncio
async def test_active_path_route_web_message_happy_path_avoids_legacy_processor(monkeypatch):
    from app.engine import runtime_router

    calls = {"graph": 0, "legacy": 0}

    async def _graph(**_kwargs):
        calls["graph"] += 1
        return ActionPayload(message="active-path", widget_directive="TEXT_INPUT")

    async def _legacy(**_kwargs):
        calls["legacy"] += 1
        return ActionPayload(message="legacy-path", widget_directive="TEXT_INPUT")

    monkeypatch.setattr(runtime_router, "process_message_with_graph", _graph)
    monkeypatch.setattr(runtime_router, "process_message", _legacy)

    result = await runtime_router.route_web_message(**_build_context())

    assert result.message == "active-path"
    assert calls["graph"] == 1
    assert calls["legacy"] == 0


@pytest.mark.asyncio
async def test_canary_fallback_triggers_only_for_retryable_runtime_failures(monkeypatch):
    from app.core.config import settings
    from app.engine import runtime_router

    monkeypatch.setattr(settings, "RUNTIME_CANARY_FALLBACK_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "RUNTIME_FALLBACK_SUNSET_ENFORCED", False, raising=False)

    calls = {"legacy": 0}

    async def _legacy(**_kwargs):
        calls["legacy"] += 1
        return ActionPayload(message="legacy", widget_directive="TEXT_INPUT")

    monkeypatch.setattr(runtime_router, "process_message", _legacy)

    async def _retryable_graph(**_kwargs):
        raise TimeoutError("provider timeout")

    monkeypatch.setattr(runtime_router, "process_message_with_graph", _retryable_graph)
    retryable_result = await runtime_router.route_web_message(**_build_context())
    assert retryable_result.message == "legacy"

    async def _non_retryable_graph(**_kwargs):
        raise ValueError("low confidence extraction")

    monkeypatch.setattr(runtime_router, "process_message_with_graph", _non_retryable_graph)
    with pytest.raises(ValueError):
        await runtime_router.route_web_message(**_build_context())

    assert calls["legacy"] == 1


@pytest.mark.asyncio
async def test_fallback_emits_audit_hook_and_response_stays_user_silent(monkeypatch):
    from app.core.config import settings
    from app.engine import runtime_router

    monkeypatch.setattr(settings, "RUNTIME_CANARY_FALLBACK_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "RUNTIME_FALLBACK_SUNSET_ENFORCED", False, raising=False)

    events = []

    async def _graph(**_kwargs):
        raise TimeoutError("graph timeout")

    async def _legacy(**_kwargs):
        return ActionPayload(message="We can still continue.", widget_directive="TEXT_INPUT")

    def _emit(event):
        events.append(event)

    monkeypatch.setattr(runtime_router, "process_message_with_graph", _graph)
    monkeypatch.setattr(runtime_router, "process_message", _legacy)
    monkeypatch.setattr(runtime_router, "emit_fallback_event", _emit)

    result = await runtime_router.route_web_message(**_build_context())

    assert "fallback" not in result.message.lower()
    assert events
    assert events[0]["channel"] == "web"
    assert events[0]["fallback_to"] == "legacy"


@pytest.mark.asyncio
async def test_sunset_gate_blocks_automatic_fallback_after_phase_end(monkeypatch):
    from app.core.config import settings
    from app.engine import runtime_router

    monkeypatch.setattr(settings, "RUNTIME_CANARY_FALLBACK_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "RUNTIME_FALLBACK_SUNSET_ENFORCED", True, raising=False)

    async def _graph(**_kwargs):
        raise TimeoutError("graph timeout")

    async def _legacy(**_kwargs):
        return ActionPayload(message="legacy", widget_directive="TEXT_INPUT")

    monkeypatch.setattr(runtime_router, "process_message_with_graph", _graph)
    monkeypatch.setattr(runtime_router, "process_message", _legacy)

    with pytest.raises(runtime_router.FallbackSunsetError):
        await runtime_router.route_web_message(**_build_context())


@pytest.mark.asyncio
async def test_whatsapp_routing_uses_active_graph(monkeypatch):
    from app.engine import runtime_router

    calls = {"graph": 0, "legacy": 0}

    async def _graph(**_kwargs):
        calls["graph"] += 1
        return ActionPayload(message="graph", widget_directive="TEXT_INPUT")

    async def _legacy(**_kwargs):
        calls["legacy"] += 1
        return ActionPayload(message="legacy", widget_directive="TEXT_INPUT")

    monkeypatch.setattr(runtime_router, "process_message_with_graph", _graph)
    monkeypatch.setattr(runtime_router, "process_message", _legacy)

    result = await runtime_router.route_whatsapp_message(**_build_context())

    assert result.message == "graph"
    assert calls == {"graph": 1, "legacy": 0}
