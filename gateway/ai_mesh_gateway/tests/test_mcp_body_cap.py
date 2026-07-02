"""CHG-0063: MCP request bodies are capped on ACTUAL streamed bytes, not just the
declared Content-Length.

`_mcp_body_too_large` only pre-checks the Content-Length HEADER; a chunked /
no-Content-Length body slips past it and `request.body()`/`request.json()` then
buffer the whole stream into memory unbounded (memory-exhaustion DoS — CHG-0034's
documented limitation). `_mcp_read_body_capped` reads the stream incrementally and
raises `_MCPBodyTooLarge` the instant the accumulated size crosses the ceiling.
"""
import sys
from pathlib import Path

_GW = Path(__file__).resolve().parents[1]
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))

import pytest  # noqa: E402
from unittest.mock import AsyncMock, patch  # noqa: E402

import mcp_proxy  # noqa: E402


class _StreamReq:
    """A request double exposing an async stream() of chunks (a chunked body)."""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.consumed = 0

    async def stream(self):
        for c in self._chunks:
            self.consumed += 1
            yield c


@pytest.mark.asyncio
async def test_chunked_body_over_ceiling_raises():
    with patch.object(mcp_proxy, "_MCP_MAX_BODY_BYTES", 100):
        req = _StreamReq([b"a" * 60, b"b" * 60])  # 120 > 100, no Content-Length
        with pytest.raises(mcp_proxy._MCPBodyTooLarge):
            await mcp_proxy._mcp_read_body_capped(req)


@pytest.mark.asyncio
async def test_cap_stops_reading_early_bounds_memory():
    # the cap must fire on the chunk that crosses the ceiling, NOT after buffering
    # the whole (potentially unbounded) stream
    with patch.object(mcp_proxy, "_MCP_MAX_BODY_BYTES", 100):
        req = _StreamReq([b"x" * 80, b"x" * 80, b"x" * 80, b"x" * 80])
        with pytest.raises(mcp_proxy._MCPBodyTooLarge):
            await mcp_proxy._mcp_read_body_capped(req)
        assert req.consumed <= 2  # stopped after the 2nd chunk crossed 100, not all 4


@pytest.mark.asyncio
async def test_body_under_ceiling_returns_and_caches():
    with patch.object(mcp_proxy, "_MCP_MAX_BODY_BYTES", 100):
        req = _StreamReq([b"hello ", b"world"])
        out = await mcp_proxy._mcp_read_body_capped(req)
        assert out == b"hello world"
        assert req._body == b"hello world"          # cached for downstream reads
        # a second call reuses the cache (does not re-consume the stream)
        again = await mcp_proxy._mcp_read_body_capped(req)
        assert again == b"hello world"


@pytest.mark.asyncio
async def test_exactly_at_ceiling_is_ok_one_over_raises():
    with patch.object(mcp_proxy, "_MCP_MAX_BODY_BYTES", 100):
        assert await mcp_proxy._mcp_read_body_capped(_StreamReq([b"z" * 100])) == b"z" * 100
        with pytest.raises(mcp_proxy._MCPBodyTooLarge):
            await mcp_proxy._mcp_read_body_capped(_StreamReq([b"z" * 101]))


@pytest.mark.asyncio
async def test_cached_oversize_body_raises():
    # a body already buffered (e.g. by an earlier read) is still ceiling-checked
    with patch.object(mcp_proxy, "_MCP_MAX_BODY_BYTES", 100):
        req = _StreamReq([])
        req._body = b"q" * 200
        with pytest.raises(mcp_proxy._MCPBodyTooLarge):
            await mcp_proxy._mcp_read_body_capped(req)


@pytest.mark.asyncio
async def test_fallback_to_body_for_test_doubles_still_capped():
    # an object without stream() (a test double) falls back to body() but the ceiling
    # is STILL enforced on the returned bytes
    from types import SimpleNamespace

    with patch.object(mcp_proxy, "_MCP_MAX_BODY_BYTES", 100):
        small = SimpleNamespace(body=AsyncMock(return_value=b"small"))
        assert await mcp_proxy._mcp_read_body_capped(small) == b"small"
        big = SimpleNamespace(body=AsyncMock(return_value=b"B" * 500))
        with pytest.raises(mcp_proxy._MCPBodyTooLarge):
            await mcp_proxy._mcp_read_body_capped(big)


@pytest.mark.asyncio
async def test_org_mcp_tool_call_oversized_chunked_stream_returns_413():
    # end-to-end: a chunked (no Content-Length) oversized body reaches the entry
    # point's capped read and is rejected with 413 BEFORE any scan/forward.
    from types import SimpleNamespace

    from middleware import AuthContext

    auth = AuthContext(
        key_hash="h" * 64,
        payload={"key_id": "k1", "user_id": 1, "project_id": "p1", "org_slug": "demo"},
    )

    async def _big_stream():
        for _ in range(5):
            yield b"x" * 80  # 400 bytes total, no Content-Length header

    req = SimpleNamespace(
        state=SimpleNamespace(auth_context=auth),
        headers={},          # no Content-Length -> _mcp_body_too_large can't pre-check
        stream=_big_stream,
        query_params={},
    )
    with patch.object(mcp_proxy, "_MCP_MAX_BODY_BYTES", 100), \
         patch.object(mcp_proxy, "_validate_org_scope", return_value=None), \
         patch("main._enforce_org_tpm_rate_limit", new_callable=AsyncMock, return_value=None), \
         patch("main._enforce_org_burst_rpm", new_callable=AsyncMock, return_value=None):
        resp = await mcp_proxy.org_mcp_tool_call("demo", "srv", req)
    import json as _json
    assert resp.status_code == 413
    assert _json.loads(bytes(resp.body))["code"] == "mcp_body_too_large"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


class _RespStream:
    """An httpx-response double exposing aiter_bytes() of chunks (CHG-0064)."""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c


@pytest.mark.asyncio
async def test_response_over_ceiling_raises():
    with patch.object(mcp_proxy, "_MCP_MAX_RESPONSE_BYTES", 100):
        with pytest.raises(mcp_proxy._MCPBodyTooLarge):
            await mcp_proxy._read_response_capped(_RespStream([b"a" * 60, b"b" * 60]))


@pytest.mark.asyncio
async def test_response_under_ceiling_returns_joined():
    with patch.object(mcp_proxy, "_MCP_MAX_RESPONSE_BYTES", 100):
        out = await mcp_proxy._read_response_capped(_RespStream([b"hi ", b"there"]))
        assert out == b"hi there"
