"""``AdmissionController.admit`` tests (R2-07 / R2-08 / GW19), tasks 9.1 / 9.2 / 9.3.

Covers the event-loop seam :class:`gateway_v2.admit.admission.AdmissionController`:

* **Property 1 — no abandonment** (task 9.2): every request the controller admits is enqueued and
  tracked; a completed admitted request produces a response and is never dropped. Shedding happens
  only at admission, before service begins.
* **Property 10 — error conditions fail closed** (task 9.3): a request that cannot reach an
  admission decision sheds (never admits without a decision), an enqueue past a full bounded queue
  sheds at the door, and ``fail_open_total`` stays 0 across the whole overload burst. Refuse-to-
  start on an unset ``q_safe`` is covered where the bounds are derived (``test_lgw19_quota``);
  here we assert the runtime fail-closed branches and the pinned-zero FAIL_OPEN counter.

House idiom: seeded ``random.Random`` loop of >= 10,000 iterations, seed logged; NO ``hypothesis``.
Async via a local ``_run`` helper. Test files are not under the import-linter layer contract.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable

from gateway_v2.admit.admission import AdmissionController
from gateway_v2.admit.codel import CoDelParams
from gateway_v2.admit.grant import Admitted, ShedVerdict
from gateway_v2.admit.metrics import AdmissionMetrics, ShedReason
from gateway_v2.admit.quota import AdmissionBounds
from gateway_v2.admit.supervisor import WorkerSupervisor
from gateway_v2.domain.posture import MIN_RETRY_AFTER_S, OVERLOAD_SHED

_ITERATIONS = 10_000


def _run[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _bounds(depth: int) -> AdmissionBounds:
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
    depth: int = 1_000_000,
    metrics: AdmissionMetrics | None = None,
) -> AdmissionController:
    return AdmissionController(
        bounds=_bounds(depth),
        params=CoDelParams(),
        rng=random.Random(0),
        supervisor=WorkerSupervisor(),
        metrics=metrics if metrics is not None else AdmissionMetrics(),
        drain_window_s=1.0,
        clock=lambda: box["t"],
    )


# --------------------------------------------------------------------------- #
# Task 9.2 — Property 1: no abandonment
# Feature: admission-control, Property 1
# Validates: Requirements 2.1, 2.2, 2.3
# --------------------------------------------------------------------------- #


def test_property1_every_admitted_request_is_tracked_and_completes() -> None:
    """Every Admitted request is enqueued and, on completion, answered (never dropped)."""
    seed = 0x19_01
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        box = {"t": 0.0}
        metrics = AdmissionMetrics()
        ctl = _controller(box, metrics=metrics)
        admitted_ids: list[str] = []
        n = rng.randint(1, 20)
        for k in range(n):
            box["t"] += rng.uniform(0.0, 0.01)
            # Sub-target sojourn so CoDel admits; the point is the admitted-request contract.
            sojourn_ms = rng.uniform(0.0, 4.0)
            enq = box["t"] - sojourn_ms / 1000.0
            outcome = _run(ctl.admit("owner", f"r{k}", enq))
            if isinstance(outcome, Admitted):
                admitted_ids.append(outcome.request_id)
                assert outcome.request_id == f"r{k}", f"seed={seed:#x} iter={i} id mismatch"
        # Every admitted request is in flight (tracked), not lost.
        for rid in admitted_ids:
            assert rid in ctl._in_flight, (  # noqa: SLF001 -- white-box no-abandonment check
                f"seed={seed:#x} iter={i} admitted {rid} was dropped before completion"
            )
        # Completing each admitted request produces a response and never drops a tracked request.
        admitted_total = metrics.snapshot().admitted_total
        assert admitted_total == len(admitted_ids), (
            f"seed={seed:#x} iter={i} admitted_total {admitted_total} != {len(admitted_ids)}"
        )
        for rid in admitted_ids:
            ctl.complete(rid)
            assert ctl._in_flight[rid].finished is True, (  # noqa: SLF001
                f"seed={seed:#x} iter={i} admitted {rid} did not complete"
            )


def test_property1_shedding_only_at_admission() -> None:
    """A shed outcome is a ShedVerdict (decided at the door); an admit is never later reversed."""
    seed = 0x19_01B
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        box = {"t": 0.0}
        ctl = _controller(box)
        box["t"] = rng.uniform(0.0, 1.0)
        # A hard-cap sojourn is shed at the door.
        shed = _run(ctl.admit("owner", "r", box["t"] - 0.070))
        assert isinstance(shed, ShedVerdict), f"seed={seed:#x} iter={i} hard-cap not shed"
        assert shed.request_id == "r"
        # The shed request is NOT tracked in flight (it never passed the door).
        assert "r" not in ctl._in_flight, (  # noqa: SLF001
            f"seed={seed:#x} iter={i} a shed request was tracked as in-flight"
        )


# --------------------------------------------------------------------------- #
# Task 9.3 — Property 10: error conditions fail closed
# Feature: admission-control, Property 10
# Validates: Requirements 4.5, 13.1, 13.2, 13.3
# --------------------------------------------------------------------------- #


def test_property10_full_queue_sheds_at_the_door_and_never_fails_open() -> None:
    """When the request queue is full, admission sheds (QUEUE_FULL) and fail_open_total stays 0."""
    seed = 0x19_10
    rng = random.Random(seed)
    for i in range(_ITERATIONS):
        box = {"t": 0.0}
        metrics = AdmissionMetrics()
        max_depth = rng.randint(1, 8)
        ctl = _controller(box, depth=max_depth, metrics=metrics)
        # Admit up to the bound with sub-target sojourns (CoDel admits; the queue fills).
        for k in range(max_depth):
            box["t"] += 0.001
            out = _run(ctl.admit("owner", f"a{k}", box["t"]))
            assert isinstance(out, Admitted), f"seed={seed:#x} iter={i} setup admit failed"
        # The next admit must shed at the door (queue full), not exceed the bound.
        box["t"] += 0.001
        shed = _run(ctl.admit("owner", "overflow", box["t"]))
        assert isinstance(shed, ShedVerdict), (
            f"seed={seed:#x} iter={i} a full queue admitted past the bound"
        )
        snap = metrics.snapshot()
        assert snap.shed_for(ShedReason.QUEUE_FULL) >= 1, (
            f"seed={seed:#x} iter={i} QUEUE_FULL shed not recorded"
        )
        assert snap.fail_open_total == 0, (
            f"seed={seed:#x} iter={i} fail_open_total was {snap.fail_open_total}, must be 0"
        )
        # The queue never exceeded its declared max.
        assert ctl.queue_report().depth("request") <= max_depth, (
            f"seed={seed:#x} iter={i} request depth exceeded max {max_depth}"
        )


def test_property10_undecidable_path_sheds_fail_closed() -> None:
    """A controller that raises mid-decision sheds (UNDECIDABLE); never admits undecided."""
    seed = 0x19_10B
    rng = random.Random(seed)

    class _Boom(AdmissionController):
        def _decide(self, owner_id: str, request_id: str, enqueued_at: float):  # type: ignore[override]
            raise RuntimeError("controller cannot reach a decision")

    for i in range(_ITERATIONS):
        box = {"t": rng.uniform(0.0, 10.0)}
        metrics = AdmissionMetrics()
        ctl = _Boom(
            bounds=_bounds(1_000),
            params=CoDelParams(),
            rng=random.Random(1),
            supervisor=WorkerSupervisor(),
            metrics=metrics,
            drain_window_s=1.0,
            clock=lambda: box["t"],
        )
        outcome = _run(ctl.admit("owner", "r", box["t"]))
        assert isinstance(outcome, ShedVerdict), (
            f"seed={seed:#x} iter={i} an undecidable path did not fail closed"
        )
        assert outcome.code == OVERLOAD_SHED
        assert outcome.should_retry is False
        snap = metrics.snapshot()
        assert snap.shed_for(ShedReason.UNDECIDABLE) == 1, (
            f"seed={seed:#x} iter={i} UNDECIDABLE shed not recorded"
        )
        assert snap.fail_open_total == 0, (
            f"seed={seed:#x} iter={i} fail_open_total was non-zero on a fail-closed path"
        )


def test_fail_open_total_stays_zero_across_an_overload_burst() -> None:
    """Across a mixed admit/shed burst the FAIL_OPEN counter is never incremented (Req 13.2)."""
    seed = 0x19_10C
    rng = random.Random(seed)
    box = {"t": 0.0}
    metrics = AdmissionMetrics()
    ctl = _controller(box, depth=16, metrics=metrics)
    for k in range(5_000):
        box["t"] += rng.uniform(0.0, 0.02)
        sojourn_ms = rng.uniform(0.0, 80.0)  # spans admit, backoff, and hard-cap regions
        _run(ctl.admit("owner", f"r{k}", box["t"] - sojourn_ms / 1000.0))
        if rng.random() < 0.5:
            ctl.complete(f"r{k}")
    snap = metrics.snapshot()
    assert snap.fail_open_total == 0, (
        f"seed={seed:#x} fail_open_total was {snap.fail_open_total} after an overload burst"
    )


def test_shed_verdict_carries_a_floored_jittered_retry_after() -> None:
    """A shed verdict from the controller honours the >= 1 s floor and never a 6-11 ms value."""
    box = {"t": 0.0}
    ctl = _controller(box, depth=1)
    _run(ctl.admit("owner", "fill", box["t"]))  # fill the single slot
    shed = _run(ctl.admit("owner", "r", box["t"]))
    assert isinstance(shed, ShedVerdict)
    assert shed.retry_after_s >= MIN_RETRY_AFTER_S
    assert not (0.006 <= shed.retry_after_s <= 0.011)
