"""v2-enforcement-leaks Group D fix-checking property test (design **Property 10**).

Task 23.5 (this file): the idempotent/exact/pruning ``complete`` property, design **Property 10**
(Bug Condition D), clause 2.11. The Group D ``admit`` fix (task 23.2) made
:meth:`gateway_v2.admit.admission.AdmissionController.complete` slot-exact, idempotent and pruning:
it keeps a ``self._admitted_counts: dict[str, int]`` refcount alongside ``_in_flight``, ``_decide``
increments the count on each admit, and ``complete(request_id)`` releases at most ONE request-queue
slot per prior unmatched admit via :meth:`BoundedQueue.remove` (NOT the FIFO head), decrements the
refcount, and prunes both ``_admitted_counts[id]`` and ``_in_flight[id]`` when the count hits 0. An
unknown or already-completed id is a no-op (touches no queue, publishes nothing).

Property 10 (verbatim, design.md). For any interleaving of ``admit`` and ``complete`` calls,
including unknown, repeated and colliding ids, ``complete`` SHALL release at most one slot per
prior unmatched admit of that id and SHALL release none otherwise. ``len(_in_flight)`` SHALL be at
most the number of admitted, unfinished ids.

What the property exercises, over >= 10,000 seeded iterations (seeded ``random.Random``, no
hypothesis, the seed in every assertion message): a random sequence of ``admit`` / ``complete``
operations over a SMALL id alphabet, so ``admit`` / ``complete`` hit:

* **unknown** ids -- ``complete`` of an id never admitted;
* **repeated** ids -- an id admitted then completed more than once;
* **colliding** ids -- the SAME id admitted twice concurrently (the content-hash collision case),
  which occupies two request-queue slots and needs two ``complete`` calls to fully release.

An independent reference oracle -- a ``dict[str, int]`` of outstanding (admitted-but-not-completed)
counts per id -- tracks reality. An ``admit`` that returns :class:`Admitted` increments the id's
count; a :class:`ShedVerdict` (CoDel / queue-full) does NOT. A ``complete(id)`` with a positive
count decrements it (a slot is released); with a zero count it is a no-op (release nothing). After
every step the test asserts the two invariants Property 10 names:

1. the request-queue depth equals ``sum(oracle.values())`` (total outstanding admits) -- so a
   ``complete`` of a zero-count id changes no depth, and a ``complete`` of a positive-count id
   drops the depth by exactly one;
2. ``len(_in_flight)`` equals the number of distinct ids currently outstanding (count > 0) --
   pruned ids are gone. This test never calls ``register_stream``, so ``_in_flight`` is exactly the
   outstanding-admit id set.

The admit path SHEDS when the request queue is full (depth == bound) -- both admits and sheds occur
across the run, and the oracle tracks reality either way by branching on the verdict type.

The accompanying unit tests pin the task's named cases directly: :meth:`BoundedQueue.remove`
index-alignment of the depth and age deques, and ``complete`` with unknown, repeated and colliding
ids on a real controller.

Reading ``controller._in_flight`` and ``controller._admitted_counts`` is deliberate and minimal:
the shipped exploration test (``test_v2_leaks_admission.py``) already reads ``_in_flight``, and
Property 10 is stated over ``_in_flight``; the pruning invariant cannot be asserted without it.
"""

from __future__ import annotations

import asyncio
import random

from gateway_v2.admit.admission import AdmissionController
from gateway_v2.admit.codel import CoDelParams
from gateway_v2.admit.grant import Admitted, ShedVerdict
from gateway_v2.admit.metrics import AdmissionMetrics
from gateway_v2.admit.queues import BoundedQueue
from gateway_v2.admit.quota import AdmissionBounds
from gateway_v2.admit.supervisor import WorkerSupervisor

_OWNER = "v2-leaks-d-org"

_SEED = 0x23D5  # v2-enforcement-leaks task 23.5 (Property 10)
_ITERS = 10_000

# A SMALL id alphabet, so a random admit/complete sequence reliably hits unknown ids (completed
# before ever admitted), repeated ids (admitted, then completed more than once) and colliding ids
# (the same id admitted twice concurrently -- the content-hash collision case).
_IDS = ("a", "b", "c", "d")

# A deliberately SMALL request-queue bound, so the queue fills and the admit path sheds across the
# run while still admitting plenty. Four distinct ids each admissible twice = up to eight
# concurrent slots against a bound of three forces both outcomes.
_DEPTH = 3


def _controller(seed: int, *, depth: int = _DEPTH) -> AdmissionController:
    """A controller over a fixed clock (sojourn 0, so CoDel admits below the hard cap).

    Built exactly as ``test_v2_leaks_admission.py::_controller`` does -- all-equal depth bounds,
    default :class:`CoDelParams`, a seeded RNG, a real :class:`WorkerSupervisor` /
    :class:`AdmissionMetrics`, a 1 s drain window and a frozen ``clock=lambda: 0.0`` -- except the
    request-queue bound is parameterised so the property run can keep it small (to exercise sheds).
    """
    bounds = AdmissionBounds(
        concurrency=depth,
        request_depth=depth,
        guard_depth=depth,
        dispatch_depth=depth,
        egress_depth=depth,
        audit_depth=depth,
    )
    return AdmissionController(
        bounds=bounds,
        params=CoDelParams(),
        rng=random.Random(seed),
        supervisor=WorkerSupervisor(),
        metrics=AdmissionMetrics(),
        drain_window_s=1.0,
        clock=lambda: 0.0,
    )


def _request_depth(controller: AdmissionController) -> int:
    """The request queue's current depth, read through the public ``queue_report``."""
    report = controller.queue_report(now=0.0)
    depth, _age = report.per_queue["request"]
    return depth


def _outstanding_ids(oracle: dict[str, int]) -> int:
    """The number of distinct ids with a positive outstanding count."""
    return sum(1 for count in oracle.values() if count > 0)


# --------------------------------------------------------------------------- #
# Property 10: idempotent, exact, pruning ``complete``.
# --------------------------------------------------------------------------- #


def test_property10_complete_is_idempotent_exact_and_pruning() -> None:
    """**Validates: Requirements 2.11**

    Over 10,000 seeded interleavings of ``admit`` and ``complete`` against a real
    :class:`AdmissionController`, with a small id alphabet so unknown, repeated and colliding ids
    all occur:

    * an ``admit`` that returns :class:`Admitted` increments the reference oracle for its id; a
      :class:`ShedVerdict` does not;
    * a ``complete`` of a positive-count id decrements the oracle (one slot released); a
      ``complete`` of a zero-count id is a no-op;
    * after EVERY step the request-queue depth equals ``sum(oracle.values())`` and
      ``len(_in_flight)`` equals the number of distinct outstanding ids.

    Coverage counters assert the run actually exercised admits, sheds, unknown-id completes,
    repeated completes, and colliding (two concurrent admits of one id) completes, so a green
    result is not a vacuous pass.
    """
    rng = random.Random(_SEED)
    controller = _controller(_SEED)
    # The reference oracle: outstanding (admitted-but-not-completed) count per id.
    oracle: dict[str, int] = {}

    admitted_events = 0
    shed_events = 0
    complete_release = 0  # complete of a positive-count id (a real slot release)
    complete_noop = 0  # complete of a zero-count id (unknown or already-completed)
    collide_releases = 0  # a release of an id whose outstanding count was >= 2 (collision)

    for i in range(_ITERS):
        request_id = rng.choice(_IDS)
        op = rng.choice(("admit", "complete"))

        if op == "admit":
            verdict = asyncio.run(controller.admit(_OWNER, request_id, 0.0))
            if isinstance(verdict, Admitted):
                admitted_events += 1
                oracle[request_id] = oracle.get(request_id, 0) + 1
            else:
                assert isinstance(verdict, ShedVerdict), (
                    f"seed={_SEED} iter={i} op=admit id={request_id!r} "
                    f"expected Admitted|ShedVerdict, got {verdict!r}"
                )
                shed_events += 1
        else:  # complete
            depth_before = _request_depth(controller)
            outstanding_before = oracle.get(request_id, 0)
            controller.complete(request_id)
            depth_after = _request_depth(controller)

            if outstanding_before > 0:
                complete_release += 1
                if outstanding_before >= 2:
                    collide_releases += 1
                oracle[request_id] = outstanding_before - 1
                # Invariant 1 (release side): a positive-count complete drops the depth by one.
                assert depth_after == depth_before - 1, (
                    f"seed={_SEED} iter={i} complete id={request_id!r} outstanding="
                    f"{outstanding_before} depth {depth_before}->{depth_after}, expected -1"
                )
            else:
                complete_noop += 1
                # Invariant 1 (no-op side): a zero-count complete changes no depth.
                assert depth_after == depth_before, (
                    f"seed={_SEED} iter={i} complete id={request_id!r} outstanding=0 "
                    f"depth {depth_before}->{depth_after}, expected unchanged"
                )

        # Invariant 1: the request-queue depth equals the total outstanding admits.
        expected_depth = sum(oracle.values())
        actual_depth = _request_depth(controller)
        assert actual_depth == expected_depth, (
            f"seed={_SEED} iter={i} op={op} id={request_id!r} "
            f"request depth {actual_depth} != sum(oracle)={expected_depth} oracle={oracle!r}"
        )

        # Invariant 2: _in_flight holds exactly the distinct outstanding ids (pruned ids are gone).
        # No register_stream is ever called, so _in_flight is exactly the outstanding-admit id set.
        distinct_outstanding = _outstanding_ids(oracle)
        in_flight = len(controller._in_flight)
        assert in_flight == distinct_outstanding, (
            f"seed={_SEED} iter={i} op={op} id={request_id!r} "
            f"len(_in_flight)={in_flight} != distinct outstanding={distinct_outstanding} "
            f"oracle={oracle!r}"
        )
        # _admitted_counts mirrors the oracle's positive entries exactly (pruned at zero).
        positive_oracle = {rid: c for rid, c in oracle.items() if c > 0}
        assert controller._admitted_counts == positive_oracle, (
            f"seed={_SEED} iter={i} _admitted_counts={controller._admitted_counts!r} "
            f"!= positive oracle={positive_oracle!r}"
        )

    cov = (
        f"seed={_SEED} admitted={admitted_events} shed={shed_events} "
        f"complete_release={complete_release} complete_noop={complete_noop} "
        f"collide_releases={collide_releases}"
    )
    assert admitted_events > 0, cov
    assert shed_events > 0, cov
    assert complete_release > 0, cov
    assert complete_noop > 0, cov
    assert collide_releases > 0, cov


# --------------------------------------------------------------------------- #
# Unit tests: BoundedQueue.remove index-alignment, and complete's named cases.
# --------------------------------------------------------------------------- #


def test_bounded_queue_remove_keeps_depth_and_age_deques_aligned() -> None:
    """``BoundedQueue.remove`` drops one match and keeps ``_items``/``_enqueued_at`` aligned.

    Offers four distinct items at four known, increasing instants (a settable clock advances
    between offers so each age entry differs), then:

    * removing a middle item drops the depth by one;
    * ``remove`` of an absent item returns ``False`` and mutates nothing;
    * removing the FIRST item makes ``oldest_age_s`` read the SECOND item's enqueue instant --
      proving the aligned age entry was removed with the item, not a mismatched one;
    * removing one of two equal items leaves exactly one (the content-hash collision case).

    **Validates: Requirements 2.11**
    """
    now = {"t": 0.0}
    queue: BoundedQueue = BoundedQueue(8, clock=lambda: now["t"])

    # Offer four distinct items at t = 10, 20, 30, 40.
    for item, instant in (("x", 10.0), ("y", 20.0), ("z", 30.0), ("w", 40.0)):
        now["t"] = instant
        assert queue.offer(item) is True
    assert queue.depth == 4

    # (b) remove of an absent item returns False and mutates nothing.
    assert queue.remove("absent") is False
    assert queue.depth == 4
    # Oldest is still "x" at t=10: at the read clock 50.0 its age is 40.0.
    assert queue.oldest_age_s(now=50.0) == 40.0

    # (a) remove a MIDDLE item ("y" at t=20): depth drops by one, oldest unchanged ("x" at 10).
    assert queue.remove("y") is True
    assert queue.depth == 3
    assert queue.oldest_age_s(now=50.0) == 40.0  # still "x" at t=10

    # (c) remove the FIRST item ("x" at t=10): oldest age now reflects the SECOND survivor ("z" at
    # t=30 -- "y" was already removed), proving the age entry stayed index-aligned with its item.
    assert queue.remove("x") is True
    assert queue.depth == 2
    assert queue.oldest_age_s(now=50.0) == 20.0  # "z" enqueued at t=30 -> age 20 at 50

    # (d) two EQUAL items in two slots: remove one, exactly one remains (collision case).
    now["t"] = 60.0
    assert queue.offer("dup") is True
    now["t"] = 70.0
    assert queue.offer("dup") is True
    depth_before = queue.depth
    assert queue.remove("dup") is True
    assert queue.depth == depth_before - 1
    # One "dup" still present: a second remove still finds it.
    assert queue.remove("dup") is True
    assert queue.depth == depth_before - 2
    # No third occurrence.
    assert queue.remove("dup") is False


def test_complete_with_unknown_repeated_and_colliding_ids() -> None:
    """``complete`` is slot-exact for unknown, repeated and colliding ids on a real controller.

    * an unknown id on a populated queue changes no depth and no ``_in_flight`` entry;
    * a repeated ``complete`` of an already-completed id is a no-op;
    * a colliding id admitted twice needs two ``complete`` calls to fully release -- after the
      first the depth drops by one and one slot remains, after the second it reaches the baseline
      and ``_in_flight`` no longer holds the id.

    **Validates: Requirements 2.11**
    """
    controller = _controller(_SEED, depth=8)

    def _admit(request_id: str) -> None:
        verdict = asyncio.run(controller.admit(_OWNER, request_id, 0.0))
        assert isinstance(verdict, Admitted), f"{request_id}: expected Admitted, got {verdict!r}"

    # Populate the queue with two distinct ids and one colliding id admitted twice.
    _admit("p")
    _admit("q")
    _admit("dup")
    _admit("dup")  # content-hash collision: same id, a second slot
    assert _request_depth(controller) == 4
    assert controller._admitted_counts == {"p": 1, "q": 1, "dup": 2}
    assert set(controller._in_flight) == {"p", "q", "dup"}

    # Unknown id on a populated queue: no slot released, nothing touched.
    before = _request_depth(controller)
    controller.complete("never-admitted")
    assert _request_depth(controller) == before
    assert set(controller._in_flight) == {"p", "q", "dup"}
    assert controller._admitted_counts == {"p": 1, "q": 1, "dup": 2}

    # Complete "p" once, then a repeated complete of the now-finished id is a no-op.
    controller.complete("p")
    after_p = _request_depth(controller)
    assert after_p == before - 1
    assert "p" not in controller._in_flight
    assert "p" not in controller._admitted_counts
    controller.complete("p")  # already completed: releases nothing
    assert _request_depth(controller) == after_p
    assert "p" not in controller._in_flight

    # The colliding id needs two completes. First complete: depth -1, one slot and the record stay.
    depth_before_dup = _request_depth(controller)
    controller.complete("dup")
    assert _request_depth(controller) == depth_before_dup - 1
    assert controller._admitted_counts["dup"] == 1
    assert "dup" in controller._in_flight  # one slot still outstanding
    # Second complete: reaches the baseline and the record is pruned.
    controller.complete("dup")
    assert _request_depth(controller) == depth_before_dup - 2
    assert "dup" not in controller._admitted_counts
    assert "dup" not in controller._in_flight
