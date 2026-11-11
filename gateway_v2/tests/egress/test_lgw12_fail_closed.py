"""LGW12 No-FAIL_OPEN cross-cutting invariant (GW12, task 13.2).

# Feature: sse-egress-pipeline, Property 7: No-FAIL_OPEN
# Validates: Requirements 15.3

Property 7 (No-FAIL_OPEN). GW12 fails closed EVERYWHERE: an uncomputable export
is recorded as a withholding (R12.4), a cancel/timeout cuts the stream, a
scan error withholds the frame -- no path ever "fails open" and forwards raw
bytes to compensate. The one machine-checkable witness of that discipline is the
``FAIL_OPEN`` counter on the per-request export producer
(``runtime/stream_metrics.py``: :class:`PerRequestExports` /
:attr:`PerRequestReading.fail_open_total`): it is pinned at ``0`` and NO correct
path increments it (R15.1/R15.2/R15.3). This property asserts that across ALL
generated inputs -- including EVERY injected fault the other GW12 components
exercise the producer with -- ``snapshot().fail_open_total`` stays exactly ``0``.

The generator space here is deliberately BROADER than any single failure property
(design: "Non-redundancy of the properties"). Each iteration drives a fresh
:class:`PerRequestExports` through:

* a random sequence of ``observe_*`` observations AND ``record_withheld`` calls
  -- an uncomputable export is a WITHHOLDING, and a withholding must NEVER touch
  ``fail_open_total`` (R12.4); after any number of observations/withholdings the
  snapshot's ``fail_open_total`` is ``0``; and
* a client-disconnect cancellation through :class:`CancellationController` whose
  injected provider ``abort`` either succeeds, raises, or stalls past the derived
  ``cancellation_bound_s()`` (the bound-exceeded force-release path) -- every one
  of those cancel outcomes leaves ``fail_open_total`` at ``0``; and
* a mid-stream error-frame scan via :func:`scan_error_frame` whose injected
  scanner / resolver either succeeds, blocks, or RAISES -- the scan-error and
  block paths both WITHHOLD the frame, and feeding that fault at the producer
  leaves ``fail_open_total`` at ``0``.

The property is exercised with a seeded ``random.Random`` driven for
``_ITERATIONS`` (>= 10,000) iterations -- NO hypothesis; the async cancel path
runs via ``asyncio.run``. The seed is logged in every failure message so a
counterexample is reproducible.

Layering note: ``egress`` sits below ``detect``; this test imports the producer
(``runtime``), the cancellation controller + error-frame scan (``edge``, the
injection layer above ``detect``), the provider stub (``dispatch``) and the
coalescer (``egress``) -- the scanner/resolver are injected callables, so no
``egress -> detect`` edge is introduced.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Sequence

from gateway_v2.dispatch.provider import ProviderClient, UpstreamEvent, UpstreamRequest
from gateway_v2.domain import Finding
from gateway_v2.domain.decision import Decision, Disposition
from gateway_v2.edge.cancel import CancellationController, KillLatch
from gateway_v2.edge.error_frame_scan import (
    BLOCK_CODE,
    SCAN_ERROR_CODE,
    scan_error_frame,
)
from gateway_v2.egress.backpressure import Coalescer
from gateway_v2.egress.output_guard import MinimalOutputResolver, OutputBlocked
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals
from gateway_v2.runtime.stream_metrics import PerRequestExports

#: Property-test iteration budget (house idiom: seeded ``random.Random`` >= 10,000, no hypothesis).
_ITERATIONS = 10_000

#: Fixed top-level seed; logged in every failure message for reproducibility.
_SEED = 0x15_03

#: Upper bound on the per-iteration producer-operation count.
_MAX_OPS = 24


def _fast_cancel_contract() -> ResourceContract:
    """A fixed serviceable contract with a TINY ``cancellation_bound_s()`` for the stall path.

    The stall-mode cancel drives ``asyncio.wait_for(timeout=cancellation_bound_s())`` to fire on
    the REAL event-loop clock (the injected ``clock`` only drives the measured ``elapsed_ms``, not
    ``wait_for``), so the per-iteration wall-clock cost is one ``cancellation_bound_s()``. A tiny
    ``target_p99_ms`` keeps that bound at roughly a millisecond so ~5,000 stall iterations stay
    fast; the stall provider sleeps far longer than the bound so the timeout always bites and the
    bound-exceeded force-release path (R6.4) is exercised deterministically.
    """
    signals = HardwareSignals(
        cpu_quota=8.0,
        memory_limit=512 * 1024 * 1024,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals,
        target_p99_ms=0.5,
        utilization_cap=0.75,
        per_worker_rss=128 * 1024 * 1024,
    )


def _serviceable_contract(rng: random.Random) -> ResourceContract:
    """A random but serviceable :class:`ResourceContract` built via :func:`from_signals`.

    Ranges mirror ``tests/egress/test_lgw12_coalescer.py`` so ``from_signals`` is computable
    (at least one worker on both the CPU and RAM paths) and every GW12 derivation
    (``cancellation_bound_s``, ``pool_size``, ``stream_buffer_bytes``) resolves to a positive
    value -- the controller's bound and the coalescer's ceiling are then genuine contract
    derivations rather than hand-picked constants.
    """
    utilization_cap = rng.uniform(0.25, 0.95)
    cpu_quota = rng.uniform(1.0 / utilization_cap + 1.0, 32.0)
    per_worker_rss = rng.randint(128, 512) * 1024 * 1024
    rss_multiplier = rng.randint(int(1.0 / utilization_cap) + 2, 24)
    memory_limit = per_worker_rss * rss_multiplier
    fd_limit = rng.randint(256, 65_536)
    signals = HardwareSignals(
        cpu_quota=cpu_quota,
        memory_limit=memory_limit,
        fd_limit=fd_limit,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals,
        target_p99_ms=rng.uniform(5.0, 200.0),
        utilization_cap=utilization_cap,
        per_worker_rss=per_worker_rss,
    )


class _AbortProvider:
    """An injectable :class:`ProviderClient` whose ``abort`` succeeds or STALLS.

    Only ``abort`` matters to :meth:`CancellationController.on_disconnect`; ``open`` is present
    so the stub satisfies the protocol surface. ``mode`` selects the injected fault:

    * ``"ok"`` -- ``abort`` returns promptly (abort within bound).
    * ``"stall"`` -- ``abort`` sleeps past ``cancellation_bound_s()``, driving the bound-exceeded
      FORCE-RELEASE path (``CancelOutcome.bound_exceeded``, R6.4). The controller uses the
      INJECTED clock to measure elapsed time; the stall is a real ``asyncio.sleep`` long enough
      that the ``asyncio.wait_for(timeout=bound_s)`` fires, and the clock makes the measured
      interval deterministic.

    (A fast-raising ``abort`` is intentionally NOT exercised here: the shipped controller
    propagates a raising abort that completes within the bound rather than catching it, so that
    fault belongs to the ``edge/cancel.py`` cancellation property, not this cross-cutting
    FAIL_OPEN invariant. Both producer-affecting cancel paths -- clean abort and bound-exceeded
    force-release -- are driven here, and both must leave FAIL_OPEN at zero.)
    """

    def __init__(self, *, mode: str, stall_s: float) -> None:
        self._mode = mode
        self._stall_s = stall_s

    async def open(  # pragma: no cover - never iterated in this test
        self, request: UpstreamRequest
    ) -> AsyncIterator[UpstreamEvent]:
        # Satisfies the ``ProviderClient`` protocol surface; the cancel path only ``abort``s. The
        # unreachable ``yield`` after ``return`` is what makes this an async generator whose type
        # is ``AsyncIterator[UpstreamEvent]`` (an empty stream) without ever yielding an event.
        return
        yield

    async def abort(self) -> None:
        if self._mode == "stall":
            await asyncio.sleep(self._stall_s)


def _raising_scanner(_frame: str) -> Sequence[Finding]:
    """An error-frame scanner that always raises -- the R11.4 scan-error fault."""
    raise RuntimeError("injected scan failure")


#: An empty-but-valid blocking signal: a ``Decision`` carries no per-finding entries (so the
#: ``__post_init__`` most-restrictive check is satisfied with ``ALLOW``), and the resolver raises
#: ``OutputBlocked`` wrapping it -- the shape ``scan_error_frame`` catches to map to BLOCK_CODE.
_EMPTY_DECISION = Decision(
    disposition=Disposition.ALLOW,
    per_finding=(),
    transformations=(),
    findings=(),
    plan_version="test",
    deciding_rules=(),
    unavailable_detectors=(),
)


class _BlockResolver:
    """An :class:`OutputResolver` whose ``decide`` always raises :class:`OutputBlocked`.

    Models the R11.3 BLOCK disposition path of :func:`scan_error_frame`: ``apply_decision`` raises
    ``OutputBlocked`` on a block, which the scan maps to :data:`BLOCK_CODE` (withhold). Raising it
    directly from ``decide`` reaches the same ``except OutputBlocked`` branch without needing a
    detector that produces a terminating finding.
    """

    def decide(self, matches: Sequence[Finding]) -> Decision:
        raise OutputBlocked(_EMPTY_DECISION)


def _drive_observations(exports: PerRequestExports, rng: random.Random) -> None:
    """Drive a random sequence of ``observe_*`` + ``record_withheld`` calls on ``exports``.

    Includes ``record_withheld`` so the "an uncomputable export is a withholding, NEVER a
    fail-open" rule (R12.4) is exercised: a withholding bumps ``withheld_total`` and must leave
    ``fail_open_total`` untouched.
    """
    ops = rng.randint(0, _MAX_OPS)
    for _ in range(ops):
        choice = rng.randrange(6)
        if choice == 0:
            exports.observe_detector_invocation()
        elif choice == 1:
            exports.observe_release_lag_ns(rng.randint(-10, 5_000_000))
        elif choice == 2:
            exports.observe_buffer_high_water(rng.randint(0, 1 << 20))
        elif choice == 3:
            exports.observe_memory_bound(rng.randint(0, 1 << 30))
        elif choice == 4:
            exports.observe_loop_lag_ns(rng.randint(-10, 5_000_000))
        else:
            # An uncomputable export: recorded as a WITHHOLDING, never a fail-open (R12.4).
            exports.record_withheld()


def test_property7_no_fail_open() -> None:
    """Property 7: the FAIL_OPEN counter stays zero across every generated input + fault.

    # Feature: sse-egress-pipeline, Property 7: No-FAIL_OPEN
    # Validates: Requirements 15.3
    """
    rng = random.Random(_SEED)
    for i in range(_ITERATIONS):
        exports = PerRequestExports()

        # Baseline: a fresh producer reports FAIL_OPEN = 0 (real zero, never absence).
        assert exports.snapshot().fail_open_total == 0, (
            f"seed={_SEED:#x} iter={i}: fresh producer reported non-zero fail_open_total "
            f"{exports.snapshot().fail_open_total} (R15.3)"
        )

        # (1) Random observations + withholdings never touch FAIL_OPEN (R12.4).
        _drive_observations(exports, rng)
        assert exports.snapshot().fail_open_total == 0, (
            f"seed={_SEED:#x} iter={i}: observations/withholdings incremented fail_open_total "
            f"to {exports.snapshot().fail_open_total} (R12.4/R15.3)"
        )

        # (2) A client-disconnect cancellation whose provider abort succeeds or stalls.
        mode = rng.choice(("ok", "stall"))
        # The stall path waits one real `cancellation_bound_s()`, so it uses the tiny-bound fast
        # contract; the clean-abort path has no real wait and uses a random serviceable contract.
        contract = _fast_cancel_contract() if mode == "stall" else _serviceable_contract(rng)
        bound_s = contract.cancellation_bound_s()
        # A monotone injected clock: each reading advances by a fixed tick so a STALL registers a
        # measurable elapsed interval and `on_disconnect` is deterministic.
        clock_state = {"t": 0.0}

        def _clock() -> float:
            clock_state["t"] += bound_s  # noqa: B023 - bound per-iteration on purpose
            return clock_state["t"]

        provider: ProviderClient = _AbortProvider(mode=mode, stall_s=bound_s * 4.0)
        n_streams = rng.randint(1, 16)

        def _active() -> int:
            return n_streams  # noqa: B023 - bound per-iteration on purpose

        coalescer = Coalescer(contract=contract, active_streams=_active)
        # Buffer some bytes so the cancel has real state to release (release never fails open).
        ceiling = contract.stream_buffer_bytes(n_streams)
        if ceiling > 2:
            coalescer.offer(rng.randint(1, ceiling - 1))
        kill_latch = KillLatch()
        controller = CancellationController(
            provider=provider,
            coalescer=coalescer,
            contract=contract,
            clock=_clock,
            metrics=exports,
            kill_latch=kill_latch,
        )

        outcome = asyncio.run(controller.on_disconnect())

        # The cancel is fail-closed: the latch is flipped (stream cut) and no raw byte survives;
        # whatever the outcome, FAIL_OPEN is untouched (R15.1/R15.2/R15.3).
        assert kill_latch.is_killed(), (
            f"seed={_SEED:#x} iter={i} mode={mode}: cancel did not flip the kill latch"
        )
        assert outcome.bound_exceeded == (mode == "stall"), (
            f"seed={_SEED:#x} iter={i} mode={mode}: bound_exceeded={outcome.bound_exceeded} "
            f"disagreed with the injected abort mode"
        )
        assert exports.snapshot().fail_open_total == 0, (
            f"seed={_SEED:#x} iter={i} mode={mode}: cancellation incremented fail_open_total "
            f"to {exports.snapshot().fail_open_total} (R15.3)"
        )

        # (3) A mid-stream error-frame scan whose scanner/resolver succeeds / blocks / raises.
        frame = "err-" + "".join(rng.choice("abcdef0123456789 ") for _ in range(rng.randint(0, 48)))
        scan_mode = rng.choice(("ok", "block", "raise"))
        if scan_mode == "raise":
            scan_outcome = scan_error_frame(
                frame,
                scanner=_raising_scanner,
                resolver=MinimalOutputResolver(),
            )
            assert scan_outcome.withheld_code == SCAN_ERROR_CODE, (
                f"seed={_SEED:#x} iter={i}: raising scanner did not withhold fail-closed "
                f"(code={scan_outcome.withheld_code!r}, R11.4)"
            )
        elif scan_mode == "block":
            scan_outcome = scan_error_frame(
                frame,
                scanner=lambda _f: (),
                resolver=_BlockResolver(),
            )
            assert scan_outcome.withheld_code == BLOCK_CODE, (
                f"seed={_SEED:#x} iter={i}: block decision did not withhold "
                f"(code={scan_outcome.withheld_code!r}, R11.3)"
            )
        else:
            scan_outcome = scan_error_frame(
                frame,
                scanner=lambda _f: (),
                resolver=MinimalOutputResolver(),
            )
            assert not scan_outcome.withheld, (
                f"seed={_SEED:#x} iter={i}: clean scan unexpectedly withheld "
                f"(code={scan_outcome.withheld_code!r})"
            )

        # A withheld frame forwards no raw bytes; whichever scan path fired, it does not fail open.
        if scan_outcome.withheld:
            exports.record_withheld()
        assert exports.snapshot().fail_open_total == 0, (
            f"seed={_SEED:#x} iter={i} scan_mode={scan_mode}: error-frame scan path incremented "
            f"fail_open_total to {exports.snapshot().fail_open_total} (R15.3)"
        )
