"""
Unit tests for gateway AuthMiddleware -- payload corruption fix,
defensive AuthContext, and validate_api_key payload validation.
"""
import asyncio
import hashlib
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "gateway"))

from gateway.middleware import AuthContext, AuthMiddleware, validate_api_key, EXCLUDED_PATH_PREFIXES


VALID_PAYLOAD = {
    "key_id": "abc-123",
    "prefix": "testpref",
    "user_id": 42,
    "project_id": "proj-1",
    "permissions": {"allowed_actions": ["chat"]},
    "allowed_models": ["gpt-4"],
    "rate_limit_tpm": 50_000,
    "risk_score": 0.1,
    "max_context_tokens": 4096,
    "mcp_allowed_tools": [],
    "mcp_max_tool_calls": 0,
    "is_active": True,
    "expires_at": None,
}

RAW_API_KEY = "a" * 48
KEY_HASH = hashlib.sha256(RAW_API_KEY.encode("utf-8")).hexdigest()
REDIS_KEY = f"auth:apikey:{KEY_HASH}"


def _make_redis_mock(payload_dict=None, get_return=None):
    """Create a mock async Redis client."""
    client = AsyncMock()
    if get_return is not None:
        client.get = AsyncMock(return_value=get_return)
    elif payload_dict is not None:
        client.get = AsyncMock(return_value=json.dumps(payload_dict))
    else:
        client.get = AsyncMock(return_value=None)
    client.set = AsyncMock()
    return client


class TestAuthContextDefensive:
    """AuthContext.__init__ must not crash on missing fields."""

    def test_full_payload_succeeds(self):
        ctx = AuthContext(key_hash=KEY_HASH, payload=VALID_PAYLOAD)
        assert ctx.key_id == "abc-123"
        assert ctx.user_id == 42
        assert ctx.project_id == "proj-1"

    def test_empty_payload_uses_defaults(self):
        ctx = AuthContext(key_hash=KEY_HASH, payload={})
        assert ctx.key_id == ""
        assert ctx.user_id == 0
        assert ctx.project_id == ""
        assert ctx.prefix == KEY_HASH[:8]
        assert ctx.permissions == {}
        assert ctx.allowed_models == []
        assert ctx.rate_limit_tpm == 100_000
        assert ctx.risk_score == 0.0
        assert ctx.is_active is True
        assert ctx.expires_at is None

    def test_partial_payload_fills_defaults(self):
        ctx = AuthContext(
            key_hash=KEY_HASH,
            payload={"key_id": "k1", "user_id": 7},
        )
        assert ctx.key_id == "k1"
        assert ctx.user_id == 7
        assert ctx.project_id == ""


class TestValidateApiKey:
    """validate_api_key must reject short keys, missing payloads,
    corrupt payloads, and payloads missing required fields."""

    def test_short_key_rejected(self):
        client = _make_redis_mock()
        ctx, err = asyncio.run(validate_api_key("short", client))
        assert ctx is None
        assert err["status_code"] == 401

    def test_key_not_in_redis_rejected(self):
        client = _make_redis_mock(get_return=None)
        ctx, err = asyncio.run(validate_api_key(RAW_API_KEY, client))
        assert ctx is None
        assert err["status_code"] == 401

    def test_corrupt_json_rejected(self):
        client = _make_redis_mock(get_return="not-valid-json{{{")
        ctx, err = asyncio.run(validate_api_key(RAW_API_KEY, client))
        assert ctx is None
        assert err["status_code"] == 500
        assert "corrupted" in err["message"].lower()

    def test_missing_required_fields_rejected(self):
        """Payload with last_used_at only (the corruption scenario)."""
        client = _make_redis_mock(
            payload_dict={"last_used_at": "2026-03-01T00:00:00+00:00"}
        )
        ctx, err = asyncio.run(validate_api_key(RAW_API_KEY, client))
        assert ctx is None
        assert err["status_code"] == 500
        assert "Re-sync" in err["message"]

    def test_missing_some_required_fields_rejected(self):
        """Payload with key_id but missing user_id and project_id."""
        client = _make_redis_mock(
            payload_dict={"key_id": "k1", "is_active": True}
        )
        ctx, err = asyncio.run(validate_api_key(RAW_API_KEY, client))
        assert ctx is None
        assert err["status_code"] == 500

    def test_valid_payload_accepted(self):
        client = _make_redis_mock(payload_dict=VALID_PAYLOAD)
        ctx, err = asyncio.run(validate_api_key(RAW_API_KEY, client))
        assert err is None
        assert ctx is not None
        assert ctx.key_id == "abc-123"
        assert ctx.user_id == 42
        assert ctx.project_id == "proj-1"

    def test_disabled_key_rejected(self):
        payload = {**VALID_PAYLOAD, "is_active": False}
        client = _make_redis_mock(payload_dict=payload)
        ctx, err = asyncio.run(validate_api_key(RAW_API_KEY, client))
        assert ctx is None
        assert err["status_code"] == 403

    def test_expired_key_rejected(self):
        payload = {
            **VALID_PAYLOAD,
            "expires_at": "2020-01-01T00:00:00+00:00",
        }
        client = _make_redis_mock(payload_dict=payload)
        ctx, err = asyncio.run(validate_api_key(RAW_API_KEY, client))
        assert ctx is None
        assert err["status_code"] == 403

    def test_redis_error_returns_503(self):
        import redis.asyncio as aioredis

        client = AsyncMock()
        client.get = AsyncMock(side_effect=aioredis.RedisError("conn refused"))
        ctx, err = asyncio.run(validate_api_key(RAW_API_KEY, client))
        assert ctx is None
        assert err["status_code"] == 503


class TestUpdateLastUsed:
    """_update_last_used must read-modify-write, not overwrite."""

    def _make_middleware(self):
        app = AsyncMock()
        mw = AuthMiddleware(app, redis_url="redis://localhost:6379/0")
        return mw

    def test_preserves_existing_payload(self):
        mw = self._make_middleware()
        client = _make_redis_mock(payload_dict=VALID_PAYLOAD)

        asyncio.run(mw._update_last_used(client, REDIS_KEY))
        client.set.assert_called_once()
        written_json = client.set.call_args[0][1]
        written = json.loads(written_json)

        assert written["key_id"] == "abc-123"
        assert written["user_id"] == 42
        assert written["project_id"] == "proj-1"
        assert "last_used_at" in written

    def test_adds_timestamp(self):
        mw = self._make_middleware()
        client = _make_redis_mock(payload_dict=VALID_PAYLOAD)

        asyncio.run(mw._update_last_used(client, REDIS_KEY))

        written_json = client.set.call_args[0][1]
        written = json.loads(written_json)
        ts = datetime.fromisoformat(written["last_used_at"])
        assert ts.tzinfo is not None

    def test_keepttl_preserved(self):
        mw = self._make_middleware()
        client = _make_redis_mock(payload_dict=VALID_PAYLOAD)

        asyncio.run(mw._update_last_used(client, REDIS_KEY))

        _, kwargs = client.set.call_args
        assert kwargs.get("keepttl") is True

    def test_handles_missing_redis_key(self):
        mw = self._make_middleware()
        client = _make_redis_mock(get_return=None)

        asyncio.run(mw._update_last_used(client, REDIS_KEY))

        client.set.assert_not_called()

    def test_handles_redis_error_gracefully(self):
        import redis.asyncio as aioredis

        mw = self._make_middleware()
        client = AsyncMock()
        client.get = AsyncMock(side_effect=aioredis.RedisError("timeout"))

        asyncio.run(mw._update_last_used(client, REDIS_KEY))


def test_mcp_routes_are_not_auth_excluded():
    """
    MCP proxy routes must not bypass authentication at middleware level.
    """
    assert "/v1/mcp/secure-gateway/" not in EXCLUDED_PATH_PREFIXES
