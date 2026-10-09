"""Property 6 — idempotent read retries at most once on timeout (R2-14 / GW06), task 9.1.

# Feature: budget-lease, Property 6
# Validates: Requirements 8.1, 8.2, 8.3, 8.4

R2-14 folds the retry-once-on-timeout boundary into the lease. The code-side requirement (the zone
placement is a deployment dependency, documented in ``admit/lease.py``) is:

* an ``Idempotent_Read`` (a side-effect-free ``GET``, exposed as ``BudgetLease.read_generation``)
  that times out is retried **at most once** — it succeeds on the 2nd attempt (two store calls
  total), and a **second** timeout surfaces after exactly two attempts with no 3rd (Req 8.1, 8.3);
* a **mutating** operation — the atomic WATCH/MULTI ``acquire`` / ``return_unspent`` — is **never**
  retried on timeout: the first timeout surfaces immediately with exactly one attempt (Req 8.2).

A ``random.Random``-seeded stub times out a configurable number of times then succeeds. The stub
counts attempts so the exact attempt count is asserted, not merely the final outcome. House idiom:
seeded ``random.Random``, >= 10,000 iterations, no ``hypothesis``, async via ``asyncio.run``.
"""

from __future__ import annotations

import random
from collections.abc import Awaitable
from typing import Any

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from gateway_v2.admit.lease import BudgetLease, LeaseConfig
from gateway_v2.runtime.store_keys import StoreKeys

_ITERATIONS = 10_000
_ORG = "org-retry"
_KEYS = StoreKeys(namespace="{t}")

# The timeout signatures a bounded cross-zone store read can raise. ``asyncio.TimeoutError`` is the
# builtin ``TimeoutError`` on 3.11+, so it is covered by the builtin entry.
_TIMEOUT_ERRORS: tuple[type[BaseException], ...] = (RedisTimeoutError, TimeoutError)


class _NoMetrics:
    """A metrics double that records nothing — retry tests never fire a refill."""

    def observe_async_refill(self) -> None:  # pragma: no cover - never reached in retry tests
        raise AssertionError("no refill should run in a retry-boundary test")


class _ReadStub:
    """A client stub whose idempotent ``get`` times out ``timeouts`` times, then succeeds.

    ``attempts`` counts every ``get`` call so the test can assert the EXACT number of attempts (the
    original plus retries), proving the retry budget is one and only one.
    """

    def __init__(self, *, timeouts: int, exc: type[BaseException], value: int) -> None:
        self._timeouts = timeouts
        self._exc = exc
        self._value = value
        self.attempts = 0

    async def get(self, _key: str) -> int:
        self.attempts += 1
        if self.attempts <= self._timeouts:
            raise self._exc("simulated cross-zone store read timeout")
        return self._value


class _MutatingTimeoutPipeline:
    """A pipeline whose first store command (``watch``) times out — the mutating path.

    The lease's acquire/return begin the atomic transaction with ``WATCH``; raising the timeout
    there is the natural point a store op would time out. ``watch_attempts`` counts the WATCH calls
    so the test asserts the mutating op was attempted exactly once (never retried on timeout).
    """

    def __init__(self, counter: _MutatingStub, exc: type[BaseException]) -> None:
        self._counter = counter
        self._exc = exc

    async def __aenter__(self) -> _MutatingTimeoutPipeline:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def watch(self, *_keys: str) -> None:
        self._counter.watch_attempts += 1
        raise self._exc("simulated store timeout on a mutating WATCH/MULTI")

    # These are never reached because WATCH times out first; present for shape only.
    def multi(self) -> None:  # pragma: no cover - unreachable in these tests
        raise AssertionError("MULTI reached despite a WATCH timeout")

    async def unwatch(self) -> None:  # pragma: no cover - unreachable in these tests
        raise AssertionError("UNWATCH reached despite a WATCH timeout")


class _MutatingStub:
    """A client stub whose ``pipeline()`` yields a transaction that times out on WATCH."""

    def __init__(self, exc: type[BaseException]) -> None:
        self._exc = exc
        self.watch_attempts = 0

    def pipeline(self, *_args: Any, **_kwargs: Any) -> _MutatingTimeoutPipeline:
        return _MutatingTimeoutPipeline(self, self._exc)


def _config(chunk: int = 16, low: int = 4, ttl_s: float = 30.0) -> LeaseConfig:
    return LeaseConfig(org=_ORG, chunk=chunk, low_watermark=low, ttl_s=ttl_s)


def _lease(client: Any) -> BudgetLease:
    return BudgetLease(
        client,
        _config(),
        worker_id="w0",
        clock=lambda: 0.0,
        spawn=lambda coro: coro.close(),  # type: ignore[attr-defined]
        metrics=_NoMetrics(),
        keys=_KEYS,
    )


def _run(coro: Awaitable[object]) -> object:
    import asyncio

    return asyncio.run(coro)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Property 6 — the idempotent read retries at most once.
# --------------------------------------------------------------------------- #


def test_property6_idempotent_read_succeeds_on_the_single_retry() -> None:
    """One timeout then success → the read succeeds on the 2nd attempt (exactly two calls)."""
    seed = 0x06_06
    rng = random.Random(seed)

    async def go() -> None:
        for i in range(_ITERATIONS):
            exc = rng.choice(_TIMEOUT_ERRORS)
            value = rng.randint(0, 1_000_000)
            stub = _ReadStub(timeouts=1, exc=exc, value=value)
            lease = _lease(stub)
            got = await lease.read_generation()
            assert got == value, f"seed={seed:#x} iter={i} wrong value {got} != {value}"
            assert stub.attempts == 2, (
                f"seed={seed:#x} iter={i} exc={exc.__name__}: an idempotent read that timed out "
                f"once must retry exactly once (2 attempts), saw {stub.attempts}"
            )

    _run(go())


def test_property6_idempotent_read_no_retry_when_first_attempt_succeeds() -> None:
    """A read that never times out is attempted exactly once (the retry is at MOST one)."""
    seed = 0x06_06_01
    rng = random.Random(seed)

    async def go() -> None:
        for i in range(_ITERATIONS):
            value = rng.randint(0, 1_000_000)
            stub = _ReadStub(timeouts=0, exc=rng.choice(_TIMEOUT_ERRORS), value=value)
            lease = _lease(stub)
            got = await lease.read_generation()
            assert got == value, f"seed={seed:#x} iter={i} wrong value {got}"
            assert stub.attempts == 1, (
                f"seed={seed:#x} iter={i}: a successful read must not retry, saw {stub.attempts}"
            )

    _run(go())


def test_property6_second_timeout_surfaces_after_exactly_two_attempts() -> None:
    """Two or more timeouts → the single retry also times out and the timeout surfaces (no 3rd)."""
    seed = 0x06_06_02
    rng = random.Random(seed)

    async def go() -> None:
        for i in range(_ITERATIONS):
            exc = rng.choice(_TIMEOUT_ERRORS)
            # Time out at least twice: the original plus the single retry both fail; a 3rd attempt
            # must never happen even if the stub would keep timing out.
            timeouts = rng.randint(2, 6)
            stub = _ReadStub(timeouts=timeouts, exc=exc, value=7)
            lease = _lease(stub)
            with pytest.raises(_TIMEOUT_ERRORS):
                await lease.read_generation()
            assert stub.attempts == 2, (
                f"seed={seed:#x} iter={i} exc={exc.__name__}: a second timeout must surface after "
                f"exactly two attempts (no 3rd), saw {stub.attempts}"
            )

    _run(go())


def test_property6_non_timeout_error_is_not_retried() -> None:
    """A non-timeout store error on an idempotent read surfaces immediately (one attempt).

    Only the transient cross-zone TCP RTO *timeout* is retried; a connection error is not a timeout
    and must not consume the retry budget (Req 8.1 is scoped to timeouts).
    """
    seed = 0x06_06_03

    async def go() -> None:
        for _ in range(_ITERATIONS):
            stub = _ReadStub(timeouts=1, exc=RedisConnectionError, value=1)
            lease = _lease(stub)
            with pytest.raises(RedisConnectionError):
                await lease.read_generation()
            assert stub.attempts == 1, (
                f"seed={seed:#x}: a non-timeout error must not be retried, saw {stub.attempts}"
            )

    _run(go())


# --------------------------------------------------------------------------- #
# Property 6 — a MUTATING op is NEVER retried on timeout.
# --------------------------------------------------------------------------- #


def test_property6_mutating_acquire_not_retried_on_timeout() -> None:
    """A timeout on the mutating ``acquire`` surfaces immediately — exactly one WATCH attempt."""
    seed = 0x06_06_10
    rng = random.Random(seed)

    async def go() -> None:
        for i in range(_ITERATIONS):
            exc = rng.choice(_TIMEOUT_ERRORS)
            stub = _MutatingStub(exc)
            lease = _lease(stub)
            with pytest.raises(_TIMEOUT_ERRORS):
                await lease.acquire(rng.randint(0, 5))
            assert stub.watch_attempts == 1, (
                f"seed={seed:#x} iter={i} exc={exc.__name__}: a mutating acquire must NOT retry on "
                f"timeout (one attempt), saw {stub.watch_attempts}"
            )

    _run(go())


def test_property6_mutating_return_not_retried_on_timeout() -> None:
    """A timeout on the mutating ``return_unspent`` surfaces immediately — one WATCH attempt."""
    seed = 0x06_06_11
    rng = random.Random(seed)

    async def go() -> None:
        for i in range(_ITERATIONS):
            exc = rng.choice(_TIMEOUT_ERRORS)
            stub = _MutatingStub(exc)
            lease = _lease(stub)
            with pytest.raises(_TIMEOUT_ERRORS):
                await lease.return_unspent()
            assert stub.watch_attempts == 1, (
                f"seed={seed:#x} iter={i} exc={exc.__name__}: a mutating return must NOT retry on "
                f"timeout (one attempt), saw {stub.watch_attempts}"
            )

    _run(go())
