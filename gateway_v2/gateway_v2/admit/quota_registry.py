"""Per-org budget-lease registry keyed by org id (GW36 S4, Req 4.1-4.4, 4.6, 7.3).

One org gets at most one :class:`~gateway_v2.admit.quota.QuotaComponent` (and thus one
:class:`~gateway_v2.admit.lease.BudgetLease`) per worker, built on the first request for that org
from the org's served :class:`~gateway_v2.admit.budget_state.BudgetRecord` (pool tokens +
generation) and cached. A later request for an org already present reuses the cached component with
**no request-path record read** (Req 4.3) — the record was read once from the served snapshot the
BUDGET applier maintains, never from the store on the hot path. Each org's component holds its own
lease over its own ``{rv2}:budget:{org}:remaining`` pool, so the registry couples no two orgs: a
spend or an exhaustion for one org never touches another's lease (Req 4.4 / 4.6).

Design invariants carried here (GW36 S4 design §4, "Per-org budget lease registry"):

* **Build-on-first-use and cache** — :meth:`component_for` builds a component from the served record
  on the first request for an org and caches it (Req 4.1, 4.2); later requests reuse it with no
  record read (Req 4.3).
* **No record served ⇒ None** — :meth:`component_for` returns ``None`` when the snapshot serves no
  :class:`BudgetRecord` for the org. The composed quota gate turns ``None`` into a narrow
  ``budget_unavailable`` refusal (never ``shared_state_unavailable``), spending zero tokens and
  making zero provider calls (Req 4.5, handled above this layer).
* **Generation change ⇒ re-acquire** — :meth:`on_generation_change` marks an org so the next
  :meth:`component_for` rebuilds its component from the current served record under the new
  generation (Req 7.3). The BUDGET applier calls it when a drained delta advances an org's
  generation. Until the rebuilt component is in hand the org has no cached component, so a request
  resolving the org re-reads the served record and rebuilds under the current generation.

Construction seam
------------------
The per-org component needs a contract-derived lease chunk (:func:`derive_lease_cap_tokens`), a
Low_Watermark (:func:`derive_low_watermark`), the bounded store client, a stable per-worker id, and
an off-path ``spawn`` — none of which the registry knows about, and all of which would drag the
contract / ``q_safe`` / store client into this pure, per-worker cache. So the registry takes an
**injected** ``make_component: Callable[[str, BudgetRecord], QuotaComponent]`` factory (supplied by
the production boot, GW36 S4 task 10.3, which closes over the contract, ``q_safe``, the store
client, the worker id, and ``spawn``). The registry only calls ``make_component(org, record)`` on
the first use for an org, caching the result; it holds no capacity size and constructs no lease, so
the ``admit/`` capacity-literal gate stays green.

Snapshot seam
-------------
The registry reads the served record through a minimal structural interface — a snapshot exposing
``record_for(org) -> BudgetRecord | None`` — declared here as :class:`BudgetSnapshotLike`. The
concrete ``BudgetSnapshot`` the BUDGET applier maintains (GW36 S4 task 8.1) satisfies it
structurally, so this module does not import or depend on that later task and 3.1 does not block on
8.1. ``record_for`` is the only snapshot method the registry needs.

The registry state is per-instance (one registry per worker): a ``dict`` of cached components keyed
by org and a ``set`` of orgs marked for re-acquire. There is no module-level mutable state, so the
``admit/`` module-mutable AST gate holds. ``admit`` may import ``runtime`` + ``domain`` only — this
module imports the sibling :mod:`gateway_v2.admit.budget_state` and :mod:`gateway_v2.admit.quota`
value/façade types and nothing from ``edge``/``resolve``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from gateway_v2.admit.budget_state import BudgetRecord
from gateway_v2.admit.quota import QuotaComponent

__all__ = ("BudgetSnapshotLike", "QuotaRegistry")


class BudgetSnapshotLike(Protocol):
    """The minimal served-snapshot surface the registry reads (structural, not a hard dep).

    The concrete ``BudgetSnapshot`` that the BUDGET applier maintains (GW36 S4 task 8.1) exposes
    ``record_for`` among other methods; declaring only this one method structurally keeps
    ``quota_registry.py`` from importing that later task, so this module and task 3.1 do not block
    on 8.1. Any object exposing ``record_for`` satisfies it.
    """

    def record_for(self, org: str) -> BudgetRecord | None:
        """Return the served :class:`BudgetRecord` for ``org``, or ``None`` when none is served."""
        ...


class QuotaRegistry:
    """Build, cache, and re-acquire per-org ``QuotaComponent`` instances, keyed by org id.

    One instance per worker. ``snapshot`` is the served-budget snapshot read on the first use for an
    org (and on a re-acquire); ``make_component`` is the injected factory that turns a served
    ``(org, BudgetRecord)`` into a ``QuotaComponent`` over a fresh per-org ``BudgetLease``. The
    registry holds no capacity sizes and constructs no lease itself — it only caches what the
    factory returns.
    """

    __slots__ = ("_cache", "_make_component", "_reacquire", "_snapshot")

    def __init__(
        self,
        snapshot: BudgetSnapshotLike,
        *,
        make_component: Callable[[str, BudgetRecord], QuotaComponent],
    ) -> None:
        self._snapshot: BudgetSnapshotLike = snapshot
        self._make_component: Callable[[str, BudgetRecord], QuotaComponent] = make_component
        # org id -> its cached QuotaComponent (over its own per-org BudgetLease). Instance state
        # only; no module-level mutable state (admit/ module-mutable gate).
        self._cache: dict[str, QuotaComponent] = {}
        # orgs whose next component_for must rebuild from the current served record under a new
        # generation (set by on_generation_change). Instance state only.
        self._reacquire: set[str] = set()

    def component_for(self, org: str) -> QuotaComponent | None:
        """Return ``org``'s ``QuotaComponent``, building and caching it on first use.

        Reuses the cached component when one is present and the org is not marked for re-acquire,
        with **no request-path record read** (Req 4.3). Otherwise reads the served
        :class:`BudgetRecord` from the snapshot once: ``None`` ⇒ the org has no served budget, so
        return ``None`` (the caller refuses ``budget_unavailable``, Req 4.5); a record ⇒ build the
        component via the injected factory, cache it, clear any re-acquire mark, and return it
        (Req 4.1, 4.2). Each org's component is independent (its own pool key), so nothing here
        couples two orgs (Req 4.4, 4.6).
        """
        if org not in self._reacquire:
            cached = self._cache.get(org)
            if cached is not None:
                return cached

        record = self._snapshot.record_for(org)
        if record is None:
            # No served budget for this org: refuse above this layer. Leave any re-acquire mark in
            # place so a record arriving later still rebuilds under the current generation.
            return None

        component = self._make_component(org, record)
        self._cache[org] = component
        self._reacquire.discard(org)
        return component

    def on_generation_change(self, org: str) -> None:
        """Mark ``org`` for re-acquisition so the next ``component_for`` rebuilds it (Req 7.3).

        The BUDGET applier calls this when a drained delta advances ``org``'s generation. The mark
        drops any cached component so the next request for ``org`` reads the current served record
        and rebuilds a component (and lease) under the new generation. Marking an org never seen is
        harmless: the next ``component_for`` already reads the snapshot.
        """
        self._cache.pop(org, None)
        self._reacquire.add(org)
