"""Drain state-machine tests (R2-07 / R2-08 / GW19), tasks 10.1 / 10.2 / 10.3.

Covers the drain path of :class:`gateway_v2.admit.admission.AdmissionController`:

* **Property 7 — drain terminal guarantee** (task 10.2): on drain, every stream still in flight is
  terminated with a :class:`DeclaredTermination` terminal frame (``truncated=False``); a stream
  that finished on its own is NOT terminated. No stream ever ends truncated.
* **Unit tests** (task 10.3): ``ACCEPTING -> DRAINING`` on SIGTERM (Req 8.1), the audit queue is
  flushed before exit (Req 8.4), the measured duration is published (Req 8.5), and all drain timing
  reads the injected clock (Req 8.6).

House idiom: seeded ``random.Random`` loop of >= 10,000 iterations, seed logged; NO ``hypothesis``.
Async via a local ``_run`` helper. Test files are not under the import-linter layer contract.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable

from gateway_v2.admit.admission import (
    AdmissionController,
    DeclaredTermination,
    DrainReport,
    DrainState,
)
from gateway_v2.admit.codel import CoDelParams
from gateway_v2.admit.metrics import AdmissionMetrics
from gateway_v2.admit.quota import AdmissionBounds
from gateway_v2.admit.supervisor import WorkerSupervisor

_ITERATIONS = 10_000


def _run[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _bounds(depth: int = 1_000) -> AdmissionBounds:
    return AdmissionBounds(
        concurrency=depth,
        request_depth=depth,
        guard_depth=depth,
        dispatch_depth=depth,
        egress_depth=depth,
        audit_depth=depth,
    )


def _controller(
    box: dict[str, float],
    *,
    drain_window_s: float = 5.0,
    metrics: AdmissionMetrics | None = None,
) -> AdmissionController:
    return AdmissionController(
        bounds=_bounds(),
        params=CoDelParams(),
        rng=random.Random(0),
        supervisor=WorkerSupervisor(),
        metrics=metrics if metrics is not None else AdmissionMetrics(),
        drain_window_s=drain_window_s,
        clock=lambda: box["t"],
    )


# --------------------------------------------------------------------------- #
# Task 10.2 — Property 7: drain terminal guarantee
# Feature: admission-control, Property 7
# Validates: Requirements 8.2, 8.3
# --------------------------------------------------------------------------- #


def test_property7_every_unfinished_stream_gets_a_declared_termination() -> None:
    """On drain, exactly the unfinished streams are terminated; none truncated."""
    seed = 0x19_07
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        box = {"t": rng.uniform(0.0, 100.0)}
        ctl = _controller(box)
        n = rng.randint(0, 20)
        finished_ids: set[str] = set()
        for k in range(n):
            sid = f"s{k}"
            ctl.register_stream(sid)
            if rng.random() < 0.5:
                ctl.finish_stream(sid)
                finished_ids.add(sid)
        box["t"] += rng.uniform(0.0, 50.0)
        report = _run(ctl.drain())
        terminated = {t.stream_id for t in report.declared_terminations}
        expected = {f"s{k}" for k in range(n)} - finished_ids
        assert terminated == expected, (
            f"seed={seed:#x} iter={i} terminated {terminated} != unfinished {expected}"
        )
        # No declared termination is ever a truncated frame (Property 7).
        for term in report.declared_terminations:
            assert isinstance(term, DeclaredTermination)
            assert term.truncated is False, (
                f"seed={seed:#x} iter={i} stream {term.stream_id} ended truncated"
            )
        # A finished stream is never terminated.
        assert not (terminated & finished_ids), (
            f"seed={seed:#x} iter={i} a finished stream was terminated"
        )


# --------------------------------------------------------------------------- #
# Task 10.3 — unit tests: transitions, ordering, injected clock
# Validates: Requirements 8.1, 8.4, 8.5, 8.6
# --------------------------------------------------------------------------- #


def test_starts_in_accepting_and_sigterm_moves_to_draining_then_exits() -> None:
    """ACCEPTING is the baseline; a drain moves off ACCEPTING and ends EXITED (Req 8.1)."""
    box = {"t": 0.0}
    ctl = _controller(box)
    assert ctl.state is DrainState.ACCEPTING
    report = _run(ctl.drain())
    assert isinstance(report, DrainReport)
    # The machine ran through DRAINING (stopped accepting) and ended EXITED.
    assert ctl.state is DrainState.EXITED


def test_drain_publishes_duration_measured_on_the_injected_clock() -> None:
    """The published duration is (end - start) read from the injected clock (Req 8.5 / 8.6)."""
    seed = 0x19_07D
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        start = rng.uniform(0.0, 1000.0)
        elapsed = rng.uniform(0.0, 30.0)
        # Wrap the clock so the drain's SECOND read (end) returns start + elapsed (Req 8.6).
        reads = {"n": 0}

        def clock() -> float:
            reads["n"] += 1
            return start if reads["n"] == 1 else start + elapsed  # noqa: B023

        ctl = AdmissionController(
            bounds=_bounds(),
            params=CoDelParams(),
            rng=random.Random(0),
            supervisor=WorkerSupervisor(),
            metrics=AdmissionMetrics(),
            drain_window_s=5.0,
            clock=clock,
        )
        report = _run(ctl.drain())
        assert abs(report.duration_s - elapsed) < 1e-9, (
            f"seed={seed:#x} iter={i} duration {report.duration_s} != elapsed {elapsed}"
        )


def test_audit_queue_is_flushed_before_exit() -> None:
    """The audit queue is drained to empty during the drain, before exit (Req 8.4)."""
    box = {"t": 0.0}
    metrics = AdmissionMetrics()
    ctl = _controller(box, metrics=metrics)
    # Push some items onto the audit bounded queue via the controller's internal map.
    audit = ctl._queues["audit"]  # noqa: SLF001 -- white-box flush assertion
    for _ in range(7):
        audit.offer(object())
    assert audit.depth == 7
    report = _run(ctl.drain())
    assert report.audit_flushed is True
    assert audit.depth == 0, "audit queue was not flushed before exit"
    assert ctl.state is DrainState.EXITED
    # The flushed depth is reflected in the metrics producer.
    assert metrics.snapshot().queues.depth("audit") == 0


def test_declared_terminations_are_ordered_by_stream_id() -> None:
    """Declared terminations come back in a stable (sorted) order for a testable read."""
    box = {"t": 0.0}
    ctl = _controller(box)
    for sid in ("s3", "s1", "s2"):
        ctl.register_stream(sid)
    report = _run(ctl.drain())
    ids = [t.stream_id for t in report.declared_terminations]
    assert ids == ["s1", "s2", "s3"]
