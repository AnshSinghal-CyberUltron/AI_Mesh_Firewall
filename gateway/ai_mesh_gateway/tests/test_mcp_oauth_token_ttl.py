"""CHG-0049: pin the OAuth token Redis TTL-derivation (item 11 Redis correctness).

`_token_save` must derive the Redis key TTL from the access token's own lifetime
(`expires_at`), NOT a fixed constant — a fixed TTL would either serve an EXPIRED
access token (TTL longer than the token) or evict a still-valid token early (TTL
shorter). These tests pin that behaviour so a future refactor can't silently
regress to a fixed TTL. (Audited alongside: the OAuth flow + token writes use
atomic `setex`; the flow key is `delete`d on pop; `mcp:scan_ver:*` is read-only on
the gateway; the tool-call cap counter is atomic since CHG-0048 — the MCP Redis
write surface is otherwise clean.)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

_GW = Path(__file__).resolve().parents[1]
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))

import mcp_oauth_proxy as oauth  # noqa: E402


class _RecordingRedis:
    def __init__(self):
        self.setex_calls: list[tuple[str, int, str]] = []

    async def setex(self, key, ttl, value):
        self.setex_calls.append((key, int(ttl), value))


@pytest.fixture()
def rec(monkeypatch):
    client = _RecordingRedis()

    async def _fake_get_redis():
        return client

    monkeypatch.setattr(oauth, "_get_redis", _fake_get_redis)
    # Default OFF encryption so the stored value stays inspectable plaintext.
    monkeypatch.delenv("MCP_OAUTH_ENCRYPTION_KEY", raising=False)
    return client


@pytest.mark.asyncio
async def test_ttl_derived_from_expires_at(rec):
    # A 1-hour access token -> Redis TTL ~= 3600 + 60s grace (NOT a fixed constant).
    await oauth._token_save("acme", "https://srv/mcp", {
        "access_token": "at", "expires_at": time.time() + 3600,
    })
    assert len(rec.setex_calls) == 1
    _key, ttl, _val = rec.setex_calls[0]
    assert 3600 < ttl <= 3661                       # expiry-derived + 60 grace
    assert ttl != oauth._TOKEN_DEFAULT_TTL           # NOT the fixed default


@pytest.mark.asyncio
async def test_short_lived_token_ttl_floored_to_300(rec):
    # A very short token still gets a >=300s floor (so the refresh window can act).
    await oauth._token_save("acme", "https://srv/mcp", {
        "access_token": "at", "expires_at": time.time() + 10,
    })
    _key, ttl, _val = rec.setex_calls[0]
    assert ttl == 300


@pytest.mark.asyncio
async def test_default_ttl_when_no_expires_at(rec):
    # No provider expiry -> the 30-day default (documented behaviour).
    await oauth._token_save("acme", "https://srv/mcp", {"access_token": "at"})
    _key, ttl, _val = rec.setex_calls[0]
    assert ttl == oauth._TOKEN_DEFAULT_TTL


@pytest.mark.asyncio
async def test_refresh_token_keeps_key_alive_longer(rec):
    # A short access token WITH a refresh_token must persist at least the default
    # TTL, so the refresh_token survives to rotate the access token.
    await oauth._token_save("acme", "https://srv/mcp", {
        "access_token": "at", "expires_at": time.time() + 10, "refresh_token": "rt",
    })
    _key, ttl, _val = rec.setex_calls[0]
    assert ttl >= oauth._TOKEN_DEFAULT_TTL


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
