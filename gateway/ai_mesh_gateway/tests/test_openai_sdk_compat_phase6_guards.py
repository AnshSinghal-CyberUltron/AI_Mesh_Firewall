"""PHASE 6 — guards for the two contract gaps the adversarial verifiers surfaced.

Both were PRE-EXISTING (not regressions of the Phase-4/5 fixes) and LOW/INFO; fixed here
for full OpenAI conformance and guarded against regression.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import openai
import pytest
import pytest_asyncio

import ai_mesh_gateway.main as gm
from ai_mesh_gateway.tests import test_openai_sdk_compat as T


@pytest_asyncio.fixture()
async def appctx(monkeypatch):
    app, auth_redis = await T._make_sdk_app(monkeypatch, redis_client=None)
    yield app
    await auth_redis.aclose()


def _raw(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver",
        headers={"Authorization": f"Bearer {T.API_KEY}"})


@pytest.mark.asyncio
async def test_missing_model_returns_400_invalid_request_param_model(appctx):
    """P6-missing-model: a chat request with no 'model' (routing off) -> 400
    invalid_request_error param='model' (OpenAI shape), not 403 model_not_allowed."""
    app = appctx
    async with _raw(app) as rc:
        r = await rc.post("/v1/chat/completions",
                          json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 400, f"missing-model status={r.status_code} body={r.text[:200]}"
    err = r.json()["error"]
    assert err.get("param") == "model"
    assert err.get("type") == "invalid_request_error"
    # stock SDK raises BadRequestError (not PermissionDenied) and exposes e.param
    client = openai.AsyncOpenAI(base_url="http://testserver/v1", api_key=T.API_KEY,
                                http_client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver"),
                                max_retries=0)
    try:
        with pytest.raises(openai.BadRequestError) as exc:
            await client.chat.completions.create(model="", messages=[{"role": "user", "content": "hi"}])
        assert exc.value.param == "model"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_stream_upstream_error_emits_error_event_not_clean_empty(appctx):
    """P6-STREAM-error: an upstream raise before the first token must surface an
    OpenAI-parseable error event in the SSE body (status already 200), not a clean
    empty stream the SDK reads as a successful empty completion."""
    app = appctx

    async def _boom_stream(body, redacted_prompt=None, metrics=None, **kw):
        raise RuntimeError("upstream boom before first token")
        yield ""  # unreachable; makes this an async generator

    gm.LLM_ROUTER.acompletion_stream = _boom_stream
    async with _raw(app) as rc:
        async with rc.stream("POST", "/v1/chat/completions",
                             json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}], "stream": True}) as resp:
            body = ""
            async for chunk in resp.aiter_text():
                body += chunk
    # The stream must carry an in-band OpenAI-shaped error event before [DONE].
    assert '"error"' in body, f"no error event in errored stream body: {body[:300]}"
    assert "upstream_error" in body
    assert "data: [DONE]" in body
