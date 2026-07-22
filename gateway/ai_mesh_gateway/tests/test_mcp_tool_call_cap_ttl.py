"""CHG-0048: the MCP per-key tool-call cap counter must set its window TTL
ATOMICALLY with the increment.

The old code (`count = INCR; if count == 1: EXPIRE`) set the TTL only on the first
increment, so a crash / dropped EXPIRE at that moment left `mcp:toolcalls:<key>`
with no TTL forever — the counter never reset and the key was permanently capped
once it crossed `mcp_max_tool_calls`. The fix runs EXPIRE (NX) on every increment
inside a MULTI/EXEC pipeline: fixed 60s window preserved, missing TTL healed next
call. These tests use a REAL (fake) Redis so the atomic behaviour is exercised, not
mocked away.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_GW = Path(__file__).resolve().parents[1]
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))

import fakeredis.aioredis as fakeaioredis  # noqa: E402

import mcp_proxy  # noqa: E402


class _Auth:
    def __init__(self, key_hash: str):
        self.key_hash = key_hash


@pytest.fixture()
def fake_redis(monkeypatch):
    client = fakeaioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(mcp_proxy, "_get_scan_ver_redis", lambda: client)
    return client


@pytest.mark.asyncio
async def test_incr_sets_ttl_atomically_on_first_call(fake_redis):
    n = await mcp_proxy._incr_tool_call_count(_Auth("k1"))
    assert n == 1
    ttl = await fake_redis.ttl("mcp:toolcalls:k1")
    # A TTL was set together with the increment (the whole point of the fix).
    assert 0 < ttl <= mcp_proxy._MCP_TOOL_CALL_WINDOW_SEC


@pytest.mark.asyncio
async def test_incr_preserves_fixed_window(fake_redis):
    assert await mcp_proxy._incr_tool_call_count(_Auth("k2")) == 1
    ttl1 = await fake_redis.ttl("mcp:toolcalls:k2")
    assert await mcp_proxy._incr_tool_call_count(_Auth("k2")) == 2
    ttl2 = await fake_redis.ttl("mcp:toolcalls:k2")
    # NX means the second increment does NOT extend the window (fixed, not sliding).
    assert ttl2 <= ttl1


@pytest.mark.asyncio
async def test_incr_heals_missing_ttl(fake_redis):
    # Simulate the OLD-bug state: a counter key that lost its TTL (e.g. the original
    # EXPIRE was dropped). The next increment MUST re-establish a TTL.
    assert await mcp_proxy._incr_tool_call_count(_Auth("k3")) == 1
    await fake_redis.persist("mcp:toolcalls:k3")               # drop the TTL
    assert await fake_redis.ttl("mcp:toolcalls:k3") == -1      # no TTL (would leak forever)
    assert await mcp_proxy._incr_tool_call_count(_Auth("k3")) == 2
    healed = await fake_redis.ttl("mcp:toolcalls:k3")
    assert 0 < healed <= mcp_proxy._MCP_TOOL_CALL_WINDOW_SEC   # TTL healed


@pytest.mark.asyncio
async def test_incr_fails_open_on_redis_error(monkeypatch):
    class _Boom:
        def pipeline(self, *a, **k):
            raise RuntimeError("redis down")

    monkeypatch.setattr(mcp_proxy, "_get_scan_ver_redis", lambda: _Boom())
    # Fail-open: a Redis outage must not block the caller (cap is a soft limit).
    assert await mcp_proxy._incr_tool_call_count(_Auth("k4")) == 0


@pytest.mark.asyncio
async def test_incr_no_key_returns_zero():
    assert await mcp_proxy._incr_tool_call_count(None) == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
