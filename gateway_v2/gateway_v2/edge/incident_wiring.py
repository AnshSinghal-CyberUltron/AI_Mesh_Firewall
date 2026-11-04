"""GW29 incident wiring: one object that holds every incident collaborator (design 6.5, 5.5).

:func:`build_incident` assembles the real :class:`~gateway_v2.admit.incident_gate.IncidentGate`,
the per-stream :class:`~gateway_v2.edge.inflight_source.KillSwitchControlFactory`, the risk
tracker, the auto-isolator, the revocation ledger, the notifier pump and ticker, and the metrics
producer, around the worker's :class:`~gateway_v2.admit.killswitch.KillSwitchSnapshot`.

The catalog (and so the alias table the snapshot parser needs) is read BEFORE the state runtime is
built: call :func:`~gateway_v2.edge.incident_catalog.load_catalog_config` first, give its
``aliases`` to the runtime, then call :func:`build_incident` with the same config.

``outcome_for`` is the ONE place a provider attempt becomes a risk sample (design 5.5). It maps a
closed set of provider facts and never looks at request content, so a tenant cannot trip a model
isolation with malformed requests or its own bad key (client-attributable statuses count nothing).
"""
from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from gateway_v2.admit.auto_isolation import AutoIsolator, IsolatorParams
from gateway_v2.admit.incident_gate import IncidentGate
from gateway_v2.admit.incident_metrics import CUT_SERIES, IncidentMetrics
from gateway_v2.admit.incident_state import IncidentState, IsolationView
from gateway_v2.admit.model_risk import ModelRiskTracker, RiskParams
from gateway_v2.admit.revocation import RevocationLedger
from gateway_v2.audit.record import AuditRecord
from gateway_v2.domain.incident import KS_SCOPE_GENERATION, ModelOutcome, Notifier
from gateway_v2.edge.incident_attempt import outcome_for
from gateway_v2.edge.incident_catalog import CatalogConfig, load_catalog_config
from gateway_v2.edge.inflight_source import KillSwitchControlFactory
from gateway_v2.edge.notifier_pump import (
    IncidentTicker,
    LoggingNotifier,
    NotifierPump,
    RerouteLimiter,
)
from gateway_v2.edge.state_runtime import StateRuntime
from gateway_v2.runtime.resources import ResourceContract

__all__ = ("IncidentAuditTap", "IncidentWiring", "build_incident", "outcome_for")

LOG = logging.getLogger("amf.gateway.incident")
_MS_PER_S = 1000.0

class IncidentAuditTap:
    """Wraps the audit sink: counts incident cuts, then forwards. Never raises, never blocks.

    With no downstream sink the record is dropped and ``False`` is returned (the lifecycle ignores
    the return value), so wiring the tap cannot create a sink that was not configured.
    """

    __slots__ = ("_downstream", "_feed", "_metrics")

    def __init__(
        self,
        downstream: Callable[[AuditRecord], bool] | None,
        metrics: IncidentMetrics,
    ) -> None:
        self._downstream = downstream
        self._metrics = metrics
        self._feed: Callable[..., None] | None = None

    def bind_attempts(self, feed: Callable[..., None]) -> None:
        """Late-bind the risk feed (the tap exists before the tracker it feeds)."""
        self._feed = feed

    def record_attempt(self, *, org_id: str, model: str, outcome: ModelOutcome | None) -> None:
        """One provider attempt for the risk tracker. Never raises; no feed bound counts nothing."""
        feed = self._feed
        if feed is not None:
            feed(org_id=org_id, model=model, outcome=outcome)

    def __call__(self, record: AuditRecord) -> bool:
        try:
            cause = record.detail.get("cause")
            if isinstance(cause, str) and cause in CUT_SERIES:
                self._metrics.observe_cut(cause)
        except Exception as exc:  # noqa: BLE001 - counting must never fail an audit emit
            LOG.warning("incident audit tap failed: %s", type(exc).__name__)
        if self._downstream is None:
            return False
        return self._downstream(record)


@dataclass(frozen=True, slots=True)
class IncidentWiring:
    """Every incident collaborator for one worker."""

    gate: IncidentGate
    control_factory: KillSwitchControlFactory
    state: IncidentState
    pump: NotifierPump
    ticker: IncidentTicker
    risk: ModelRiskTracker
    isolator: AutoIsolator
    ledger: RevocationLedger
    metrics: IncidentMetrics
    audit: IncidentAuditTap
    config: CatalogConfig
    runtime: StateRuntime

    def read_model(self) -> tuple[IsolationView, ...]:
        """The display read model GW35 mounts: every active isolation, same expiry as the gate."""
        return self.state.snapshot_models()

    def record_attempt(self, *, org_id: str, model: str, outcome: ModelOutcome | None) -> None:
        """Feed one provider attempt to the tracker, then let the isolator look. Never raises."""
        if outcome is None:
            return
        try:
            self.risk.observe(model, outcome, org_id)
            self.isolator.evaluate(model)
        except Exception as exc:  # noqa: BLE001 - risk accounting never changes a request outcome
            LOG.warning("incident risk accounting failed: %s", type(exc).__name__)

    def refresh_gauges(self) -> None:
        """Copy the live read-model counts onto the producer. RAM only; called on every scrape."""
        switches = self.runtime.switches
        view = switches.view()
        self.metrics.set_engaged_scopes(max(0, view.count - self.state.expired_signed_count()))
        self.metrics.set_quarantined(view.quarantined)
        self.metrics.set_isolated_models(len(self.state.snapshot_models()))
        self.metrics.set_local_evicted(self.state.local_evicted_total)
        self.metrics.set_risk_untracked(self.risk.untracked_total)
        self.metrics.set_scope_generation(KS_SCOPE_GENERATION)
        self.metrics.set_killswitch_age(switches.refresh_age_seconds())

    def ready(self) -> tuple[bool, str | None]:
        """``/readyz`` check: the wiring is built, and a configured catalog has a digest."""
        if self.config.catalog is not None and not self.config.digest:
            return False, "incident_catalog"
        return True, None

    def status_line(self) -> str:
        digest = self.config.digest[:12] or "none"
        return (
            "boot: kill switch -> enforced at admission and per chunk; scopes "
            f"global,org,model,org-model,isolation (scope generation {KS_SCOPE_GENERATION}, "
            f"catalog digest {digest}, fallbacks {len(self.config.fallbacks.models())} models)"
        )

    async def startup(self) -> None:
        self.pump.start()
        self.ticker.start()

    async def shutdown(self) -> None:
        await self.ticker.stop()
        await self.pump.stop()


def build_incident(
    runtime: StateRuntime,
    contract: ResourceContract,
    *,
    env: Mapping[str, str],
    clock: Callable[[], float],
    wall: Callable[[], float],
    rng: random.Random,
    config: CatalogConfig | None = None,
    notifier: Notifier | None = None,
    audit: Callable[[AuditRecord], bool] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> IncidentWiring:
    """Assemble the incident components around ``runtime.switches``.

    Raises ``CapacityUnset`` / ``CapacityUnavailable`` when an incident knob cannot be derived and
    ``CatalogError`` when ``config`` is None and the catalog inputs are invalid: the boot refuses.
    """
    cfg = load_catalog_config(env) if config is None else config
    metrics = IncidentMetrics()
    tap = IncidentAuditTap(audit, metrics)
    risk = _risk_tracker(contract, cfg, clock)
    state = IncidentState(
        switches=runtime.switches, wall=wall, clock=clock,
        local_slots=contract.local_isolation_slots(),
        auto_duration_s=contract.auto_isolation_duration_s(),
        max_hold_s=contract.max_isolation_hold_s(),
        probation_hook=risk.reset_for_recovery,
    )
    pump = NotifierPump(
        slots=contract.incident_event_queue_slots(),
        notifier=LoggingNotifier() if notifier is None else notifier,
        timeout_s=contract.notifier_timeout_s(),
        limiter=RerouteLimiter(
            window_s=contract.incident_event_window_s(),
            slots=contract.incident_event_queue_slots(), clock=clock,
        ),
        metrics=metrics, wall=wall, audit=tap,
    )
    gate = IncidentGate(
        switches=runtime.switches, state=state, catalog=cfg.catalog, aliases=cfg.aliases,
        fallbacks=cfg.fallbacks, constraints=cfg.constraints,
        retry_jitter_s=contract.incident_retry_jitter_s(), metrics=metrics, emit=pump.emit,
        rng=rng, wall=wall, auto_target=cfg.auto_target, refresh_ms=float(runtime.refresh_ms),
    )
    isolator = AutoIsolator(
        state=state, risk=risk, switches=runtime.switches, fallbacks=cfg.fallbacks,
        catalog=cfg.model_catalog, constraints=cfg.constraints,
        params=IsolatorParams(
            threshold=contract.risk_threshold(),
            min_distinct_orgs=contract.risk_min_distinct_orgs(),
            auto_duration_s=contract.auto_isolation_duration_s(),
        ),
        wall=wall, emit=pump.emit, metrics=metrics,
    )
    ledger = RevocationLedger(
        clock=clock, slots=contract.revocation_ledger_slots(),
        retention_s=contract.max_stream_duration_s() + runtime.fresh_ms / _MS_PER_S,
        on_floor_cut=metrics.observe_revocation_floor_cut,
    )
    wiring = IncidentWiring(
        gate=gate, control_factory=KillSwitchControlFactory(runtime.switches, ledger),
        state=state, pump=pump,
        ticker=IncidentTicker(
            sweeper=isolator, period_s=contract.risk_bucket_s(), sleep=sleep, metrics=metrics,
        ),
        risk=risk, isolator=isolator, ledger=ledger, metrics=metrics, audit=tap,
        config=cfg, runtime=runtime,
    )
    tap.bind_attempts(wiring.record_attempt)
    return wiring


def _risk_tracker(
    contract: ResourceContract, cfg: CatalogConfig, clock: Callable[[], float],
) -> ModelRiskTracker:
    """Only operator-known models are tracked: catalog models and fallback-map participants."""
    named = cfg.fallbacks.models()
    return ModelRiskTracker(
        clock=clock, params=RiskParams.from_contract(contract),
        max_models=contract.risk_max_models(),
        known=lambda model: cfg.knows(model) or model in named,
    )
