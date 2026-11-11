"""B4 task 5.4 — ``Fake_Guard`` routing integration test (GW18, R5.4, R6.2).

EXAMPLE integration tests (plain asserts) proving the C43 guard ROUTING selection
(``GuardRouter.select``) + the guard-egress SEAM is exercisable END TO END against a
reachable ``Fake_Guard`` with NO real GW07/GW08 detector model present. The ``GuardRouter``
*properties* (min-queue-wait selection, determinism) live in ``test_b4_guard_routing.py`` as
seeded ``random.Random`` property tests; this module deliberately uses straight-line examples
to show the selection-then-dispatch seam works with the shipped fake harness.

What each example proves (design §Components §C5 "guard egress SEAM, not the detector call",
Testing Strategy "integration — ``Fake_Guard`` routing"):

* **select → dispatch end to end** — build a ``FakeGuardPool`` of >= 2 reachable guard
  destinations, feed a ``LoadSnapshot`` of ``GuardLoad``s with distinct ``owner_queue_wait_s``
  into a ``GuardRouter``, ``select`` the min-wait winner, then route that selected destination
  back through ``pool.dispatch(selected)`` and assert the matching ``FakeGuard`` recorded
  exactly one hit and NO other guard was hit. The routing selection + the egress seam work with
  a reachable fake guard and no detector model (R5.4, R6.2).
* **empty snapshot fails closed** — an empty ``LoadSnapshot`` makes ``GuardRouter.select`` raise
  ``ValueError`` (no silent default guard dialed) and NO ``FakeGuard`` is hit (R4.1, R5.3 via the
  seam; the detector-free seam never dispatches on a failed selection).

Test files are not under the import-linter layer contract, so importing the ``dispatch`` value
types and the ``_b4_fakes`` harness directly here is allowed. The selection is a pure synchronous
function and ``FakeGuard.dispatch`` is synchronous, so no event loop is needed.
"""

from __future__ import annotations

import pytest

from gateway_v2.dispatch.guard_routing import (
    GuardLoad,
    GuardRouter,
    LoadSnapshot,
)

from ._b4_fakes import FakeGuardPool

# The RC-measured ~20 ms C43 owner-queue bound, in seconds. In production this is passed in from
# ``contract.owner_queue_bound_s()`` at the ``edge`` wiring site; a fixed example value is honest
# here because the seam, not the bound derivation, is under test (the bound derivation is covered
# by ``tests/runtime/test_b4_resources.py``).
_OWNER_QUEUE_BOUND_S = 0.020


def _min_tie_break(candidates: tuple[str, ...]) -> str:
    """A deterministic choice-among-equals: the lexicographic-minimum destination.

    The examples below are constructed so the winner is unambiguous (distinct queue-waits), so
    this is never actually invoked; it is supplied only because ``GuardRouter`` requires an
    injected tie-break and it keeps the selection deterministic.
    """
    return min(candidates)


def test_select_then_dispatch_routes_to_min_wait_guard_only() -> None:
    """select → pool.dispatch reaches exactly the min-queue-wait guard, no other (R5.4, R6.2).

    Two reachable fake guards with distinct owner-queue waits: ``guard-b`` is clearly the
    min-wait winner. ``GuardRouter.select`` picks it, the selected destination is dispatched back
    through the pool, and the end-to-end seam is proven: the winner recorded exactly one hit and
    the loser recorded none — the routing picked exactly one reachable guard with no detector.
    """
    pool = FakeGuardPool(["guard-a", "guard-b"])
    # Both inside the owner-queue bound, so the min-wait rule (not the bound tier) decides;
    # guard-b's 5 ms beats guard-a's 15 ms.
    snapshot = LoadSnapshot(
        guards=(
            GuardLoad(destination="guard-a", owner_queue_wait_s=0.015, queue_depth=4),
            GuardLoad(destination="guard-b", owner_queue_wait_s=0.005, queue_depth=1),
        ),
    )
    router = GuardRouter(
        owner_queue_bound_s=_OWNER_QUEUE_BOUND_S,
        tie_break=_min_tie_break,
    )

    selected = router.select(snapshot)
    assert selected == "guard-b"

    # Route the selected destination back through the guard-egress seam.
    dispatched = pool.dispatch(selected)

    assert dispatched is pool.get("guard-b")
    assert pool.get("guard-b").hits == 1
    assert pool.get("guard-b").destination == selected
    # Routing picked EXACTLY one guard — the loser was never hit.
    assert pool.get("guard-a").hits == 0


def test_select_dispatch_seam_carries_the_payload() -> None:
    """The selected destination reaches its FakeGuard carrying the seam payload (R6.2).

    Confirms the egress seam hands the guard whatever payload the caller passes (the fake records
    it verbatim) — the seam is exercisable with a reachable fake guard and no detector model.
    """
    pool = FakeGuardPool(["guard-x", "guard-y", "guard-z"])
    snapshot = LoadSnapshot(
        guards=(
            GuardLoad(destination="guard-x", owner_queue_wait_s=0.030, queue_depth=9),
            GuardLoad(destination="guard-y", owner_queue_wait_s=0.004, queue_depth=0),
            GuardLoad(destination="guard-z", owner_queue_wait_s=0.012, queue_depth=3),
        ),
    )
    router = GuardRouter(
        owner_queue_bound_s=_OWNER_QUEUE_BOUND_S,
        tie_break=_min_tie_break,
    )

    selected = router.select(snapshot)
    # guard-y has the minimum owner-queue wait and is within the bound.
    assert selected == "guard-y"

    payload = {"prompt": "scan me", "channels": ("content",)}
    guard = pool.dispatch(selected, payload)

    assert guard.last_payload == payload
    assert guard.hits == 1
    # No other guard in the pool was touched.
    assert pool.get("guard-x").hits == 0
    assert pool.get("guard-z").hits == 0


def test_empty_snapshot_fails_closed_and_dispatches_no_guard() -> None:
    """An empty candidate pool → ValueError, and NO FakeGuard is dispatched (R4.1, R5.3).

    The seam only ever dispatches a destination that ``select`` returned; a failed (fail-closed)
    selection raises before any dispatch, so no reachable guard is hit — routing never dials an
    unverified default endpoint.
    """
    pool = FakeGuardPool(["guard-a", "guard-b"])
    router = GuardRouter(
        owner_queue_bound_s=_OWNER_QUEUE_BOUND_S,
        tie_break=_min_tie_break,
    )

    with pytest.raises(ValueError, match="empty candidate guard pool"):
        router.select(LoadSnapshot(guards=()))

    # Fail-closed selection means the egress seam was never reached.
    assert all(pool.get(dest).hits == 0 for dest in pool.destinations)
