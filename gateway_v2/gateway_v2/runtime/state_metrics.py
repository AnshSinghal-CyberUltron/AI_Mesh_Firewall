"""State-propagation metrics whose cardinality does not depend on the tenant count.

RC2 exported one series PER TENANT: `plan_version_info{org="...",version="..."}` for every org in
the snapshot, plus `killswitch_engaged{scope,key}` per engaged scope, and it rebuilt that label
set on the serving loop roughly once a second (finding F7). Round 1's C28 measured 50,003 series
per worker at 50,000 tenants; the round-2 scale lane flagged overflow of the shared-memory gauge
directory (512 slots per worker) at about 1,000 active orgs. R2-10 then measured the consequence
of a full directory: 40% of new tenants' first requests returned HTTP 500.

So this surface emits a FIXED set of series. The only label is `kind`, which is a closed
four-member enum, and `series()` therefore returns the same keys whether the estate holds three
tenants or twenty-five thousand — asserted directly in the tests rather than argued.

What is deliberately NOT here:

* **No org, tenant, key or scope label anywhere.** Not even for the engaged kill-switch set,
  which is normally tiny: a label whose domain is tenant-derived is unbounded in principle, and
  "it is small today" is how 50,003 series happened. The engaged SCOPES belong in a log line,
  where retention bounds them; the engaged COUNT belongs here.
* **No per-tenant plan version.** `plan_version_info{org,version}` answered "what is org X
  serving", which is a query, not a metric. The aggregate answer — how far behind the published
  position this worker is — is `amf_state_lag{kind="plan"}`, one series.
* **No registry, exposition format or `# TYPE` line.** That is GW14d's card. This produces the
  numbers; GW14d decides how they are published, and owns the bounded top-K that restores
  per-tenant visibility safely.

Recording is O(1) per round and reads nothing. Nothing in here walks a collection, so no metric
can become the thing it is measuring.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Protocol

from gateway_v2.domain.state import Cursor, StateKind
from gateway_v2.runtime.state_nudge import ListenerCounters
from gateway_v2.runtime.state_task import RoundReport

PREFIX = "amf_state"


class IdentityStats(Protocol):
    """What a reading needs from an identity cache.

    Declared structurally rather than imported: `admit` sits ABOVE `runtime` in the layer
    contract, so importing `admit.identity.CacheStats` here would invert the dependency.
    `CacheStats` satisfies this as it is, and the import-linter contract stays intact.
    """

    @property
    def held(self) -> int:
        ...

    @property
    def negative(self) -> int:
        ...

    @property
    def inflight(self) -> int:
        ...

    @property
    def evicted(self) -> int:
        ...

    @property
    def single_flight_joins(self) -> int:
        ...

    @property
    def feed_seq(self) -> int:
        ...


class EngagedView(Protocol):
    """What a reading needs from a kill-switch snapshot: counts, never scope names."""

    @property
    def engaged(self) -> bool:
        ...

    @property
    def count(self) -> int:
        ...


class FreshnessView(Protocol):
    """What a reading needs from GW05b's `StampView`.

    Structural like its siblings, for a different reason: `StampView` is in this same layer, so
    the import would be legal -- but declaring the surface keeps the recorder testable with a
    plain stub and keeps the metrics layer from reaching for anything the contract does not
    promise, such as the stamp's raw bytes.
    """

    def fresh(self, now: float | None = None) -> bool:
        ...

    def age_seconds(self, now: float | None = None) -> float | None:
        ...

    def deep_age_seconds(self, now: float | None = None) -> float | None:
        ...

    def floors(self) -> Mapping[StateKind, Cursor]:
        ...

    @property
    def verified_at(self) -> float | None:
        ...

    @property
    def degraded(self) -> bool:
        ...

    @property
    def missing(self) -> int:
        ...

    @property
    def invalid(self) -> int:
        ...

    @property
    def lapses(self) -> int:
        ...


@dataclass(frozen=True, slots=True)
class KindMetrics:
    """One state kind. Every field is a scalar, so a kind cannot grow a dimension."""

    rounds: int = 0
    rounds_failed: int = 0
    rounds_truncated: int = 0
    rounds_offloaded: int = 0
    records_applied: int = 0
    cursor: int = 0
    attested: int = 0

    @property
    def lag(self) -> int:
        """Records between what this worker has applied and what the store attests."""
        return max(self.attested - self.cursor, 0)


@dataclass(frozen=True, slots=True)
class IdentityMetrics:
    held: int = 0
    negative: int = 0
    inflight: int = 0
    evicted: int = 0
    single_flight_joins: int = 0
    cursor: int = 0


@dataclass(frozen=True, slots=True)
class KillSwitchMetrics:
    engaged_total: int = 0
    """How many scopes are engaged. NOT which ones -- that is a log line."""

    global_engaged: int = 0
    stale: int = 0


@dataclass(frozen=True, slots=True)
class NudgeMetrics:
    subscribed: int = 0
    dead: int = 0
    nudges: int = 0
    nudge_failures: int = 0
    messages: int = 0
    pings: int = 0


@dataclass(frozen=True, slots=True)
class FreshnessMetrics:
    """GW05b. The single most important operational reading in the state subsystem.

    `fresh` is what an alarm pages on. `age_seconds` is what it trends towards the bound -- and
    it must be alarmed on going NEGATIVE as well, because that means the gateway's clock and the
    re-hydrator's have drifted apart, which is the one dependency this design cannot remove.
    """

    fresh: int = 0
    age_seconds: float = -1.0
    """Seconds since the state was verified. -1 means no stamp has ever been seen."""

    verified_at_seconds: float = 0.0
    deep_age_seconds: float = -1.0
    degraded: int = 0
    missing: int = 0
    invalid: int = 0
    lapses: int = 0
    floors: Mapping[StateKind, int] = field(default_factory=dict)
    """Per kind, the stamp-derived floor position. Proves I2 holds in a live run."""


@dataclass(frozen=True, slots=True)
class StateMetrics:
    """A full reading. `by_kind` is keyed by a closed enum, so it holds at most four entries."""

    by_kind: Mapping[StateKind, KindMetrics]
    identity: IdentityMetrics
    killswitch: KillSwitchMetrics
    nudge: NudgeMetrics
    freshness: FreshnessMetrics = field(default_factory=FreshnessMetrics)

    def series(self) -> dict[str, float]:
        """The flat, label-resolved series a registry would publish.

        The key set is a function of the StateKind enum alone. It does not depend on how many
        tenants, keys or kill-switch scopes exist, which is the GW05c requirement and what the
        cardinality tests assert.
        """
        out: dict[str, float] = {}
        for kind in StateKind:
            metrics = self.by_kind.get(kind, KindMetrics())
            tag = f'{{kind="{kind.value}"}}'
            out[f"{PREFIX}_rounds_total{tag}"] = metrics.rounds
            out[f"{PREFIX}_rounds_failed_total{tag}"] = metrics.rounds_failed
            out[f"{PREFIX}_rounds_truncated_total{tag}"] = metrics.rounds_truncated
            out[f"{PREFIX}_rounds_offloaded_total{tag}"] = metrics.rounds_offloaded
            out[f"{PREFIX}_records_applied_total{tag}"] = metrics.records_applied
            out[f"{PREFIX}_cursor{tag}"] = metrics.cursor
            out[f"{PREFIX}_attested{tag}"] = metrics.attested
            out[f"{PREFIX}_lag{tag}"] = metrics.lag
            out[f"{PREFIX}_floor{tag}"] = self.freshness.floors.get(kind, 0)
        out[f"{PREFIX}_identity_held"] = self.identity.held
        out[f"{PREFIX}_identity_negative"] = self.identity.negative
        out[f"{PREFIX}_identity_inflight"] = self.identity.inflight
        out[f"{PREFIX}_identity_evicted_total"] = self.identity.evicted
        out[f"{PREFIX}_identity_single_flight_joins_total"] = (
            self.identity.single_flight_joins
        )
        out[f"{PREFIX}_identity_cursor"] = self.identity.cursor
        out[f"{PREFIX}_killswitch_engaged_total"] = self.killswitch.engaged_total
        out[f"{PREFIX}_killswitch_global"] = self.killswitch.global_engaged
        out[f"{PREFIX}_killswitch_stale"] = self.killswitch.stale
        out[f"{PREFIX}_nudge_subscribed_total"] = self.nudge.subscribed
        out[f"{PREFIX}_nudge_dead_total"] = self.nudge.dead
        out[f"{PREFIX}_nudge_total"] = self.nudge.nudges
        out[f"{PREFIX}_nudge_failures_total"] = self.nudge.nudge_failures
        out[f"{PREFIX}_nudge_messages_total"] = self.nudge.messages
        out[f"{PREFIX}_nudge_pings_total"] = self.nudge.pings
        out[f"{PREFIX}_stamp_fresh"] = self.freshness.fresh
        out[f"{PREFIX}_stamp_age_seconds"] = self.freshness.age_seconds
        out[f"{PREFIX}_stamp_verified_at_seconds"] = self.freshness.verified_at_seconds
        out[f"{PREFIX}_stamp_deep_age_seconds"] = self.freshness.deep_age_seconds
        out[f"{PREFIX}_stamp_degraded"] = self.freshness.degraded
        out[f"{PREFIX}_stamp_missing_total"] = self.freshness.missing
        out[f"{PREFIX}_stamp_invalid_total"] = self.freshness.invalid
        out[f"{PREFIX}_unverified_transitions_total"] = self.freshness.lapses
        return out


SERIES_COUNT = 9 * len(StateKind) + 6 + 3 + 6 + 8
"""How many series this surface can ever produce. A constant, by construction.

`9 *` is the eight per-kind round metrics plus GW05b's per-kind floor; the trailing `+ 8` is
freshness. R2-10 is the reason this is a fixed expression rather than a count of whatever
happens to be emitted: a full metric directory made 40% of new tenants' first requests fail
with HTTP 500, so a series whose existence depends on tenant count must not be addable by
accident.
"""


class StateMetricsRecorder:
    """Consumes what the propagation layer already returns. O(1) per observation."""

    def __init__(self) -> None:
        self._kinds: dict[StateKind, KindMetrics] = {}
        self._identity = IdentityMetrics()
        self._killswitch = KillSwitchMetrics()
        self._nudge = NudgeMetrics()
        self._freshness = FreshnessMetrics()

    def observe_round(self, report: RoundReport, *, attested: int | None = None) -> None:
        """One round. Takes the report the synchroniser already produced, so nothing is re-read."""
        current = self._kinds.get(report.kind, KindMetrics())
        self._kinds[report.kind] = replace(
            current,
            rounds=current.rounds + 1,
            rounds_failed=current.rounds_failed + (0 if report.ok else 1),
            rounds_truncated=current.rounds_truncated + (1 if report.truncated else 0),
            rounds_offloaded=current.rounds_offloaded + (1 if report.offloaded else 0),
            records_applied=current.records_applied + report.applied,
            cursor=max(current.cursor, report.cursor.feed_seq),
            attested=max(
                current.attested,
                report.cursor.feed_seq if attested is None else attested,
            ),
        )

    def observe_identity(self, stats: IdentityStats) -> None:
        self._identity = IdentityMetrics(
            held=stats.held,
            negative=stats.negative,
            inflight=stats.inflight,
            evicted=stats.evicted,
            single_flight_joins=stats.single_flight_joins,
            cursor=stats.feed_seq,
        )

    def observe_killswitch(self, view: EngagedView, *, stale: bool) -> None:
        """Only the COUNT of engaged scopes. Their names are tenant-derived cardinality."""
        self._killswitch = KillSwitchMetrics(
            engaged_total=view.count,
            global_engaged=1 if view.engaged else 0,
            stale=1 if stale else 0,
        )

    def observe_nudge(self, counters: ListenerCounters) -> None:
        self._nudge = NudgeMetrics(
            subscribed=counters.subscribed,
            dead=counters.dead,
            nudges=counters.nudges,
            nudge_failures=counters.nudge_failures,
            messages=counters.messages,
            pings=counters.pings,
        )

    def observe_freshness(self, view: FreshnessView) -> None:
        """One reading of GW05b's freshness view. Ages are -1 when nothing has been seen.

        A sentinel rather than an omitted series, because a series that appears and disappears
        cannot be alarmed on and cannot be rate-computed (R2-11: "no baseline => omit" applies
        to counters, not to a gauge whose absence is itself the signal).
        """
        self._freshness = FreshnessMetrics(
            fresh=1 if view.fresh() else 0,
            age_seconds=_or_absent(view.age_seconds()),
            verified_at_seconds=view.verified_at or 0.0,
            deep_age_seconds=_or_absent(view.deep_age_seconds()),
            degraded=1 if view.degraded else 0,
            missing=view.missing,
            invalid=view.invalid,
            lapses=view.lapses,
            floors={kind: cursor.feed_seq for kind, cursor in view.floors().items()},
        )

    def snapshot(self) -> StateMetrics:
        return StateMetrics(
            by_kind=dict(self._kinds),
            identity=self._identity,
            killswitch=self._killswitch,
            nudge=self._nudge,
            freshness=self._freshness,
        )


ABSENT = -1.0
"""Gauge value for "this has never happened", so the series exists from the first scrape."""


def _or_absent(value: float | None) -> float:
    return ABSENT if value is None else value
