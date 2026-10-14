"""B3 drain-extension tests — the minimal behaviour-preserving ``drain()`` extension (card B3).

Covers the two B3 obligations on the shipped
:class:`gateway_v2.admit.admission.AdmissionController.drain` extension (task 2.1): the two optional
keyword-only parameters (``accept_grace_s`` / ``drain_window_s``) plus the injected ``sleep``.

* **Property 2 — termination set + duration clamp** (task 2.2): over random finished/unfinished
  in-flight sets and random ``accept_grace_s >= 0`` / ``drain_window_s > 0``, ``drain(...)`` emits
  exactly one :class:`DeclaredTermination` (``truncated=False``) per UNFINISHED stream and none for
  a finished one, the report's declared-termination id set equals exactly the unfinished stream
  ids, and the clamp invariant ``min(contract.max_stream_duration_s(), drain_s) <= drain_s`` holds.
* **No-op-at-defaults regression** (task 2.3): ``drain()`` called with NO args walks
  ``ACCEPTING -> DRAINING -> TERMINATING -> FLUSHED -> EXITED`` identically to the shipped machine
  (same ``DrainReport`` shape, immediate transition, audit flushed, declared terminations for the
  unfinished streams exactly as before the extension). Because the two new parameters default to
  no-ops, the full shipped ``tests/admit/`` suite must still pass unchanged.

House idiom: seeded ``random.Random`` loop of >= 10,000 iterations, seed logged in each assertion
message; NO ``hypothesis``. Async via a local ``_run`` helper (``asyncio.run``). The property test
injects an INSTANT ``sleep`` that advances the fake clock by its argument, so the bounded in-flight
wait terminates at the window deadline deterministically and the >= 10,000 iterations stay fast.
Test files are not under the import-linter layer contract.
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
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

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
    """A drain-only controller fixture (same construction shape as ``test_lgw19_drain.py``)."""
    return AdmissionController(
        bounds=_bounds(),
        params=CoDelParams(),
        rng=random.Random(0),
        supervisor=WorkerSupervisor(),
        metrics=metrics if metrics is not None else AdmissionMetrics(),
        drain_window_s=drain_window_s,
        clock=lambda: box["t"],
    )


def _contract(*, target_p99_ms: float = 20.0) -> ResourceContract:
    """A serviceable contract via ``from_signals`` (the shared LGW12/15 edge-test fixture shape).

    The signals clear ``from_signals``' refuse-to-start checks on the CPU and RAM worker paths, so
    ``max_stream_duration_s()`` is a valid derivation of ``target_p99_ms``. These are TEST signals,
    not production capacity literals.
    """
    per_worker_rss = 400 * 1024 * 1024
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=per_worker_rss * 8,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals,
        target_p99_ms=target_p99_ms,
        utilization_cap=0.75,
        per_worker_rss=per_worker_rss,
    )


# --------------------------------------------------------------------------- #
# Task 2.2 — Property 2: drain declares exactly the unfinished streams + duration clamp
# Feature: serving-entrypoint-gw-b3, Property 2: Drain declares a terminal frame for exactly the
#   unfinished streams, never truncates, and the declared max duration is covered by the window
# Validates: Requirements 3.4, 3.5, 3.8
# --------------------------------------------------------------------------- #


def test_property2_drain_declares_exactly_unfinished_and_clamp_covers_window() -> None:
    """Over random in-flight sets + random grace/window, drain terminates exactly the unfinished.

    An INSTANT injected ``sleep`` advances the fake clock by its argument, so the bounded in-flight
    wait reaches the window deadline immediately and the >= 10,000 iterations run fast while staying
    deterministic. Every unfinished stream gets exactly one ``DeclaredTermination`` (never
    truncated), a finished stream gets none, the report's declared-termination id set equals exactly
    the unfinished ids, and ``min(contract.max_stream_duration_s(), drain_s) <= drain_s`` holds.
    """
    seed = 0xB3_02
    rng = random.Random(seed)
    contract = _contract()
    max_stream_duration_s = contract.max_stream_duration_s()
    for i in range(_ITERATIONS):
        box = {"t": rng.uniform(0.0, 100.0)}
        ctl = _controller(box)

        # An instant sleep that advances the injected clock so the window deadline is reached and
        # the drain's in-flight wait terminates after one poll rather than looping.
        async def instant_sleep(seconds: float, *, _box: dict[str, float] = box) -> None:
            _box["t"] += seconds

        n = rng.randint(0, 20)
        finished_ids: set[str] = set()
        all_ids: set[str] = set()
        for k in range(n):
            sid = f"s{k}"
            all_ids.add(sid)
            ctl.register_stream(sid)
            if rng.random() < 0.5:
                ctl.finish_stream(sid)
                finished_ids.add(sid)
        unfinished_ids = all_ids - finished_ids

        accept_grace_s = rng.uniform(0.0, 10.0)
        drain_s = rng.uniform(0.001, 30.0)
        report = _run(
            ctl.drain(
                accept_grace_s=accept_grace_s,
                drain_window_s=drain_s,
                sleep=instant_sleep,
            )
        )

        terminated = {t.stream_id for t in report.declared_terminations}
        # Exactly the unfinished streams are terminated (Req 3.5) — one per unfinished stream, none
        # for a finished one; the report id set equals exactly the unfinished id set (Req 3.4/3.5).
        assert terminated == unfinished_ids, (
            f"seed={seed:#x} iter={i} terminated {terminated} != unfinished {unfinished_ids}"
        )
        assert len(report.declared_terminations) == len(unfinished_ids), (
            f"seed={seed:#x} iter={i} not exactly one declared termination per unfinished stream"
        )
        # No declared termination is ever a truncated frame (Req 3.5 — never silently truncated).
        for term in report.declared_terminations:
            assert isinstance(term, DeclaredTermination)
            assert term.truncated is False, (
                f"seed={seed:#x} iter={i} stream {term.stream_id} ended truncated"
            )
        # A finished stream is never terminated.
        assert not (terminated & finished_ids), (
            f"seed={seed:#x} iter={i} a finished stream was terminated"
        )
        # The declared max stream duration is covered by the drain window (Req 3.8 / G-02): the
        # clamp min(contract.max_stream_duration_s(), drain_s) never exceeds the window.
        clamped = min(max_stream_duration_s, drain_s)
        assert clamped <= drain_s, (
            f"seed={seed:#x} iter={i} clamp {clamped} exceeded drain window {drain_s}"
        )


# --------------------------------------------------------------------------- #
# Task 2.3 — the extension is a no-op at defaults (shipped-behaviour regression)
# Validates: Requirements 3.2, 3.3, 3.9
#
# NB: because the two new ``drain()`` parameters (``accept_grace_s=0.0`` / ``drain_window_s=None``)
# default to no-ops, the FULL shipped admission suite (``tests/admit/``, in particular
# ``test_lgw19_drain.py``) MUST still pass unchanged — the assertions below mirror that shipped
# drain-state walk to pin the no-op-at-defaults contract.
# --------------------------------------------------------------------------- #


def test_defaults_walk_the_shipped_drain_state_machine_unchanged() -> None:
    """``drain()`` with no args walks ACCEPTING -> ... -> EXITED as shipped (Req 3.2/3.3)."""
    box = {"t": 0.0}
    ctl = _controller(box)
    assert ctl.state is DrainState.ACCEPTING
    report = _run(ctl.drain())
    assert isinstance(report, DrainReport)
    # The machine ran through DRAINING (stopped accepting, Req 3.3) and ended EXITED, immediately —
    # the defaults add no wait (Req 3.2).
    assert ctl.state is DrainState.EXITED


def test_defaults_report_shape_matches_the_shipped_machine() -> None:
    """The default-drain ``DrainReport`` has the shipped shape: duration, terminations, flush."""
    box = {"t": 0.0}
    ctl = _controller(box)
    report = _run(ctl.drain())
    # Same frozen DrainReport shape as the shipped machine (Req 3.9 audit side).
    assert isinstance(report.duration_s, float)
    assert isinstance(report.declared_terminations, tuple)
    assert report.audit_flushed is True


def test_defaults_declare_terminations_for_unfinished_streams_exactly_as_before() -> None:
    """At defaults the declared-termination set is exactly the unfinished streams, as shipped."""
    box = {"t": 0.0}
    ctl = _controller(box)
    for sid in ("s3", "s1", "s2"):
        ctl.register_stream(sid)
    ctl.finish_stream("s2")
    report = _run(ctl.drain())
    terminated = {t.stream_id for t in report.declared_terminations}
    # Only the unfinished streams are terminated; the finished one is not — identical to the shipped
    # ``test_lgw19_drain.py`` behaviour, confirming the extension is a no-op at defaults.
    assert terminated == {"s1", "s3"}
    for term in report.declared_terminations:
        assert term.truncated is False


def test_defaults_audit_flushed_before_exit_as_shipped() -> None:
    """The audit queue is drained to empty during the default drain, before exit (Req 3.9)."""
    box = {"t": 0.0}
    metrics = AdmissionMetrics()
    ctl = _controller(box, metrics=metrics)
    audit = ctl._queues["audit"]  # noqa: SLF001 -- white-box flush assertion, as in the shipped test
    for _ in range(5):
        audit.offer(object())
    assert audit.depth == 5
    report = _run(ctl.drain())
    assert report.audit_flushed is True
    assert audit.depth == 0, "audit queue was not flushed before exit"
    assert ctl.state is DrainState.EXITED
    assert metrics.snapshot().queues.depth("audit") == 0
