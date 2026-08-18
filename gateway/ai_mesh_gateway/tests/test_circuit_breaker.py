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
    assert payload["action"] == "disable"
    assert not payload.get("fallback_model")


def test_open_circuit_trip_reroutes_when_model_state_has_fallback(fake_redis, fake_sync_redis):
    """CB OPEN mirrors KS action=reroute when ModelState.fallback_model is set."""
    import json

    fake_sync_redis.set(
        "model_state:acme:gpt-4o",
        json.dumps({"status": "active", "fallback_model": "gpt-4o-mini", "action": "block"}),
    )
    cb = _new_breaker(fake_redis, error_threshold=0.5, min_requests=4, cooldown_seconds=30)

    async def go():
        for _ in range(4):
            await cb.record_error("gpt-4o", org_slug="acme")
        return await cb.check("gpt-4o")

    status = _run(go())
    assert status.should_block is True
    raw = fake_sync_redis.get("kill_switch:acme:model:gpt-4o")
    payload = json.loads(raw)
    assert payload["action"] == "reroute"
    assert payload["fallback_model"] == "gpt-4o-mini"
    assert payload["trigger_source"] == "circuit_breaker"


def test_open_circuit_trip_uses_fallback_resolver(fake_redis, fake_sync_redis):
    import json

    def resolver(org_slug, model):
        assert org_slug == "acme"
        assert model == "primary"
        return "fallback-model"

    cb = CircuitBreaker(
        fake_redis,
        error_threshold=0.5,
        min_requests=4,
        cooldown_seconds=30,
        fallback_resolver=resolver,
    )

    async def go():
        for _ in range(4):
            await cb.record_error("primary", org_slug="acme")
        return await cb.check("primary")

    _run(go())
    payload = json.loads(fake_sync_redis.get("kill_switch:acme:model:primary"))
    assert payload["action"] == "reroute"
    assert payload["fallback_model"] == "fallback-model"


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


# ---------- M-14: atomic HALF_OPEN probe counting -------------------------

def test_half_open_transition_resets_stale_probe_counters(fake_redis, fake_sync_redis):
    """Defect 1: OPEN->HALF_OPEN must wipe stale probe counts so they
    cannot instantly re-close the circuit."""
    cb = _new_breaker(fake_redis, probe_success_count=2, cooldown_seconds=1)

    async def go():
        for _ in range(4):
            await cb.record_error("m")
        # Plant stale counters: the legacy unscoped key and the
        # epoch-scoped key the next cycle will use (epoch 1).
        fake_sync_redis.set("circuit:probes:m", 5)
        fake_sync_redis.set("circuit:probes:m:1", 5)
        await asyncio.sleep(1.1)
        st = await cb.check("m")
        assert st.state == CircuitState.HALF_OPEN
        # One success must NOT close (would have with the stale count).
        await cb.record_success("m")
        mid = await cb.check("m")
        assert mid.state == CircuitState.HALF_OPEN
        # The second success closes — count continued correctly from 1.
        await cb.record_success("m")
        return await cb.check("m")

    status = _run(go())
    assert status.state == CircuitState.CLOSED


def test_concurrent_checks_single_transition_bounded_probes(fake_redis, fake_sync_redis):
    """Defect 2: concurrent check() calls after cooldown must perform the
    OPEN->HALF_OPEN transition exactly once and admit at most
    probe_success_count probes."""
    cb = _new_breaker(fake_redis, probe_success_count=2, cooldown_seconds=1)

    async def go():
        for _ in range(4):
            await cb.record_error("m")
        await asyncio.sleep(1.1)
        return await asyncio.gather(*[cb.check("m") for _ in range(10)])

    results = _run(go())
    admitted = [r for r in results if not r.should_block]
    blocked = [r for r in results if r.should_block]
    assert 1 <= len(admitted) <= 2  # winner + at most one more slot
    assert len(blocked) == 10 - len(admitted)
    # Exactly one transition happened: epoch bumped exactly once.
    assert int(fake_sync_redis.get("circuit:epoch:m")) == 1
    assert fake_sync_redis.get("circuit:state:m") == "half_open"


def test_half_open_admission_cap_blocks_excess_probes(fake_redis):
    """Sequential check() calls in HALF_OPEN are capped per cycle too."""
    cb = _new_breaker(fake_redis, probe_success_count=2, cooldown_seconds=1)

    async def go():
        for _ in range(4):
            await cb.record_error("m")
        await asyncio.sleep(1.1)
        first = await cb.check("m")    # transition winner = probe 1
        second = await cb.check("m")   # probe 2
        third = await cb.check("m")    # over the cap
        return first, second, third

    first, second, third = _run(go())
    assert first.state == CircuitState.HALF_OPEN and first.should_block is False
    assert second.state == CircuitState.HALF_OPEN and second.should_block is False
    assert third.state == CircuitState.HALF_OPEN and third.should_block is True


def test_concurrent_probe_successes_close_circuit(fake_redis):
    """Concurrent probe successes (WATCH contention + retries) must all be
    counted — the circuit closes at exactly the threshold."""
    cb = _new_breaker(fake_redis, probe_success_count=3, cooldown_seconds=1)

    async def go():
        for _ in range(4):
            await cb.record_error("m")
        await asyncio.sleep(1.1)
        st = await cb.check("m")
        assert st.state == CircuitState.HALF_OPEN
        await asyncio.gather(*[cb.record_success("m") for _ in range(3)])
        return await cb.check("m")

    status = _run(go())
    assert status.state == CircuitState.CLOSED
    assert status.should_block is False


def test_success_after_reopen_does_not_count_or_close(fake_redis):
    """Defect 3: a success landing after a probe failure re-opened the
    circuit must not count — neither now nor in the next half-open cycle."""
    cb = _new_breaker(fake_redis, probe_success_count=2, cooldown_seconds=1)

    async def go():
        for _ in range(4):
            await cb.record_error("m")
        await asyncio.sleep(1.1)
        st = await cb.check("m")
        assert st.state == CircuitState.HALF_OPEN
        await cb.record_success("m")   # probe 1 of 2
        await cb.record_error("m")     # probe failure -> re-OPEN
        st = await cb.check("m")
        assert st.state == CircuitState.OPEN and st.should_block is True
        await cb.record_success("m")   # stale success while OPEN: ignored
        st = await cb.check("m")
        assert st.state == CircuitState.OPEN and st.should_block is True
        # Next half-open cycle starts from a clean probe count.
        await asyncio.sleep(1.1)
        st = await cb.check("m")
        assert st.state == CircuitState.HALF_OPEN
        await cb.record_success("m")   # 1 of 2 -> must NOT close yet
        mid = await cb.check("m")
        assert mid.state == CircuitState.HALF_OPEN
        await cb.record_success("m")   # 2 of 2 -> closes
        return await cb.check("m")

    status = _run(go())
    assert status.state == CircuitState.CLOSED


def test_probe_success_rechecks_state_atomically(fake_redis, fake_sync_redis):
    """Defect 3 (race window): even if record_success observed HALF_OPEN,
    a re-open landing before the probe transaction must prevent the
    success from counting or closing."""
    cb = _new_breaker(fake_redis, probe_success_count=1, cooldown_seconds=1)
    orig = cb._count_probe_success
    raced = {}

    async def race(model, org_slug):
        # Simulate a concurrent probe-failure re-open landing between the
        # outer HALF_OPEN read in record_success and the probe transaction.
        raced["reopened"] = await cb._reopen_from_half_open(model)
        await orig(model, org_slug)

    async def go():
        for _ in range(4):
            await cb.record_error("m")
        await asyncio.sleep(1.1)
        st = await cb.check("m")
        assert st.state == CircuitState.HALF_OPEN
        cb._count_probe_success = race  # type: ignore[method-assign]
        # With probe_success_count=1 this single success would have closed
        # the circuit pre-fix despite the concurrent re-open.
        await cb.record_success("m")
        return await cb.check("m")

    status = _run(go())
    assert raced.get("reopened") is True
    assert status.state == CircuitState.OPEN
    assert status.should_block is True
    # No probe count survived for the dead cycle.
    epoch = int(fake_sync_redis.get("circuit:epoch:m") or 0)
    assert fake_sync_redis.get(f"circuit:probes:m:{epoch}") is None


def test_concurrent_reopen_wins_once(fake_redis):
    """Concurrent probe failures in HALF_OPEN re-open exactly once
    (single kill-switch trip / log), and the circuit ends OPEN."""
    cb = _new_breaker(fake_redis, probe_success_count=2, cooldown_seconds=1)

    async def go():
        for _ in range(4):
            await cb.record_error("m")
        await asyncio.sleep(1.1)
        st = await cb.check("m")
        assert st.state == CircuitState.HALF_OPEN
        wins = await asyncio.gather(
            *[cb._reopen_from_half_open("m") for _ in range(5)]
        )
        return wins, await cb.check("m")

    wins, status = _run(go())
    assert sum(1 for w in wins if w) == 1
    assert status.state == CircuitState.OPEN
    assert status.should_block is True
