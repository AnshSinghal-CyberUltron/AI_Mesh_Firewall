"""
Phase 1 §1.5 — unit tests for CircuitBreaker state transitions.

Uses fakeredis async client. Targets ``circuit_breaker.CircuitBreaker`` directly.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from ai_mesh_gateway.circuit_breaker import CircuitBreaker, CircuitState


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _new_breaker(redis_client, **kwargs):
    return CircuitBreaker(
        redis_client,
        error_threshold=kwargs.get("error_threshold", 0.5),
        min_requests=kwargs.get("min_requests", 4),
        cooldown_seconds=kwargs.get("cooldown_seconds", 1),
        probe_success_count=kwargs.get("probe_success_count", 2),
    )


# ---------- Initial state -------------------------------------------------

def test_initial_state_is_closed(fake_redis):
    cb = _new_breaker(fake_redis)
    status = _run(cb.check("m"))
    assert status.state == CircuitState.CLOSED
    assert status.should_block is False


def test_below_min_requests_stays_closed(fake_redis):
    cb = _new_breaker(fake_redis, min_requests=10)

    async def go():
        # 5 errors but min_requests=10 -> circuit must stay CLOSED
        for _ in range(5):
            await cb.record_error("m")
        return await cb.check("m")

    status = _run(go())
    assert status.state == CircuitState.CLOSED
    assert status.should_block is False


# ---------- CLOSED -> OPEN -----------------------------------------------

def test_opens_when_error_rate_exceeds_threshold(fake_redis):
    cb = _new_breaker(fake_redis, error_threshold=0.5, min_requests=4)

    async def go():
        # 4 errors, 0 successes -> 100% error rate -> OPEN
        for _ in range(4):
            await cb.record_error("m", org_slug="acme")
        return await cb.check("m")

    status = _run(go())
    assert status.state == CircuitState.OPEN
    assert status.should_block is True


def test_open_circuit_sets_kill_switch_key(fake_redis, fake_sync_redis):
    cb = _new_breaker(fake_redis, error_threshold=0.5, min_requests=4, cooldown_seconds=30)

    async def go():
        for _ in range(4):
            await cb.record_error("gpt-4o", org_slug="acme")
        return await cb.check("gpt-4o")

    _run(go())
    raw = fake_sync_redis.get("kill_switch:acme:model:gpt-4o")
    assert raw is not None
    import json

    payload = json.loads(raw)
    assert payload["is_active"] is True
    assert payload["trigger_source"] == "circuit_breaker"


def test_does_not_open_when_under_threshold(fake_redis):
    cb = _new_breaker(fake_redis, error_threshold=0.5, min_requests=4)

    async def go():
        # 1 error, 9 successes -> 10% error rate -> stays CLOSED
        await cb.record_error("m")
        for _ in range(9):
            await cb.record_success("m")
        return await cb.check("m")

    status = _run(go())
    assert status.state == CircuitState.CLOSED


# ---------- OPEN -> HALF_OPEN after cooldown -----------------------------

def test_transitions_to_half_open_after_cooldown(fake_redis):
    cb = _new_breaker(fake_redis, error_threshold=0.5, min_requests=4, cooldown_seconds=1)

    async def go():
        for _ in range(4):
            await cb.record_error("m")
        first = await cb.check("m")
        assert first.state == CircuitState.OPEN
        # Wait out the cooldown
        await asyncio.sleep(1.1)
        return await cb.check("m")

    status = _run(go())
    assert status.state == CircuitState.HALF_OPEN
    assert status.should_block is False


# ---------- HALF_OPEN -> CLOSED on probe success -------------------------

def test_half_open_closes_after_probe_successes(fake_redis):
    cb = _new_breaker(
        fake_redis,
        error_threshold=0.5,
        min_requests=4,
        cooldown_seconds=1,
        probe_success_count=2,
    )

    async def go():
        for _ in range(4):
            await cb.record_error("m")
        await asyncio.sleep(1.1)
        # Trigger HALF_OPEN via check
        st = await cb.check("m")
        assert st.state == CircuitState.HALF_OPEN
        # Two successful probes -> CLOSED
        await cb.record_success("m")
        await cb.record_success("m")
        return await cb.check("m")

    status = _run(go())
    assert status.state == CircuitState.CLOSED


# ---------- HALF_OPEN -> OPEN on probe failure ---------------------------

def test_half_open_reopens_on_probe_failure(fake_redis):
    cb = _new_breaker(fake_redis, error_threshold=0.5, min_requests=4, cooldown_seconds=1)

    async def go():
        for _ in range(4):
            await cb.record_error("m")
        await asyncio.sleep(1.1)
        st = await cb.check("m")
        assert st.state == CircuitState.HALF_OPEN
        # Probe fails -> re-OPEN
        await cb.record_error("m")
        return await cb.check("m")

    status = _run(go())
    assert status.state == CircuitState.OPEN
    assert status.should_block is True


# ---------- Failure mode: Redis errors -----------------------------------

def test_redis_failure_fails_closed():
    """Per INVARIANT 6, check() must fail-closed (should_block=True)."""
    from unittest.mock import AsyncMock, MagicMock

    bad = MagicMock()
    bad.get = AsyncMock(side_effect=RuntimeError("redis down"))
    cb = _new_breaker(bad)
    status = _run(cb.check("m"))
    assert status.should_block is True
    assert status.state == CircuitState.OPEN
