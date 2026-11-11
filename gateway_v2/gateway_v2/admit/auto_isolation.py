"""GW29 `AutoIsolator`: a risk-score trip becomes a process-local isolation, never an outage.

`evaluate(model)` runs after an outcome is recorded; `sweep()` runs on the `IncidentTicker`. Both
are cheap (one score, O(isolations)). Trigger: `score >= threshold`, the sample floor met (the
score is 0 below it), `distinct_error_orgs >= min(floor, distinct_orgs_seen)`, and no active
isolation for the model, signed or local.

On a trigger the isolator isolates ONLY if a destination exists (decision C6/C20): it asks the SAME
`decide_route` the request path uses whether the fallback is eligible right now, and when it is
not (no fallback named, fallback killed or isolated, constraints unknown, weaker compliance or
sensitivity, snapshot stale) it emits `AUTO_ISOLATE_SKIPPED` and creates nothing. Auto-isolation
therefore cannot turn a degraded model into a self-inflicted outage. Signed operator records are
not subject to this pre-check; an ineligible fallback there is the declared `REROUTE_REFUSED`.

It tightens only: it never removes a kill, never overrides, shortens or clears an operator
isolation (`IncidentState.add_local` refuses), and emits ISOLATE/RECOVER only for process-local
entries. Signed transitions belong to control. The notifier contract is at-least-once with dedup
key `model:origin`, so N workers tripping on one model produce N events with one key.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from gateway_v2.admit.incident_metrics import IncidentMetrics
from gateway_v2.admit.incident_state import IncidentState
from gateway_v2.admit.killswitch import KillSwitchSnapshot
from gateway_v2.admit.model_risk import ModelRiskTracker, RiskReading
from gateway_v2.dispatch.incident_routing import decide_route
from gateway_v2.domain.incident import (
    ConstraintSource,
    FallbackPolicy,
    IncidentEvent,
    IncidentEventKind,
    IsolationOrigin,
    IsolationRecord,
    ModelCatalog,
    ModelOutcome,
    ModelStatus,
    RouteOutcome,
)

__all__ = ("AutoIsolator", "IsolatorParams")

_SCOPE = "global"


@dataclass(frozen=True, slots=True)
class IsolatorParams:
    threshold: float
    min_distinct_orgs: int
    auto_duration_s: float


class AutoIsolator:
    """Turns a sustained risk score into a local isolation, or a recorded skip."""

    def __init__(
        self,
        *,
        state: IncidentState,
        risk: ModelRiskTracker,
        switches: KillSwitchSnapshot,
        fallbacks: FallbackPolicy,
        catalog: ModelCatalog,
        constraints: ConstraintSource,
        params: IsolatorParams,
        wall: Callable[[], float],
        emit: Callable[[IncidentEvent], None],
        metrics: IncidentMetrics | None = None,
    ) -> None:
        self._state = state
        self._risk = risk
        self._switches = switches
        self._fallbacks = fallbacks
        self._catalog = catalog
        self._constraints = constraints
        self._params = params
        self._wall = wall
        self._emit = emit
        self._metrics = metrics
        self._latched: set[str] = set()
        """Models whose current trip was already skipped: one event per trip, not per request."""

    def evaluate(self, model: str) -> str:
        """Check one model. Returns `none`, `isolated`, `skipped:<reason>` or `suppressed`."""
        reading = self._risk.score(model)
        if reading.score < self._params.threshold:
            self._latched.discard(model)
            return "none"
        floor = min(self._params.min_distinct_orgs, reading.distinct_orgs_seen)
        if reading.distinct_error_orgs < max(floor, 1) or model in self._latched:
            return "none"
        if self._state.active_isolation(model) is not None:
            return "none"
        fallback = self._fallbacks.fallback_for(model)
        if fallback is None:
            return self._skip(model, None, "no_fallback")
        if not self._fallback_eligible(model, fallback):
            return self._skip(model, fallback, "fallback_ineligible")
        now = self._wall()
        record = IsolationRecord(
            model, fallback, IsolationOrigin.AUTO, _reason(reading), now,
            now + self._params.auto_duration_s, False, 0, False,
        )
        if not self._state.add_local(record):
            return "suppressed"
        self._emit(self._event(IncidentEventKind.ISOLATE, model, fallback, record.reason, now))
        if self._metrics is not None:
            self._metrics.observe_auto_isolate()
        return "isolated"

    def sweep(self) -> None:
        """Emit RECOVER once per local expiry and reset risk windows; signed ends only reset."""
        result = self._state.sweep()
        now = self._wall()
        for model in result.local_expired:
            self._emit(self._event(IncidentEventKind.RECOVER, model, None, "expired", now))
            self._risk.reset_for_recovery(model)
            self._latched.discard(model)
            if self._metrics is not None:
                self._metrics.observe_recover()
        for model in result.signed_expired:
            self._risk.reset_for_recovery(model)
            self._latched.discard(model)
        if self._metrics is not None:
            self._metrics.set_isolated_models(len(self._state.snapshot_models()))

    def _fallback_eligible(self, model: str, fallback: str) -> bool:
        """Run the request path's own `decide_route` with the model treated as isolated."""
        evaluation = self._switches.evaluate("", fallback)
        if evaluation.stale:
            return False
        probe = IsolationRecord(
            model, fallback, IsolationOrigin.AUTO, "", None, None, False, 0, False,
        )
        decision = decide_route(
            model,
            ModelStatus(model, False, probe),
            self._state.status_of("", fallback, evaluation),
            self._catalog.profile(model),
            self._catalog.profile(fallback),
            self._constraints.for_request(None, "", model),
        )
        return decision.outcome is RouteOutcome.REROUTE

    def _skip(self, model: str, fallback: str | None, reason: str) -> str:
        self._latched.add(model)
        self._emit(self._event(
            IncidentEventKind.AUTO_ISOLATE_SKIPPED, model, fallback, reason, self._wall(),
        ))
        if self._metrics is not None:
            self._metrics.observe_auto_skipped()
        return f"skipped:{reason}"

    @staticmethod
    def _event(
        kind: IncidentEventKind, model: str, fallback: str | None, reason: str, now: float,
    ) -> IncidentEvent:
        origin = IsolationOrigin.AUTO
        return IncidentEvent(kind, _SCOPE, model, fallback, reason, origin, now,
                             f"{model}:{origin.value}")


def _reason(reading: RiskReading) -> str:
    if reading.on_probation:
        return "probation_failed"
    return "guard_rate" if reading.dominant is ModelOutcome.GUARD_HIT else "error_rate"
