# Feature: gw29-killswitch-isolation, Property 10: the rendered series count equals the declared one
# Validates: Requirements 7.1, 7.3 (AC29)
"""Property 10: with the incident producer registered, the Publisher renders EXACTLY the declared
series count, whatever the tenant count, the model count (beyond K) or the metric values.

Seeded ``random.Random``; the Publisher itself asserts ``rendered == SERIES_COUNT`` on every
scrape, so a mismatch fails loudly even before the explicit checks below.
"""
from __future__ import annotations

import random

from gateway_v2.admit.incident_metrics import IncidentMetrics
from gateway_v2.admit.model_risk import ModelRiskTracker, RiskParams
from gateway_v2.domain.incident import ModelOutcome
from gateway_v2.edge.incident_publish import IncidentPublication, incident_series_count
from gateway_v2.edge.publisher import Publisher
from tests.edge._publisher_fixture import small_k_contract
from tests.edge.test_publisher_exposition_count_property import _count_physical_lines

_SEED = 0x0629_0010
_ITERATIONS = 300


class _Clock:
    now = 100.0

    def __call__(self) -> float:
        return self.now


def _publishers(contract: object, clock: _Clock) -> tuple[Publisher, Publisher, IncidentMetrics,
                                                           ModelRiskTracker]:
    from tests.edge._publisher_fixture import build_publisher
    base = build_publisher(contract, clock)  # type: ignore[arg-type]
    metrics = IncidentMetrics()
    tracker = ModelRiskTracker(
        clock=clock, params=RiskParams.from_contract(contract),  # type: ignore[arg-type]
        max_models=contract.risk_max_models(),  # type: ignore[attr-defined]
        known=lambda model: True,
    )
    from gateway_v2.admit.metrics import AdmissionMetrics, QuotaMetrics
    from gateway_v2.audit.metrics import AuditMetrics
    from gateway_v2.edge.event_log import StructuredEventLog
    from gateway_v2.runtime.holdback_metrics import HoldbackMetrics
    from gateway_v2.runtime.state_metrics import StateMetricsRecorder
    from gateway_v2.runtime.stream_metrics import PerRequestExports
    from state_control.metrics import RehydrationMetricsRecorder
    with_incident = Publisher.from_contract(
        admission=AdmissionMetrics(), quota=QuotaMetrics(), audit=AuditMetrics(),
        rehydration=RehydrationMetricsRecorder().snapshot(),
        state=StateMetricsRecorder().snapshot(), stream=PerRequestExports(),
        holdback=HoldbackMetrics(),
        event_log=StructuredEventLog.from_contract(contract, clock=clock),  # type: ignore[arg-type]
        contract=contract,  # type: ignore[arg-type]
        clock=clock,
        incident=IncidentPublication(
            metrics=metrics, risk=tracker, contract=contract,  # type: ignore[arg-type]
            refresh=lambda: None,
        ),
    )
    return base, with_incident, metrics, tracker


def test_incident_series_are_declared_and_rendered_exactly() -> None:
    contract = small_k_contract()
    clock = _Clock()
    base, full, _metrics, _tracker = _publishers(contract, clock)
    assert full.SERIES_COUNT == base.SERIES_COUNT + incident_series_count(contract)
    assert incident_series_count(contract) == IncidentMetrics.SERIES_COUNT + (
        contract.metrics_top_k() + 2
    )
    delta = _count_physical_lines(full.exposition()) - _count_physical_lines(base.exposition())
    assert delta == incident_series_count(contract)


def test_the_count_is_invariant_to_tenants_models_and_values() -> None:
    contract = small_k_contract()
    clock = _Clock()
    _base, full, metrics, tracker = _publishers(contract, clock)
    rng = random.Random(_SEED)
    outcomes = list(ModelOutcome)
    top_k = contract.metrics_top_k()
    for i in range(_ITERATIONS):
        ctx = f"seed={_SEED:#x} iter={i}"
        for _ in range(rng.randrange(0, 12)):
            full.count_tenant(f"org-{rng.randrange(0, 500)}")
        for _ in range(rng.randrange(0, 12)):
            tracker.observe(
                f"model-{rng.randrange(0, 60)}", rng.choice(outcomes), f"org-{rng.randrange(8)}",
            )
        for _ in range(rng.randrange(0, 6)):
            metrics.observe_refusal(rng.choice(("stale", "global", "model", "recheck", "weird")))
            metrics.observe_cut(rng.choice(("cut_killed", "cut_key_revoked", "x")))
        clock.now += rng.random() * 3
        body = full.exposition()
        series = [ln for ln in body.split("\n") if ln and not ln.startswith("# TYPE ")]
        risk_lines = [ln for ln in series if ln.startswith("amf_incident_risk_score{")]
        assert len(risk_lines) == top_k, f"{ctx}: risk family not padded to exactly K"
        assert sum(ln.startswith("amf_incident_") for ln in series) == (
            IncidentMetrics.SERIES_COUNT + top_k + 2
        ), ctx


def test_every_incident_family_is_typed_once() -> None:
    contract = small_k_contract()
    _base, full, _m, _t = _publishers(contract, _Clock())
    types = [ln for ln in full.exposition().split("\n") if ln.startswith("# TYPE amf_incident_")]
    names = [ln.split(" ")[2] for ln in types]
    assert len(names) == len(set(names)), "one # TYPE per family"
    assert "amf_incident_risk_score" in names and "amf_incident_refused_stale_total" in names
    assert dict(ln.split(" ")[2:4] for ln in types)["amf_incident_refused_stale_total"] == "counter"
