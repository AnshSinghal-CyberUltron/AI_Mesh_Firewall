# Validates: Requirements 7.1 (fixed label-free series, real zeros, SERIES_COUNT == len(series()))
"""`IncidentMetrics`: 27 fixed scalars, real zeros, no tenant dimension."""

from __future__ import annotations

import inspect

from gateway_v2.admit import incident_metrics as im
from gateway_v2.admit.incident_metrics import IncidentMetrics
from gateway_v2.domain.incident import INCIDENT_CAUSES


def test_series_count_is_twenty_seven_and_pinned_to_the_rendered_set() -> None:
    metrics = IncidentMetrics()
    assert IncidentMetrics.SERIES_COUNT == 27 == len(metrics.series()) == 9 + 3 + 11 + 4


def test_every_series_is_a_prefixed_label_free_real_zero_except_the_age_sentinel() -> None:
    series = IncidentMetrics().series()
    assert all(k.startswith("amf_incident_") and "{" not in k for k in series)
    zeros = {k: v for k, v in series.items() if v != 0.0}
    assert zeros == {"amf_incident_killswitch_age_seconds": -1.0}


def test_no_recorder_accepts_a_tenant_key_or_request_dimension() -> None:
    forbidden = {"org", "org_id", "tenant", "key", "request_id", "model", "prompt"}
    for name, member in inspect.getmembers(IncidentMetrics, inspect.isfunction):
        if name.startswith(("observe_", "set_")):
            assert not forbidden & set(inspect.signature(member).parameters), name


def test_every_closed_cause_lands_on_a_series_and_unknown_ones_on_gate_error() -> None:
    metrics = IncidentMetrics()
    for cause in INCIDENT_CAUSES:
        if cause.startswith("cut_"):
            metrics.observe_cut(cause)
        elif cause in im.REFUSAL_SERIES:
            metrics.observe_refusal(cause)
    series = metrics.series()
    assert series["amf_incident_refused_model_total"] == 3.0  # model + unresolvable + unknown
    assert series["amf_incident_refused_gate_error_total"] == 2.0  # gate_error + unwired
    assert series["amf_incident_refused_reroute_refused_total"] == 2.0
    assert series["amf_incident_cut_killed_total"] == 1.0
    metrics.observe_refusal("totally-new-cause")
    assert metrics.series()["amf_incident_refused_gate_error_total"] == 3.0
    assert len(metrics.series()) == IncidentMetrics.SERIES_COUNT


def test_counters_gauges_and_the_snapshot_do_not_drift() -> None:
    metrics = IncidentMetrics()
    metrics.observe_reroute()
    metrics.observe_auto_isolate()
    metrics.set_isolated_models(2)
    metrics.set_killswitch_age(0.25)
    metrics.set_scope_generation(2)
    reading = metrics.snapshot()
    metrics.observe_reroute()
    assert reading.values["reroute_total"] == 1.0
    assert metrics.series()["amf_incident_reroute_total"] == 2.0
    assert reading.values["isolated_models"] == 2.0 and reading.values["scope_generation"] == 2.0
    metrics.set_killswitch_age(None)
    assert metrics.series()["amf_incident_killswitch_age_seconds"] == -1.0


def test_the_risk_family_adds_k_plus_other_plus_overflow_whatever_the_model_count() -> None:
    assert im.risk_family_series_count(8, 1, 1) == 10
    assert im.RISK_FAMILY == "amf_incident_risk_score"
