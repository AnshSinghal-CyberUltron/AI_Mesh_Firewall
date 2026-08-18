"""GATEWAY_LOADTEST_STUB_LLM skips LiteLLM and returns an instant completion."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from ai_mesh_gateway import llm_router as lr


def test_stub_disabled_by_default(monkeypatch):
    monkeypatch.delenv("GATEWAY_LOADTEST_STUB_LLM", raising=False)
    assert lr.loadtest_stub_llm_enabled() is False


@pytest.mark.parametrize("val", ["1", "true", "YES", "on"])
def test_stub_enabled_truthy(monkeypatch, val):
    monkeypatch.setenv("GATEWAY_LOADTEST_STUB_LLM", val)
    assert lr.loadtest_stub_llm_enabled() is True


def test_stub_payload_is_openai_shaped():
    body = lr.loadtest_stub_completion({"model": "org::gpt-4o-mini"})
    assert body["object"] == "chat.completion"
    assert body["model"] == "gpt-4o-mini"
    assert body["choices"][0]["message"]["content"] == "ok"


@pytest.mark.asyncio
async def test_acompletion_stub_does_not_call_litellm(monkeypatch):
    monkeypatch.setenv("GATEWAY_LOADTEST_STUB_LLM", "1")
    router = lr.LLMRouter({"org_only_inference": True})
    router._execute_completion = AsyncMock(side_effect=AssertionError("LiteLLM must not run"))
    code, resp = await router.acompletion(
        {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        None,
    )
    assert code == 200
    assert resp["choices"][0]["message"]["content"] == "ok"
    router._execute_completion.assert_not_called()
