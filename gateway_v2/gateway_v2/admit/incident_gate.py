"""GW29 `IncidentGate`: phase 4b, the one place a request is refused, rerouted or let through.

Synchronous and RAM-only (no await, no store call): one `KillSwitchSnapshot.evaluate` for the
requested model, a second only on the isolated path. It returns a frozen `GateVerdict`; `edge`
renders the 503, the headers and the audit record. This module constructs no HTTP object.

Order (design 4.1, R1.2): resolve the model name, then stale, global, org, org-model, model, then
isolation. `canonical_model` runs BEFORE any kill matching, so a case/alias variant of a killed
model cannot bypass the kill, and the canonical name is the only form used afterwards (it is also
what goes upstream). `auto` is the routing sentinel, not a model: it is resolved through
`AMF_MODEL_AUTO_TARGET` or refused. An absent model is never defaulted.

One broad `except` sits at the boundary of `decide` / `recheck` / `decide_org`: a bug in the gate
must refuse (`kill_switch_unavailable`, cause `gate_error`), never admit. It logs the exception
class only, never content.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable, Mapping

from gateway_v2.admit.incident_metrics import IncidentMetrics
from gateway_v2.admit.incident_state import IncidentState
from gateway_v2.admit.killswitch import KillSwitchSnapshot
from gateway_v2.dispatch.incident_routing import decide_route
from gateway_v2.domain.incident import (
    NO_ROUTED_MODEL,
    ROUTING_SENTINEL,
    ConstraintSource,
    FallbackPolicy,
    GateVerdict,
    IncidentEvent,
    IncidentEventKind,
    KillEvaluation,
    ModelCatalog,
    ModelStatus,
    RouteDecision,
    RouteOutcome,
    canonical_model,
)
from gateway_v2.domain.posture import (
    KILL_SWITCH_ENGAGED,
    KILL_SWITCH_UNAVAILABLE,
    MIN_RETRY_AFTER_S,
    MODEL_ISOLATED_NO_FALLBACK,
    MODEL_KILLED,
    REROUTE_REFUSED,
    gap_retry_after_s,
)
from gateway_v2.domain.state_knobs import DEFAULT_REFRESH_PERIOD_MS, DEFAULT_REHYDRATE_PERIOD_MS

__all__ = ("IncidentGate",)

LOG = logging.getLogger("amf.gateway.incident")



class IncidentGate:
    """Implements `IncidentDecider`. Holds no request state; safe to share across requests."""

    def __init__(
        self,
        *,
        switches: KillSwitchSnapshot,
        state: IncidentState,
        catalog: ModelCatalog | None,
        aliases: Mapping[str, str],
        fallbacks: FallbackPolicy,
        constraints: ConstraintSource,
        retry_jitter_s: float,
        metrics: IncidentMetrics,
        emit: Callable[[IncidentEvent], None],
        rng: random.Random,
        wall: Callable[[], float],
        auto_target: str | None = None,
        rehydrate_period_ms: float = DEFAULT_REHYDRATE_PERIOD_MS,
        refresh_ms: float = DEFAULT_REFRESH_PERIOD_MS,
    ) -> None:
        self._switches = switches
        self._state = state
        self._catalog = catalog
        self._aliases = aliases
        self._fallbacks = fallbacks
        self._constraints = constraints
        self._jitter_s = retry_jitter_s
        self._metrics = metrics
        self._emit = emit
        self._rng = rng
        self._wall = wall
        self._auto_target = auto_target
        self._base_retry_s = max(
            MIN_RETRY_AFTER_S,
            gap_retry_after_s(rehydrate_period_ms=rehydrate_period_ms, refresh_ms=refresh_ms),
        )

    # --- public surface ------------------------------------------------------------------------

    def decide(self, *, org_id: str, requested_model: str, request_id: str) -> GateVerdict:
        try:
            return self._decide(org_id, requested_model)
        except Exception as exc:  # the single boundary: a gate bug refuses, it never admits
            return self._gate_error(exc)

    def recheck(self, *, org_id: str, model: str) -> GateVerdict:
        """Re-run kill + isolation for the ALREADY-ROUTED model. Never reroutes mid-request."""
        try:
            ev = self._switches.evaluate(org_id, model)
            code = self._kill_code(ev)
            if code is None and self._state.active_isolation(model) is not None:
                code = MODEL_ISOLATED_NO_FALLBACK
            if code is None:
                return GateVerdict.proceed(model)
            return self._refuse(code, "recheck")
        except Exception as exc:
            return self._gate_error(exc)

    def decide_org(self, *, org_id: str, request_id: str) -> GateVerdict:
        """Global + org scopes and snapshot freshness only: surfaces that name no routable model."""
        try:
            ev = self._switches.evaluate(org_id, NO_ROUTED_MODEL)
            if ev.stale:
                return self._refuse(KILL_SWITCH_UNAVAILABLE, "stale")
            if ev.global_engaged:
                return self._refuse(KILL_SWITCH_ENGAGED, "global")
            if ev.org_engaged:
                return self._refuse(KILL_SWITCH_ENGAGED, "org")
            return GateVerdict.proceed(NO_ROUTED_MODEL)
        except Exception as exc:
            return self._gate_error(exc)

    # --- the decision --------------------------------------------------------------------------

    def _decide(self, org_id: str, requested_model: str) -> GateVerdict:
        model, why = self._resolve_model(requested_model)
        if model is None:
            return self._refuse(MODEL_KILLED, why)
        ev = self._switches.evaluate(org_id, model)
        code = self._kill_code(ev)
        if code is not None:
            return self._refuse(code, self._kill_cause(ev))
        status = self._state.status_of(org_id, model, ev)
        if status.isolation is None:
            return GateVerdict.proceed(model)
        return self._route_isolated(org_id, model, status)

    def _resolve_model(self, raw: str) -> tuple[str | None, str]:
        """Canonical model name to check and send, or `(None, cause)` to refuse (4.6)."""
        model = canonical_model(raw, self._aliases)
        if model is None:
            return None, "unresolvable_model"
        if model == ROUTING_SENTINEL:
            target = self._auto_target
            if self._catalog is None or target is None:
                return None, "unknown_model"
            model = canonical_model(target, self._aliases)
            if model is None:
                return None, "unknown_model"
        if self._catalog is not None and self._catalog.profile(model) is None:
            return None, "unknown_model"
        return model, ""

    @staticmethod
    def _kill_code(ev: KillEvaluation) -> str | None:
        """The refusal code for the first applicable fact, in R1.2 order (stale, then scopes)."""
        if ev.stale:
            return KILL_SWITCH_UNAVAILABLE
        if ev.global_engaged or ev.org_engaged or ev.org_model_engaged:
            return KILL_SWITCH_ENGAGED
        if ev.model_engaged:
            return MODEL_KILLED
        return None

    @staticmethod
    def _kill_cause(ev: KillEvaluation) -> str:
        if ev.stale:
            return "stale"
        if ev.global_engaged:
            return "global"
        if ev.org_engaged:
            return "org"
        if ev.org_model_engaged:
            return "org_model"
        return "model"

    def _route_isolated(self, org_id: str, model: str, status: ModelStatus) -> GateVerdict:
        isolation = status.isolation
        fallback = None if isolation is None else isolation.fallback
        fb_status: ModelStatus | None = None
        if fallback is not None:
            fb_ev = self._switches.evaluate(org_id, fallback)
            if fb_ev.stale:
                return self._refuse(KILL_SWITCH_UNAVAILABLE, "stale")
            fb_status = self._state.status_of(org_id, fallback, fb_ev)
        profile = self._catalog.profile if self._catalog is not None else None
        decision = decide_route(
            model,
            status,
            fb_status,
            profile(model) if profile else None,
            profile(fallback) if profile and fallback else None,
            self._constraints.for_request(None, org_id, model),
        )
        if decision.outcome is RouteOutcome.REROUTE and decision.routed_model is not None:
            self._on_reroute(org_id, decision)
            return GateVerdict.proceed(decision.routed_model, decision)
        if decision.outcome is RouteOutcome.REFUSE_NO_FALLBACK:
            return self._refuse(MODEL_ISOLATED_NO_FALLBACK, "isolated_no_fallback")
        if decision.outcome is RouteOutcome.REFUSE_REROUTE:
            return self._refuse(REROUTE_REFUSED, "reroute_refused")
        return self._refuse(MODEL_KILLED, "model")

    # --- outcomes ------------------------------------------------------------------------------

    def _on_reroute(self, org_id: str, decision: RouteDecision) -> None:
        self._metrics.observe_reroute()
        if decision.isolated_model is None or decision.routed_model is None:
            return
        record = self._state.active_isolation(decision.isolated_model)
        if record is None:
            return
        event = IncidentEvent(
            IncidentEventKind.REROUTE, org_id, decision.isolated_model, decision.routed_model,
            decision.reason, record.origin, self._wall(),
            f"{decision.isolated_model}->{decision.routed_model}",
        )
        try:
            self._emit(event)
        except Exception as exc:  # a notification must never change the request's outcome
            LOG.warning("incident event emit failed: %s", type(exc).__name__)

    def _retry_after_s(self) -> float:
        return self._base_retry_s + self._rng.uniform(0.0, self._jitter_s)

    def _refuse(self, code: str, cause: str) -> GateVerdict:
        self._metrics.observe_refusal(cause)
        return GateVerdict.refuse(code, cause, self._retry_after_s())

    def _gate_error(self, exc: Exception) -> GateVerdict:
        LOG.error("incident gate error: %s", type(exc).__name__)
        try:
            self._metrics.observe_refusal("gate_error")
        except Exception:  # the counter must not turn a refusal into an exception
            LOG.error("incident gate metrics error")
        return GateVerdict.refuse(KILL_SWITCH_UNAVAILABLE, "gate_error", self._base_retry_s)
