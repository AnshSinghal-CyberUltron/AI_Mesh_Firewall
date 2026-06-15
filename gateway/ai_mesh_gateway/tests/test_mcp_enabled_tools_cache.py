"""M-15: cross-process invalidation of the MCP enabled-tools cache.

The gateway caches per-(org, server) tool enable/disable + scan config for
``_ENABLED_TOOLS_TTL`` (30s). Control bumps Redis version keys
(``mcp:scan_ver:{org}`` / ``mcp:scan_ver:{org}:{server}``) on every config
change; the gateway folds both into a composite version checked on each
cache hit, so a bump invalidates the entry immediately (one Redis MGET per
call — no HTTP refetch unless stale).

Also under test: backend lookup failure (fail-open None) is negative-cached
for only ``_ENABLED_TOOLS_NEG_TTL`` (~2s), never the full TTL; and a Redis
outage degrades to the pre-M-15 pure-TTL behaviour instead of refetching.

Backend HTTP is stubbed by patching ``httpx.AsyncClient`` on the module;
Redis is the async fakeredis client from conftest, injected directly into
``mcp_proxy._scan_ver_redis``.
"""

from __future__ import annotations

import time

import pytest

import mcp_proxy


# ── HTTP stub ────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text
        self.headers = {}

    def json(self):
        return self._payload


class _StubAsyncClient:
    """Counts backend GETs; serves scripted responses (last one repeats)."""

    calls = 0
    responses: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, **kwargs):
        cls = type(self)
        cls.calls += 1
        item = cls.responses[min(cls.calls - 1, len(cls.responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


def _payload(disabled=()):
    tools = ["read_file", "delete_file"]
    return {
        "known_tools": tools,
        "enabled_tools": [t for t in tools if t not in disabled],
        "disabled_tools": list(disabled),
        "default_scan_action": "tag",
        "tool_scan_actions": {},
        "server_id": "sid-1",
        "effective_scan_controls": {},
        "effective_scan_controls_by_tool": {},
        "mcp_tier2_enabled": False,
        "tier2_strict": True,
    }


class _BrokenRedis:
    async def mget(self, *keys):
        raise ConnectionError("redis down")


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_state(fake_redis, monkeypatch):
    """Fresh cache + fakeredis-backed version reads + stubbed backend HTTP."""
    mcp_proxy._enabled_tools_cache.clear()
    mcp_proxy._enabled_tools_ttl.clear()
    mcp_proxy._enabled_tools_ver.clear()
    mcp_proxy._scan_ver_redis = fake_redis
    _StubAsyncClient.calls = 0
    _StubAsyncClient.responses = [_FakeResponse(payload=_payload())]
    monkeypatch.setattr(mcp_proxy.httpx, "AsyncClient", _StubAsyncClient)
    yield
    mcp_proxy._enabled_tools_cache.clear()
    mcp_proxy._enabled_tools_ttl.clear()
    mcp_proxy._enabled_tools_ver.clear()
    mcp_proxy._scan_ver_redis = None


CACHE_KEY = "acme/files"


# ── Cache + version-key invalidation ─────────────────────────────────


@pytest.mark.asyncio
async def test_first_call_fetches_then_serves_from_cache():
    r1 = await mcp_proxy._get_enabled_tools("acme", "files")
    assert r1 is not None and r1["known"] == {"read_file", "delete_file"}
    assert _StubAsyncClient.calls == 1

    r2 = await mcp_proxy._get_enabled_tools("acme", "files")
    assert r2 is r1
    assert _StubAsyncClient.calls == 1  # no HTTP refetch within TTL


@pytest.mark.asyncio
async def test_server_version_bump_invalidates_immediately(fake_sync_redis):
    r1 = await mcp_proxy._get_enabled_tools("acme", "files")
    assert r1["disabled"] == set()
    assert _StubAsyncClient.calls == 1

    # Operator disables a tool -> control INCRs the server version key.
    fake_sync_redis.incr("mcp:scan_ver:acme:files")
    _StubAsyncClient.responses = [
        _FakeResponse(payload=_payload(disabled=("delete_file",)))
    ]

    r2 = await mcp_proxy._get_enabled_tools("acme", "files")
    assert _StubAsyncClient.calls == 2  # refetched despite TTL not expired
    assert r2["disabled"] == {"delete_file"}
    assert mcp_proxy._is_tool_disabled("delete_file", r2)


@pytest.mark.asyncio
async def test_org_version_bump_invalidates_immediately(fake_sync_redis):
    await mcp_proxy._get_enabled_tools("acme", "files")
    assert _StubAsyncClient.calls == 1

    # Org-scoped scan-control change -> org-level key bump.
    fake_sync_redis.incr("mcp:scan_ver:acme")

    await mcp_proxy._get_enabled_tools("acme", "files")
    assert _StubAsyncClient.calls == 2


@pytest.mark.asyncio
async def test_unchanged_version_does_not_refetch(fake_sync_redis):
    fake_sync_redis.set("mcp:scan_ver:acme", 4)
    fake_sync_redis.set("mcp:scan_ver:acme:files", 9)

    await mcp_proxy._get_enabled_tools("acme", "files")
    assert mcp_proxy._enabled_tools_ver[CACHE_KEY] == "4:9"

    for _ in range(3):
        await mcp_proxy._get_enabled_tools("acme", "files")
    assert _StubAsyncClient.calls == 1


# ── Negative cache: failure-None must NOT live for the full TTL ──────


@pytest.mark.asyncio
async def test_backend_failure_negative_cached_briefly_then_recovers():
    _StubAsyncClient.responses = [RuntimeError("backend down")]

    r1 = await mcp_proxy._get_enabled_tools("acme", "files")
    assert r1 is None  # fail-open
    assert _StubAsyncClient.calls == 1

    # Within the ~2s negative window: served from negative cache, no refetch.
    r2 = await mcp_proxy._get_enabled_tools("acme", "files")
    assert r2 is None
    assert _StubAsyncClient.calls == 1

    # Age the entry past the negative TTL but FAR within the 30s full TTL:
    # the old behaviour (cache None for the full TTL) would still serve None.
    mcp_proxy._enabled_tools_ttl[CACHE_KEY] = (
        time.time() - mcp_proxy._ENABLED_TOOLS_NEG_TTL - 0.1
    )
    _StubAsyncClient.responses = [_FakeResponse(payload=_payload())]

    r3 = await mcp_proxy._get_enabled_tools("acme", "files")
    assert _StubAsyncClient.calls == 2
    assert r3 is not None and r3["known"] == {"read_file", "delete_file"}


@pytest.mark.asyncio
async def test_non_200_also_negative_cached_briefly():
    _StubAsyncClient.responses = [_FakeResponse(status_code=503, text="boom")]

    assert await mcp_proxy._get_enabled_tools("acme", "files") is None
    assert mcp_proxy._enabled_tools_cache[CACHE_KEY] is None
    assert CACHE_KEY not in mcp_proxy._enabled_tools_ver

    mcp_proxy._enabled_tools_ttl[CACHE_KEY] = (
        time.time() - mcp_proxy._ENABLED_TOOLS_NEG_TTL - 0.1
    )
    _StubAsyncClient.responses = [_FakeResponse(payload=_payload())]
    assert await mcp_proxy._get_enabled_tools("acme", "files") is not None
    assert _StubAsyncClient.calls == 2


# ── Redis outage: degrade to TTL-only, never per-call HTTP refetch ───


@pytest.mark.asyncio
async def test_redis_unavailable_degrades_to_ttl_cache():
    mcp_proxy._scan_ver_redis = _BrokenRedis()

    r1 = await mcp_proxy._get_enabled_tools("acme", "files")
    assert r1 is not None
    assert _StubAsyncClient.calls == 1
    # Version unknown at fetch time -> no stored version association.
    assert CACHE_KEY not in mcp_proxy._enabled_tools_ver

    # Subsequent calls serve the cached entry (pure-TTL fallback).
    r2 = await mcp_proxy._get_enabled_tools("acme", "files")
    assert r2 is r1
    assert _StubAsyncClient.calls == 1


@pytest.mark.asyncio
async def test_redis_recovery_after_blind_fetch_refetches_once(fake_redis, fake_sync_redis):
    # Fetch while Redis is down: entry cached with no version association.
    mcp_proxy._scan_ver_redis = _BrokenRedis()
    await mcp_proxy._get_enabled_tools("acme", "files")
    assert _StubAsyncClient.calls == 1

    # Redis comes back: stored-version mismatch (None vs "0:0") forces ONE
    # conservative refetch, after which the version is associated again.
    mcp_proxy._scan_ver_redis = fake_redis
    await mcp_proxy._get_enabled_tools("acme", "files")
    assert _StubAsyncClient.calls == 2
    assert mcp_proxy._enabled_tools_ver[CACHE_KEY] == "0:0"

    await mcp_proxy._get_enabled_tools("acme", "files")
    assert _StubAsyncClient.calls == 2
