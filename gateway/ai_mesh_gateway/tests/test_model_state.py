"""
Phase 1 §1.5 — unit tests for ``model_state.check_model_state``.

Verifies org-scoped isolation, action routing (block/reroute/alert),
auto-recovery via ``isolated_until``, and fail-CLOSED on Redis errors.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import redis.asyncio as aioredis

from ai_mesh_gateway.model_state import check_model_state


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------- Active / empty ------------------------------------------------

def test_empty_state_returns_active(fake_redis):
    v = _run(check_model_state(fake_redis, "m", org_slug="acme"))
    assert v.status == "active"
    assert v.action == ""


# ---------- Isolated: block / reroute / alert ----------------------------

def test_isolated_block(fake_redis, fake_sync_redis):
    fake_sync_redis.set(
        "model_state:acme:openai.gpt-oss-120b-1:0",
        json.dumps({
            "status": "isolated",
            "action": "block",
            "isolation_reason": "manual-isolate",
            "risk_score": 95.0,
            "threshold": 80.0,
        }),
    )
    v = _run(check_model_state(fake_redis, "openai.gpt-oss-120b-1:0", org_slug="acme"))
    assert v.status == "isolated"
    assert v.action == "block"
    assert v.risk_score == 95.0
    assert v.threshold == 80.0


def test_isolated_reroute_with_fallback(fake_redis, fake_sync_redis):
    fake_sync_redis.set(
        "model_state:acme:openai.gpt-oss-120b-1:0",
        json.dumps({
            "status": "isolated",
            "action": "reroute",
            "fallback_model": "anthropic.claude-3-haiku",
            "isolation_reason": "drift",
            "risk_score": 90.0,
            "threshold": 80.0,
        }),
    )
    v = _run(check_model_state(fake_redis, "openai.gpt-oss-120b-1:0", org_slug="acme"))
    assert v.status == "isolated"
    assert v.action == "reroute"
    assert v.fallback_model == "anthropic.claude-3-haiku"


def test_degraded_returns_alert(fake_redis, fake_sync_redis):
    fake_sync_redis.set(
        "model_state:acme:m",
        json.dumps({"status": "degraded", "risk_score": 60.0, "threshold": 80.0}),
    )
    v = _run(check_model_state(fake_redis, "m", org_slug="acme"))
    assert v.status == "degraded"
    assert v.action == "alert"


# ---------- Auto-recovery -------------------------------------------------

def test_auto_recovery_when_isolated_until_passed(fake_redis, fake_sync_redis):
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    key = "model_state:acme:m"
    fake_sync_redis.set(
        key,
        json.dumps({
            "status": "isolated",
            "action": "block",
            "isolated_until": past,
            "risk_score": 95.0,
            "threshold": 80.0,
            "isolation_reason": "stale",
        }),
    )
    v = _run(check_model_state(fake_redis, "m", org_slug="acme"))
    assert v.status == "active"
    healed = json.loads(fake_sync_redis.get(key))
    assert healed["status"] == "active"
    assert healed.get("isolated_until") in (None, "")


def test_still_isolated_when_isolated_until_future(fake_redis, fake_sync_redis):
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
    fake_sync_redis.set(
        "model_state:acme:m",
        json.dumps({
            "status": "isolated",
            "action": "block",
            "isolated_until": future,
            "risk_score": 95.0,
            "threshold": 80.0,
        }),
    )
    v = _run(check_model_state(fake_redis, "m", org_slug="acme"))
    assert v.status == "isolated"
    assert v.action == "block"


# ---------- Org isolation -------------------------------------------------

def test_org_scoped_isolation(fake_redis, fake_sync_redis):
    fake_sync_redis.set(
        "model_state:acme:m",
        json.dumps({"status": "isolated", "action": "block"}),
    )
    v_acme = _run(check_model_state(fake_redis, "m", org_slug="acme"))
    v_zero = _run(check_model_state(fake_redis, "m", org_slug="zero-shield"))
    assert v_acme.status == "isolated"
    assert v_zero.status == "active"


# ---------- Failure modes -------------------------------------------------

def test_redis_failure_fails_closed():
    bad = MagicMock()
    bad.get = AsyncMock(side_effect=aioredis.RedisError("down"))
    v = _run(check_model_state(bad, "m", org_slug="acme"))
    assert v.status == "suspended"
    assert "Redis unavailable" in v.reason


def test_malformed_payload_is_suspended(fake_redis, fake_sync_redis):
    fake_sync_redis.set("model_state:acme:m", "{not json")
    v = _run(check_model_state(fake_redis, "m", org_slug="acme"))
    assert v.status == "suspended"
