"""GATEWAY_LOADTEST_STUB_LLM skips LiteLLM.

Default (no duration env) stays instant for existing benches. Phase 0.1 adds a
token-emitting hold so capacity soaks measure in-flight memory, not a one-token
no-op. A stub completion is never capacity-eligible (bench honesty predicate).
"""
from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock

import pytest

from ai_mesh_gateway import llm_router as lr


def _clear_stub_timing(monkeypatch):
    monkeypatch.delenv("GATEWAY_LOADTEST_STUB_DURATION_S", raising=False)
    monkeypatch.delenv("GATEWAY_LOADTEST_STUB_TOK_PER_S", raising=False)


def test_stub_disabled_by_default(monkeypatch):
    monkeypatch.delenv("GATEWAY_LOADTEST_STUB_LLM", raising=False)
    _clear_stub_timing(monkeypatch)
    assert lr.loadtest_stub_llm_enabled() is False


@pytest.mark.parametrize("val", ["1", "true", "YES", "on"])
def test_stub_enabled_truthy(monkeypatch, val):
    monkeypatch.setenv("GATEWAY_LOADTEST_STUB_LLM", val)
    assert lr.loadtest_stub_llm_enabled() is True


def test_stub_payload_is_openai_shaped():
    body = lr.loadtest_stub_completion({"model": "org::gpt-4o-mini"})
    assert body["object"] == "chat.completion"
    assert body["model"] == "gpt-4o-mini"
    assert body["id"] == "chatcmpl-loadtest-stub"
    assert body["choices"][0]["message"]["content"] == "ok"


@pytest.mark.asyncio
async def test_acompletion_stub_does_not_call_litellm(monkeypatch):
    monkeypatch.setenv("GATEWAY_LOADTEST_STUB_LLM", "1")
    _clear_stub_timing(monkeypatch)
    router = lr.LLMRouter({"org_only_inference": True})
    router._execute_completion = AsyncMock(side_effect=AssertionError("LiteLLM must not run"))
    code, resp = await router.acompletion(
        {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        None,
    )
    assert code == 200
    assert resp["choices"][0]["message"]["content"] == "ok"
    router._execute_completion.assert_not_called()


def test_stub_tok_per_s_clamped_to_30_100(monkeypatch):
    monkeypatch.setenv("GATEWAY_LOADTEST_STUB_TOK_PER_S", "5")
    assert lr.loadtest_stub_tok_per_s() == 30.0
    monkeypatch.setenv("GATEWAY_LOADTEST_STUB_TOK_PER_S", "500")
    assert lr.loadtest_stub_tok_per_s() == 100.0
    monkeypatch.setenv("GATEWAY_LOADTEST_STUB_TOK_PER_S", "50")
    assert lr.loadtest_stub_tok_per_s() == 50.0


def test_stub_duration_zero_when_unset(monkeypatch):
    _clear_stub_timing(monkeypatch)
    assert lr.loadtest_stub_duration_s() == 0.0


def test_stub_duration_clamped_to_10s_ceiling(monkeypatch):
    monkeypatch.setenv("GATEWAY_LOADTEST_STUB_DURATION_S", "99")
    assert lr.loadtest_stub_duration_s() == 10.0


@pytest.mark.asyncio
async def test_acompletion_stub_holds_and_emits_tokens(monkeypatch):
    monkeypatch.setenv("GATEWAY_LOADTEST_STUB_LLM", "1")
    monkeypatch.setenv("GATEWAY_LOADTEST_STUB_DURATION_S", "0.08")
    monkeypatch.setenv("GATEWAY_LOADTEST_STUB_TOK_PER_S", "50")
    router = lr.LLMRouter({"org_only_inference": True})
    router._execute_completion = AsyncMock(side_effect=AssertionError("LiteLLM must not run"))
    t0 = time.perf_counter()
    code, resp = await router.acompletion(
        {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        None,
    )
    elapsed = time.perf_counter() - t0
    assert code == 200
    assert resp["id"] == "chatcmpl-loadtest-stub"
    assert resp["usage"]["completion_tokens"] >= 2
    content = resp["choices"][0]["message"]["content"]
    assert content != "ok"
    assert len(content.split()) >= 2
    assert elapsed >= 0.05
    router._execute_completion.assert_not_called()


@pytest.mark.asyncio
async def test_acompletion_stream_stub_emits_multiple_tokens_then_done(monkeypatch):
    monkeypatch.setenv("GATEWAY_LOADTEST_STUB_LLM", "1")
    monkeypatch.setenv("GATEWAY_LOADTEST_STUB_DURATION_S", "0.08")
    monkeypatch.setenv("GATEWAY_LOADTEST_STUB_TOK_PER_S", "50")
    router = lr.LLMRouter({"org_only_inference": True})
    frames = []
    t0 = time.perf_counter()
    async for line in router.acompletion_stream(
        {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        None,
    ):
        frames.append(line)
    elapsed = time.perf_counter() - t0
    data = [f for f in frames if f.startswith("data: ") and "[DONE]" not in f]
    assert len(data) >= 2
    assert any("[DONE]" in f for f in frames)
    payloads = [json.loads(f[len("data: "):].strip()) for f in data]
    contents = [
        (p.get("choices") or [{}])[0].get("delta", {}).get("content") or ""
        for p in payloads
    ]
    assert sum(1 for c in contents if c) >= 2
    assert elapsed >= 0.05
