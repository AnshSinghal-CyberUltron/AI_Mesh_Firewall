"""GW27 Task 4: the new label-free series (org suppression, org table, plan schema version)."""
from __future__ import annotations

from gateway_v2.admit.metrics import AdmissionMetrics
from gateway_v2.audit.metrics import SERIES_COUNT as AUDIT_SERIES_COUNT
from gateway_v2.audit.metrics import AuditMetricsRecorder
from gateway_v2.runtime import state_metrics
from tests.edge._publisher_fixture import build_publisher, small_k_contract


def test_org_limit_series_exist_as_real_zeros_then_move() -> None:
    metrics = AdmissionMetrics()
    first = metrics.snapshot()
    assert (first.org_limits_table_full_total, first.org_limits_slots_used) == (0, 0)
    metrics.observe_org_limits_table_full()
    metrics.set_org_slots_used(7)
    after = metrics.snapshot()
    assert (after.org_limits_table_full_total, after.org_limits_slots_used) == (1, 7)
    assert first.org_limits_table_full_total == 0


def test_audit_suppression_series_is_fixed_and_label_free() -> None:
    recorder = AuditMetricsRecorder()
    series = recorder.snapshot().series()
    assert series["amf_audit_suppressed_by_org_total"] == 0
    assert len(series) == AUDIT_SERIES_COUNT
    recorder.observe_suppressed_by_org(3)
    assert recorder.snapshot().series()["amf_audit_suppressed_by_org_total"] == 3


def test_publisher_counts_suppression_and_exposes_every_new_series() -> None:
    pub = build_publisher(small_k_contract(), lambda: 0.0)
    pub.observe_audit_suppressed()
    pub.observe_audit_suppressed()
    body = pub.exposition()
    assert "amf_audit_suppressed_by_org_total 2.0" in body or (
        "amf_audit_suppressed_by_org_total 2" in body
    )
    assert "amf_admit_org_limits_table_full_total" in body
    assert "amf_admit_org_limits_slots_used" in body
    assert "amf_state_plan_schema_version" in body


def test_plan_schema_version_is_one() -> None:
    assert state_metrics.PLAN_SCHEMA_VERSION == 1
