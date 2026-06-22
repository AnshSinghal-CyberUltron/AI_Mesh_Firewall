"""PHASE 2 ADVERSARIAL extension — NEW defects + challenges beyond
``test_openai_sdk_compat_phase2.py`` (the parallel session's 12-defect pass).

This file's mandate: assume the parallel triage is INCOMPLETE and try to find what it
missed. The richest vein is SEAM-B: the responses->chat adapter's narrow allowlist
``_RESP_DIRECT_PASSTHROUGH`` (responses_adapters.py:111) drops MORE than the 4 params
first reported — most importantly the NATIVE Responses structured-output key
``text.format`` (what ``client.responses.parse()`` emits), which ``responses_to_chat``
does not handle at all.

Same convention as phase2: each test asserts the CORRECT OpenAI behavior under
``xfail(strict=True)`` — XFAIL = confirmed defect, XPASS = claim wrong (drop it).
Run: cd gateway && .venv/bin/python -m pytest \
  ai_mesh_gateway/tests/test_openai_sdk_compat_phase2_adversarial.py -q -rxX
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import openai
import pytest
import pytest_asyncio

import ai_mesh_gateway.main as gm
from ai_mesh_gateway.tests import test_openai_sdk_compat as T


async def _capturing_factory(cap: dict):
    async def _cap(body, redacted_prompt=None, **_kw):
        cap.clear()
        cap.update(body)
        return 200, {
            "id": "chatcmpl-adv", "object": "chat.completion", "created": 1700000000,
            "model": "gpt-4o-mini",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    return _cap


@pytest_asyncio.fixture()
async def appctx(monkeypatch):
    """Real app (REDIS_CLIENT=None) + a body-capturing upstream so we can see exactly
    which params the responses->chat adapter forwarded. Yields (app, cap)."""
    app, auth_redis = await T._make_sdk_app(monkeypatch, redis_client=None)
    cap: dict = {}
    gm.LLM_ROUTER.acompletion = AsyncMock(side_effect=await _capturing_factory(cap))
    yield app, cap
    await auth_redis.aclose()


async def _responses_capture(app, cap: dict, **extra) -> dict:
    """Drive a stock-SDK responses.create with extra params; return the chat body the
    adapter forwarded to the upstream router."""
    client = openai.AsyncOpenAI(
        base_url="http://testserver/v1", api_key=T.API_KEY,
        http_client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver"),
        max_retries=0,
    )
    try:
        await client.responses.create(model="gpt-4o-mini", input="hello", extra_body=extra)
    finally:
        await client.close()
    return dict(cap)


# ── SEAM-B EXTENSIONS — the allowlist drops more than first reported ──

@pytest.mark.xfail(strict=True, reason="P2-RESP-drops-text-format (HIGH): the NATIVE Responses structured-output mechanism text:{format:{...}} (emitted by responses.parse()) is not handled by responses_to_chat AT ALL -> structured output silently dropped on /v1/responses. Distinct from (and bigger than) the chat-style response_format gap. Fix: map text.format -> chat response_format in responses_to_chat.")
@pytest.mark.asyncio
async def test_responses_adapter_maps_native_text_format(appctx):
    app, cap = appctx
    body = await _responses_capture(app, cap, text={"format": {"type": "json_schema", "name": "w", "schema": {"type": "object", "properties": {}}}})
    assert body.get("response_format", {}).get("type") == "json_schema"


@pytest.mark.xfail(strict=True, reason="P2-RESP-drops-logit_bias (MED): logit_bias is absent from _RESP_DIRECT_PASSTHROUGH -> token biasing silently ignored on /v1/responses while the chat path forwards it. Same narrow-allowlist root cause.")
@pytest.mark.asyncio
async def test_responses_adapter_forwards_logit_bias(appctx):
    app, cap = appctx
    body = await _responses_capture(app, cap, logit_bias={"123": -100})
    assert body.get("logit_bias") == {"123": -100}


@pytest.mark.xfail(strict=True, reason="P2-RESP-drops-service_tier (LOW-MED): service_tier (auto/default/flex/priority) dropped -> the caller's latency/billing tier choice is silently ignored on /v1/responses.")
@pytest.mark.asyncio
async def test_responses_adapter_forwards_service_tier(appctx):
    app, cap = appctx
    body = await _responses_capture(app, cap, service_tier="flex")
    assert body.get("service_tier") == "flex"


@pytest.mark.xfail(strict=True, reason="P2-RESP-drops-prediction (LOW): predicted outputs (prediction) dropped -> the latency optimization is silently ignored on /v1/responses.")
@pytest.mark.asyncio
async def test_responses_adapter_forwards_prediction(appctx):
    app, cap = appctx
    body = await _responses_capture(app, cap, prediction={"type": "content", "content": "draft"})
    assert "prediction" in body


@pytest.mark.xfail(strict=True, reason="P2-RESP-drops-truncation (LOW): the Responses-native truncation:'auto' (which prevents context-overflow 400s by auto-dropping middle-of-context) is dropped -> long-context callers that rely on it get a hard 400 instead.")
@pytest.mark.asyncio
async def test_responses_adapter_forwards_truncation(appctx):
    app, cap = appctx
    body = await _responses_capture(app, cap, truncation="auto")
    assert body.get("truncation") == "auto"
