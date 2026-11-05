"""Unit tests for ``StreamTimeouts`` upstream-liveness mode (strict-withhold-idle-timeout, 3.2).

# Feature: strict-withhold-idle-timeout, Property 1 / Property 4 (unit level)
# Validates: Requirements 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.5, 3.8

These are the task-3.2 UNIT tests of the ``upstream_liveness`` flag added to
:class:`~gateway_v2.edge.stream_timeouts.StreamTimeouts` (design.md Decision 2; the "Unit Tests"
list of the Testing Strategy). They drive a REAL ``StreamTimeouts`` built from a real
``ResourceContract`` directly (no pipeline), exactly as the shipped LGW12 property test
(``tests/runtime/test_lgw12_timeouts.py``) does: an injected ``_StubClock`` makes the clock-based
idle bound exact with zero wall time, and a tiny ``target_p99_ms`` makes the two REAL-time bounds
(inter-chunk, write, via ``asyncio.wait_for``) land in the tens of microseconds so a
never-resolving awaitable trips them in a couple of real sub-millisecond waits.

What this suite pins (design "Unit Tests"):

* ``upstream_liveness=True``: ``next_event`` refreshes the idle clock on every event; a ``send``
  after a 6 s withheld stream (idle bound 3 s) succeeds instead of being cut (2.1, 2.2); a
  ``next_event`` TIMEOUT does NOT refresh the clock (fail closed — the cut path never re-seeds).
* Default and explicit ``False``: ``next_event`` never assigns ``_last_write_s`` except in the
  GW28 D-18 first-event branch — the old path is byte-for-byte unchanged (3.1, 3.2, 3.5, 3.8).
* After liveness mode, a second write whose inter-write gap exceeds ``idle_timeout_s`` is still
  cut with ``stream_idle_timeout`` — back-pressure once writing begins is undiminished (2.4).
* A 1.5 s upstream silence still gives ``stream_inter_chunk_timeout`` in liveness mode — the
  upstream-stall guard is unchanged and is stricter than idle (2.3, 3.2).

House idiom: a module-constant seed, ``random.Random``, ``asyncio.run`` per async case, no
``hypothesis``, each property tagged in source.
"""

from __future__ import annotations

import asyncio
import random

from gateway_v2.domain import posture
from gateway_v2.edge.cancel import KillLatch
from gateway_v2.edge.stream_timeouts import StreamTimeout, StreamTimeouts
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

_SEED = 0xF15751D1E2
_ITERATIONS = 200

#: A TINY per-worker RSS so a small memory limit still clears ``from_signals`` worker floors.
_PER_WORKER_RSS = 4 * 1024 * 1024


def _contract(*, target_p99_ms: float = 20.0) -> ResourceContract:
    """A serviceable contract. Default 20 ms SLO: idle 3 s, inter-chunk 1 s, write 0.5 s."""
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
    """A manually-advanced seconds clock for the injected ``clock`` seam (zero wall time)."""

    __slots__ = ("now",)

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


async def _ok() -> str:
    """An awaitable that resolves immediately — a successful upstream next-event."""
    return "event"


async def _never() -> str:
    """An awaitable that never resolves — ``wait_for`` must cancel it on timeout."""
    await asyncio.Event().wait()
    return "unreachable"


# --------------------------------------------------------------------------- #
# Liveness True: next_event refreshes the idle clock; a 6 s withheld stream succeeds.
# --------------------------------------------------------------------------- #


def test_liveness_next_event_refreshes_the_idle_clock_each_event() -> None:
    """``upstream_liveness=True``: each returned event re-seeds ``_last_write_s`` (2.1, 2.2).

    # Feature: strict-withhold-idle-timeout, Property 1
    # Validates: Requirements 2.1, 2.2

    40 chunks 0.15 s apart (6 s total, well past the 3 s idle bound) with the clock advanced
    before each await. After every successful ``next_event`` the idle clock equals the current
    clock, so the gap since the last activity never grows beyond one inter-event step.
    """
    contract = _contract()
    clock = _StubClock()
    latch = KillLatch()
    timeouts = StreamTimeouts(
        contract=contract, kill_latch=latch, clock=clock, upstream_liveness=True,
    )

    async def _run() -> None:
        for _ in range(40):
            clock.now += 0.15
            await timeouts.next_event(_ok())
            assert timeouts._last_write_s == clock.now, (  # noqa: SLF001
                f"idle clock not refreshed: {timeouts._last_write_s!r} != {clock.now!r}"
            )

    asyncio.run(_run())
    assert not latch.is_killed(), "liveness stream must not be cut while upstream is healthy"


def test_liveness_send_after_a_6s_withheld_stream_succeeds() -> None:
    """A ``send`` after a 6 s withheld liveness stream is NOT cut as idle (2.1, 2.2).

    # Feature: strict-withhold-idle-timeout, Property 1
    # Validates: Requirements 2.1, 2.2

    The healthy-stream regression the bug report describes: 40 chunks 0.15 s apart (6 s > 3 s
    idle) then the first and only downstream write. With liveness the idle clock was refreshed by
    the final event, so the write is accepted; on the unfixed write-based model it would be a
    false ``stream_idle_timeout``.
    """
    contract = _contract()
    clock = _StubClock()
    latch = KillLatch()
    timeouts = StreamTimeouts(
        contract=contract, kill_latch=latch, clock=clock, upstream_liveness=True,
    )
    assert contract.idle_timeout_s() == 3.0, "default-SLO idle bound must be 3 s"
    wrote = False

    async def _do_send() -> None:
        nonlocal wrote
        wrote = True

    async def _run() -> None:
        for _ in range(40):
            clock.now += 0.15  # 6 s total upstream time, well past the 3 s idle bound
            await timeouts.next_event(_ok())
        # The decision write immediately after the final event: the idle gap is ~0 s now.
        await timeouts.send(_do_send)

    asyncio.run(_run())
    assert wrote, "the decision write must be issued"
    assert not latch.is_killed(), f"withheld stream falsely cut: {latch.reason()!r}"


def test_liveness_next_event_timeout_does_not_refresh_the_idle_clock() -> None:
    """A ``next_event`` TIMEOUT in liveness mode never re-seeds the idle clock (fail closed).

    # Feature: strict-withhold-idle-timeout, Property 2
    # Validates: Requirements 2.3

    The re-seed is on the SUCCESSFUL return path only; a timeout raises before reaching it. A tiny
    SLO makes the inter-chunk bound microseconds; a never-resolving upstream read trips it. The
    idle clock must still hold its pre-await value, and the latch carries the inter-chunk code.
    """
    contract = _contract(target_p99_ms=0.001)
    clock = _StubClock(start=5.0)
    latch = KillLatch()
    timeouts = StreamTimeouts(
        contract=contract, kill_latch=latch, clock=clock, upstream_liveness=True,
    )
    before = timeouts._last_write_s  # noqa: SLF001
    raised: StreamTimeout | None = None

    async def _run() -> None:
        nonlocal raised
        try:
            await timeouts.next_event(_never())
        except StreamTimeout as exc:
            raised = exc

    asyncio.run(_run())
    assert raised is not None, "an inter-chunk stall must raise"
    assert raised.code == posture.STREAM_INTER_CHUNK_TIMEOUT, raised.code
    assert timeouts._last_write_s == before, (  # noqa: SLF001
        "a timeout cut must not re-seed the idle clock (fail closed)"
    )
    assert latch.reason() == posture.STREAM_INTER_CHUNK_TIMEOUT, latch.reason()


# --------------------------------------------------------------------------- #
# Liveness off (default / explicit False): next_event never re-seeds outside D-18.
# --------------------------------------------------------------------------- #


def test_default_and_false_never_reseed_from_next_event_outside_d18() -> None:
    """Default and ``False``: ``next_event`` leaves ``_last_write_s`` untouched (3.1, 3.2, 3.8).

    # Feature: strict-withhold-idle-timeout, Property 4: Preservation
    # Validates: Requirements 3.1, 3.2, 3.8

    With no ``first_event_s`` (so no D-18 branch), a non-liveness ``StreamTimeouts`` never assigns
    the idle clock from ``next_event`` — exactly the old path. Checked for the default (flag
    omitted) and for an explicit ``False`` across random clock starts and event counts.
    """
    seed = _SEED
    print(f"test_default_and_false seed={seed:#x} iterations={_ITERATIONS}")  # noqa: T201
    rng = random.Random(seed)

    def _default(c: ResourceContract, k: KillLatch, cl: _StubClock) -> StreamTimeouts:
        return StreamTimeouts(contract=c, kill_latch=k, clock=cl)

    def _explicit_false(c: ResourceContract, k: KillLatch, cl: _StubClock) -> StreamTimeouts:
        return StreamTimeouts(contract=c, kill_latch=k, clock=cl, upstream_liveness=False)

    for i in range(_ITERATIONS):
        start = rng.uniform(0.0, 1_000.0)
        n_events = rng.randint(1, 10)
        for build in (_default, _explicit_false):
            contract = _contract()
            clock = _StubClock(start=start)
            latch = KillLatch()
            timeouts = build(contract, latch, clock)
            seeded = timeouts._last_write_s  # noqa: SLF001

            async def _run(t: StreamTimeouts = timeouts, k: int = n_events) -> None:
                for _ in range(k):
                    clock.now += 0.15
                    await t.next_event(_ok())

            asyncio.run(_run())
            assert timeouts._last_write_s == seeded, (  # noqa: SLF001
                f"i={i} seed={seed:#x} start={start!r}: next_event re-seeded the idle clock "
                f"with liveness off ({timeouts._last_write_s!r} != {seeded!r})"
            )


def test_d18_first_event_branch_still_reseeds_with_liveness_off() -> None:
    """With ``first_event_s`` set and liveness off, the D-18 branch still re-seeds once (3.5).

    # Feature: strict-withhold-idle-timeout, Property 4: Preservation
    # Validates: Requirements 3.5

    The GW28 D-18 re-seed is independent of the new flag: a routed (``first_event_s`` set),
    non-liveness stream re-seeds the idle clock on the FIRST event only, then never again.
    """
    contract = _contract()
    clock = _StubClock()
    latch = KillLatch()
    timeouts = StreamTimeouts(
        contract=contract, kill_latch=latch, clock=clock, first_event_s=30.0,
    )

    async def _run() -> None:
        clock.now += 4.0
        await timeouts.next_event(_ok())  # first event: D-18 re-seeds
        assert timeouts._last_write_s == 4.0, timeouts._last_write_s  # noqa: SLF001
        clock.now += 0.5
        await timeouts.next_event(_ok())  # subsequent event: no re-seed (liveness off)
        assert timeouts._last_write_s == 4.0, timeouts._last_write_s  # noqa: SLF001

    asyncio.run(_run())


# --------------------------------------------------------------------------- #
# 2.4 — back-pressure once writing begins is undiminished in liveness mode.
# --------------------------------------------------------------------------- #


def test_liveness_second_write_gap_over_idle_is_still_cut() -> None:
    """After liveness mode, an inter-write gap over ``idle_timeout_s`` is still an idle cut (2.4).

    # Feature: strict-withhold-idle-timeout, Property 3
    # Validates: Requirements 2.4

    ``send`` is unmodified: once the decision write has completed, the idle clock is write-based
    again. A later write whose gap since the last completed write exceeds the idle bound is cut
    with ``stream_idle_timeout`` — client back-pressure is undiminished.
    """
    contract = _contract()
    idle = contract.idle_timeout_s()
    clock = _StubClock()
    latch = KillLatch()
    timeouts = StreamTimeouts(
        contract=contract, kill_latch=latch, clock=clock, upstream_liveness=True,
    )
    writes = 0
    raised: StreamTimeout | None = None

    async def _do_send() -> None:
        nonlocal writes
        writes += 1

    async def _run() -> None:
        nonlocal raised
        clock.now += 0.15
        await timeouts.next_event(_ok())
        await timeouts.send(_do_send)  # first write completes; clock becomes write-based
        clock.now += idle + 0.5  # client stops accepting bytes, gap past the idle bound
        try:
            await timeouts.send(_do_send)
        except StreamTimeout as exc:
            raised = exc

    asyncio.run(_run())
    assert writes == 1, "only the first write should complete"
    assert raised is not None, "the stalled second write must be cut"
    assert raised.code == posture.STREAM_IDLE_TIMEOUT, raised.code
    assert latch.reason() == posture.STREAM_IDLE_TIMEOUT, latch.reason()


# --------------------------------------------------------------------------- #
# 2.3 / 3.2 — a 1.5 s upstream silence still gives stream_inter_chunk_timeout.
# --------------------------------------------------------------------------- #


def test_liveness_upstream_silence_still_gives_inter_chunk_timeout() -> None:
    """In liveness mode, an upstream stall is still cut with ``stream_inter_chunk_timeout`` (2.3).

    # Feature: strict-withhold-idle-timeout, Property 2
    # Validates: Requirements 2.3, 3.2

    The 1.5 s silence of the bugfix example: inter-chunk (1 s at defaults) is stricter than idle
    (3 s), so a silent upstream is caught by ``next_event`` first. Driven with a tiny SLO so the
    real ``wait_for`` bound is microseconds and a never-resolving read trips it; the liveness flag
    does not suppress or change the stall cut.
    """
    contract = _contract(target_p99_ms=0.001)
    # Preserve the ordering the design relies on: inter-chunk strictly below idle.
    assert contract.inter_chunk_timeout_s() < contract.idle_timeout_s()
    clock = _StubClock()
    latch = KillLatch()
    timeouts = StreamTimeouts(
        contract=contract, kill_latch=latch, clock=clock, upstream_liveness=True,
    )
    raised: StreamTimeout | None = None

    async def _run() -> None:
        nonlocal raised
        # One healthy event (re-seeds in liveness mode), then the upstream goes silent.
        clock.now += 0.15
        await timeouts.next_event(_ok())
        try:
            await timeouts.next_event(_never())
        except StreamTimeout as exc:
            raised = exc

    asyncio.run(_run())
    assert raised is not None, "an upstream stall must still cut in liveness mode"
    assert raised.code == posture.STREAM_INTER_CHUNK_TIMEOUT, raised.code
    assert latch.reason() == posture.STREAM_INTER_CHUNK_TIMEOUT, latch.reason()
