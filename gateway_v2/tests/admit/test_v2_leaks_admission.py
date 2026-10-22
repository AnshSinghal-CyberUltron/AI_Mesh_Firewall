"""v2-enforcement-leaks Group D (admit): ``complete`` must be exact, idempotent and pruning.

Bug-condition exploration test (spec ``v2-enforcement-leaks``, task 7; design Property 1 (D) and
Property 10). Bug condition ``C_D``: ``complete(id)`` is called with an id that is not admitted
and unfinished, or admitted requests complete and their in-flight records are never removed.
The expected behaviour (2.11):

* ``complete`` for an unknown or already-completed id releases no slot and changes no queue
  depth;
* completed records are pruned, so ``len(_in_flight)`` is bounded by the admitted, unfinished
  requests (0 after every admit has been completed).

On the unfixed tree ``AdmissionController.complete`` pops the request-queue FIFO head whenever
the queue is non-empty, whatever id it is given, and only flags the in-flight record finished.
The failing cases carry a strict xfail naming the observed counterexample; they flip when the
Group D ``admit`` fix lands (task 30.2). The empty-queue case already holds and stays unmarked.
"""

from __future__ import annotations

import asyncio
import random

import pytest

from gateway_v2.admit.admission import AdmissionController
from gateway_v2.admit.codel import CoDelParams
from gateway_v2.admit.grant import Admitted
from gateway_v2.admit.metrics import AdmissionMetrics
from gateway_v2.admit.quota import AdmissionBounds
from gateway_v2.admit.supervisor import WorkerSupervisor

_DEPTH = 8
_OWNER = "v2-leaks-d-org"
_LEAK = "v2-enforcement-leaks D: "


def _controller() -> AdmissionController:
    """A controller over a fixed clock (sojourn 0, so CoDel always admits below the cap)."""
    bounds = AdmissionBounds(
        concurrency=_DEPTH,
        request_depth=_DEPTH,
        guard_depth=_DEPTH,
        dispatch_depth=_DEPTH,
        egress_depth=_DEPTH,
        audit_depth=_DEPTH,
    )
    return AdmissionController(
        bounds=bounds,
        params=CoDelParams(),
        rng=random.Random(0),
        supervisor=WorkerSupervisor(),
        metrics=AdmissionMetrics(),
        drain_window_s=1.0,
        clock=lambda: 0.0,
    )


def _admit(controller: AdmissionController, request_id: str) -> None:
    verdict = asyncio.run(controller.admit(_OWNER, request_id, 0.0))
    assert isinstance(verdict, Admitted), f"{request_id}: expected Admitted, got {verdict!r}"


def _depths(controller: AdmissionController) -> dict[str, int]:
    report = controller.queue_report(now=0.0)
    return {name: depth for name, (depth, _age) in report.per_queue.items()}


def test_complete_unknown_on_an_empty_queue_changes_nothing() -> None:
    """Baseline: with nothing admitted, ``complete("unknown")`` leaves every depth at 0.

    **Validates: Requirements 2.11**
    """
    controller = _controller()
    before = _depths(controller)
    controller.complete("unknown")
    assert _depths(controller) == before


@pytest.mark.xfail(
    strict=True,
    reason=_LEAK + "admit('a'), admit('b') -> request depth 2; complete('unknown') pops the "
    "FIFO head ('a') -> request depth 1, releasing a slot that belongs to an in-flight request",
)
def test_complete_unknown_id_leaves_every_queue_depth_unchanged() -> None:
    """``complete`` for an id that was never admitted releases no slot (Property 10).

    **Validates: Requirements 2.11**
    """
    controller = _controller()
    _admit(controller, "a")
    _admit(controller, "b")
    before = _depths(controller)
    assert before["request"] == 2
    controller.complete("unknown")
    after = _depths(controller)
    assert after == before, f"complete('unknown') changed queue depths: {before} -> {after}"


@pytest.mark.xfail(
    strict=True,
    reason=_LEAK + "admit('a'), admit('b'), complete('a') -> depth 1; a repeated "
    "complete('a') pops 'b' -> depth 0 (an already-completed id releases a foreign slot)",
)
def test_repeated_complete_releases_no_second_slot() -> None:
    """A second ``complete`` of an already-completed id is a no-op (Property 10).

    **Validates: Requirements 2.11**
    """
    controller = _controller()
    _admit(controller, "a")
    _admit(controller, "b")
    controller.complete("a")
    before = _depths(controller)
    assert before["request"] == 1
    controller.complete("a")
    after = _depths(controller)
    assert after == before, f"repeated complete('a') changed queue depths: {before} -> {after}"


@pytest.mark.xfail(
    strict=True,
    reason=_LEAK + "after 3 x request_depth (24) admit/complete pairs len(_in_flight) == 24: "
    "complete flags InFlightStream.finished and never deletes the record (unbounded growth)",
)
def test_completed_records_are_pruned_from_in_flight() -> None:
    """After N admit/complete pairs the in-flight table is empty (Property 10).

    N is ``3 x request_depth`` so the pairs also cycle the request queue past its bound.

    **Validates: Requirements 2.11**
    """
    controller = _controller()
    pairs = 3 * _DEPTH
    for index in range(pairs):
        request_id = f"req-{index}"
        _admit(controller, request_id)
        controller.complete(request_id)
    assert _depths(controller)["request"] == 0
    in_flight = len(controller._in_flight)
    assert in_flight == 0, f"after {pairs} admit/complete pairs len(_in_flight) == {in_flight}"
