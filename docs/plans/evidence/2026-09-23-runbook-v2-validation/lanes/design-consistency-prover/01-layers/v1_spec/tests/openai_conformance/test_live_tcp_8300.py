"""LGW01-2 live TCP against this host's v1 gateway :8300 (no staging nginx).

Does not assert stubbed upstream bytes. Proves SDK-visible auth and JSON
error envelopes on the real socket.
"""

from __future__ import annotations

import os

import httpx
import openai
import pytest

from gateway_v2.contracts.openai_conformance.harness import tcp_base_url

pytestmark = pytest.mark.skipif(
    not os.environ.get("AMF_CONFORMANCE_BASE_URL"),
    reason="AMF_CONFORMANCE_BASE_URL unset (CI uses loopback uvicorn instead)",
)


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
        with pytest.raises(openai.AuthenticationError):
            await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "hi"}],
            )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_live_tcp_error_body_is_json_not_html() -> None:
    base = tcp_base_url()
    assert base
    async with httpx.AsyncClient(base_url=base, timeout=15) as raw:
        resp = await raw.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code in {401, 403}
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
