"""Scan-only chat: explicit max_tokens=0 must not call LLM_ROUTER.acompletion.

Contract (burst / Attack Simulator): max_tokens=0 and stream false → after input
scan/redact/block return 200 with zeroshield.action and never inject org
max_response_tokens or hit LiteLLM.

Omitted max_tokens remains OpenAI-compatible (infer with org default).
"""
from __future__ import annotations

import copy
import hashlib
import json
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio

from ai_mesh_gateway.tests import test_openai_sdk_compat as T

KEY = T.API_KEY


def _hash(k: str) -> str:
    return hashlib.sha256(k.encode()).hexdigest()


@pytest_asyncio.fixture()
async def scan_app(monkeypatch):
    import fakeredis.aioredis
    from ai_mesh_gateway import main as gateway_main

    state_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    app, auth_redis = await T._make_sdk_app(monkeypatch, redis_client=state_redis)
    chat_bodies: list[dict] = []

    async def _cap_chat(body, redacted_prompt=None, **kw):
        chat_bodies.append(copy.deepcopy(body))
        return await T._fake_completion(body, redacted_prompt, **kw)

    gateway_main.LLM_ROUTER.acompletion = AsyncMock(side_effect=_cap_chat)
    yield gateway_main, app, chat_bodies, auth_redis
    await state_redis.aclose()
    await auth_redis.aclose()


async def _post(app, payload, *, key=KEY):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        timeout=30,
    ) as client:
        return await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json=payload,
        )


@pytest.mark.asyncio
async def test_absent_max_tokens_is_openai_compat_and_calls_acompletion(scan_app):
    gm, app, chat_bodies = scan_app[0], scan_app[1], scan_app[2]
    r = await _post(app, {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello burst scan only"}],
    })
    assert r.status_code == 200, r.text[:500]
    assert gm.LLM_ROUTER.acompletion.call_count == 1
    assert len(chat_bodies) == 1
    assert "max_tokens" in chat_bodies[0]
    assert int(chat_bodies[0]["max_tokens"]) > 0


@pytest.mark.asyncio
async def test_max_tokens_zero_is_scan_only_and_skips_acompletion(scan_app):
    gm, app, chat_bodies = scan_app[0], scan_app[1], scan_app[2]
    r = await _post(app, {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 0,
    })
    assert r.status_code == 200, r.text[:500]
    assert gm.LLM_ROUTER.acompletion.call_count == 0
    assert chat_bodies == []
    body = r.json()
    zs = body.get("zeroshield") or {}
    assert zs.get("scan_only") is True
    assert "Scan-only probe" in (zs.get("reason") or "")


@pytest.mark.asyncio
async def test_positive_max_tokens_still_calls_acompletion(scan_app):
    gm, app, chat_bodies = scan_app[0], scan_app[1], scan_app[2]
    r = await _post(app, {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 16,
    })
    assert r.status_code == 200, r.text[:500]
    assert gm.LLM_ROUTER.acompletion.call_count == 1
    assert len(chat_bodies) == 1


@pytest.mark.asyncio
async def test_pii_scan_only_does_not_call_model(scan_app):
    gm, app, chat_bodies = scan_app[0], scan_app[1], scan_app[2]
    r = await _post(app, {
        "model": "gpt-4o-mini",
        "messages": [{
            "role": "user",
            "content": "My SSN is 123-45-6789 and email is bob.jones@corp.example",
        }],
        "max_tokens": 0,
    })
    assert gm.LLM_ROUTER.acompletion.call_count == 0
    assert chat_bodies == []
    assert r.status_code in (200, 400, 403)
    if r.status_code == 200:
        action = (r.json().get("zeroshield") or {}).get("action")
        assert action in ("redact", "block", "monitor", "flag")


class _CaptureLimiter:
    def __init__(self):
        self.estimates: list[int] = []

    async def check_rate_limit(self, key_hash, limit, est):
        self.estimates.append(int(est))
        return True, est

    async def check_org_rate_limit(self, org, limit, est):
        return True, 0

    async def check_model_rate_limit(self, *a, **kw):
        return True, 0

    async def record_usage(self, *a, **kw):
        return None

    async def record_org_usage(self, *a, **kw):
        return None


@pytest.mark.asyncio
async def test_estimated_tokens_honored_for_simulator_key_tpm(scan_app, monkeypatch):
    gm, app, _bodies, auth_redis = scan_app
    limiter = _CaptureLimiter()
    monkeypatch.setattr(gm, "RATE_LIMITER", limiter)

    sim_key = "zs_sim_burst_probe_0123456789abcd"
    payload = T._auth_payload()
    payload["project_id"] = "simulator-zeroshield"
    payload["rate_limit_tpm"] = 100_000
    await auth_redis.set(f"auth:apikey:{_hash(sim_key)}", json.dumps(payload))

    r = await _post(app, {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hi"}],
        "estimated_tokens": 8000,
        "max_tokens": 0,
    }, key=sim_key)
    assert r.status_code == 200, r.text[:500]
    assert limiter.estimates, "TPM pre-check never ran"
    assert limiter.estimates[0] == 8000
    # Must not leak into LiteLLM even if inference ran.
    assert gm.LLM_ROUTER.acompletion.call_count == 0


@pytest.mark.asyncio
async def test_estimated_tokens_ignored_for_non_simulator_key(scan_app, monkeypatch):
    gm, app, _bodies, _auth_redis = scan_app
    limiter = _CaptureLimiter()
    monkeypatch.setattr(gm, "RATE_LIMITER", limiter)
    r = await _post(app, {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hi"}],
        "estimated_tokens": 8000,
        "max_tokens": 0,
    })
    assert r.status_code == 200, r.text[:500]
    assert limiter.estimates
    # Regular keys keep the prompt-length estimate (~1 token for "hi"), not 8000.
    assert limiter.estimates[0] != 8000
    assert limiter.estimates[0] < 100
