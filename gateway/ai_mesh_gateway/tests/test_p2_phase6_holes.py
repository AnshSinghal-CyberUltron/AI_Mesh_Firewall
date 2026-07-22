"""PHASE-6 adversarial holes — regression tests for the 3 defects the verifiers found
in the Phase-4 fixes, so they stay closed.

- EMB-500-FLAT-NORID / RESP-STORE-500-FLAT-NORID: an exception escaping a /v1 handler
  bypasses the compat shim -> must STILL be a nested OpenAI 500 + x-request-id (fixed in
  the @app.exception_handler(Exception) catch-all). Driven with raise_app_exceptions=False
  so the handler's 500 response is returned (mirrors real uvicorn).
- D5-RESP-STREAM-UPSTREAM-ERROR: a terminal zeroshield.action='error' frame on the responses
  stream -> response.failed, not a false response.completed.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import fakeredis.aioredis
import httpx
import openai
import pytest
import pytest_asyncio

from ai_mesh_gateway.tests import test_openai_sdk_compat as T


def _resolved_main():
    """Resolve the SAME ``main`` module object the app under test is built from.

    The gateway file is importable under two identities (``main`` and
    ``ai_mesh_gateway.main``). A sibling test deletes ``ai_mesh_gateway.main``
    from ``sys.modules`` during teardown, so a later dotted re-import re-executes
    main.py into a SECOND module object with its own ``app`` / ``LLM_ROUTER``.
    A module-level ``import ai_mesh_gateway.main as gm`` binds the FIRST object
    and then silently patches a module the app no longer uses (passes alone,
    fails in-suite). ``T._make_sdk_app`` resolves the module via
    ``from ai_mesh_gateway import main``; mirror that, at call time.
    """
    from ai_mesh_gateway import main as gateway_main

    return gateway_main


def _raw500_client(app):
    # raise_app_exceptions=False -> the exception handler's 500 response is RETURNED to the
    # caller (what a real uvicorn server does), instead of re-raising into the test client.
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {T.API_KEY}"},
    )


@pytest.mark.asyncio
async def test_embeddings_unhandled_exception_is_nested_500_with_request_id(monkeypatch):
    rc = fakeredis.aioredis.FakeRedis(decode_responses=True)
    app, auth = await T._make_sdk_app(monkeypatch, redis_client=rc)
    _resolved_main().LLM_ROUTER.aembedding = AsyncMock(side_effect=RuntimeError("embedding provider connection failed"))
    try:
        async with _raw500_client(app) as c:
            r = await c.post("/v1/embeddings", json={"model": "zs-embed", "input": "hi"})
    finally:
        await auth.aclose()
    assert r.status_code == 500
    assert r.headers.get("x-request-id"), "escaped-exception 500 must carry x-request-id"
    b = r.json()
    assert isinstance(b.get("error"), dict), "must be the NESTED envelope, not flat"
    assert b["error"].get("code") == "gateway_internal_error"
    assert b["error"].get("type") == "server_error"
    assert b.get("request_id")


@pytest.mark.asyncio
async def test_responses_store_exception_is_nested_500_with_request_id(monkeypatch):
    rc = fakeredis.aioredis.FakeRedis(decode_responses=True)
    app, auth = await T._make_sdk_app(monkeypatch, redis_client=rc)
    import ai_mesh_gateway.responses_store as rs
    monkeypatch.setattr(rs.ResponseStore, "save", AsyncMock(side_effect=RuntimeError("store.save exploded")))
    try:
        async with _raw500_client(app) as c:
            r = await c.post("/v1/responses", json={"model": "gpt-4o-mini", "input": "hi", "store": True})
    finally:
        await auth.aclose()
    assert r.status_code == 500
    assert r.headers.get("x-request-id")
    b = r.json()
    assert isinstance(b.get("error"), dict)
    assert b["error"].get("code") == "gateway_internal_error"
    assert b.get("request_id")


@pytest.mark.asyncio
async def test_responses_stream_zeroshield_error_emits_failed_not_completed(monkeypatch):
    app, auth = await T._make_sdk_app(monkeypatch, redis_client=None)

    async def _err_stream(body, redacted_prompt=None, metrics=None, **_kw):
        yield "data: " + json.dumps({"id": "x", "object": "chat.completion.chunk", "created": 1,
            "model": "gpt-4o-mini", "choices": [{"index": 0, "delta": {"content": "partial"}, "finish_reason": None}]}) + "\n\n"
        # terminal trace frame: action='error', empty choices, NO top-level "error" key
        yield "data: " + json.dumps({"id": "x", "object": "chat.completion.chunk", "created": 1,
            "model": "gpt-4o-mini", "choices": [], "zeroshield": {"action": "error", "reason": "upstream drop"}}) + "\n\n"
        yield "data: [DONE]\n\n"

    _resolved_main().LLM_ROUTER.acompletion_stream = _err_stream
    client = openai.AsyncOpenAI(
        base_url="http://testserver/v1", api_key=T.API_KEY,
        http_client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver"),
        max_retries=0,
    )
    types = []
    try:
        stream = await client.responses.create(model="gpt-4o-mini", input="hi", stream=True)
        async for e in stream:
            types.append(getattr(e, "type", None))
    finally:
        await client.close()
        await auth.aclose()
    assert "response.failed" in types, f"upstream mid-stream error must -> response.failed; got {types}"
    assert "response.completed" not in types, "must NOT report a false response.completed"
