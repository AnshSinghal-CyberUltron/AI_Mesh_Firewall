"""LGW12 timeout-termination property test (GW12, task 11.2).

# Feature: sse-egress-pipeline, Property 10: Timeout termination
# Validates: Requirements 9.2, 9.3, 9.4

Property 10: *for all* stalls exceeding a derived timeout — inter-chunk (R9.2),
idle (R9.3), or write (R9.4) — the stream terminates with the declared
``Error_Frame`` code and releases its resources. The target is
:class:`~gateway_v2.edge.stream_timeouts.StreamTimeouts`, which wraps the serving
loop's awaits with the three C27 bounds derived from the injected
``ResourceContract`` and flips the shared
:class:`~gateway_v2.edge.cancel.KillLatch` on any breach.

The property is checked for all three bounds together on each iteration:

* **inter-chunk (R9.2).** ``next_event`` wrapping an awaitable that never
  resolves inside ``inter_chunk_timeout_s()`` raises
  ``StreamTimeout(STREAM_INTER_CHUNK_TIMEOUT)`` and flips the latch with that
  code. The stalled awaitable is cancelled (``wait_for`` cancels it) — resource
  release.
* **write (R9.4).** ``send`` wrapping a ``do_send`` that blocks past
  ``write_timeout_s()`` raises ``StreamTimeout(STREAM_WRITE_TIMEOUT)`` and flips
  the latch; the stuck write coroutine is cancelled.
* **idle (R9.3).** With the inter-write gap driven past ``idle_timeout_s()`` on
  the injected clock, the next ``send`` raises
  ``StreamTimeout(STREAM_IDLE_TIMEOUT)`` *before* issuing the write — the write
  is never attempted (``do_send`` is never awaited).

**Determinism + speed.** inter-chunk and write genuinely use
``asyncio.wait_for`` with a REAL (wall-clock) timeout derived from the contract,
so the contract is built with a TINY ``target_p99_ms`` (sub-millisecond): the
derived inter-chunk/write bounds land in the tens-of-microseconds range, the
stalled awaitable is an ``asyncio.Event().wait()`` that never completes, and
``wait_for`` cancels it after the tiny real bound. The idle case is purely
clock-based (``StreamTimeouts.send`` reads the INJECTED clock, not real time), so
it is driven to the exact microsecond by advancing a stub clock past
``idle_timeout_s()`` — zero wall time, exact determinism. Per iteration costs a
couple of real sub-millisecond waits, so >= 10,000 iterations run in seconds.

The sweep is a seeded ``random.Random`` driven for >= 10,000 iterations (house
idiom; no ``hypothesis``). The seed is a module constant, logged, and
interpolated into every failure message so a counterexample is reproducible.
Each per-iteration async case is run via ``asyncio.run``.

Property 7 (no spurious fail-open) rides along: on EVERY breach the latch is left
killed carrying exactly the breach's code (fail closed), never left unset.

_Design: Correctness Properties → Property 10; Components §1._
"""

from __future__ import annotations

import asyncio
import random

from gateway_v2.domain import posture
from gateway_v2.edge.cancel import KillLatch
from gateway_v2.edge.stream_timeouts import StreamTimeout, StreamTimeouts
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

_ITERATIONS = 10_000
_SEED = 0x120B02

#: A TINY per-worker RSS so a tiny memory limit still clears ``from_signals``'
#: refuse-to-start worker checks while keeping the fixture cheap.
_PER_WORKER_RSS = 4 * 1024 * 1024


def _contract(*, target_p99_ms: float) -> ResourceContract:
    """A serviceable contract with a caller-chosen (tiny) SLO.

    The signals clear ``from_signals``' CPU and RAM worker-floor checks for any
    positive ``target_p99_ms``, so the derived stream bounds are a pure multiple
    of the chosen SLO (``inter_chunk = p99_s*50``, ``write = p99_s*25``,
    ``idle = p99_s*150``). A sub-millisecond SLO makes the real
    ``asyncio.wait_for`` bounds tens of microseconds — fast and deterministic.
    """
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=_PER_WORKER_RSS * 8,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals,
        target_p99_ms=target_p99_ms,
        utilization_cap=0.75,
        per_worker_rss=_PER_WORKER_RSS,
    )


class _StubClock:
    """A manually-advanced seconds clock for the injected ``clock`` seam.

    ``StreamTimeouts`` reads this for the idle-gap accounting (R9.3), so advancing
    it drives the idle bound to the exact microsecond with zero wall time. The
    inter-chunk/write bounds use ``asyncio.wait_for`` (real time), not this clock.
    """

    __slots__ = ("now",)

    def __init__(self, start: float) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


async def _never() -> None:
    """An awaitable that never resolves — ``wait_for`` must cancel it on timeout."""
    await asyncio.Event().wait()


async def _check_inter_chunk(contract: ResourceContract, clock: _StubClock) -> None:
    """``next_event`` on a never-resolving upstream read breaches R9.2 and fails closed."""
    latch = KillLatch()
    timeouts = StreamTimeouts(contract=contract, kill_latch=latch, clock=clock)
    raised: StreamTimeout | None = None
    try:
        await timeouts.next_event(_never())
    except StreamTimeout as exc:
        raised = exc
    assert raised is not None, "inter-chunk stall did not raise StreamTimeout"
    assert raised.code == posture.STREAM_INTER_CHUNK_TIMEOUT, raised.code
    assert latch.is_killed(), "latch not flipped on inter-chunk breach"
    assert latch.reason() == posture.STREAM_INTER_CHUNK_TIMEOUT, latch.reason()


async def _check_write(contract: ResourceContract, clock: _StubClock) -> None:
    """``send`` on a never-completing write breaches R9.4 and fails closed."""
    latch = KillLatch()
    timeouts = StreamTimeouts(contract=contract, kill_latch=latch, clock=clock)
    raised: StreamTimeout | None = None
    try:
        await timeouts.send(_never)
    except StreamTimeout as exc:
        raised = exc
    assert raised is not None, "write stall did not raise StreamTimeout"
    assert raised.code == posture.STREAM_WRITE_TIMEOUT, raised.code
    assert latch.is_killed(), "latch not flipped on write breach"
    assert latch.reason() == posture.STREAM_WRITE_TIMEOUT, latch.reason()


async def _check_idle(
    contract: ResourceContract,
    clock: _StubClock,
    overshoot_s: float,
) -> None:
    """A clock gap past ``idle_timeout_s()`` cuts the NEXT ``send`` before the write (R9.3)."""
    latch = KillLatch()
    timeouts = StreamTimeouts(contract=contract, kill_latch=latch, clock=clock)
    # Advance the injected clock so the gap since the stream-start seed (read in
    # __init__) exceeds the derived idle bound. The write must be refused before
    # it is attempted, so a would-be-blocking do_send is never awaited.
    clock.now += contract.idle_timeout_s() + overshoot_s
    attempted = False

    async def _do_send() -> None:
        nonlocal attempted
        attempted = True

    raised: StreamTimeout | None = None
    try:
        await timeouts.send(_do_send)
    except StreamTimeout as exc:
        raised = exc
    assert raised is not None, "idle gap did not raise StreamTimeout"
    assert raised.code == posture.STREAM_IDLE_TIMEOUT, raised.code
    assert not attempted, "idle cut must precede the write (do_send must not run)"
    assert latch.is_killed(), "latch not flipped on idle breach"
    assert latch.reason() == posture.STREAM_IDLE_TIMEOUT, latch.reason()


async def _one_iteration(contract: ResourceContract, clock: _StubClock, overshoot_s: float) -> None:
    """Exercise all three bounds for one generated contract/clock."""
    await _check_inter_chunk(contract, clock)
    await _check_write(contract, clock)
    await _check_idle(contract, clock, overshoot_s)


def test_property10_stall_past_a_derived_timeout_terminates_and_releases() -> None:
    """Any stall past a derived C27 bound raises the declared code + flips the latch (R9.2-9.4).

    # Feature: sse-egress-pipeline, Property 10: Timeout termination
    # Validates: Requirements 9.2, 9.3, 9.4
    """
    seed = _SEED
    print(f"test_property10 seed={seed:#x} iterations={_ITERATIONS}")  # noqa: T201 — log the seed
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        # A tiny, serviceable SLO so the REAL wait_for bounds (inter-chunk/write)
        # are sub-millisecond: p99 in [0.2us, 20us] -> write bound p99*25 in
        # [5us, 500us], inter-chunk p99*50 in [10us, 1ms]. Fast and deterministic.
        target_p99_ms = rng.uniform(0.0002, 0.02)
        contract = _contract(target_p99_ms=target_p99_ms)
        clock = _StubClock(start=rng.uniform(0.0, 1_000.0))
        # Strictly-positive overshoot so the idle gap is unambiguously PAST the bound.
        overshoot_s = rng.uniform(1e-9, contract.idle_timeout_s())
        try:
            asyncio.run(_one_iteration(contract, clock, overshoot_s))
        except AssertionError as exc:  # pragma: no cover - reproducibility aid
            raise AssertionError(
                f"i={i} seed={seed:#x} p99_ms={target_p99_ms!r} "
                f"start={clock.now!r} overshoot_s={overshoot_s!r}: {exc}"
            ) from exc
