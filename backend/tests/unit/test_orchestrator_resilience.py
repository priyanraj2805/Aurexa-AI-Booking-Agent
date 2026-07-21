import pytest
import httpx
from unittest.mock import AsyncMock

from app.engine.orchestrator import LLMOrchestrator


@pytest.mark.asyncio
async def test_classifies_http_5xx_as_retryable():
    request = httpx.Request("POST", "https://example.com/llm")
    response = httpx.Response(status_code=503, request=request)
    err = httpx.HTTPStatusError("service unavailable", request=request, response=response)
    assert LLMOrchestrator._classify_provider_failure(err) == "retryable_failure"


@pytest.mark.asyncio
async def test_classifies_http_4xx_as_terminal():
    request = httpx.Request("POST", "https://example.com/llm")
    response = httpx.Response(status_code=401, request=request)
    err = httpx.HTTPStatusError("unauthorized", request=request, response=response)
    assert LLMOrchestrator._classify_provider_failure(err) == "terminal_failure"


@pytest.mark.asyncio
async def test_call_llm_stops_retry_on_terminal(monkeypatch):
    orchestrator = LLMOrchestrator()
    orchestrator.api_key = "test-key"
    orchestrator.max_retries = 3
    attempts = {"count": 0}

    async def _raise_terminal(*_args, **_kwargs):
        attempts["count"] += 1
        req = httpx.Request("POST", "https://example.com/llm")
        res = httpx.Response(status_code=400, request=req)
        raise httpx.HTTPStatusError("bad request", request=req, response=res)

    monkeypatch.setattr(orchestrator.client, "post", _raise_terminal)
    emit_mock = AsyncMock()
    monkeypatch.setattr(orchestrator, "_emit_llm_reliability_alert", emit_mock)
    result = await orchestrator._call_llm("hello", json_mode=True)
    await orchestrator.aclose()
    assert result is None
    assert attempts["count"] == 1
    emit_mock.assert_awaited_once()
    assert emit_mock.await_args.kwargs["code"] == "LLM_PROVIDER_DOWN"


@pytest.mark.asyncio
async def test_call_llm_retries_for_retryable(monkeypatch):
    orchestrator = LLMOrchestrator()
    orchestrator.api_key = "test-key"
    orchestrator.max_retries = 3
    attempts = {"count": 0}

    async def _raise_retryable(*_args, **_kwargs):
        attempts["count"] += 1
        raise httpx.TimeoutException("timeout")

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(orchestrator.client, "post", _raise_retryable)
    monkeypatch.setattr("app.engine.orchestrator.asyncio.sleep", _no_sleep)
    emit_mock = AsyncMock()
    monkeypatch.setattr(orchestrator, "_emit_llm_reliability_alert", emit_mock)
    result = await orchestrator._call_llm("hello", json_mode=True)
    await orchestrator.aclose()
    assert result is None
    assert attempts["count"] == 3
    emit_mock.assert_awaited_once()
    assert emit_mock.await_args.kwargs["code"] == "LLM_PROVIDER_DOWN"

