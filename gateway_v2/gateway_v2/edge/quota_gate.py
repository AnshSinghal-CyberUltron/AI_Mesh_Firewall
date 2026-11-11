"""The composed quota gate: per-key GCRA then per-org budget (GW36 S4, design §5).

:class:`QuotaGate` is the :class:`~gateway_v2.edge.chat_deps.BudgetDecider` the production boot
injects as ``ChatRoute.quota`` (GW36 S4 task 10.3), replacing the fail-closed
``fail_closed_budget`` default. It composes the two pieces S4 stands up — the per-principal
:class:`~gateway_v2.admit.gcra_cache.GcraCache` and the per-org
:class:`~gateway_v2.admit.quota_registry.QuotaRegistry` — into the single
``evaluate`` / ``refund`` surface the §10.4 request lifecycle couples to.

**Order and short-circuit (Req 5.1–5.3).** Admission is UPSTREAM of this gate: the lifecycle runs
the GW19 ``AdmissionController.admit`` before the budget phase, so by the time ``evaluate`` is
reached the request has already passed the door. Inside ``evaluate`` the order is fixed
GCRA → budget and it stops at the first refusal:

1. **Per-key GCRA.** ``gcra_cache.for_principal(key_hash, principal)`` yields the request's
   per-worker :class:`~gateway_v2.admit.gcra.LocalGCRA`, or ``None`` when the principal's
   ``rate_per_s == 0`` (no per-key limit, Req 3.5) — a ``None`` skips the rate check. A non-``None``
   GCRA whose ``admit()`` returns ``False`` means the per-worker burst / sustained rate was
   exceeded: the gate returns the declared 429 rate-limited verdict (``rate_limited_verdict``), the
   allowance is unchanged (``LocalGCRA`` leaves the TAT untouched on reject), and zero provider
   calls are made (Req 3.6, 5.3, 11.1).
2. **Per-org budget.** ``registry.component_for(owner)`` yields the ``owner``'s
   :class:`~gateway_v2.admit.quota.QuotaComponent`, or ``None`` when no ``BudgetRecord`` is served
   for the org (or the owner is missing / unknown). A ``None`` is refused narrowly with
   ``budget_unavailable`` (NEVER ``shared_state_unavailable``), zero tokens spent, zero provider
   calls (Req 4.5, 6.5).
3. **Delegate.** Otherwise the gate delegates to ``component.evaluate(cost, request_id=...)`` — the
   shipped façade that runs its own internal GCRA-then-lease spend with no request-path store read.
   On ``Admitted`` the request proceeds to the provider (Req 5.6); the ``Admitted``'s ``owner_id``
   equals the ``owner`` passed in, because the component is that owner's (Req 6.4). A
   ``BudgetVerdict`` is passed straight back.

**Fail-closed (Req 5.5).** The whole ``evaluate`` body is wrapped in a catch-all: any unexpected
error refuses with ``budget_unavailable`` — the gate never admits without a decision, and no path
increments a fail-open counter.

**Refund (Req 5.4, 6.1–6.3).** ``refund(cost, *, request_id, owner)`` resolves the SAME owner's
component and returns exactly the spent cost to THAT org's lease and no other — a missing component
is a no-op, so a request can never credit another org. It is synchronous, performs no store I/O,
and is best-effort / fail-closed (wrapped so it can never raise into the lifecycle ``finally``),
matching ``QuotaComponent.refund`` and the ``RequestHolds.release`` contract.

``edge`` is the top layer, so importing ``admit`` value/façade types and the ``chat_deps``
``BudgetDecider`` protocol is allowed (``edge`` may import ``admit`` + ``plan`` + ``domain``, and an
intra-layer ``edge`` → ``edge`` import). The gate holds only instance state (the two injected
collaborators); there is no module-level mutable state and no capacity literal.
"""

from __future__ import annotations

from gateway_v2.admit.gcra_cache import GcraCache
from gateway_v2.admit.grant import Admitted, BudgetVerdict, budget_verdict, rate_limited_verdict
from gateway_v2.admit.quota_registry import QuotaRegistry
from gateway_v2.domain.identity import Principal

__all__ = ("QuotaGate",)


class QuotaGate:
    """Compose the per-key GCRA and the per-org budget lease into one ``BudgetDecider`` (design §5).

    One instance per worker, injected as ``ChatRoute.quota`` by the production boot. ``gcra_cache``
    is the per-principal :class:`GcraCache` (built / reused / invalidated on a KEY delta), and
    ``registry`` is the per-org :class:`QuotaRegistry` over the served ``BudgetSnapshot``. The gate
    holds only these two collaborators (instance state); it constructs no lease, holds no capacity
    size, and keeps no module-level mutable state.
    """

    __slots__ = ("_gcra_cache", "_registry")

    def __init__(self, gcra_cache: GcraCache, registry: QuotaRegistry) -> None:
        """Construct over the per-principal GCRA cache and the per-org budget-lease registry."""
        self._gcra_cache: GcraCache = gcra_cache
        self._registry: QuotaRegistry = registry

    async def evaluate(
        self,
        cost: int,
        *,
        request_id: str,
        owner: str,
        principal: Principal,
        key_hash: str,
    ) -> BudgetVerdict | Admitted:
        """Decide one request: per-key GCRA, then the ``owner``'s budget lease. Fail-closed.

        See the module docstring for the full order. ``cost`` is the request's token cost;
        ``request_id`` is echoed into any refusal verdict; ``owner`` is the resolved org the spend
        is attributed to; ``principal`` + ``key_hash`` identify the per-key GCRA. Returns
        :class:`Admitted` on admission, else a :class:`BudgetVerdict` carrying
        ``posture.RATE_LIMITED`` (GCRA reject → declared 429) or ``posture.BUDGET_UNAVAILABLE`` (no
        served budget / lease exhausted / any undecidable path → 503). Every refusal makes zero
        provider calls (Req 5.5).
        """
        try:
            # 1. Per-key GCRA: None means rate 0 (no per-key limit, Req 3.5) -> skip the check.
            gcra = self._gcra_cache.for_principal(key_hash, principal)
            if gcra is not None and not gcra.admit():
                # Over the per-worker burst / sustained rate. The TAT is left untouched on reject,
                # so the allowance is unchanged and the next legitimately-timed arrival is served;
                # zero provider calls (Req 3.6, 5.3, 11.1).
                return rate_limited_verdict(request_id)

            # 2. Per-org budget: None (or a missing / unknown owner) -> narrow budget_unavailable,
            #    zero tokens, zero provider calls, never shared_state_unavailable (Req 4.5, 6.5).
            component = self._registry.component_for(owner)
            if component is None:
                return budget_verdict(request_id)

            # 3. Delegate to the owner's component (its own internal GCRA + local lease spend). On
            #    Admitted the owner_id == owner (the component is this owner's, Req 6.4). Proceed to
            #    the provider on Admitted (Req 5.6); a BudgetVerdict is passed straight back.
            return await component.evaluate(cost, request_id=request_id)
        except Exception:  # noqa: BLE001 — fail-closed catch-all (Req 5.5): never admit on error.
            return budget_verdict(request_id)

    def refund(self, cost: int, *, request_id: str, owner: str) -> None:
        """Return the spent cost to the ``owner``'s lease, no other org's (Req 5.4, 6.1-6.3).

        Resolves the SAME owner's component and calls its synchronous ``refund``; a missing
        component (``None``) is a no-op, so a request can never credit another org (Req 6.3). No
        principal / key_hash is needed — there is no GCRA on a refund. Synchronous, no store I/O,
        and wrapped best-effort / fail-closed so it never raises into the lifecycle ``finally`` (a
        lost refund is the conservative direction, matching ``RequestHolds.release`` and
        ``QuotaComponent.refund``).
        """
        try:
            component = self._registry.component_for(owner)
            if component is None:
                return
            component.refund(cost, request_id=request_id)
        except Exception:  # noqa: BLE001 — best-effort: a lost refund must never mask the response.
            return
