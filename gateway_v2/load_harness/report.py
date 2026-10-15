"""Per-run report value types for the load harness (B6 / R2-21).

A :class:`RunReport` is the immutable, frozen snapshot of ONE offered run — the GW20b per-run
diagnostic fields a knee is interpreted and signed from (Req 11). It is a pure value *carrier*: the
report does not compute the C4 p99 (the C4 collector / ``runtime.latency_slo`` own that), does not
run the ladder (``ladder.py`` owns the pass/fail decision), and above all does not *fabricate* any
measurement. Every field is a value the harness already measured or read elsewhere and hands to the
report to carry.

Read-from-the-gateway, never fabricated (Req 11.2, 11.3, 11.4; and the Error-Handling §
"GPU-busy never fabricated"). Three of the carried fields — ``owner_queue_wait``,
``event_loop_lag``, and the :class:`ReasonMix` — are READ from the gateway's own exposed telemetry
(GW14d exposition / per-request exports) at the client boundary (the driver). The report only
stores them; the harness never invents a value for them. The windowed GPU-busy field goes one step
further and is modeled as an explicit sum type (:class:`GpuBusy`) so the *absence* of a configured
source is a first-class value (``GpuBusy.UNAVAILABLE``) rather than a fabricated number (Req 11.6,
15.2).

The ``GpuBusy`` sum type (Req 11.5, 11.6, 15.2). ``GpuBusy`` is a frozen+slots two-arm sum:

- ``GpuBusy.available(value)`` — a windowed GPU-busy fraction READ from a configured source
  (``available is True``, ``value`` is the float).
- ``GpuBusy.UNAVAILABLE`` — the single module-level sentinel instance meaning "no GPU-busy source
  was configured" (``available is False``, ``value is None``).

Because the sentinel carries ``value is None`` and ``available is False``, there is NO float the
harness could print as a busy fraction when no source exists — the type makes fabrication
unrepresentable rather than merely discouraged. The real GPU-busy data source is a
Deferred_Cloud_Gate (Req 15.2): this module *defines and positions* the field but, with no source
wired locally, every local run reports ``GpuBusy.UNAVAILABLE``. ``UNAVAILABLE`` is a module-level
*frozen* instance, which is not a module-level mutable (the ``check_no_module_mutable`` gate keys on
list/dict/set), so the house conventions are kept.

The ``ReasonMix`` frozen representation (Req 11.4). ``ReasonMix`` is the per-run distribution of
outcome reasons (``admitted`` and each shed-or-error reason) over the OFFERED requests. It must be a
frozen value (``RunReport`` is frozen+slots and the ``check_no_module_mutable`` gate forbids a
module-level mutable), so the counts are stored as a sorted ``tuple[tuple[str, int], ...]`` of
``(reason, count)`` pairs — NOT a mutable ``dict`` field. :meth:`ReasonMix.from_counts` builds it
from an ordinary counts mapping (summing duplicate reasons, dropping nothing), and
:meth:`as_mapping` reconstructs a read-only mapping for callers that want lookup. The reasons are
the gateway's own outcome vocabulary (admitted + the shed/error reasons the telemetry reports); the
harness classifies nothing itself.

Constant-arrival upper-bound labeling (Req 1.6). ``RunReport`` carries ``arrival_mode_constant:
bool`` so a report produced under :class:`~load_harness.config.ArrivalMode.CONSTANT` can label its
published limits as *upper bounds* (constant arrivals understate tail pressure vs the Poisson
process), per Req 1.6. The report only records the fact; the labeling text is a presentation
concern.

Capacity-literal + house posture (Req 14.2, 14.3). This module holds NO capacity-position literal:
the SLO pass bound is not declared here — it is read from
``gateway_v2.runtime.resources.ResourceContract.target_p99_ms`` by the ladder/collector and handed
in as ``c4_passes`` / ``slo_bound_ms``. All value types are ``@dataclass(frozen=True, slots=True)``;
there is no module-level mutable; the module stays well under the 800-line / 120-line gate bounds.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar

__all__ = (
    "GpuBusy",
    "ReasonMix",
    "RunReport",
)


@dataclass(frozen=True, slots=True)
class GpuBusy:
    """A windowed GPU-busy reading as an explicit sum type (Req 11.5, 11.6, 15.2).

    Two arms, discriminated by ``available``:

    - ``available is True`` with a non-``None`` ``value`` — a windowed GPU-busy fraction READ from a
      configured source (build with :meth:`available`).
    - ``available is False`` with ``value is None`` — no GPU-busy source was configured; the single
      shared sentinel is :data:`GpuBusy.UNAVAILABLE`.

    The sum type is what makes fabrication *unrepresentable*: when no source exists there is simply
    no float to report, so a local run (where the real GPU-busy source is a Deferred_Cloud_Gate, Req
    15.2) reports :data:`UNAVAILABLE` rather than inventing a number (Req 11.6). Frozen + slots so a
    report is an immutable value (Req 14.3).
    """

    #: The single sentinel for "no GPU-busy source was configured" (Req 11.6, 15.2). Declared as a
    #: ``ClassVar`` so it is NOT a dataclass field (it does not become a constructor arg or a slot)
    #: and is bound to the one shared frozen instance just below the class. A ``ClassVar`` on a
    #: ``slots=True`` dataclass is the house-sanctioned way to carry a class-level constant.
    UNAVAILABLE: ClassVar[GpuBusy]

    available: bool
    value: float | None

    def __post_init__(self) -> None:
        """Enforce the two-arm invariant so no half-formed reading can exist.

        An ``available`` reading MUST carry a float ``value``; an unavailable reading MUST carry
        ``value is None``. Rejecting the mixed shapes at construction is what guarantees a consumer
        can trust ``available`` alone and never read a fabricated number out of an unavailable arm.
        """
        if self.available and self.value is None:
            raise ValueError("an available GpuBusy reading must carry a numeric value")
        if not self.available and self.value is not None:
            raise ValueError("an unavailable GpuBusy reading must not carry a value")

    @classmethod
    def available_reading(cls, value: float) -> GpuBusy:
        """Build an AVAILABLE reading carrying ``value`` read from a configured source.

        This is the only constructor that produces a reading with a float; it is never called when
        no source is configured, so no code path can turn "no source" into a number.
        """
        return cls(available=True, value=value)


# Bind the single shared sentinel to the ``GpuBusy.UNAVAILABLE`` class attribute declared above.
# This is a module-level FROZEN instance — not a module-level mutable (the
# ``check_no_module_mutable`` gate keys on list/dict/set literals and list()/dict()/set() calls) —
# so one shared sentinel is in-policy. Every local run carries this instance for its GPU-busy field
# because the real source is a Deferred_Cloud_Gate (Req 15.2).
GpuBusy.UNAVAILABLE = GpuBusy(available=False, value=None)


@dataclass(frozen=True, slots=True)
class ReasonMix:
    """The per-run distribution of outcome reasons over the OFFERED requests (Req 11.4).

    Stores the counts as a ``tuple[tuple[str, int], ...]`` of ``(reason, count)`` pairs, sorted by
    reason — NOT a mutable ``dict`` field — so a :class:`ReasonMix` is a genuinely frozen value and
    carries no module-level mutable (the house ``check_frozen_dataclasses`` /
    ``check_no_module_mutable`` gates). The reasons are the gateway's own outcome vocabulary:
    ``admitted`` plus each shed-or-error reason the telemetry reports. The harness READS this mix
    from the gateway's exposition and only carries it; it classifies no outcome itself.

    Build with :meth:`from_counts` from an ordinary counts mapping; recover a lookup view with
    :meth:`as_mapping`. :attr:`total` is the sum of all counts (the offered-request denominator).
    """

    counts: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        """Enforce the stored shape so the frozen value is well-formed and canonical.

        Reasons must be unique and sorted (canonical form ⇒ two mixes with the same distribution are
        equal), and no count may be negative. These are structural guarantees of the carried value,
        not a classification the harness performs.
        """
        reasons = [reason for reason, _ in self.counts]
        if reasons != sorted(reasons):
            raise ValueError("ReasonMix counts must be sorted by reason")
        if len(set(reasons)) != len(reasons):
            raise ValueError("ReasonMix counts must not repeat a reason")
        if any(count < 0 for _, count in self.counts):
            raise ValueError("ReasonMix counts must be non-negative")

    @classmethod
    def from_counts(cls, counts: Mapping[str, int]) -> ReasonMix:
        """Build a :class:`ReasonMix` from an ordinary ``{reason: count}`` mapping.

        Sorts by reason for a canonical frozen form and drops nothing (a zero count is kept so the
        report can show a reason that was possible but did not occur). The input mapping is READ,
        never mutated or stored by reference.
        """
        pairs = tuple(sorted((reason, int(count)) for reason, count in counts.items()))
        return cls(counts=pairs)

    @property
    def total(self) -> int:
        """The total offered-request count (sum of every reason's count)."""
        return sum(count for _, count in self.counts)

    def as_mapping(self) -> Mapping[str, int]:
        """A read-only ``{reason: count}`` view for callers that want lookup, not iteration."""
        return dict(self.counts)


@dataclass(frozen=True, slots=True)
class RunReport:
    """The frozen+slots per-run report — the GW20b diagnostic fields for one offered run (Req 11).

    A pure value carrier. Every field is measured or read elsewhere and handed in; the report
    computes and fabricates nothing.

    SLO (Req 11.1): ``c4_p99_ms`` is the run's C4 p99 (``+inf`` for an all-errored run),
    ``slo_bound_ms`` is the bound it was judged against (read from
    ``ResourceContract.target_p99_ms`` by the caller — never declared in this module), and
    ``c4_passes`` is the pass/fail outcome against that bound.

    Gateway telemetry (Req 11.2, 11.3, 11.4): ``owner_queue_wait_s``, ``event_loop_lag_s``, and
    ``reason_mix`` are READ from the gateway's exposed telemetry at the client boundary; the harness
    carries them unmodified.

    Generator self-proof (Req 1.4, 2.3, 2.4): ``schedule_drop_count`` is the number of
    Schedule_Drops (a non-zero count means the run is not a clean server measurement, Req 2.4), and
    ``loadgen_headroom`` is the margin between the rate the generator can issue and the configured
    offered rate (Req 2.3). ``generator_bound`` records whether the run was excluded from knee
    determination because headroom fell below the margin (Req 2.2).

    Realized strata (Req 3–7): ``realized_strata_shares`` is a frozen ``(name, share)`` tuple of the
    realized long-prompt / retry / secret-shaped shares, and ``distinct_keys`` / ``distinct_orgs`` /
    ``distinct_tenants`` are the identity cardinalities actually exercised.

    GPU-busy (Req 11.5, 11.6, 15.2): ``gpu_busy`` is a :class:`GpuBusy` sum value — an available
    reading from a configured source, or :data:`GpuBusy.UNAVAILABLE` (never a fabricated number).

    Arrival mode (Req 1.6): ``arrival_mode_constant`` is ``True`` when the run used constant
    arrivals, so its published limits are labeled upper bounds.
    """

    c4_p99_ms: float
    slo_bound_ms: float
    c4_passes: bool
    owner_queue_wait_s: float
    event_loop_lag_s: float
    reason_mix: ReasonMix
    schedule_drop_count: int
    loadgen_headroom: float
    generator_bound: bool
    realized_strata_shares: tuple[tuple[str, float], ...]
    distinct_keys: int
    distinct_orgs: int
    distinct_tenants: int
    gpu_busy: GpuBusy
    arrival_mode_constant: bool
