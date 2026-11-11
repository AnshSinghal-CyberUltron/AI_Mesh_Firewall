"""B4 tasks 2.2 + 2.3 — property tests for the C43 load-aware ``GuardRouter`` (GW18, R4).

This single module owns BOTH guard-routing properties (the design groups them on the one
selector and the ``tasks.md`` shared-file note assigns §2.2 and §2.3 to the same file):

* **Property 3** — ``GuardRouter.select`` returns the destination of a guard whose *effective*
  queue-wait equals the minimum across all injected candidates, with a guard WITHIN the
  ``owner_queue_bound_s`` bound preferred over one that EXCEEDS it, then min ``owner_queue_wait_s``,
  then min ``queue_depth``. An empty candidate pool fails closed with ``ValueError``.
* **Property 4** — ``select`` is deterministic given its inputs: for a fixed injected
  ``tie_break`` two successive calls return the identical destination, no wall-clock or randomness
  is read, and when ``>= 2`` candidates share the identical effective-wait key the injected
  ``tie_break`` is the ONLY source of choice-among-equals (a different ``tie_break`` can change the
  winner).

Both use the house idiom: a seeded ``random.Random`` driven for ``>= 10,000`` iterations, NO
``hypothesis``. The selection is a pure synchronous function, so no event loop is needed.

The expected minimum effective-wait key is computed INDEPENDENTLY in the test (``_expected_key``
below) — it does not call the router's private ``_effective_wait`` — so the test is an independent
oracle for the production key, not a tautology.

Test files are not under the import-linter layer contract, so importing the ``dispatch`` value
types directly here is allowed.
"""

from __future__ import annotations

import random

from gateway_v2.dispatch.guard_routing import (
    GuardLoad,
    GuardRouter,
    Load_Snapshot,
    LoadSnapshot,
)

_ITERATIONS = 10_000

# Independent oracle for one candidate's effective-wait key. Mirrors the design's documented
# ordering — (over_bound, owner_queue_wait_s, queue_depth) — WITHOUT touching the router's private
# key method, so the two implementations are cross-checked rather than self-referential.
_ExpectedKey = tuple[bool, float, int]


def _expected_key(guard: GuardLoad, owner_queue_bound_s: float) -> _ExpectedKey:
    """Recompute the effective-wait key independently of ``GuardRouter._effective_wait``."""
    over_bound = guard.owner_queue_wait_s > owner_queue_bound_s
    return (over_bound, guard.owner_queue_wait_s, guard.queue_depth)


def _fixed_tie_break(candidates: tuple[str, ...]) -> str:
    """A deterministic choice-among-equals: the lexicographic minimum destination."""
    return min(candidates)


def _random_bound(rng: random.Random) -> float:
    """A positive owner-queue bound in seconds, around the RC-measured ~20 ms C43 default."""
    return round(rng.uniform(0.001, 0.100), 4)


def _random_guard(rng: random.Random, index: int, bound: float) -> GuardLoad:
    """One guard whose owner-queue wait deliberately STRADDLES the bound.

    Drawing the wait from a window that spans ``bound`` on both sides guarantees the property
    exercises the bound-preference tier (some candidates within, some exceeding) rather than only
    the plain min-wait path. ``destination`` is unique per index so winners are identifiable.
    """
    kind = rng.choice(("under", "at", "over", "wide"))
    if kind == "under":
        wait = round(rng.uniform(0.0, bound), 6)
    elif kind == "at":
        wait = bound
    elif kind == "over":
        wait = round(rng.uniform(bound, bound + 0.100), 6)
    else:  # wide — anywhere in a broad window straddling the bound
        wait = round(rng.uniform(0.0, bound * 2.0 + 0.050), 6)
    return GuardLoad(
        destination=f"guard-{index}",
        owner_queue_wait_s=wait,
        queue_depth=rng.randint(0, 50),
    )


def _random_snapshot(rng: random.Random, bound: float) -> LoadSnapshot:
    """A non-empty snapshot of 1..8 guards with unique destinations."""
    count = rng.randint(1, 8)
    guards = tuple(_random_guard(rng, i, bound) for i in range(count))
    return LoadSnapshot(guards=guards)


# ===========================================================================
# Property 3 — minimum-queue-wait selection (bound-aware)
# ===========================================================================

# Feature: provider-egress-gw-b4, Property 3: GuardRouter selects a minimum-queue-wait guard (bound-aware)  # noqa: E501
# Validates: Requirements 4.1, 4.4

_SEED_P3 = 0x0B4_0032


def test_property_guard_router_selects_minimum_queue_wait() -> None:
    """The returned destination belongs to a guard whose effective-wait key is the minimum.

    Over random non-empty snapshots (random guard counts, waits straddling the bound, random
    depths, random bound): the chosen guard's INDEPENDENTLY-computed effective-wait key equals the
    minimum key across all candidates. This proves the bound-preference tier (within-bound < over-
    bound), then min ``owner_queue_wait_s``, then min ``queue_depth`` — because the oracle key is
    exactly that lexicographic tuple.
    """
    rng = random.Random(_SEED_P3)
    for i in range(_ITERATIONS):
        bound = _random_bound(rng)
        snapshot = _random_snapshot(rng, bound)
        router = GuardRouter(owner_queue_bound_s=bound, tie_break=_fixed_tie_break)

        chosen = router.select(snapshot)

        keyed = {g.destination: _expected_key(g, bound) for g in snapshot.guards}
        min_key = min(keyed.values())
        ctx = f"seed={_SEED_P3:#x} iter={i} bound={bound} chosen={chosen!r}"

        # The chosen destination exists and its own effective-wait key is the global minimum.
        assert chosen in keyed, ctx
        assert keyed[chosen] == min_key, ctx

        # A within-bound guard is NEVER passed over for an over-bound one: if any candidate is
        # within the bound, the winner must be within the bound too (over_bound component False).
        any_within = any(not key[0] for key in keyed.values())
        if any_within:
            assert keyed[chosen][0] is False, ctx


def test_property_guard_router_empty_pool_raises_value_error() -> None:
    """An empty candidate pool fails closed with ``ValueError`` (R4.1, R5.3), for both spellings."""
    router = GuardRouter(owner_queue_bound_s=0.020, tie_break=_fixed_tie_break)
    for empty in (LoadSnapshot(guards=()), Load_Snapshot(guards=())):
        raised = False
        try:
            router.select(empty)
        except ValueError:
            raised = True
        assert raised, "GuardRouter.select must raise ValueError on an empty guard pool"


# ===========================================================================
# Property 4 — determinism given inputs
# ===========================================================================

# Feature: provider-egress-gw-b4, Property 4: GuardRouter is deterministic given its inputs
# Validates: Requirements 4.2, 4.3

_SEED_P4 = 0x0B4_0042


def test_property_guard_router_is_deterministic_given_inputs() -> None:
    """Two successive ``select`` calls on equal inputs with a FIXED tie-break are identical.

    No wall-clock and no randomness is read inside ``select`` (the only injected non-input source,
    ``tie_break``, is held fixed), so repeated evaluation on the same snapshot is always equal.
    """
    rng = random.Random(_SEED_P4)
    for i in range(_ITERATIONS):
        bound = _random_bound(rng)
        snapshot = _random_snapshot(rng, bound)
        router = GuardRouter(owner_queue_bound_s=bound, tie_break=_fixed_tie_break)

        first = router.select(snapshot)
        second = router.select(snapshot)

        ctx = f"seed={_SEED_P4:#x} iter={i} bound={bound}"
        assert first == second, ctx


def _equal_load_snapshot(rng: random.Random, bound: float) -> LoadSnapshot:
    """A snapshot where >= 2 guards share the IDENTICAL effective-wait key (an equal-load tie).

    All tied guards get the same wait and depth so the full key — (over_bound, wait, depth) — is
    identical; only the destination differs. The tie is therefore resolvable ONLY by the injected
    ``tie_break``. Extra non-tied guards with strictly WORSE keys are added so the tied set is the
    unique minimum and the winner is one of the tied destinations.
    """
    tie_count = rng.randint(2, 5)
    # A within-bound shared key so the tied set is the minimum (over_bound False beats any over).
    shared_wait = round(rng.uniform(0.0, bound), 6)
    shared_depth = rng.randint(0, 10)
    tied = tuple(
        GuardLoad(
            destination=f"tied-{i}",
            owner_queue_wait_s=shared_wait,
            queue_depth=shared_depth,
        )
        for i in range(tie_count)
    )
    # Strictly-worse decoys: over the bound, so their key sorts after the within-bound tied set.
    decoy_count = rng.randint(0, 3)
    decoys = tuple(
        GuardLoad(
            destination=f"decoy-{j}",
            owner_queue_wait_s=round(rng.uniform(bound + 0.001, bound + 0.100), 6),
            queue_depth=rng.randint(0, 50),
        )
        for j in range(decoy_count)
    )
    guards = list(tied + decoys)
    rng.shuffle(guards)
    return LoadSnapshot(guards=tuple(guards))


def test_property_guard_router_tie_break_is_the_only_choice_among_equals() -> None:
    """On an equal-load tie the injected ``tie_break`` decides, deterministically and solely.

    For each generated equal-load snapshot:
      * a ``min``-tie-break router returns the tied destination that is the lexicographic minimum,
        and returns it identically on a repeat call (deterministic);
      * a ``max``-tie-break router returns the lexicographic maximum tied destination;
      * when ``>= 2`` destinations are genuinely tied the two tie-breaks DISAGREE — proving the
        tie-break is the only source of choice-among-equals (the router does not otherwise pick).
    """
    rng = random.Random(_SEED_P4 ^ 0xFFFF)
    for i in range(_ITERATIONS):
        bound = _random_bound(rng)
        snapshot = _equal_load_snapshot(rng, bound)

        router_min = GuardRouter(owner_queue_bound_s=bound, tie_break=lambda c: min(c))
        router_max = GuardRouter(owner_queue_bound_s=bound, tie_break=lambda c: max(c))

        chosen_min = router_min.select(snapshot)
        chosen_max = router_max.select(snapshot)
        ctx = f"seed={_SEED_P4 ^ 0xFFFF:#x} iter={i} bound={bound}"

        # The tied destinations are exactly those sharing the minimum effective-wait key.
        keyed = {g.destination: _expected_key(g, bound) for g in snapshot.guards}
        min_key = min(keyed.values())
        tied_dests = tuple(sorted(d for d, k in keyed.items() if k == min_key))
        assert len(tied_dests) >= 2, ctx  # the generator guarantees a genuine tie

        # Each injected tie-break's documented choice is returned, deterministically on repeat.
        assert chosen_min == tied_dests[0], ctx
        assert chosen_min == router_min.select(snapshot), ctx
        assert chosen_max == tied_dests[-1], ctx
        assert chosen_max == router_max.select(snapshot), ctx

        # Different tie-breaks change the winner → tie-break is the ONLY choice-among-equals.
        assert chosen_min != chosen_max, ctx
