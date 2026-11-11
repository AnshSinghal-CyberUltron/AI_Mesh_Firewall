"""Property 5 — refill is never on the request path (R2-09 / GW06), task 4.7.

# Feature: budget-lease, Property 5
# Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5

No request-path admission ever issues a store call to refill the lease: ``try_spend`` is synchronous
and local, and the Async_Refill is handed to the injected ``spawn`` (off the request path) under a
single-flight guard. The count of store calls made DURING ``try_spend`` is always zero — including
while a refill is already in flight.

A counting wrapper around the ``fakeredis`` async client records every attribute access that would
issue a command. A ``try_spend``-scoped gate flips on around each call so any store command the hot
path tried to make is attributed to it; the assertion is that the count across >= 10,000
``try_spend`` calls is exactly zero.

House idiom: seeded ``random.Random``, >= 10,000 iterations, no ``hypothesis``, async via
``asyncio.run``. The injected ``spawn`` collects the refill coroutines so the test can drive them
deterministically off the request path and prove single-flight.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable
from typing import Any

import fakeredis

from gateway_v2.admit.lease import BudgetLease, LeaseConfig
from gateway_v2.runtime.store_keys import StoreKeys

_ITERATIONS = 10_000
_ORG = "org-offpath"
_KEYS = StoreKeys(namespace="{t}")

_STORE_COMMANDS = frozenset(
    {"get", "set", "decrby", "incrby", "delete", "pexpire", "expire", "watch",
     "unwatch", "multi", "execute", "pipeline"},
)


class _CountingClient:
    """Wraps a ``fakeredis`` async client, counting store commands made WHILE the gate is open.

    ``on_request_path`` is flipped on around each ``try_spend`` call. Any store command issued while
    it is set increments ``request_path_calls`` — which must stay zero. Pipeline objects returned
    while the gate is open are wrapped too, so a command queued through a pipeline on the hot path
    would also be caught.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.on_request_path = False
        self.request_path_calls = 0

    def _count(self, name: str) -> None:
        if self.on_request_path and name in _STORE_COMMANDS:
            self.request_path_calls += 1

    def pipeline(self, *args: Any, **kwargs: Any) -> Any:
        self._count("pipeline")
        return _CountingPipeline(self._inner.pipeline(*args, **kwargs), self)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if callable(attr) and name in _STORE_COMMANDS:
            def wrapped(*args: Any, **kwargs: Any) -> Any:
                self._count(name)
                return attr(*args, **kwargs)
            return wrapped
        return attr


class _CountingPipeline:
    """Wraps a pipeline/transaction, attributing queued + executed commands to the gate."""

    def __init__(self, inner: Any, counter: _CountingClient) -> None:
        self._inner = inner
        self._counter = counter

    async def __aenter__(self) -> _CountingPipeline:
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> Any:
        return await self._inner.__aexit__(*exc)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if callable(attr) and name in _STORE_COMMANDS:
            def wrapped(*args: Any, **kwargs: Any) -> Any:
                self._counter._count(name)
                return attr(*args, **kwargs)
            return wrapped
        return attr


class _CountingMetrics:
    def __init__(self) -> None:
        self.async_refills = 0

    def observe_async_refill(self) -> None:
        self.async_refills += 1


def _run(coro: Awaitable[None]) -> None:
    asyncio.run(coro)  # type: ignore[arg-type]


def test_property5_no_store_call_on_the_request_path() -> None:
    """Across >= 10,000 ``try_spend`` calls, the request-path store-call count is exactly zero."""
    seed = 0x06_05
    rng = random.Random(seed)

    async def go() -> None:
        server = fakeredis.FakeServer()
        inner = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
        client = _CountingClient(inner)
        pending: list[Awaitable[None]] = []
        metrics = _CountingMetrics()
        chunk = 32
        config = LeaseConfig(org=_ORG, chunk=chunk, low_watermark=8, ttl_s=30.0)
        await inner.set(_KEYS.budget_remaining(_ORG), 10_000_000)
        await inner.set(_KEYS.budget_generation(_ORG), 0)
        lease = BudgetLease(
            client,
            config,
            worker_id="w0",
            clock=lambda: 0.0,
            spawn=pending.append,
            metrics=metrics,
            keys=_KEYS,
        )
        await lease.acquire(0)  # off the request path (gate closed)

        refills_driven = 0
        for i in range(_ITERATIONS):
            # Open the gate strictly around the synchronous try_spend.
            client.on_request_path = True
            lease.try_spend(1, 0)
            client.on_request_path = False
            assert client.request_path_calls == 0, (
                f"seed={seed:#x} iter={i} a store call was issued on the request path: "
                f"{client.request_path_calls}"
            )
            # Drive the off-path refill(s) OUTSIDE the gate, deterministically. Sometimes defer so a
            # refill is in flight during subsequent try_spends — the count must still be zero.
            if pending and rng.random() < 0.5:
                batch, pending[:] = list(pending), []
                for coro in batch:
                    await coro
                    refills_driven += 1

        # Flush any remaining refills.
        while pending:
            coro = pending.pop()
            await coro
            refills_driven += 1

        assert client.request_path_calls == 0
        assert refills_driven >= 1, (
            f"seed={seed:#x} no refill ever ran — the watermark path was not exercised"
        )
        assert metrics.async_refills == refills_driven, (
            f"seed={seed:#x} async_refill metric {metrics.async_refills} != driven {refills_driven}"
        )
        await inner.aclose()

    _run(go())


def test_property5_single_flight_during_in_progress_refill() -> None:
    """While a refill is in flight, watermark-crossing spends schedule no new refill (single-fl.).

    The injected ``spawn`` collects coroutines but does not run them, so the first watermark cross
    leaves a refill "in flight". Subsequent spends that also cross the watermark must NOT enqueue a
    second coroutine while the guard is set.
    """
    seed = 0x06_05_02
    rng = random.Random(seed)

    async def go() -> None:
        for _ in range(2_000):
            server = fakeredis.FakeServer()
            inner = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
            pending: list[Awaitable[None]] = []
            metrics = _CountingMetrics()
            chunk = rng.randint(8, 24)
            low = rng.randint(2, chunk - 2)
            config = LeaseConfig(org=_ORG, chunk=chunk, low_watermark=low, ttl_s=30.0)
            await inner.set(_KEYS.budget_remaining(_ORG), 10_000)
            await inner.set(_KEYS.budget_generation(_ORG), 0)
            lease = BudgetLease(
                inner,
                config,
                worker_id="w0",
                clock=lambda: 0.0,
                spawn=pending.append,
                metrics=metrics,
                keys=_KEYS,
            )
            await lease.acquire(0)
            # Spend down to/below the watermark to trigger the first refill schedule.
            while lease.remaining > low:
                lease.try_spend(1, 0)
            assert lease.refill_in_flight is True
            assert len(pending) == 1, "first watermark cross must schedule exactly one refill"
            # Further spends while the guard is set schedule nothing new.
            for _ in range(5):
                if lease.remaining > 0:
                    lease.try_spend(1, 0)
            assert len(pending) == 1, "a second refill was scheduled while one was in flight"
            # Draining the refill clears the guard; a later watermark cross can schedule again.
            coro = pending.pop()
            await coro
            assert lease.refill_in_flight is False
            for leftover in pending:  # close any undrained coroutines (test hygiene)
                leftover.close()  # type: ignore[attr-defined]
            pending.clear()
            await inner.aclose()

    _run(go())
