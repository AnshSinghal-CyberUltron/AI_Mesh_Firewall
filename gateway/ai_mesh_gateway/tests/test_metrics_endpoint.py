"""Tests for the Prometheus /metrics endpoint and recorder helpers."""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def metrics_module():
    """Reload the metrics module so each test gets a fresh registry."""
    import ai_mesh_gateway.metrics as m

    importlib.reload(m)
    return m


def test_render_latest_returns_prometheus_text(metrics_module):
    body, content_type = metrics_module.render_latest()
    assert isinstance(body, (bytes, bytearray))
    assert "text/plain" in content_type or content_type.startswith("text/plain")


def test_metric_names_present_after_recording(metrics_module):
    metrics_module.record_request("acme", "allowed", 0.123)
    metrics_module.record_request("acme", "blocked", 0.045)
    metrics_module.record_policy_block("acme", "semantic_injection")
    metrics_module.record_rate_limit("acme", "gpt-oss-120b", allowed=False)
    metrics_module.record_kill_switch("gpt-oss-120b", "block")
    metrics_module.record_bedrock_embed("success")
    metrics_module.inc_active_connections(1)

    body, _ = metrics_module.render_latest()
    text = body.decode("utf-8")

    for name in (
        "amf_gateway_requests_total",
        "amf_gateway_request_latency_seconds",
        "amf_gateway_active_connections",
        "amf_gateway_policy_blocks_total",
        "amf_gateway_rate_limit_total",
        "amf_gateway_kill_switch_triggers_total",
        "amf_gateway_bedrock_embed_total",
    ):
        assert name in text, f"missing metric {name} in /metrics output"

    # decision label rendered for both allowed and blocked
    assert 'decision="allowed"' in text
    assert 'decision="blocked"' in text
    assert 'org="acme"' in text


def test_labels_are_bounded_and_lowercased(metrics_module):
    metrics_module.record_request("  ACME-Corp  ", "ALLOWED", 0.01)
    body, _ = metrics_module.render_latest()
    text = body.decode("utf-8")
    assert 'org="acme-corp"' in text
    assert 'decision="allowed"' in text


def test_long_label_values_are_truncated(metrics_module):
    long_org = "x" * 200
    metrics_module.record_request(long_org, "allowed", 0.01)
    body, _ = metrics_module.render_latest()
    text = body.decode("utf-8")
    # 64-char cap from _safe_label
    assert f'org="{"x" * 64}"' in text
    assert f'org="{"x" * 65}"' not in text


def test_anonymous_default_when_org_missing(metrics_module):
    metrics_module.record_request("", "allowed", 0.01)
    metrics_module.record_request(None, "allowed", 0.01)
    body, _ = metrics_module.render_latest()
    text = body.decode("utf-8")
    assert 'org="anonymous"' in text


def test_invalid_latency_does_not_raise(metrics_module):
    # Should silently drop bad latency observations instead of 500-ing the request.
    metrics_module.record_request("acme", "allowed", "not-a-float")  # type: ignore[arg-type]


def test_pipeline_stage_and_chat_completion_metrics(metrics_module):
    metrics_module.record_chat_completion(
        "acme",
        "success",
        1.25,
        {
            "auth_ms": 2.0,
            "policy_ms": 15.0,
            "tier1_ms": 40.0,
            "upstream_ms": 800.0,
            "telemetry_enqueue_ms": 3.0,
        },
    )
    body, _ = metrics_module.render_latest()
    text = body.decode("utf-8")
    assert "amf_gateway_chat_completions_total" in text
    assert "amf_gateway_chat_request_duration_seconds" in text
    assert "amf_gateway_pipeline_stage_seconds" in text
    assert 'stage="policy"' in text
    assert 'stage="upstream"' in text
    assert 'outcome="success"' in text
