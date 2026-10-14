"""C43 load-aware guard routing — the `GuardRouter` sibling selector (GW18, R4).

`DispatchRouter` (`gateway_v2/dispatch/routing.py`, left UNCHANGED — R4.5) is a pure
plan-identity→destination function with no input but the plan. Guard routing is a DISTINCT
selection concern: C43 "load-aware guard routing" picks one member of the self-hosted guard pool
to minimize queue-wait, so it *must* read per-guard load state. The purity tension is resolved by
INJECTION — the load state arrives as a `Load_Snapshot` value, never read from a wall-clock or an
RNG — so `select` stays a deterministic total function of its inputs (R4.2, R4.3) and the shipped
`DispatchRouter` purity property is untouched.

This reproduces the measured C43 behaviour: routing across the guard pool to minimize queue-wait
(runbook queue-wait ratio 0.16–0.20 with routing on vs 0.48–0.50 off; "190 per guard" at a ~20 ms
owner-queue bound). The owner-queue bound (`RV_OWNER_QUEUE_MS`, R4.4) is PASSED IN as
`owner_queue_bound_s` from `contract.owner_queue_bound_s()` at the `edge` wiring site, so this
`dispatch` module holds no capacity literal.

Deferred seam (GW20 / GW20b), R8.3. The FLEET-SCALE C43 capacity measurement — the measured
760 RPS at 190 requests/guard, and the queue-wait ratio AT FLEET SCALE (3 gateways × 4 guards) —
is a deferred live gate (runbook R2-16, R2-21), NOT scaffolded here. B4 reproduces the LOCAL
routing SELECTION logic only (min-queue-wait selection over an injected `Load_Snapshot`); the
fleet-scale capacity number is cloud and is proven by GW20 / GW20b, not by this module.

Deferred seam (GW25). The real per-guard load signal — live queue-depth / owner-queue-wait read
from the GPU guard fleet over the split-topology TCP path — is the deferred live half; it is NOT
scaffolded here. The `Load_Snapshot` consumed by `select` is INJECTED: locally a `Fake_Guard`
harness or a test supplies a fabricated reading; in production a signal reader (GW25) supplies the
real one. `GuardRouter` is indifferent to the source — it selects over whatever snapshot it is
given.

Layer. `dispatch` sits below `plan`/`detect`/`resolve`/`admit`/`edge` in the import-linter layers
contract, so this module imports neither of those; it needs only the standard library.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

__all__: tuple[str, ...] = (
    "GuardLoad",
    "GuardRouter",
    "LoadSnapshot",
    "Load_Snapshot",
)


@dataclass(frozen=True, slots=True)
class GuardLoad:
    """One candidate guard's injected, point-in-time load reading.

    `owner_queue_wait_s` is the measured owner-queue wait for this guard in seconds (the C43
    signal the router minimizes); `queue_depth` is the number of items queued ahead, used only as
    a secondary discriminator. Both are injected (see the module docstring's GW25 seam), never
    read from a clock here.
    """

    destination: str
    owner_queue_wait_s: float
    queue_depth: int


@dataclass(frozen=True, slots=True)
class LoadSnapshot:
    """A point-in-time, injected reading over all candidate guards.

    The design names this value type ``Load_Snapshot``; the Python class-naming convention is
    CapWords, so the canonical class is ``LoadSnapshot`` and ``Load_Snapshot`` is kept as an alias
    (defined at module scope below) so the design spelling resolves to the same type. Choosing
    CapWords keeps the name clean under a pep8-naming (N801) check should one ever be enabled,
    while the alias preserves the design-document reference.
    """

    guards: tuple[GuardLoad, ...]


# Design-spelling alias: the design, the requirements glossary, and the Data Models table all name
# this type ``Load_Snapshot``. It is the SAME class as ``LoadSnapshot`` (CapWords, above); callers
# may use either spelling and both are exported.
Load_Snapshot = LoadSnapshot


# The effective-queue-wait sort key for one candidate. Ordering is lexicographic over, in order:
#   1. `over_bound`  — a bool (False < True), so a guard whose owner-queue wait is WITHIN the
#      `owner_queue_bound_s` bound sorts strictly before one that EXCEEDS it. This is the C43
#      eligibility/preference tier (R4.4): the bound gates eligibility, it does not replace the
#      min-queue-wait rule.
#   2. `owner_queue_wait_s` — the primary C43 signal; smaller is better (R4.1).
#   3. `queue_depth` — a secondary discriminator when the owner-queue wait ties; smaller is better.
# `select` picks the candidate with the minimum key; a genuine tie on the full key (bound tier,
# wait, AND depth all equal) is the only "choice among equals," resolved by the injected tie-break.
_EffectiveWait = tuple[bool, float, int]


class GuardRouter:
    """C43 load-aware selector over an injected per-guard `Load_Snapshot` (R4).

    A pure function of its inputs: `select(snapshot)` reads no wall-clock and no `random`. The one
    source of choice-among-equals is the injected `tie_break`, so the selection is deterministic
    given the snapshot, the owner-queue bound, and that tie-break (R4.2, R4.3).
    """

    __slots__ = ("_owner_queue_bound_s", "_tie_break")

    def __init__(
        self,
        *,
        owner_queue_bound_s: float,
        tie_break: Callable[[tuple[str, ...]], str],
    ) -> None:
        """Construct the selector.

        `owner_queue_bound_s` is the `RV_OWNER_QUEUE_MS` owner-queue bound in seconds, PASSED IN
        from `contract.owner_queue_bound_s()` at the `edge` wiring site (R4.4) — no capacity
        literal lives in this module. `tie_break` maps the destinations of the equally-loaded
        candidates to the chosen destination; it is the ONLY source of choice-among-equals and is
        injected, so determinism-given-inputs holds (R4.3).
        """
        self._owner_queue_bound_s = owner_queue_bound_s
        self._tie_break = tie_break

    def select(self, snapshot: LoadSnapshot) -> str:
        """Return the destination of the minimum-effective-queue-wait guard (R4.1).

        Effective queue-wait is defined by `_effective_wait`: a guard within the owner-queue bound
        is preferred over one that exceeds it, then the smaller `owner_queue_wait_s`, then the
        smaller `queue_depth`. The candidate with the minimum key wins. When two or more candidates
        share the identical minimum key (equally loaded), the injected `tie_break` chooses among
        their destinations — the only source of choice-among-equals (R4.2, R4.3).

        Fails CLOSED on an empty candidate pool: a `ValueError` is raised rather than returning a
        silent default guard, so routing never dials an unverified endpoint (R4.1, R5.3).
        """
        guards = snapshot.guards
        if not guards:
            raise ValueError(
                "GuardRouter.select: empty candidate guard pool; "
                "failing closed rather than selecting a default guard",
            )
        best_key = min(self._effective_wait(guard) for guard in guards)
        winners = tuple(
            guard.destination
            for guard in guards
            if self._effective_wait(guard) == best_key
        )
        if len(winners) == 1:
            return winners[0]
        return self._tie_break(winners)

    def _effective_wait(self, guard: GuardLoad) -> _EffectiveWait:
        """The lexicographic effective-queue-wait key for one candidate (see `_EffectiveWait`)."""
        over_bound = guard.owner_queue_wait_s > self._owner_queue_bound_s
        return (over_bound, guard.owner_queue_wait_s, guard.queue_depth)
