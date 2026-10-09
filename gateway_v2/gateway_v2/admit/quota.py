"""Admission bounds derivation (R2-07 / R2-08, GW19) + the budget-lease façade (R2-09, GW06).

The card reserves ``admit/quota.py`` for "Local GCRA + shared lease"; the admission-control design
narrows its responsibility to **bounds derivation**. This module answers one question: given the
signed :class:`~gateway_v2.runtime.resources.ResourceContract` and the injected, measured guard
rate ``q_safe`` (from GW20, *not* the deployment-derived ``offered_service_rate()``), what is the
declared maximum depth of each :class:`Bounded_Queue` and the concurrency cap?

The budget-lease amendment (R2-09, card GW06) folds its façade into this same module: the pure
Lease_Chunk / Low_Watermark derivation (:func:`derive_lease_chunk` / :func:`derive_low_watermark`)
and the :class:`QuotaComponent` that ties the pure per-worker
:class:`~gateway_v2.admit.gcra.LocalGCRA` and the store-backed
:class:`~gateway_v2.admit.lease.BudgetLease` into one admit/refuse decision.
The façade's request path (:meth:`QuotaComponent.evaluate`) performs **no store I/O**: the GCRA is
local and the lease spend is a local decrement; the only ``await`` is the bounded generation-change
re-acquire (the revoke-on-change path, Req 6.4 — never the per-request refill). It owns only the
narrow ``budget_unavailable`` posture and fails closed on any undecidable path (Req 13.1); it never
returns ``shared_state_unavailable`` (Req 5.3). ``derive_bounds`` is unchanged.

Every number here is a :class:`ResourceContract` call — the contract is the only module permitted
to hold a capacity-position literal, enforced by the AST capacity gate. ``quota.py`` holds **no**
numeric capacity literal: an uncomputable bound simply propagates the contract's
:class:`CapacityUnavailable` (refuse-to-admit / refuse-to-start, Req 13.3), and an unset ``q_safe``
raises :class:`CapacityUnset` before anything else runs (refuse-to-start, Req 4.5).

The queue → contract-method mapping (design "Bounds derivation", Req 7.5):

======== =================================================================================
Queue    Bound source
======== =================================================================================
request  ``contract.queue_depth(q_safe)`` — the measured guard rate is the service rate
guard    ``contract.queue_depth(q_safe)`` — guard-owner share, same rule as every owner (1.7)
dispatch ``contract.queue_depth(q_safe)``
egress   ``contract.queue_depth(q_safe)`` for *slot* admission; the memory byte bound comes
         from ``contract.stream_buffer_bytes(...)`` and is owned by backpressure (task 12)
audit    ``contract.audit_queue_depth(drain_rate, bytes_per_record)`` — deliberately NOT
         ``queue_depth`` (an audit record is a few KB; sizing it by per-worker RSS would drop
         audit under any load — see the contract docstring), Req 7.5
======== =================================================================================

The module is pure, has no module-level mutable state, and depends only on ``runtime`` — within the
``admit`` layer's import-linter contract.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from gateway_v2.admit.gcra import LocalGCRA
from gateway_v2.admit.grant import Admitted, BudgetVerdict, budget_verdict
from gateway_v2.admit.lease import BudgetLease
from gateway_v2.runtime.errors import CapacityUnavailable, CapacityUnset
from gateway_v2.runtime.resources import ResourceContract

__all__ = (
    "AdmissionBounds",
    "QuotaComponent",
    "derive_bounds",
    "derive_lease_chunk",
    "derive_low_watermark",
)

_WATERMARK_FRACTION = 0.25
"""The Low_Watermark is this fraction of the Lease_Chunk (Req 4.5).

A fraction, not a capacity position: the watermark is *derived from* the chunk so a refill is
triggered with roughly a quarter of a chunk still in hand — enough runway to acquire a fresh chunk
off the request path before the Remaining_Lease is exhausted, without acquiring so eagerly that the
pool churns. It is a function of the chunk only (:func:`derive_low_watermark`), never a literal
size.
"""


@dataclass(frozen=True, slots=True)
class AdmissionBounds:
    """Declared maximum depths for admission, each derived from the ResourceContract.

    ``concurrency`` is the in-flight admission cap (Req 3.3, 4.1); the ``*_depth`` fields are the
    declared maximum depth of each :class:`Bounded_Queue` (Req 7.1, 7.5). ``egress_depth`` is the
    *slot* admission depth only — the streaming memory byte bound lives in the backpressure path and
    is not stored here.
    """

    concurrency: int
    request_depth: int
    guard_depth: int
    dispatch_depth: int
    egress_depth: int
    audit_depth: int


def derive_bounds(
    contract: ResourceContract,
    *,
    q_safe: float | None,
    audit_drain_rate_per_s: float,
    audit_bytes_per_record: int,
) -> AdmissionBounds:
    """Derive every admission bound from ``contract`` and the injected ``q_safe``.

    ``q_safe`` is the measured guard service rate (GW20), injected at construction and never
    hardcoded. If it is unset or non-positive the component refuses to start (Req 4.5, 13 start):
    a missing measurement is explicitly not guessed. Each depth is a :class:`ResourceContract`
    call per the queue → method mapping above; an uncomputable bound propagates
    :class:`~gateway_v2.runtime.errors.CapacityUnavailable` from the contract unchanged, which the
    caller treats as refuse-to-admit / refuse-to-start (Req 13.3).

    :param contract: the signed capacity contract — the only holder of capacity literals.
    :param q_safe: the measured guard service rate; ``None`` or ``<= 0`` ⇒ refuse to start.
    :param audit_drain_rate_per_s: the audit sink's measured drain rate (``AuditSink.calibrate``).
    :param audit_bytes_per_record: the measured bytes a single audit record costs.
    :raises CapacityUnset: if ``q_safe`` is unset or non-positive.
    :raises CapacityUnavailable: if any bound is below the minimum required to serve.
    """
    if q_safe is None or q_safe <= 0:
        raise CapacityUnset("q_safe is unset; refuse to start")

    # The measured guard rate is the service rate for the request/guard/dispatch share and for
    # egress slot admission; the guard owner is just another owner on the same rule (Req 1.7).
    slot_depth = contract.queue_depth(q_safe)

    # Audit is NOT sized by queue_depth: a record is a few KB, so a per-worker-RSS slot count would
    # drop audit under any load (Req 7.5, see ResourceContract.audit_queue_depth docstring).
    audit_depth = contract.audit_queue_depth(audit_drain_rate_per_s, audit_bytes_per_record)

    return AdmissionBounds(
        concurrency=slot_depth,
        request_depth=slot_depth,
        guard_depth=slot_depth,
        dispatch_depth=slot_depth,
        egress_depth=slot_depth,
        audit_depth=audit_depth,
    )


# --------------------------------------------------------------------------- #
# Budget-lease derivation (R2-09, GW06) — pure, contract-derived (Property 9)
# --------------------------------------------------------------------------- #


def derive_lease_chunk(contract: ResourceContract, *, q_safe: float | None) -> int:
    """Derive the Lease_Chunk size from ``contract`` and the injected ``q_safe``. Pure (Property 9).

    The Lease_Chunk is the slice of the org token budget a worker acquires once and then spends
    locally, topped up off the request path at the Low_Watermark. It is sized so that **one chunk
    is roughly one refresh period of ``q_safe`` throughput** — the budget a single worker is
    expected to spend between refills — and is then **bounded above by the contract's declared
    queue depth** so a worker never leases more outstanding budget than the contract says the box
    can have in flight at once:

        raw   = ceil(q_safe * target_p99_ms / 1000)      # one refresh period of throughput
        chunk = min(raw, contract.queue_depth(q_safe))    # bounded by the declared in-flight depth

    This is grounded entirely in the contract's measured inputs — ``q_safe`` (the injected,
    measured guard service rate, GW20), ``target_p99_ms`` (the refresh period / request-path SLO),
    and ``queue_depth(q_safe)`` (which itself folds in ``utilization_cap`` and the per-worker RSS
    slot count). No capacity-position literal appears here: ``_WATERMARK_FRACTION`` and the
    millisecond divisor are rate/time factors, not sizes, and every size is a contract call. The
    chunk is a pure function of the contract plus ``q_safe`` (Property 9), so N workers over one
    contract derive the identical chunk.

    Refuse-to-start, never guess (Req 2.4, 13.3):

    * ``q_safe`` unset or non-positive ⇒ :class:`CapacityUnset` (a missing measurement is explicitly
      not guessed — same contract as :func:`derive_bounds`).
    * ``queue_depth`` uncomputable ⇒ the contract's :class:`CapacityUnavailable` propagates
      unchanged.
    * a computed chunk ``< 1`` ⇒ :class:`CapacityUnavailable` (a sub-unit chunk cannot admit even
      one request, so the worker refuses to acquire rather than lease an unbounded/zero chunk).

    :param contract: the signed capacity contract — the only holder of capacity literals.
    :param q_safe: the measured guard service rate; ``None`` or ``<= 0`` ⇒ refuse to start.
    :returns: the Lease_Chunk size (a positive integer).
    :raises CapacityUnset: if ``q_safe`` is unset or non-positive.
    :raises CapacityUnavailable: if the chunk cannot be computed or is below one.
    """
    if q_safe is None or q_safe <= 0:
        raise CapacityUnset("q_safe is unset; refuse to acquire a lease")

    # One refresh period (the request-path SLO) of q_safe throughput: the budget a worker spends
    # between refills. target_p99_ms / 1000 is a time, q_safe is a rate; the product is a count.
    refresh_period_s = contract.target_p99_ms / 1000.0
    raw = math.ceil(q_safe * refresh_period_s)

    # Bound the outstanding lease by the contract's declared in-flight depth, so a worker never
    # holds more budget than the box is sized to have in flight (propagates CapacityUnavailable).
    in_flight_bound = contract.queue_depth(q_safe)
    chunk = min(raw, in_flight_bound)

    if chunk < 1:
        raise CapacityUnavailable(
            f"derived Lease_Chunk={chunk} (raw={raw} in_flight_bound={in_flight_bound}); "
            "refuse to acquire a lease with a sub-unit chunk",
        )
    return chunk


def derive_low_watermark(chunk: int) -> int:
    """Derive the Low_Watermark from the Lease_Chunk only. Pure (Req 4.5, Property 9).

    The Low_Watermark is the Remaining_Lease threshold at or below which an Async_Refill fires. It
    is a function of the chunk **only** — ``floor(_WATERMARK_FRACTION * chunk)``, floored at ``0``
    and capped at ``chunk - 1`` so it is always a valid, strictly-sub-chunk threshold (a watermark
    equal to the chunk would fire a refill the instant a chunk is acquired). No store, no clock, no
    contract: given the same chunk it always returns the same watermark.

    :param chunk: the Lease_Chunk size (must be ``>= 1``).
    :returns: the Low_Watermark in ``[0, chunk)``.
    :raises CapacityUnavailable: if ``chunk`` is below one (no valid watermark exists).
    """
    if chunk < 1:
        raise CapacityUnavailable(
            f"cannot derive a Low_Watermark from a sub-unit chunk={chunk}; refuse to acquire",
        )
    mark = math.floor(_WATERMARK_FRACTION * chunk)
    # Keep it strictly below the chunk (a watermark == chunk would refill on every full chunk).
    return min(mark, chunk - 1)


# --------------------------------------------------------------------------- #
# QuotaComponent façade (R2-09, GW06) — composes GCRA + lease, fail-closed
# --------------------------------------------------------------------------- #


class QuotaComponent:
    """The admit/refuse façade: Local GCRA (burst+rate) then the Budget_Lease (org token budget).

    Ties the pure per-worker :class:`~gateway_v2.admit.gcra.LocalGCRA` and the store-backed
    :class:`~gateway_v2.admit.lease.BudgetLease` into one decision. The composition order is fixed —
    **GCRA first, then budget** — so a burst-limited request is refused before it touches the
    budget. The request path performs **no store I/O**: :meth:`evaluate` runs the local GCRA check
    and the local lease ``try_spend``; the only ``await`` is the bounded generation-change
    re-acquire (the revoke-on-change path, Req 6.4), which is **not** the per-request refill (that
    runs off the request path inside the lease, Req 4.2 / Property 5).

    The façade owns only the narrow ``budget_unavailable`` posture (Req 5.2, 5.3): when the pool and
    the Remaining_Lease are both exhausted — including under a store outage — it returns a
    :class:`~gateway_v2.admit.grant.BudgetVerdict` carrying ``posture.BUDGET_UNAVAILABLE``,
    **never** ``shared_state_unavailable``. Identity, kill switch and plan are other components,
    served from RAM for the declared window independently of the budget posture (cross-referenced,
    not owned here). Every undecidable path fails **closed** — a refusal, never an admit without a
    decision (Req 13.1); there is no FAIL_OPEN path and nothing increments a fail-open counter.
    """

    __slots__ = ("_gcra", "_generation_source", "_lease", "_metrics")

    def __init__(
        self,
        gcra: LocalGCRA,
        lease: BudgetLease,
        *,
        generation_source: Callable[[], int],
        metrics: object,
    ) -> None:
        """Construct the façade over an injected GCRA, lease, generation source, and metrics.

        :param gcra: the pure per-worker burst/rate limiter (no store round trip).
        :param lease: the store-backed budget lease (spent locally; refills off the request path).
        :param generation_source: yields the current Budget_Generation the façade checks the held
            lease against; a change advances it and the façade re-acquires under the new generation
            before admitting (Req 6.4). Injected so tests drive the revoke-on-change path.
        :param metrics: the producer the façade observes budget_unavailable refusals into
            (structural; the concrete ``QuotaMetrics`` lands in task 10). Held only; the request
            path does not require it, and a missing observer never admits a refused request.
        """
        self._gcra = gcra
        self._lease = lease
        self._generation_source = generation_source
        self._metrics = metrics

    async def evaluate(self, cost: int, *, request_id: str) -> BudgetVerdict | Admitted:
        """Decide one request: GCRA burst+rate, then the Budget_Lease. Fail-closed (Req 13.1).

        The decision order and the fail-closed catch-all:

        1. **Local GCRA** (``gcra.admit()``) — a pure burst+rate check with no store round trip. On
           a refusal the request is over the Local_Burst_Limit / sustained rate; the façade refuses
           it with the budget verdict (the one quota-refusal value this component emits — the design
           focuses the façade's posture on ``budget_unavailable``, so a local rate refusal is
           surfaced as the same quota refusal rather than inventing a second posture).
        2. **Generation check + spend** (``lease.try_spend``) — a local decrement. If the held lease
           is stale (behind the current Budget_Generation) the façade triggers a **bounded
           generation-change re-acquire** (``lease.acquire`` under the current gen; the
           revoke-on-change path, Req 6.4 — NOT the per-request refill) and retries the local spend
           once under the current generation. A stale lease is never spent (Property 3).
        3. **Admit or refuse** — a successful spend returns :class:`Admitted`; when the pool and the
           Remaining_Lease are both exhausted (including under a store outage) the façade returns a
           :class:`BudgetVerdict` with ``posture.BUDGET_UNAVAILABLE`` (Req 5.2), never
           ``shared_state_unavailable`` (Req 5.3).

        Any exception reaching this method is caught and refused with ``budget_unavailable``
        (fail-closed, Req 13.1): the component never admits without a decision, and no path
        increments a fail-open counter.

        :param cost: the budget cost of the request (tokens).
        :param request_id: echoed into any :class:`BudgetVerdict` for ``edge`` to render.
        :returns: :class:`Admitted` on admission, else a :class:`BudgetVerdict`.
        """
        try:
            if not self._gcra.admit():
                # Over the local burst/rate allowance: refuse with the quota-refusal verdict.
                return self._refuse(request_id)

            current_gen = self._generation_source()
            result = self._lease.try_spend(cost, current_gen)
            if result.stale_generation:
                # Revoke-on-change: the held lease is behind the current generation. The stale lease
                # is NEVER spent (Req 13.4). Re-acquire under the current gen (bounded, NOT the
                # per-request refill); only retry the spend if the re-acquire actually granted
                # budget UNDER the current generation. If it granted nothing (empty pool / still
                # stale), refuse — do not retry a spend that would draw on the stale remainder the
                # re-acquire adopted into the new generation.
                acquired = await self._lease.acquire(current_gen)
                if not acquired:
                    return self._refuse(request_id)
                result = self._lease.try_spend(cost, current_gen)

            if result.admitted:
                return Admitted(
                    owner_id=self._lease.config.org,
                    request_id=request_id,
                    admitted_at=0.0,
                )
            # Pool + Remaining_Lease exhausted (or still stale): refuse ONLY quota, narrowly.
            return self._refuse(request_id)
        except Exception:  # noqa: BLE001 — fail-closed catch-all (Req 13.1), never admit on error.
            return self._refuse(request_id)

    def _refuse(self, request_id: str) -> BudgetVerdict:
        """Build the narrow ``budget_unavailable`` refusal and observe it (never fail-open).

        Reuses ``grant.budget_verdict`` (``posture.BUDGET_UNAVAILABLE`` +
        ``gap_retry_after_s`` floored at ``MIN_RETRY_AFTER_S``, ``should_retry = False``). The
        metrics observation is best-effort: an observer that lacks the hook never turns a refusal
        into an admit (fail-closed). Never returns ``shared_state_unavailable`` (Req 5.3).
        """
        observe = getattr(self._metrics, "observe_budget_unavailable", None)
        if callable(observe):
            observe()
        return budget_verdict(request_id)
