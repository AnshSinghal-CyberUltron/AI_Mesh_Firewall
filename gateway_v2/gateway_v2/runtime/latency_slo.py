"""Firewall_Added_Delta SLO definition — the pure, client-side latency-sample half of GW16b (B5).

This module computes the firewall-added latency delta from **paired client-side** C4 latency
samples, the way the runbook §1.2 (R2-06/R2-11/R2-12, line 686) certifies the SLO: as the
difference between a through-firewall path and a Direct_Path baseline measured over the SAME
network, with the rare ≈ 40 ms lost-segment tails excluded from the firewall's account. The delta
is NEVER sourced from the gateway's own request-latency histogram, which GW14d measured to
under-read C4 by 3.2–5.8 ms and therefore cannot certify it (R5.3).

It is a pure `runtime/` computation: paired client-side samples in, a delta + an assert-within-SLO
decision out. It has no HTTP object, no I/O, and no upward import — so it sits below `edge` in the
import-linter layer order and adds no new import edge. The SLO bound is read from the one capacity
authority (`ResourceContract.target_p99_ms`); the module holds no capacity-position literal. The
≈ 40 ms `_LOST_SEGMENT_TAIL_MS` threshold is an operational / statistical constant (a measured
network artefact, not a CPU/RAM/FD-derived capacity position), allowed outside `resources.py` the
same way `serving_config.py`'s operational port/drain defaults are.

Task 1.1 (this landing) ships the frozen value types, the lost-segment-tail threshold, the
symmetric per-path tail-trim (`exclude_lost_segment_tails`), and the internal p99 helper. Task 1.2
appends `firewall_added_delta`, `DirectBaselineMissing`, and `assert_within_slo` on top of these.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gateway_v2.runtime.resources import ResourceContract

__all__ = (
    "DirectBaselineMissing",
    "FirewallDelta",
    "PairedPathSamples",
    "PathSamples",
    "assert_within_slo",
    "exclude_lost_segment_tails",
    "firewall_added_delta",
)

_LOST_SEGMENT_TAIL_MS = 40.0
"""The ≈ 40 ms lost-segment TCP tail threshold, in milliseconds (R2-12; R5.2).

An **operational / statistical** constant — a measured network artefact, NOT a capacity position.
Per the runbook R2-12 register row there are "≈ 40 ms lost-segment tails on every external path,
including a direct external IP with no firewall in it"; the tail is not the firewall's cost, so it
is trimmed symmetrically from both paths before the per-path p99 is taken. It is not derived from
CPU / RAM / FD, so it is NOT a capacity-position literal and is allowed outside
`runtime/resources.py` the same way `runtime/serving_config.py`'s operational port/drain defaults
are (`lint/check_capacity_literals.py`). A `float` named constant with this runbook-citing
rationale baked as the default — no bare literal at the call site; should an env override ever be
wired it would use the same warn-and-default idiom as `serving_config.py`.
"""


@dataclass(frozen=True, slots=True)
class PathSamples:
    """Immutable client-side C4 latency samples (milliseconds) for one measured path.

    The samples are the client-measured (C39 synthetic-client vantage) C4 latencies for a single
    path — either the through-firewall path or its Direct_Path baseline. They are client-side by
    construction: this type never carries, and the module never reads, the gateway's own
    request-latency histogram, which GW14d showed under-reads C4 and cannot certify the SLO (R5.3).
    """

    samples_ms: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class PairedPathSamples:
    """A through-firewall path and its Direct_Path baseline measured over the SAME network.

    `direct` is the baseline the firewall-added delta is taken against; its absence is a
    measurement error, not an absolute latency to charge the firewall (handled in task 1.2).
    """

    through_firewall: PathSamples
    direct: PathSamples


@dataclass(frozen=True, slots=True)
class FirewallDelta:
    """The computed firewall-added delta plus the tail-free p99s and exclusion counts.

    `delta_p99_ms` is `through_p99_ms - direct_p99_ms`, each the p99 of its path after the
    symmetric lost-segment-tail trim; `excluded_through` / `excluded_direct` are the per-path
    counts of trimmed tail samples, carried for evidence and honesty.
    """

    delta_p99_ms: float
    through_p99_ms: float
    direct_p99_ms: float
    excluded_through: int
    excluded_direct: int


def exclude_lost_segment_tails(samples: PathSamples) -> tuple[PathSamples, int]:
    """Drop every sample at or above `_LOST_SEGMENT_TAIL_MS`, returning the trimmed path + count.

    The symmetric per-path tail-trim (R5.2): a sample is excluded iff its latency is at or above
    the `_LOST_SEGMENT_TAIL_MS` threshold, so every retained sample is strictly below it. Returns a
    new `PathSamples` holding only the retained samples (original order preserved) together with
    the number of samples excluded.

    This is the honest definition for unpaired client fleets: client samples are not 1:1 paired in
    time, so a per-path threshold trim — applied identically to the through-firewall path and the
    Direct_Path before each p99 — keeps the ≈ 40 ms tail out of the firewall's account on both
    sides. (A paired per-sample subtraction that cancels a tail present on both paths was
    considered and rejected for exactly this reason; design decision #4.)
    """
    retained = tuple(ms for ms in samples.samples_ms if ms < _LOST_SEGMENT_TAIL_MS)
    excluded = len(samples.samples_ms) - len(retained)
    return PathSamples(retained), excluded


def _p99_ms(samples_ms: tuple[float, ...]) -> float:
    """Nearest-rank p99 of a non-empty latency-sample tuple, in milliseconds.

    Uses the nearest-rank definition: the value at rank `ceil(0.99 * n)` of the sorted samples
    (1-indexed), which for the 99th percentile returns an actual observed sample rather than an
    interpolated value — the honest quantile for a discrete client-measured latency set. Raises
    `ValueError` on empty input; task 1.2 wraps the empty-path semantics (an absent Direct_Path
    baseline is an error, never an absolute latency).
    """
    n = len(samples_ms)
    if n == 0:
        raise ValueError("p99 is undefined for an empty sample set")
    ordered = sorted(samples_ms)
    # Nearest-rank: rank = ceil(0.99 * n), 1-indexed; index = rank - 1, clamped to the last sample.
    rank = -(-99 * n // 100)  # ceil(0.99 * n) via integer arithmetic
    index = min(rank, n) - 1
    return ordered[index]


class DirectBaselineMissing(ValueError):
    """Raised when a firewall-added delta is requested with no usable Direct_Path baseline (R5.6).

    The Firewall_Added_Delta is a *difference* against a Direct_Path baseline measured over the
    same network; it is never an absolute latency charged to the firewall. When the baseline is
    absent — no direct samples at all, or every direct sample trimmed as a lost-segment tail so no
    tail-free baseline p99 exists — there is nothing to subtract against, so the delta is
    undefined and this error is raised rather than fabricating a number (R5.6). A `ValueError`
    subtype so callers that already handle bad input catch it without a new except clause.
    """


def firewall_added_delta(paired: PairedPathSamples) -> FirewallDelta:
    """Compute the Firewall_Added_Delta from paired client-side path samples (R5.1).

    `delta_p99_ms = p99(through_firewall, tails excluded) - p99(direct, tails excluded)`, each p99
    taken over its path after the symmetric lost-segment-tail trim (`exclude_lost_segment_tails`).
    Both sides are **client-side** by construction: the only inputs are `PathSamples` carrying
    client-measured C4 latencies (C39 synthetic-client vantage). This is computed ENTIRELY from the
    `PathSamples` inputs and NEVER reads the gateway's own request-latency histogram, which GW14d
    measured to under-read C4 by 3.2-5.8 ms and therefore cannot certify the SLO (R5.3).

    Baseline handling (R5.6): if the Direct_Path has no samples, or if every direct sample is a
    lost-segment tail (trimmed to empty), there is no baseline to subtract against and
    `DirectBaselineMissing` is raised — the firewall is never charged an absolute latency. If the
    baseline is present but the through-firewall path has no samples (or trims to empty), the delta
    would be meaningless; that is a measurement error and a `ValueError` is raised rather than
    returning a bogus (e.g. negative-of-the-baseline) delta.
    """
    if not paired.direct.samples_ms:
        raise DirectBaselineMissing(
            "Direct_Path baseline is required to compute a firewall-added delta "
            "(R5.6): no direct samples were provided, and the firewall is never "
            "charged an absolute latency.",
        )
    if not paired.through_firewall.samples_ms:
        raise ValueError(
            "through-firewall path has no samples: a firewall-added delta against a "
            "present baseline is meaningless with no through-firewall measurement.",
        )

    through_tail_free, excluded_through = exclude_lost_segment_tails(paired.through_firewall)
    direct_tail_free, excluded_direct = exclude_lost_segment_tails(paired.direct)

    # A path trimmed to empty has no tail-free p99. An empty tail-free BASELINE means there is no
    # baseline left to compare against (every direct sample was a lost-segment tail) -> treat it as
    # a missing baseline (R5.6), never an absolute latency. An empty tail-free THROUGH-path is a
    # measurement error -> raise rather than fabricate a delta.
    if not direct_tail_free.samples_ms:
        raise DirectBaselineMissing(
            "Direct_Path baseline is required to compute a firewall-added delta "
            "(R5.6): every direct sample was a lost-segment tail, leaving no "
            "tail-free baseline to compare against.",
        )
    if not through_tail_free.samples_ms:
        raise ValueError(
            "through-firewall path has no tail-free samples: every through-firewall "
            "sample was a lost-segment tail, so the delta cannot be measured.",
        )

    through_p99_ms = _p99_ms(through_tail_free.samples_ms)
    direct_p99_ms = _p99_ms(direct_tail_free.samples_ms)
    return FirewallDelta(
        delta_p99_ms=through_p99_ms - direct_p99_ms,
        through_p99_ms=through_p99_ms,
        direct_p99_ms=direct_p99_ms,
        excluded_through=excluded_through,
        excluded_direct=excluded_direct,
    )


def assert_within_slo(delta: FirewallDelta, contract: ResourceContract) -> None:
    """Raise iff the firewall-added delta p99 exceeds the request-path SLO bound (R5.4/R5.5).

    The bound is read from the one capacity authority, `ResourceContract.target_p99_ms` (gate
    G-11's "firewall-added delta < 20 ms p99", runbook SS0.5); this module holds NO
    capacity-position literal of its own (R5.5). Raises a `ValueError` naming the measured
    `delta_p99_ms` and the `target_p99_ms` bound it exceeded iff
    `delta.delta_p99_ms > contract.target_p99_ms`; an over-budget delta is never silently passed.

    The authoritative live certification of this bound against a real fleet C4 — the C39
    synthetic-client measurement driving the Firewall_Added_Delta over the plateau — is the
    deferred fleet seam owned by GW20; this assert is the pure, in-process gate on an already
    measured delta.
    """
    if delta.delta_p99_ms > contract.target_p99_ms:
        raise ValueError(
            f"firewall-added delta p99 {delta.delta_p99_ms} ms exceeds the SLO bound "
            f"target_p99_ms={contract.target_p99_ms} ms (R5.4)",
        )
