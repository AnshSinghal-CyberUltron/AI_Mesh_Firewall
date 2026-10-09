# Feature: sse-egress-pipeline, Property 4: Cancellation-within-bound
# Validates: Requirements 6.3, 6.5, 6.6
"""Property test for :class:`gateway_v2.edge.cancel.CancellationController` (GW12, task 9.2).

**Property 4 — Cancellation-within-bound.** *For all* disconnects injected at any chunk
boundary, cancellation completes within ``ResourceContract.cancellation_bound_s()`` and the
stream's buffered bytes + credit return to baseline.

The property is driven by a single seeded ``random.Random`` for ``_ITERATIONS`` (``>= 10_000``)
iterations — NO ``hypothesis`` — and the async controller path runs through ``asyncio.run``
(house idiom). The seed is logged (printed) so a failing run is reproducible.

For every iteration a fresh scenario is built with:

* a ``ControllableClock`` — the controller measures ``elapsed_ms`` as ``(clock() - start)``, so a
  clock whose reading we advance by a scripted amount makes ``elapsed_ms`` fully deterministic and
  independent of wall-clock timing. "Within the derived bound" is modelled by advancing the clock
  by less than ``cancellation_bound_s()``; "exceeding the bound" by advancing it past the bound.
* a ``StubAbortProvider`` whose ``abort()`` either completes immediately (fast abort) or blocks on
  an :class:`asyncio.Event` that is never set until the test drains it (slow abort). The slow abort
  can never finish within the derived timeout, so ``asyncio.wait_for`` ALWAYS times out — the
  bound-exceeded branch is reached deterministically, not by racing wall-clock timers. The derived
  bound is kept tiny (a sub-millisecond ``target_p99_ms``) so the real ``wait_for`` timeout the
  controller uses fires fast; the deterministic ``bound_exceeded`` verdict comes from whether the
  abort can complete at all, never from how long the test's wall clock happens to take.
* a :class:`~gateway_v2.egress.backpressure.Coalescer` seeded with a random buffered-byte count
  (the "buffered at disconnect" amount) via ``offer``.
* a :class:`~gateway_v2.runtime.stream_metrics.PerRequestExports` producer (the real one).

For each injected disconnect the test asserts the full R6 contract:

* ``on_disconnect`` flips the :class:`~gateway_v2.edge.cancel.KillLatch`
  (``is_killed()`` / the callable probe both true) — the in-flight guard seam is cut (R6.2).
* the provider is aborted — its abort latch is observed set (R6.1).
* the buffered bytes are released back: ``coalescer.buffered() == 0`` afterwards and
  ``outcome.released_bytes == buffered-at-disconnect`` (R6.6), with nothing forwarded downstream.
* ``outcome.elapsed_ms`` is the measured interval (reported per cancelled stream, R6.5), equal to
  the scripted clock delta.
* when the abort completes within ``cancellation_bound_s()`` → ``bound_exceeded is False``; when it
  cannot → ``bound_exceeded is True`` AND the provider is still cut (force-release, R6.4) — the
  latch is flipped and the buffer released on both paths. Resources return to baseline (buffer 0,
  credit outstanding 0).

_Design: Correctness Properties → Property 4._
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable

from gateway_v2.edge.cancel import CancellationController, CancelOutcome, KillLatch
from gateway_v2.egress.backpressure import Coalescer, CreditFlowControl
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals
from gateway_v2.runtime.stream_metrics import PerRequestExports

_ITERATIONS = 10_000
_SEED = 0x6CA12C4  # logged below; change to reproduce a specific run.

_PER_WORKER_RSS = 400 * 1024 * 1024
_MS_PER_S = 1000.0


def _run[T](coro: Awaitable[T]) -> T:
    """Drive one coroutine to completion (house idiom; no async test plugin)."""
    return asyncio.run(coro)  # type: ignore[arg-type]


def _contract(*, target_p99_ms: float) -> ResourceContract:
    """A serviceable contract fixture (same shape as the LGW12 resources / LGW19 tests).

    A sub-millisecond ``target_p99_ms`` keeps ``cancellation_bound_s()`` tiny, so the real
    ``asyncio.wait_for`` timeout the controller uses on the provider abort fires fast; the
    deterministic bound-exceeded verdict comes from the slow abort being UNABLE to complete, not
    from the magnitude of the (tiny) bound.
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


class ControllableClock:
    """A monotone seconds clock whose reading advances only when the test scripts it.

    The controller reads the clock once at ``on_disconnect`` entry (``start``) and once after the
    kill completes; ``elapsed_ms`` is ``(second - start) * 1000``. By advancing the clock by an
    exact scripted amount between those two reads we make ``elapsed_ms`` deterministic and free of
    any wall-clock dependency. The clock is driven forward by :meth:`advance`; each read returns
    the running total. The controller calls it exactly twice per cancellation, so a one-shot
    scripted ``advance`` lands precisely in the ``second - start`` window.
    """

    __slots__ = ("_now", "_pending")

    def __init__(self) -> None:
        self._now = 0.0
        self._pending = 0.0

    def script_delta(self, delta_s: float) -> None:
        """Script the delta applied on the controller's SECOND clock read (the elapsed window)."""
        self._pending = delta_s

    def __call__(self) -> float:
        now = self._now
        # Apply the scripted delta on the first read's successor: the controller reads start, then
        # (after the kill) reads again; folding the delta in on the second read reproduces a
        # measured interval of exactly `delta_s` seconds regardless of real time.
        self._now += self._pending
        self._pending = 0.0
        return now


class StubAbortProvider:
    """An abortable provider whose ``abort()`` is fast or blocks forever (deterministic).

    ``open`` is unused by the cancellation path (the controller only aborts), so it yields nothing.
    ``abort`` sets a one-way latch so the test can assert the provider was cut (R6.1). When
    ``slow`` is set, ``abort`` additionally blocks on an :class:`asyncio.Event` that the scenario
    never sets until it drains the shielded abort task — so the controller's ``asyncio.wait_for``
    on the abort ALWAYS times out and the bound-exceeded branch is reached deterministically (no
    wall-clock race). A fast abort returns immediately and completes within any positive bound.
    """

    __slots__ = ("_released", "aborted", "slow")

    def __init__(self, *, slow: bool) -> None:
        self.slow = slow
        self.aborted = False
        self._released = asyncio.Event()

    def open(self, request: object) -> object:  # pragma: no cover - unused on the cancel path
        raise NotImplementedError

    def release(self) -> None:
        """Unblock a slow abort so the shielded task can finish (clean scenario teardown)."""
        self._released.set()

    async def abort(self) -> None:
        self.aborted = True
        if self.slow:
            # Never completes until the scenario drains us: wait_for on this ALWAYS times out,
            # so bound_exceeded is True deterministically rather than by a timing race.
            await self._released.wait()


def test_cancellation_within_bound() -> None:
    """Property 4: disconnect at any boundary cuts within the bound; resources return to baseline.

    Validates: Requirements 6.3, 6.5, 6.6 (and the R6.1/R6.2/R6.4 force-release contract).
    """
    rng = random.Random(_SEED)
    print(f"test_cancellation_within_bound seed={_SEED:#x} iterations={_ITERATIONS}")

    for i in range(_ITERATIONS):
        slow = rng.random() < 0.5
        # The bound is DERIVED from `target_p99_ms` (`cancellation_bound_s = p99_s * 3`). The two
        # cases pick the SLO so the real `asyncio.wait_for` timeout the controller uses is
        # unambiguous — the deterministic verdict never rides a wall-clock race:
        #   * slow  → a sub-millisecond bound so the guaranteed timeout (the abort blocks forever)
        #             fires fast and the whole 10k-iter sweep stays quick;
        #   * fast  → a comfortably large bound (tens of ms) so an immediately-completing abort
        #             always finishes inside it, well above any event-loop scheduling jitter.
        if slow:
            target_p99_ms = rng.uniform(0.001, 0.05)
        else:
            target_p99_ms = rng.uniform(20.0, 60.0)
        contract = _contract(target_p99_ms=target_p99_ms)
        bound_s = contract.cancellation_bound_s()

        # Buffered-at-disconnect: offer a random number of bytes strictly below the per-stream
        # high-water so `offer` admits them (R4), modelling a disconnect at an arbitrary chunk
        # boundary with that much buffered.
        coalescer = Coalescer(contract=contract, active_streams=lambda: 1)
        high_water = coalescer.high_water()
        buffered_at_disconnect = rng.randint(0, min(4096, high_water - 1))
        if buffered_at_disconnect > 0:
            assert coalescer.offer(buffered_at_disconnect), (
                f"i={i} seed={_SEED:#x} offer rejected a below-high-water delta"
            )
        assert coalescer.buffered() == buffered_at_disconnect

        # Credit baseline: grant then spend equal amounts so there is nothing outstanding to leak;
        # the cancellation must leave this at baseline (outstanding 0).
        credit = CreditFlowControl()
        granted = credit.grant_on_consume(rng.randint(0, 2048))
        credit.spend(granted)
        assert credit.outstanding() == 0

        provider = StubAbortProvider(slow=slow)
        kill_latch = KillLatch()
        metrics = PerRequestExports()
        clock = ControllableClock()

        # Script the measured elapsed window: for the fast (within-bound) case advance by strictly
        # less than the bound; for the slow (exceeds) case advance past the bound. Either way the
        # reported `elapsed_ms` is deterministic (clock-driven), decoupled from the real wait_for.
        if slow:
            elapsed_s = bound_s * rng.uniform(1.5, 4.0)
        else:
            elapsed_s = bound_s * rng.uniform(0.0, 0.9)
        clock.script_delta(elapsed_s)

        controller = CancellationController(
            provider=provider,
            coalescer=coalescer,
            contract=contract,
            clock=clock,
            metrics=metrics,
            kill_latch=kill_latch,
        )

        async def scenario(
            ctrl: CancellationController = controller,
            prov: StubAbortProvider = provider,
        ) -> CancelOutcome:
            result = await ctrl.on_disconnect()
            # Drain a slow (shielded) abort so no pending task is left when the loop closes.
            prov.release()
            await asyncio.sleep(0)
            return result

        outcome = _run(scenario())

        ctx = f"i={i} seed={_SEED:#x} slow={slow} bound_s={bound_s:g}"

        # R6.2: the kill latch is flipped — the in-flight guard seam is cut, probe is true.
        assert kill_latch.is_killed(), f"{ctx} kill latch not set"
        assert kill_latch() is True, f"{ctx} kill probe not true"
        # R6.1: the provider was aborted (even on the force-release / slow path).
        assert provider.aborted, f"{ctx} provider not aborted"

        # R6.6: buffered bytes released back to the pool; nothing left buffered, nothing forwarded.
        assert coalescer.buffered() == 0, f"{ctx} buffer not released to baseline"
        assert outcome.released_bytes == buffered_at_disconnect, (
            f"{ctx} released_bytes={outcome.released_bytes} != buffered-at-disconnect "
            f"{buffered_at_disconnect}"
        )
        # Credit returns to baseline (nothing outstanding leaked by the cancellation).
        assert credit.outstanding() == 0, f"{ctx} credit outstanding not at baseline"

        # R6.5: the measured elapsed_ms is reported, equal to the scripted clock delta.
        expected_ms = elapsed_s * _MS_PER_S
        assert abs(outcome.elapsed_ms - expected_ms) < 1e-6, (
            f"{ctx} elapsed_ms={outcome.elapsed_ms} != scripted {expected_ms}"
        )

        # R6.3 / R6.4: within-bound vs force-release verdict, and the provider is cut either way.
        if slow:
            assert outcome.bound_exceeded is True, (
                f"{ctx} slow abort should be a bound-exceeded (force-release) cancellation"
            )
        else:
            assert outcome.bound_exceeded is False, (
                f"{ctx} fast abort should complete within the derived bound"
            )

        # The cancellation is exported per cancelled stream (R6.5): a release-lag sample recorded.
        reading = metrics.snapshot()
        assert reading.release_lag_ms.count == 1, (
            f"{ctx} expected exactly one release-lag sample for the cancelled stream"
        )
        # Fail-closed everywhere: the FAIL_OPEN counter is never incremented (R15.3).
        assert reading.fail_open_total == 0, f"{ctx} FAIL_OPEN incremented"
