"""M-19: explicit signed/unsigned policy-bundle acceptance decision.

Decision matrix under test (PolicySync._accept_bundle):
1. valid signature                                  -> ACCEPT
2. POLICY_SIGNING_KEY configured + unsigned/bad sig -> REJECT (always,
   even when GATEWAY_POLICY_SIGNING_REQUIRED=false)
3. no key + GATEWAY_POLICY_SIGNING_REQUIRED=true    -> REJECT
4. no key + signing not required                    -> ACCEPT with a
   one-time warning (not per-bundle log spam)
"""

import asyncio
import hashlib
import hmac
import json
import logging
from unittest.mock import AsyncMock

import pytest

from ai_mesh_gateway import policy_sync as ps_mod
from ai_mesh_gateway.policy_sync import PolicySync

SIGNING_KEY = "test-signing-key"


def _bundle(version=1, count=1):
    return {
        "compiled_at": 1700000000.0,
        "version": version,
        "policy_count": count,
        "policies": [{"policy": {"id": 1, "code": "P1"}, "rules": []}],
    }


def _signed(bundle, key=SIGNING_KEY):
    body = {**bundle, "_sig_alg": "HMAC-SHA256"}
    payload = json.dumps(
        body, sort_keys=True, default=str, separators=(",", ":")
    ).encode("utf-8")
    sig = hmac.new(key.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return {**body, "_sig": sig}


@pytest.fixture()
def emit_spy(monkeypatch):
    spy = AsyncMock()
    monkeypatch.setattr(ps_mod, "emit_operational_event", spy)
    return spy


@pytest.fixture()
def key_configured(monkeypatch):
    monkeypatch.setenv("POLICY_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("GATEWAY_POLICY_SIGNING_REQUIRED", "true")


@pytest.fixture()
def no_key_not_required(monkeypatch):
    monkeypatch.delenv("POLICY_SIGNING_KEY", raising=False)
    monkeypatch.setenv("GATEWAY_POLICY_SIGNING_REQUIRED", "false")


# ── decision matrix (direct _accept_bundle) ────────────────────────────────


@pytest.mark.asyncio
async def test_valid_signature_accepted(key_configured, emit_spy):
    sync = PolicySync("redis://unused")
    assert sync._accept_bundle(
        _signed(_bundle()), "default", site="initial_load", redis_key="k"
    )
    emit_spy.assert_not_called()


@pytest.mark.asyncio
async def test_key_configured_unsigned_rejected(key_configured, emit_spy):
    sync = PolicySync("redis://unused")
    assert not sync._accept_bundle(
        _bundle(), "default", site="initial_load", redis_key="k"
    )
    await asyncio.sleep(0)
    emit_spy.assert_called_once()
    assert emit_spy.call_args.kwargs["metadata"]["site"] == "initial_load"


@pytest.mark.asyncio
async def test_key_configured_rejects_even_when_not_required(monkeypatch, emit_spy):
    """The presence of POLICY_SIGNING_KEY wins over the cutover flag."""
    monkeypatch.setenv("POLICY_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("GATEWAY_POLICY_SIGNING_REQUIRED", "false")
    sync = PolicySync("redis://unused")
    assert not sync._accept_bundle(
        _bundle(), "default", site="refresh", redis_key="k"
    )
    await asyncio.sleep(0)
    emit_spy.assert_called_once()
    assert emit_spy.call_args.kwargs["metadata"]["site"] == "refresh"
    assert "current_version" in emit_spy.call_args.kwargs["metadata"]


@pytest.mark.asyncio
async def test_key_configured_tampered_rejected(key_configured, emit_spy):
    tampered = _signed(_bundle())
    tampered["policies"] = []  # mutate after signing
    sync = PolicySync("redis://unused")
    assert not sync._accept_bundle(
        tampered, "default", site="refresh", redis_key="k"
    )


@pytest.mark.asyncio
async def test_no_key_required_rejected(monkeypatch, emit_spy):
    monkeypatch.delenv("POLICY_SIGNING_KEY", raising=False)
    monkeypatch.setenv("GATEWAY_POLICY_SIGNING_REQUIRED", "true")
    sync = PolicySync("redis://unused")
    assert not sync._accept_bundle(
        _bundle(), "default", site="initial_load", redis_key="k"
    )


@pytest.mark.asyncio
async def test_no_key_not_required_accepts_with_one_time_warning(
    no_key_not_required, emit_spy, caplog
):
    sync = PolicySync("redis://unused")
    with caplog.at_level(logging.WARNING, logger="gateway.policy_sync"):
        assert sync._accept_bundle(_bundle(), "org-a", site="initial_load", redis_key="k1")
        assert sync._accept_bundle(_bundle(), "org-b", site="initial_load", redis_key="k2")
        assert sync._accept_bundle(_bundle(), "org-a", site="refresh", redis_key="k1")
    warnings = [r for r in caplog.records if "Accepting UNSIGNED" in r.getMessage()]
    assert len(warnings) == 1  # one-time, not per-bundle
    emit_spy.assert_not_called()


# ── end-to-end through Redis load/refresh paths ────────────────────────────


def _patch_from_url(monkeypatch, fake_client):
    monkeypatch.setattr(
        ps_mod.aioredis.Redis, "from_url", lambda *a, **k: fake_client
    )


@pytest.mark.asyncio
async def test_initial_load_drops_unsigned_keeps_signed(
    key_configured, emit_spy, monkeypatch, fake_redis
):
    await fake_redis.set("policies:compiled:signed-org", json.dumps(_signed(_bundle())))
    await fake_redis.set("policies:compiled:unsigned-org", json.dumps(_bundle()))
    _patch_from_url(monkeypatch, fake_redis)

    sync = PolicySync("redis://unused")
    await sync._load_initial_bundle()

    assert sync.get_policies("signed-org")
    assert sync.get_policies("unsigned-org") == []
    assert "unsigned-org" not in sync._org_caches


@pytest.mark.asyncio
async def test_refresh_tampered_bundle_retains_last_good(
    key_configured, emit_spy, fake_redis
):
    sync = PolicySync("redis://unused")
    await fake_redis.set(
        "policies:compiled:default", json.dumps(_signed(_bundle(version=1)))
    )
    await sync._refresh_cache(fake_redis, "default")
    assert sync._org_versions["default"] == 1

    tampered = _signed(_bundle(version=2))
    tampered["policy_count"] = 999
    await fake_redis.set("policies:compiled:default", json.dumps(tampered))
    await sync._refresh_cache(fake_redis, "default")

    assert sync._org_versions["default"] == 1  # last-good retained
    assert sync._org_caches["default"]["policy_count"] == 1
