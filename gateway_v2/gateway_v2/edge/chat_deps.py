"""GW15 Chat_Surface dependency surfaces + fail-closed defaults (split from ``edge/routes.py``).

The §10.4 request lifecycle (``edge/openai_chat.py::ChatSurface``) couples only to the narrow
``.admit`` / ``.evaluate`` SURFACES of the shipped GW19 ``AdmissionController`` and GW06
``QuotaComponent``, to a findings-producing input detector, and to a served plan-snapshot
source. This sibling module owns those narrow ``Protocol``s + the fail-closed DEFAULT
implementations the frozen :class:`~gateway_v2.edge.routes.ChatRoute` carries until a boot card
(``edge/app.py``) injects the real components — so every ``ChatRoute`` construction keeps
compiling with a fail-CLOSED posture (an unwired route sheds at admission, zero provider calls,
before the budget / input phases ever matter).

The block lives here (not inline in ``routes.py``) so ``routes.py`` stays the ``ChatRoute``
value + the real handler + the shipped streaming orchestration + the route table, under the
800-line size gate. The split is one-directional: ``routes`` imports from ``chat_deps``;
``chat_deps`` imports nothing from ``routes`` (no cycle). Everything here is ``edge`` (the top
layer), so it may import every layer below (``admit`` / ``plan`` / ``domain``).

**No capacity literal, no module-level mutable.** The two shared default instances
(:data:`fail_closed_admission` / :data:`fail_closed_budget`) are immutable, stateless
``__slots__`` values — not module-level mutable; there is no module-level list / dict / set.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from typing import Protocol, runtime_checkable

from gateway_v2.admit.grant import (
    Admitted,
    BudgetVerdict,
    ShedVerdict,
    budget_verdict,
    shed_verdict,
)
from gateway_v2.domain import Finding
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PlanStore

__all__ = (
    "AdmissionDecider",
    "BudgetDecider",
    "InputDetector",
    "fail_closed_admission",
    "fail_closed_budget",
    "no_findings_detector",
    "unresolved_snapshot_source",
)

#: The GW15 input scanner (R7): the SAME findings-producing callable shape as
#: :data:`~gateway_v2.edge.error_frame_scan.ErrorFrameScanner` — a pure function over one channel's
#: text returning its completed :class:`~gateway_v2.domain.finding.Finding` sequence. The real
#: GW07/GW08 detector (and the test stub) satisfies it unchanged; the §10.4 input-scan phase runs
#: it over every folded ``Input_Channel`` and resolves the findings through ``ChatRoute.resolver``.
InputDetector = Callable[[str], Sequence[Finding]]


@runtime_checkable
class AdmissionDecider(Protocol):
    """The admission surface the lifecycle reads (R5): ``admit`` → ``Admitted`` | ``ShedVerdict``.

    The shipped :class:`~gateway_v2.admit.admission.AdmissionController` satisfies this structurally
    (its ``admit(owner_id, request_id, enqueued_at)`` already fails closed to a shed on any
    undecidable path, R5.4; its synchronous ``complete(request_id)`` releases the admitted slot). A
    ``Protocol`` so the lifecycle couples only to the surface and a test can inject a counting fake.
    """

    async def admit(
        self, owner_id: str, request_id: str, enqueued_at: float
    ) -> Admitted | ShedVerdict: ...

    def complete(self, request_id: str) -> None: ...


@runtime_checkable
class BudgetDecider(Protocol):
    """The budget surface the lifecycle reads (R6): ``evaluate`` → ``Admitted`` | ``BudgetVerdict``.

    The shipped :class:`~gateway_v2.admit.quota.QuotaComponent` satisfies this structurally; its
    ``evaluate(cost, *, request_id)`` spends the LOCAL lease with no request-path store read (R6.3)
    and fails closed to a budget refusal (R13.1); its synchronous ``refund(cost, *, request_id)``
    returns a spend that led to zero provider calls. A ``Protocol`` so a test can inject a fake.
    """

    async def evaluate(self, cost: int, *, request_id: str) -> BudgetVerdict | Admitted: ...

    def refund(self, cost: int, *, request_id: str) -> None: ...


class _AlwaysShedAdmission:
    """The fail-closed default ``admission``: every request is shed (GW15 2.4).

    Until the real GW19 :class:`~gateway_v2.admit.admission.AdmissionController` is wired at boot,
    ``ChatRoute.admission`` defaults to this so an unwired surface refuses rather than admits —
    never a fail-OPEN hole. It mints a shed verdict from a per-call fresh, non-shared
    :class:`random.Random` so the ``retry_after_s`` floor + jitter contract holds; the seed is
    irrelevant because the default always sheds. ``__slots__`` so there is no instance state; a
    single shared instance (:data:`fail_closed_admission`) is safe to reuse. Its ``complete`` is a
    no-op: the always-shed default admits nothing, so there is never a slot to release.
    """

    __slots__ = ()

    async def admit(
        self, owner_id: str, request_id: str, enqueued_at: float
    ) -> Admitted | ShedVerdict:
        """Shed every request (fail closed); the args are accepted but not consulted."""
        del owner_id, enqueued_at
        return shed_verdict(random.Random(0), request_id)

    def complete(self, request_id: str) -> None:
        """No-op: nothing was admitted on the always-shed default, so no slot to release."""
        del request_id
        return


class _AlwaysRefuseBudget:
    """The fail-closed default ``quota``: every request is budget-refused (GW15 2.4).

    Until the real GW06 :class:`~gateway_v2.admit.quota.QuotaComponent` is wired at boot,
    ``ChatRoute.quota`` defaults to this so an unwired surface refuses rather than forwards. It
    returns a :class:`~gateway_v2.admit.grant.BudgetVerdict` (``posture.BUDGET_UNAVAILABLE``) with
    no store I/O. In practice the admission default sheds FIRST, so this default is never reached on
    an unwired route; it is here so the budget phase is itself fail-closed. ``__slots__`` so there
    is no instance state; a single shared instance (:data:`fail_closed_budget`) is safe to reuse.
    Its ``refund`` is a no-op: the always-refuse default spent nothing, so there is nothing to
    return.
    """

    __slots__ = ()

    async def evaluate(self, cost: int, *, request_id: str) -> BudgetVerdict | Admitted:
        """Refuse every request's budget (fail closed); the cost is accepted but not consulted."""
        del cost
        return budget_verdict(request_id)

    def refund(self, cost: int, *, request_id: str) -> None:
        """No-op: nothing was spent on the always-refuse default, so there is nothing to return."""
        del cost, request_id
        return


def no_findings_detector(text: str) -> Sequence[Finding]:
    """The default ``input_detector``: a detector that finds nothing (GW15 2.4).

    Until the real GW07/GW08 detector is injected at boot, ``ChatRoute.input_detector`` defaults
    to this. A no-finding detector does NOT itself block (an empty finding set resolves to ALLOW),
    which would be unsafe as the SOLE guard — but the lifecycle's fail-closed admission default
    sheds the request in an earlier phase, so the input scan is never reached on an unwired route.
    Pairing the always-shed admission default with a benign input detector keeps every unwired
    construction fail-closed overall while leaving the detector a pure, side-effect-free stand-in
    that a test can trivially replace with a BLOCK-producing fake. Pure: no I/O.
    """
    del text
    return ()


#: The shared fail-closed default deciders. Immutable, stateless instances (not module-level
#: mutable), used as the ``ChatRoute`` defaults until a boot card wires the real GW19 / GW06
#: components. An unwired route sheds at admission (zero provider calls) before the budget or input
#: phases matter.
fail_closed_admission: AdmissionDecider = _AlwaysShedAdmission()
fail_closed_budget: BudgetDecider = _AlwaysRefuseBudget()


def unresolved_snapshot_source() -> ReplicaSnapshot:
    """The fail-closed default ``snapshot_source``: a fresh empty served snapshot (GW15 2.3).

    Until the real served :class:`~gateway_v2.plan.snapshot.ReplicaSnapshot` is wired at boot,
    ``ChatRoute.snapshot_source`` defaults to this. It returns an empty snapshot over a fresh
    :class:`~gateway_v2.plan.store.PlanStore` with no registered tenants, so ``lookup_plan`` reads
    :class:`~gateway_v2.domain.plan.PlanUnknownTenant` for every org and the lifecycle's ``_plan``
    phase refuses with the declared ``plan_unavailable`` code (fail closed — never a forward on an
    unresolved plan). Builds a new value per call (no shared mutable), so a stray mutation of the
    default cannot leak across requests.
    """
    return ReplicaSnapshot(PlanStore())
