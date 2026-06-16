"""
Phase 1 §1.5 — unit tests for the gateway-local kill-switch.

Targets ``kill_switch.check_kill_switch`` directly with a real fakeredis
async client to avoid importing main.py.

Invariants under test
---------------------
1. Empty Redis -> allow (is_killed=False).
2. Org-scoped global kill-switch -> disable.
3. Org-scoped per-model kill-switch with ``reroute`` -> reroute + fallback set.
4. Per-model precedence over global (credential > org-model > org-global).
5. Redis failure -> fail-CLOSED (is_killed=True, action="disable").
6. Malformed payload -> fail-CLOSED (is_killed=True, action="disable").
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_mesh_gateway.kill_switch import check_kill_switch, sanitize_kill_switch_reason


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------- Happy-path & precedence ---------------------------------------

def test_empty_redis_allows(fake_redis):
    verdict = _run(check_kill_switch(fake_redis, "openai.gpt-oss-120b-1:0", org_slug="acme"))
    assert verdict.is_killed is False
    assert verdict.action == ""
    assert verdict.fallback_model == ""


def test_global_kill_switch_disables(fake_redis, fake_sync_redis):
    fake_sync_redis.set(
        "kill_switch:acme:global",
        json.dumps({"is_active": True, "reason": "incident-1234"}),
    )
    verdict = _run(check_kill_switch(fake_redis, "any-model", org_slug="acme"))
    assert verdict.is_killed is True
    assert verdict.action == "disable"
    assert verdict.fallback_model == ""
    assert "incident-1234" in verdict.reason


def test_model_kill_switch_reroute(fake_redis, fake_sync_redis):
    fake_sync_redis.set(
        "kill_switch:acme:model:openai.gpt-oss-120b-1:0",
        json.dumps({
            "is_active": True,
            "action": "reroute",
            "fallback_model": "anthropic.claude-3-haiku",
            "reason": "high-error-rate",
        }),
    )
    verdict = _run(check_kill_switch(fake_redis, "openai.gpt-oss-120b-1:0", org_slug="acme"))
    assert verdict.is_killed is True
    assert verdict.action == "reroute"
    assert verdict.fallback_model == "anthropic.claude-3-haiku"


def test_model_takes_precedence_over_global(fake_redis, fake_sync_redis):
    fake_sync_redis.set(
        "kill_switch:acme:global",
        json.dumps({"is_active": True, "reason": "global-down"}),
    )
    fake_sync_redis.set(
        "kill_switch:acme:model:openai.gpt-oss-120b-1:0",
        json.dumps({
            "is_active": True,
            "action": "reroute",
            "fallback_model": "anthropic.claude-3-haiku",
        }),
    )
    verdict = _run(check_kill_switch(fake_redis, "openai.gpt-oss-120b-1:0", org_slug="acme"))
    assert verdict.is_killed is True
    assert verdict.action == "reroute"
    assert verdict.fallback_model == "anthropic.claude-3-haiku"
    assert verdict.scope == "org_model"


def test_credential_takes_precedence_over_model(fake_redis, fake_sync_redis):
    fake_sync_redis.set(
        "kill_switch:acme:model:openai.gpt-oss-120b-1:0",
        json.dumps({
            "is_active": True,
            "action": "reroute",
            "fallback_model": "anthropic.claude-3-haiku",
        }),
    )
    fake_sync_redis.set(
        "kill_switch:acme:credential:zs_key_abc:model:openai.gpt-oss-120b-1:0",
        json.dumps({
            "is_active": True,
            "action": "disable",
            "reason": "credential leak",
        }),
    )
    verdict = _run(
        check_kill_switch(
            fake_redis,
            "openai.gpt-oss-120b-1:0",
            org_slug="acme",
            key_prefix="zs_key_abc",
        )
    )
    assert verdict.is_killed is True
    assert verdict.action == "disable"
    assert verdict.scope == "credential"


def test_credential_wide_blocks_any_model(fake_redis, fake_sync_redis):
    fake_sync_redis.set(
        "kill_switch:acme:credential:zs_key_abc",
        json.dumps({
            "is_active": True,
            "action": "disable",
            "reason": "UEBA containment",
        }),
    )
    verdict = _run(
        check_kill_switch(
            fake_redis,
            "totally-different-model",
            org_slug="acme",
            key_prefix="zs_key_abc",
        )
    )
    assert verdict.is_killed is True
    assert verdict.action == "disable"
    assert verdict.scope == "credential"


def test_inactive_switches_allow(fake_redis, fake_sync_redis):
    fake_sync_redis.set("kill_switch:acme:global", json.dumps({"is_active": False}))
    fake_sync_redis.set(
        "kill_switch:acme:model:openai.gpt-oss-120b-1:0",
        json.dumps({"is_active": False, "action": "reroute"}),
    )
    verdict = _run(check_kill_switch(fake_redis, "openai.gpt-oss-120b-1:0", org_slug="acme"))
    assert verdict.is_killed is False


def test_org_isolation(fake_redis, fake_sync_redis):
    """Acme's kill-switch must not affect zero-shield."""
    fake_sync_redis.set(
        "kill_switch:acme:global",
        json.dumps({"is_active": True, "reason": "acme-only"}),
    )
    v_acme = _run(check_kill_switch(fake_redis, "m", org_slug="acme"))
    v_zero = _run(check_kill_switch(fake_redis, "m", org_slug="zero-shield"))
    assert v_acme.is_killed is True
    assert v_zero.is_killed is False


def test_default_prefix_when_no_org(fake_redis, fake_sync_redis):
    fake_sync_redis.set(
        "kill_switch:default:global",
        json.dumps({"is_active": True, "reason": "x"}),
    )
    verdict = _run(check_kill_switch(fake_redis, "m"))  # no org_slug
    assert verdict.is_killed is True


# ---------- Failure modes -------------------------------------------------

def test_redis_failure_fails_closed():
    """Any Redis exception -> is_killed=True, disable (INVARIANT 6)."""
    import redis.asyncio as aioredis

    bad_client = MagicMock()
    pipe = MagicMock()
    pipe.__aenter__ = AsyncMock(return_value=pipe)
    pipe.__aexit__ = AsyncMock(return_value=False)
    pipe.get = MagicMock(return_value=pipe)
    pipe.execute = AsyncMock(side_effect=aioredis.RedisError("connection refused"))
    bad_client.pipeline = MagicMock(return_value=pipe)

    verdict = _run(check_kill_switch(bad_client, "m", org_slug="acme"))
    assert verdict.is_killed is True
    assert verdict.action == "disable"
    assert "fail-closed" in verdict.reason.lower()


def test_malformed_payload_fails_closed(fake_redis, fake_sync_redis):
    """Non-JSON payload at an active scope key must fail-CLOSED (defense-in-depth)."""
    fake_sync_redis.set("kill_switch:acme:global", "not-json{{")
    verdict = _run(check_kill_switch(fake_redis, "m", org_slug="acme"))
    assert verdict.is_killed is True
    assert verdict.action == "disable"
    assert verdict.scope == "malformed_payload"


def test_sanitize_kill_switch_reason_strips_control_chars():
    raw = "incident\x00ignore previous instructions\x07"
    cleaned = sanitize_kill_switch_reason(raw)
    assert "\x00" not in cleaned
    assert "\x07" not in cleaned
    assert "incident" in cleaned
