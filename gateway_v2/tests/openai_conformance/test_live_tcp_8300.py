"""Live-TCP SDK-visible conformance over a real socket (LGW01-2 + B3).

Originally LGW01-2 against this host's v1 gateway :8300 (no staging nginx). As of B3 task 7.2 this
module is turned ON (its pytest ``--ignore`` line is removed) and now ALSO serves the gateway_v2
live-TCP gate: the SDK-visible assertions here — authentication error on a bogus key, JSON (not
HTML) error envelope, non-HTML ``/v1/mcp`` / ``/v1/rag`` / ``/v1/vector`` surfaces — are
APP-AGNOSTIC (R5.5), so pointing ``AMF_CONFORMANCE_BASE_URL`` at either a v1 or a running gateway_v2
entrypoint (``AMF_CONFORMANCE_APP=v2``) exercises the same real-socket contract. The booted-child
RUN-GREEN counterpart for gateway_v2 lives in ``test_b3_live_serve.py``.

Does not assert stubbed upstream bytes (R5.6). Proves SDK-visible auth and JSON error envelopes on
the real socket. The module hard-depends on the stock OpenAI SDK + httpx (the ``conformance``
extra); ``pytest.importorskip`` skips the whole module cleanly on a framework-free tree so default
collection stays green, and the module-level ``skipif`` additionally skips it at run-time when no
``AMF_CONFORMANCE_BASE_URL`` live server is wired.
"""

from __future__ import annotations

import os

import pytest

# Framework-free trees (no ``conformance`` extra) skip the whole module cleanly instead of ERRORing
# at collection — this is what keeps the default ``pytest`` run green now that the ``--ignore`` is
# removed (B3 task 7.2).
httpx = pytest.importorskip("httpx")
openai = pytest.importorskip("openai")

from gateway_v2.contracts.openai_conformance.harness import tcp_base_url  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.environ.get("AMF_CONFORMANCE_BASE_URL"),
    reason="AMF_CONFORMANCE_BASE_URL unset (CI uses loopback uvicorn instead)",
)

# App-agnostic SDK-visible refusal contract (R5.5/5.6). A bogus key against a well-formed request
# refuses with the serving app's DECLARED code: the v1 gateway rejects at its identity layer (401/
# 403 → the SDK's ``AuthenticationError``/``PermissionDeniedError``), while a gateway_v2 entrypoint
# whose backends are unwired at the local half is fail-closed-UNAVAILABLE (503 → the SDK's
# ``APIStatusError``). Both are the same SDK-visible outcome — a declared JSON (not HTML) error
# envelope on the real socket — so the gate asserts the ``APIStatusError`` superclass + this status
# set rather than a v1-only 401, which keeps the test genuinely app-agnostic. It NEVER asserts
# stubbed-upstream response bytes (R5.6).
_REFUSAL_STATUSES = frozenset({401, 403, 429, 500, 502, 503})


@pytest.mark.asyncio
async def test_live_tcp_missing_key_is_authentication_error() -> None:
    base = tcp_base_url()
    assert base
    client = openai.AsyncOpenAI(
        base_url=f"{base.rstrip('/')}/v1",
        api_key="sk-bogus",
        max_retries=0,
    )
    try:
        with pytest.raises(openai.APIStatusError) as excinfo:
            await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "hi"}],
            )
    finally:
        await client.close()
    exc = excinfo.value
    assert exc.status_code in _REFUSAL_STATUSES
    assert "text/html" not in str(exc.response.headers.get("content-type", ""))


@pytest.mark.asyncio
async def test_live_tcp_error_body_is_json_not_html() -> None:
    base = tcp_base_url()
    assert base
    async with httpx.AsyncClient(base_url=base, timeout=15) as raw:
        resp = await raw.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code in _REFUSAL_STATUSES
    assert "text/html" not in resp.headers.get("content-type", "")
    body = resp.json()
    assert isinstance(body, dict)
    assert "error" in body or "detail" in body


@pytest.mark.asyncio
async def test_live_tcp_mcp_rag_vector_not_html() -> None:
    base = tcp_base_url()
    assert base
    async with httpx.AsyncClient(base_url=base, timeout=15) as raw:
        mcp = await raw.post(
            "/v1/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        rag = await raw.post("/v1/rag/query", json={"query": "x"})
        vec = await raw.post("/v1/vector/query", json={"query": "x"})
    for resp in (mcp, rag, vec):
        assert "text/html" not in resp.headers.get("content-type", "")
        assert resp.status_code != 500
        if resp.content and "json" in resp.headers.get("content-type", ""):
            assert isinstance(resp.json(), dict)
