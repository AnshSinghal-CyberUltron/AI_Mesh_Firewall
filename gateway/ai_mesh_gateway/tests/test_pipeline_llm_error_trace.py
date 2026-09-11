"""PIPELINE-0032: HTTP 503 after input scan must keep the scan pipeline_trace.

Attack Simulator synthesized ALLOW + "No threat detected" when LiteLLM / the
circuit breaker returned JSON 503 with no stages — even after PII had been
redacted. Egress bytes are the source of truth: raw SSN/email must not appear
in the error body, and input_scan must show redact.
"""
from __future__ import annotations

import copy
import json
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio

from ai_mesh_gateway.tests import test_openai_sdk_compat as T

KEY = T.API_KEY
PII_PROMPT = (
    "Please process this user record: SSN 123-45-6789, "
    "email john.smith@acmecomp.com, phone 555-867-5309, "
    "credit card 4111-1111-1111-1111."
)
RAW_CANARIES = ("123-45-6789", "john.smith@acmecomp.com", "4111-1111-1111-1111")


def _stage(trace: dict, name: str) -> dict:
    stages = (trace or {}).get("stages") or []
    for stage in stages:
        if stage.get("name") == name:
            return stage
    return {}


@pytest_asyncio.fixture()
async def err_app(monkeypatch):
    import fakeredis.aioredis
    from ai_mesh_gateway import main as gateway_main

    state_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    app, auth_redis = await T._make_sdk_app(monkeypatch, redis_client=state_redis)
    forwarded: list[str] = []

    async def _fail(body, redacted_prompt=None, **kw):
        forwarded.append(redacted_prompt if redacted_prompt is not None else "")
        return 503, {"error": "Service Unavailable"}

    gateway_main.LLM_ROUTER.acompletion = AsyncMock(side_effect=_fail)
    yield gateway_main, app, forwarded, auth_redis
    await state_redis.aclose()
    await auth_redis.aclose()


async def _post(app, payload, *, stream=False):
    body = copy.deepcopy(payload)
    if stream:
        body["stream"] = True
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        timeout=30,
    ) as client:
        return await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {KEY}"},
            json=body,
        )


def _assert_no_raw_pii(blob: str) -> None:
    for canary in RAW_CANARIES:
        assert canary not in blob, f"raw PII leaked in 503 body: {canary}"


@pytest.mark.asyncio
async def test_pii_upstream_503_keeps_input_scan_redact_trace(err_app):
    gm, app, forwarded, _auth = err_app
    r = await _post(app, {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": PII_PROMPT}],
        "max_tokens": 64,
    })
    assert r.status_code == 503, r.text[:800]
    assert gm.LLM_ROUTER.acompletion.call_count == 1
    assert forwarded, "model was not called with a forwarded prompt"
    for canary in RAW_CANARIES:
        assert canary not in (forwarded[0] or ""), f"raw PII forwarded to model: {canary}"

    body = r.json()
    _assert_no_raw_pii(json.dumps(body))
    assert body.get("final_action") == "error"
    zs = body.get("zeroshield") or {}
    assert zs.get("action") == "error"
    assert str(zs.get("threat_type") or "").lower() == "pii"

    trace = body.get("pipeline_trace") or {}
    assert len(trace.get("stages") or []) >= 6
    assert trace.get("final_action") == "error"
    assert _stage(trace, "input_scan").get("action") == "redact"
    assert _stage(trace, "model_output").get("action") == "error"
    assert _stage(trace, "output_guardrail").get("action") == "skip"


@pytest.mark.asyncio
async def test_pii_stream_circuit_breaker_503_keeps_scan_trace(err_app, monkeypatch):
    gm, app, forwarded, _auth = err_app

    class _Open:
        should_block = True
        fallback_model = ""

    class _Breaker:
        async def check(self, model):
            return _Open()

    monkeypatch.setattr(gm, "CIRCUIT_BREAKER", _Breaker())
    r = await _post(app, {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": PII_PROMPT}],
        "max_tokens": 64,
    }, stream=True)
    assert r.status_code == 503, r.text[:800]
    assert gm.LLM_ROUTER.acompletion.call_count == 0
    assert forwarded == []
    body = r.json()
    _assert_no_raw_pii(json.dumps(body))
    err = body.get("error") if isinstance(body.get("error"), dict) else {}
    assert body.get("code") == "circuit_breaker_open"
    assert err.get("code") == "circuit_breaker_open"
    assert body.get("final_action") == "error"
    trace = body.get("pipeline_trace") or {}
    assert len(trace.get("stages") or []) >= 6
    assert _stage(trace, "input_scan").get("action") == "redact"
    assert _stage(trace, "model_output").get("action") == "error"
    assert _stage(trace, "output_guardrail").get("action") == "skip"
