"""Admission metrics producers — a FIXED, label-free series set (R2-07 / GW19, R7.3/R7.4/R13.2).

This module is a PRODUCER only. It follows the gateway_v2 producer-vs-publisher split established
by ``runtime/holdback_metrics.py`` (and ``audit/metrics.py`` / ``runtime/state_metrics.py`` before
it): it emits a fixed set of label-free series with no tenant-derived dimension, and a separate
publisher (GW14d) owns ``# TYPE`` exposition and fleet-wide publication. The two rules inherited
from the holdback producer apply here for the same measured reasons:

* **No tenant-derived label, anywhere.** R2-10 measured per-org series making 40% of new tenants'
  first requests return HTTP 500 once one series was exported per org. The only dimension on any
  series here is a fixed, finite ``queue`` name (``request`` / ``guard`` / ``dispatch`` /
  ``egress`` / ``audit``) and a fixed, finite shed ``reason`` — never an ``owner`` / ``org`` /
  tenant value. "Which tenant shed the most" is a query the trace answers, not a metric.
* **Counters use real zeros, never absence.** ``admitted_total``, every ``shed_total{reason}``
  bucket, and ``fail_open_total`` exist from the first snapshot as ``0`` so "nothing admitted / no
  sheds / no fail-opens" and "no series" cannot look the same to an alarm. ``fail_open_total`` in
  particular is pinned at ``0`` and there is NO correct-operation path that increments it: the
  component fails closed to a shed (Req 13.1/13.2), so a non-zero ``fail_open_total`` is a bug the
  invariant test can see.

Recording is O(1) per observation and reads nothing: ``observe_admit``, ``observe_shed`` and
``set_queue`` bump a counter or overwrite a per-queue reading on the hot path, and only ``snapshot``
builds a frozen reading. The ``Histogram`` percentiles (nearest-rank p50/p99) are computed on READ,
inside ``snapshot``, never during an observation.

``Histogram`` is reused directly from ``runtime.holdback_metrics``: ``admit`` sits ABOVE ``runtime``
in the import-linter ``layers`` contract, so importing it is allowed and avoids duplicating the O(1)
observe / nearest-rank-on-read helper. The admission producer uses it for the per-queue oldest-item
age distribution, so the age series carries the same empty-is-zero / percentile-on-read discipline
as the holdback latency series.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from gateway_v2.runtime.holdback_metrics import Histogram

__all__ = (
    "PREFIX",
    "QUEUE_NAMES",
    "QUOTA_PREFIX",
    "AdmissionMetrics",
    "AdmissionReading",
    "QueueReport",
    "QuotaMetrics",
    "QuotaReading",
    "ShedReason",
)

PREFIX = "amf_admit"

QUOTA_PREFIX = "amf_quota"
"""Prefix for the budget-lease quota series (R2-09 / GW06). The three fixed series are
``amf_quota_lease_overshoot``, ``amf_quota_async_refill_total`` and
``amf_quota_budget_unavailable_total`` — fixed, LABEL-FREE, and never tenant-derived (R12.2)."""


QUEUE_NAMES: tuple[str, ...] = ("request", "guard", "dispatch", "egress", "audit")
"""The fixed, finite set of queue names. The ONLY label on a depth/age series (R2-10: never a
tenant-derived label). Mirrors the ``Bounded_Queue`` set in the design (request/guard/dispatch/
egress/audit)."""


class ShedReason(StrEnum):
    """The fixed, finite set of shed reasons — the only label on ``shed_total`` (R2-10 safe).

    Every reason has a real-zero bucket from the first snapshot so "no sheds of this kind" is
    distinguishable from "series absent". ``UNDECIDABLE`` is the fail-closed reason (Req 13.1): a
    path that could not reach an admission decision sheds under this reason rather than fail open.
    """

    ADMIT = "admit"
    BACKOFF = "backoff"
    HARD_CAP = "hard_cap"
    QUEUE_FULL = "queue_full"
    UNDECIDABLE = "undecidable"


@dataclass(frozen=True, slots=True)
class QueueReport:
    """A frozen, read-only view of every bounded queue's depth and oldest-item age (R7.3/R7.4).

    ``per_queue`` maps each fixed queue name to ``(depth, oldest_age_s)``. The mapping is a
    ``MappingProxyType`` so the frozen report cannot be mutated through the view, and it always
    carries all five fixed queue names — a queue with nothing enqueued reads ``(0, 0.0)``, never an
    absent key, so the depth/age series exist from the first snapshot.
    """

    per_queue: Mapping[str, tuple[int, float]]

    def depth(self, queue: str) -> int:
        """Current depth of ``queue`` (``0`` if nothing has been observed). O(1)."""
        return self.per_queue[queue][0]

    def oldest_age_s(self, queue: str) -> float:
        """Age in seconds of the oldest item in ``queue`` (``0.0`` if empty). O(1)."""
        return self.per_queue[queue][1]


@dataclass(frozen=True, slots=True)
class AdmissionReading:
    """A full reading of the admission producer. Every series is label-free (fixed queue/reason).

    ``queues`` is the per-queue depth + oldest-age report (R7.3/R7.4). ``oldest_age_s_hist`` is the
    distribution of oldest-item ages across the fixed queues, carrying the holdback ``Histogram``
    percentile-on-read discipline. ``admitted_total`` and the ``shed_total`` buckets are real zeros
    from the first snapshot. ``fail_open_total`` is pinned at ``0`` (Req 13.2) and no correct path
    increments it.
    """

    queues: QueueReport
    oldest_age_s_hist: Histogram
    admitted_total: int
    shed_total: Mapping[str, int]
    fail_open_total: int

    def shed_for(self, reason: ShedReason) -> int:
        """Shed count for a fixed ``reason`` bucket (``0`` from the first snapshot). O(1)."""
        return self.shed_total[reason.value]


class AdmissionMetrics:
    """Producer for the admission series. O(1) per observation; reads nothing on the hot path.

    ``observe_admit`` bumps ``admitted_total``; ``observe_shed(reason)`` bumps the fixed reason's
    bucket; ``set_queue(queue, depth, oldest_age_s)`` overwrites that queue's current reading. All
    three are O(1) and allocate nothing on the hot path. ``snapshot`` is the only reader: it builds
    the oldest-age histogram, freezes the per-queue report, and returns a frozen reading.

    Every counter is seeded to ``0`` at construction (real zeros, never absence), including a bucket
    per ``ShedReason`` and ``fail_open_total``. ``fail_open_total`` has NO incrementing method in
    correct operation; the design is fail-closed, so a non-zero value is a bug the invariant test
    asserts against (Req 13.2).
    """

    def __init__(self) -> None:
        self._admitted_total = 0
        self._shed_total: dict[str, int] = {reason.value: 0 for reason in ShedReason}
        self._fail_open_total = 0
        # Each fixed queue starts at (depth=0, oldest_age_s=0.0): a real zero, never an absent key.
        self._queue_depth: dict[str, int] = {name: 0 for name in QUEUE_NAMES}
        self._queue_oldest_age_s: dict[str, float] = {name: 0.0 for name in QUEUE_NAMES}

    def observe_admit(self) -> None:
        """Record one admitted request. O(1)."""
        self._admitted_total += 1

    def observe_shed(self, reason: ShedReason) -> None:
        """Record one shed under a fixed ``reason`` bucket. O(1)."""
        self._shed_total[reason.value] += 1

    def set_queue(self, queue: str, *, depth: int, oldest_age_s: float) -> None:
        """Overwrite the current depth + oldest-item age for a fixed queue. O(1).

        ``queue`` must be one of ``QUEUE_NAMES``; an unknown name is rejected so no tenant-derived
        or ad-hoc series can ever be created (R2-10 — the label set stays fixed and finite).
        """
        if queue not in self._queue_depth:
            raise KeyError(f"unknown queue {queue!r}; must be one of {QUEUE_NAMES}")
        self._queue_depth[queue] = depth
        self._queue_oldest_age_s[queue] = oldest_age_s

    def snapshot(self) -> AdmissionReading:
        """Build the current reading. The only method that reads the accumulated state.

        Builds the oldest-age histogram across the fixed queues (percentiles computed on read),
        freezes the per-queue depth/age report behind a ``MappingProxyType``, and returns a frozen
        ``AdmissionReading``. Counters are copied so the returned reading cannot drift under later
        observations.
        """
        age_hist = Histogram()
        for name in QUEUE_NAMES:
            # The age series carries 1 ms resolution via whole-millisecond samples, mirroring the
            # holdback latency histograms (integer samples, nearest-rank percentiles on read).
            age_hist.observe(round(self._queue_oldest_age_s[name] * 1000))
        per_queue: dict[str, tuple[int, float]] = {
            name: (self._queue_depth[name], self._queue_oldest_age_s[name]) for name in QUEUE_NAMES
        }
        return AdmissionReading(
            queues=QueueReport(per_queue=MappingProxyType(per_queue)),
            oldest_age_s_hist=age_hist,
            admitted_total=self._admitted_total,
            shed_total=MappingProxyType(dict(self._shed_total)),
            fail_open_total=self._fail_open_total,
        )


# --------------------------------------------------------------------------- #
# Budget-lease quota producer (R2-09, GW06) — producer-only, label-free
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class QuotaReading:
    """A frozen reading of the budget-lease quota producer. Every series is LABEL-FREE (R12.2).

    The three fixed series the design names (Req 12.4), each present from the FIRST snapshot so
    "no refills / no refusals" and "series absent" cannot look the same to an alarm (Req 12.3):

    * ``lease_overshoot`` — ``amf_quota_lease_overshoot``, the declared aggregate overshoot a worker
      can hold in flight (Req 7.4). A gauge the operator reads to see the ``limit + overshoot``
      bound; it is a value the producer is told once (``set_overshoot``) and reports, seeded ``0``.
    * ``async_refill_total`` — ``amf_quota_async_refill_total``, the count of Async_Refills that ran
      off the request path (bumped by :meth:`QuotaMetrics.observe_async_refill`).
    * ``budget_unavailable_total`` — ``amf_quota_budget_unavailable_total``, the count of
      ``budget_unavailable`` refusals (bumped by :meth:`QuotaMetrics.observe_budget_unavailable`).

    None of the three carries a tenant-derived (owner / org) dimension — the producer exposes no
    such parameter, so no per-tenant series can ever be created (R2-10 / R12.2).
    """

    lease_overshoot: int
    async_refill_total: int
    budget_unavailable_total: int


class QuotaMetrics:
    """Producer for the budget-lease quota series. O(1) observe; reads nothing on the hot path.

    Mirrors the :class:`AdmissionMetrics` producer-vs-publisher split: it emits a FIXED set of
    label-free series with no tenant-derived dimension, and GW14d owns the ``# TYPE`` exposition and
    fleet-wide publication. Every series is seeded to ``0`` at construction (real zeros, never
    absence — Req 12.3), so the first ``snapshot`` already reports all three.

    This producer structurally satisfies **both** hooks the already-written code duck-types against:

    * :meth:`observe_async_refill` — called by
      :meth:`gateway_v2.admit.lease.BudgetLease._refill` (so a ``QuotaMetrics`` satisfies
      :class:`gateway_v2.admit.lease.QuotaMetricsLike`).
    * :meth:`observe_budget_unavailable` — called best-effort by
      :meth:`gateway_v2.admit.quota.QuotaComponent._refuse`.

    No method accepts an ``owner`` / ``org`` / ``tenant`` parameter — the label set is fixed and
    finite (R12.2). ``set_overshoot`` publishes the declared aggregate overshoot (Req 7.4) once from
    the façade/bounds derivation; ``snapshot`` is the only reader and returns a frozen
    :class:`QuotaReading` that cannot drift under later observations.
    """

    def __init__(self) -> None:
        # Real zeros from the first snapshot (R12.3): "no refills / no refusals / overshoot 0" is a
        # reported value, never an absent series.
        self._lease_overshoot = 0
        self._async_refill_total = 0
        self._budget_unavailable_total = 0

    def set_overshoot(self, overshoot: int) -> None:
        """Publish the declared aggregate lease overshoot (Req 7.4). O(1), overwrites the gauge.

        The façade/bounds derivation tells the producer the declared ``limit + overshoot`` bound
        once (at most one in-flight chunk per worker/replica); the producer reports it so the
        aggregate bound is observable (Req 7.4 / 12.4). A gauge, not a counter: the latest value
        wins. A negative value is rejected — the overshoot is a non-negative declared count.
        """
        if overshoot < 0:
            raise ValueError(f"overshoot must be a non-negative declared count, got {overshoot!r}")
        self._lease_overshoot = overshoot

    def observe_async_refill(self) -> None:
        """Record that one Async_Refill ran off the request path. O(1), no I/O (Req 12.4)."""
        self._async_refill_total += 1

    def observe_budget_unavailable(self) -> None:
        """Record one ``budget_unavailable`` refusal. O(1), no I/O (Req 12.4)."""
        self._budget_unavailable_total += 1

    def snapshot(self) -> QuotaReading:
        """Build the current reading — the only method that reads the accumulated state.

        Returns a frozen :class:`QuotaReading` carrying all three fixed series; the counters are
        copied by value so the returned reading cannot drift under later observations.
        """
        return QuotaReading(
            lease_overshoot=self._lease_overshoot,
            async_refill_total=self._async_refill_total,
            budget_unavailable_total=self._budget_unavailable_total,
        )
