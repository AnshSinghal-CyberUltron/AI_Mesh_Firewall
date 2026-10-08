"""Re-hydrator metrics. The control plane's own numbers, not a second opinion on the gateway's.

Separate from `gateway_v2.runtime.state_metrics` on purpose, and the split is not cosmetic. That
surface answers "what did THIS WORKER observe about the published state"; this one answers "what
did THIS RE-HYDRATOR do". Three of the alarms in
`deploy/observability/gw05b-state-freshness-alerts.yml` — `StatePublishPending`,
`StateStampWithheld` and `StateRehydratorSingleInstance` — can only be answered here, because a
write that is durable-but-unpublished is invisible to every gateway by construction. That
invisibility IS the SP2 defect.

Two rules inherited from the gateway surface, for the same measured reason:

* **No tenant-derived label, anywhere.** R2-10 measured a full shared-memory metric directory
  making 40% of new tenants' first requests return HTTP 500, after RC2 exported one series per
  org. The only labels here are `kind` (a four-member enum) and `reason` / `bound` (closed
  vocabularies from `rehydrate.py`), so `series()` returns the same keys on an estate of three
  tenants and of twenty-five thousand.
* **`SERIES_COUNT` is a fixed expression**, not a count of whatever happens to be emitted, so a
  series whose existence depends on the estate cannot be added by accident.

Recording is O(1) per round and reads nothing back. Durations are kept as sum / count / max
rather than as a bucketed histogram: a histogram's bucket boundaries are a capacity decision, and
the only question anyone asks of a 1 s round is "is it still inside the freshness budget", which
the max answers directly.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from gateway_v2.domain.state import StateKind
from state_control.rehydrate import (
    COUNTS,
    ENGAGED,
    FAULT_CATEGORIES,
    INDEX,
    INVALID,
    MISSING,
    STALE,
    STORE_AHEAD,
    VERSION,
    RoundSummary,
)

PREFIX = "amf_rehydration"

REPAIR_REASONS = (MISSING, INVALID, STORE_AHEAD, STALE, VERSION, COUNTS, INDEX, ENGAGED)
"""The reasons `repair` can be called with, as a closed vocabulary. `CONTENTS` is deep-only and
counted under the same label set, so it is included below rather than special-cased."""

CONTENTS_REASON = "contents"
ALL_REASONS = (*REPAIR_REASONS, CONTENTS_REASON)

ABSENT = -1.0
"""Gauge value for "this has never happened", so the series exists from the first scrape.

A series that appears and disappears cannot be alarmed on and cannot be rate-computed. R2-11's
"no baseline => omit" applies to counters, not to a gauge whose absence is itself the signal.
"""


@dataclass(frozen=True, slots=True)
class KindRehydrationMetrics:
    """One state kind. Every field is a scalar, so a kind cannot grow a dimension."""

    repairs: int = 0
    failures: int = 0
    publishes: int = 0
    records_restored: int = 0


@dataclass(frozen=True, slots=True)
class RehydrationMetrics:
    """A full reading. Keyed by closed enums throughout."""

    attempts: int = 0
    successes: int = 0
    failures: int = 0
    duration_sum_s: float = 0.0
    duration_max_s: float = 0.0
    stamps_written: int = 0
    stamps_withheld: int = 0
    stamps_degraded: int = 0
    stamp_races_lost: int = 0
    last_success_at: float = ABSENT
    active: int = 0
    publish_pending_oldest_s: float = ABSENT
    by_kind: Mapping[StateKind, KindRehydrationMetrics] = field(default_factory=dict)
    by_fault: Mapping[str, int] = field(default_factory=dict)
    by_reason: Mapping[str, int] = field(default_factory=dict)

    def series(self) -> dict[str, float]:
        """The flat, label-resolved series a registry would publish.

        The key set is a function of the StateKind enum and the two closed vocabularies alone.
        """
        out: dict[str, float] = {
            f"{PREFIX}_attempts_total": self.attempts,
            f"{PREFIX}_success_total": self.successes,
            f"{PREFIX}_failure_total": self.failures,
            f"{PREFIX}_duration_seconds_sum": self.duration_sum_s,
            f"{PREFIX}_duration_seconds_count": self.attempts,
            f"{PREFIX}_duration_seconds_max": self.duration_max_s,
            "amf_rehydrator_active": self.active,
            "amf_rehydrator_last_success_timestamp": self.last_success_at,
            "amf_state_stamp_written_total": self.stamps_written,
            "amf_state_stamp_withheld_total": self.stamps_withheld,
            "amf_state_stamp_degraded_total": self.stamps_degraded,
            "amf_state_stamp_races_lost_total": self.stamp_races_lost,
            "amf_state_publish_pending_oldest_seconds": self.publish_pending_oldest_s,
        }
        for kind in StateKind:
            metrics = self.by_kind.get(kind, KindRehydrationMetrics())
            tag = f'{{kind="{kind.value}"}}'
            out[f"{PREFIX}_repairs_total{tag}"] = metrics.repairs
            out[f"{PREFIX}_failure_total{tag}"] = metrics.failures
            out[f"{PREFIX}_publish_total{tag}"] = metrics.publishes
            out[f"{PREFIX}_records_restored_total{tag}"] = metrics.records_restored
        for category in FAULT_CATEGORIES:
            out[f'{PREFIX}_fault_total{{category="{category}"}}'] = self.by_fault.get(
                category, 0,
            )
        for reason in ALL_REASONS:
            out[f'{PREFIX}_repair_reason_total{{reason="{reason}"}}'] = self.by_reason.get(
                reason, 0,
            )
        return out


SERIES_COUNT = 13 + 4 * len(StateKind) + len(FAULT_CATEGORIES) + len(ALL_REASONS)
"""How many series this surface can ever produce. A constant, by construction.

13 process-level, four per kind, one per fault category, one per repair reason. R2-10 is why this
is an expression rather than `len(series())`: the test asserts the two agree, so a label whose
domain is not closed fails the gate instead of shipping.
"""


class RehydrationMetricsRecorder:
    """Consumes what `Rehydrator.round_once` already returns. O(1) per observation."""

    def __init__(self) -> None:
        self._metrics = RehydrationMetrics(active=1)
        self._kinds: dict[StateKind, KindRehydrationMetrics] = {}
        self._faults: dict[str, int] = {}
        self._reasons: dict[str, int] = {}

    def observe_round(self, summary: RoundSummary, *, duration_s: float, now: float) -> None:
        """One round. Takes the summary the re-hydrator already produced, so nothing is re-read."""
        for event in summary.repairs:
            current = self._kinds.get(event.kind, KindRehydrationMetrics())
            self._kinds[event.kind] = replace(
                current,
                repairs=current.repairs + 1,
                publishes=current.publishes + 1,
                records_restored=current.records_restored + event.records,
            )
            self._reasons[event.reason] = self._reasons.get(event.reason, 0) + 1
        for kind, category in summary.faults:
            current = self._kinds.get(kind, KindRehydrationMetrics())
            self._kinds[kind] = replace(current, failures=current.failures + 1)
            self._faults[category] = self._faults.get(category, 0) + 1
        stamped = summary.stamped
        self._metrics = replace(
            self._metrics,
            attempts=self._metrics.attempts + 1,
            successes=self._metrics.successes + (1 if summary.ok else 0),
            failures=self._metrics.failures + (0 if summary.ok else 1),
            duration_sum_s=self._metrics.duration_sum_s + duration_s,
            duration_max_s=max(self._metrics.duration_max_s, duration_s),
            stamps_written=self._metrics.stamps_written + (1 if stamped is not None else 0),
            stamps_withheld=self._metrics.stamps_withheld + (1 if stamped is None else 0),
            stamps_degraded=self._metrics.stamps_degraded
            + (1 if stamped is not None and stamped.degraded else 0),
            stamp_races_lost=self._metrics.stamp_races_lost
            + (1 if summary.stamp_race_lost else 0),
            last_success_at=now if summary.ok else self._metrics.last_success_at,
        )

    def observe_publish_pending(self, oldest_s: float | None) -> None:
        """The SP2 detector the runbook names: how old the oldest unpublished write is.

        `None` means "nothing is pending", which is 0 rather than the ABSENT sentinel: a healthy
        estate has a real answer to this question and it is zero.
        """
        self._metrics = replace(
            self._metrics,
            publish_pending_oldest_s=0.0 if oldest_s is None else oldest_s,
        )

    def stopping(self) -> None:
        self._metrics = replace(self._metrics, active=0)

    def snapshot(self) -> RehydrationMetrics:
        return replace(
            self._metrics,
            by_kind=dict(self._kinds),
            by_fault=dict(self._faults),
            by_reason=dict(self._reasons),
        )


def render(metrics: RehydrationMetrics) -> str:
    """Prometheus text exposition. Deliberately minimal: no registry, no client library.

    GW14d owns how metrics are published fleet-wide. This produces the numbers in the one format
    a scrape already understands, so `StateRehydratorSingleInstance` has a target to count.
    """
    lines = [f"{name} {value}" for name, value in sorted(metrics.series().items())]
    return "\n".join(lines) + "\n"
