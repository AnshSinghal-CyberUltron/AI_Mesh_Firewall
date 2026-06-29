"""Lightweight metrics emitter (logging-first).

This module provides simple functions to record Bedrock call metrics.
All Bedrock metrics are also forwarded to the dedicated bedrock_logger
for clean, separate log files in production.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

LOG = logging.getLogger("gateway.metrics")

try:
    from bedrock_logger import log_metrics as _blog_metrics, log_rule_hit as _blog_rule_hit
except Exception:
    _blog_metrics = None  # type: ignore[assignment]
    _blog_rule_hit = None  # type: ignore[assignment]


def record_bedrock_call(method: str, model: str, tokens_in: int, tokens_out: int, elapsed_s: float, success: bool = True) -> None:
    payload: Dict[str, Any] = {
        "method": method,
        "model": model,
        "tokens_in": int(tokens_in),
        "tokens_out": int(tokens_out),
        "elapsed_s": float(elapsed_s),
        "success": bool(success),
    }
    LOG.info("bedrock_call_metrics %s", payload)
    if _blog_metrics:
        _blog_metrics(
            method=method, model=model,
            tokens_in=int(tokens_in), tokens_out=int(tokens_out),
            elapsed_s=float(elapsed_s), success=bool(success),
        )


def record_rule_hit(rule_id: str, severity: str, source: str = "bedrock") -> None:
    LOG.info("rule_hit %s", {"rule_id": rule_id, "severity": severity, "source": source})
    if _blog_rule_hit:
        _blog_rule_hit(rule_id=rule_id, severity=severity, source=source)


# ─────────────────────────────────────────────────────────────────────────────
# Prometheus metrics for AI Mesh Firewall (Module 1 Phase D)
#
# Counters/gauges/histograms only. Label values are bounded to non-PII fields
# (org_slug, model name, coarse reason). No prompts, emails, IPs, or free-text.
# A dedicated CollectorRegistry keeps /metrics output focused on AMF series.
# ─────────────────────────────────────────────────────────────────────────────
try:
    from prometheus_client import (
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )
    from prometheus_client.exposition import CONTENT_TYPE_LATEST as _PROM_CONTENT_TYPE
    _PROM_AVAILABLE = True
except Exception:  # pragma: no cover - prometheus_client missing in non-gateway envs
    _PROM_AVAILABLE = False
    _PROM_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
    REGISTRY = None  # type: ignore[assignment]


if _PROM_AVAILABLE:
    REGISTRY = CollectorRegistry(auto_describe=True)

    gateway_requests_total = Counter(
        "amf_gateway_requests_total",
        "Total chat completion requests, labeled by org and final decision.",
        ["org", "decision"],
        registry=REGISTRY,
    )
    gateway_request_latency_seconds = Histogram(
        "amf_gateway_request_latency_seconds",
        "End-to-end gateway request latency in seconds.",
        ["org", "decision"],
        buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
        registry=REGISTRY,
    )
    gateway_active_connections = Gauge(
        "amf_gateway_active_connections",
        "In-flight requests currently being processed.",
        registry=REGISTRY,
    )
    policy_blocks_total = Counter(
        "amf_gateway_policy_blocks_total",
        "Requests blocked, labeled by org and high-level reason category.",
        ["org", "reason"],
        registry=REGISTRY,
    )
    rate_limit_total = Counter(
        "amf_gateway_rate_limit_total",
        "Per-model rate-limit decisions per org.",
        ["org", "model", "result"],
        registry=REGISTRY,
    )
    kill_switch_triggers_total = Counter(
        "amf_gateway_kill_switch_triggers_total",
        "Kill-switch evaluations that fired, labeled by model and action.",
        ["model", "action"],
        registry=REGISTRY,
    )
    bedrock_embed_total = Counter(
        "amf_gateway_bedrock_embed_total",
        "Bedrock Titan embedding calls, labeled by result.",
        ["result"],
        registry=REGISTRY,
    )
    stream_requests_total = Counter(
        "amf_gateway_stream_requests_total",
        "SSE chat completion streams by org and terminal decision.",
        ["org", "decision"],
        registry=REGISTRY,
    )
    stream_blocks_total = Counter(
        "amf_gateway_stream_blocks_total",
        "Stream output blocks/redactions during secure streaming.",
        ["org", "action"],
        registry=REGISTRY,
    )
    stream_provider_errors_total = Counter(
        "amf_gateway_stream_provider_errors_total",
        "Provider errors observed during SSE streams.",
        ["org", "model"],
        registry=REGISTRY,
    )
    stream_fallback_before_token_total = Counter(
        "amf_gateway_stream_fallback_before_token_total",
        "LiteLLM fallback retries before first streamed token.",
        ["org", "model"],
        registry=REGISTRY,
    )
    stream_ttft_seconds = Histogram(
        "amf_gateway_stream_ttft_seconds",
        "Time to first streamed token (seconds).",
        ["org", "model"],
        buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
        registry=REGISTRY,
    )
    stream_duration_seconds = Histogram(
        "amf_gateway_stream_duration_seconds",
        "Total stream duration until completion or error (seconds).",
        ["org", "model", "decision"],
        buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
        registry=REGISTRY,
    )
    stream_finalize_failures_total = Counter(
        "amf_gateway_stream_finalize_failures_total",
        "Post-stream finalize hook failures (timeout or exception).",
        ["org", "reason"],
        registry=REGISTRY,
    )
    chat_completions_total = Counter(
        "amf_gateway_chat_completions_total",
        "Chat completion handler invocations by org and terminal outcome.",
        ["org", "outcome"],
        registry=REGISTRY,
    )
    chat_request_duration_seconds = Histogram(
        "amf_gateway_chat_request_duration_seconds",
        "Wall-clock duration of /v1/chat/completions handler (seconds).",
        ["org"],
        buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
        registry=REGISTRY,
    )
    pipeline_stage_seconds = Histogram(
        "amf_gateway_pipeline_stage_seconds",
        "Per-stage latency inside chat completion pipeline (seconds).",
        ["org", "stage"],
        buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
        registry=REGISTRY,
    )

_PIPELINE_STAGES = (
    "auth",
    "policy",
    "tier1",
    "tier2",
    "upstream",
    "telemetry",
)


def _safe_label(value: object, fallback: str = "unknown") -> str:
    if value is None:
        return fallback
    s = str(value).strip().lower()
    return s[:64] if s else fallback


def record_request(org_slug: str, decision: str, latency_seconds: float) -> None:
    if not _PROM_AVAILABLE:
        return
    org = _safe_label(org_slug, "anonymous")
    dec = _safe_label(decision, "unknown")
    gateway_requests_total.labels(org=org, decision=dec).inc()
    try:
        gateway_request_latency_seconds.labels(org=org, decision=dec).observe(float(latency_seconds))
    except (TypeError, ValueError):
        pass


def record_policy_block(org_slug: str, reason: str) -> None:
    if not _PROM_AVAILABLE:
        return
    policy_blocks_total.labels(
        org=_safe_label(org_slug, "anonymous"),
        reason=_safe_label(reason, "unspecified"),
    ).inc()


def record_rate_limit(org_slug: str, model: str, allowed: bool) -> None:
    if not _PROM_AVAILABLE:
        return
    rate_limit_total.labels(
        org=_safe_label(org_slug, "anonymous"),
        model=_safe_label(model, "unknown"),
        result="allowed" if allowed else "blocked",
    ).inc()


def record_kill_switch(model: str, action: str) -> None:
    if not _PROM_AVAILABLE:
        return
    kill_switch_triggers_total.labels(
        model=_safe_label(model, "unknown"),
        action=_safe_label(action, "unknown"),
    ).inc()


def record_bedrock_embed(result: str) -> None:
    if not _PROM_AVAILABLE:
        return
    bedrock_embed_total.labels(result=_safe_label(result, "unknown")).inc()


def record_stream_complete(
    org_slug: str,
    model: str,
    decision: str,
    *,
    ttft_seconds: float = 0.0,
    duration_seconds: float = 0.0,
    had_error: bool = False,
    fallback_before_token: bool = False,
) -> None:
    if not _PROM_AVAILABLE:
        return
    org = _safe_label(org_slug, "anonymous")
    mdl = _safe_label(model, "unknown")
    dec = _safe_label(decision, "unknown")
    stream_requests_total.labels(org=org, decision=dec).inc()
    if ttft_seconds > 0:
        stream_ttft_seconds.labels(org=org, model=mdl).observe(ttft_seconds)
    if duration_seconds > 0:
        stream_duration_seconds.labels(org=org, model=mdl, decision=dec).observe(duration_seconds)
    if had_error:
        stream_provider_errors_total.labels(org=org, model=mdl).inc()
    if fallback_before_token:
        stream_fallback_before_token_total.labels(org=org, model=mdl).inc()


def record_stream_guard_action(org_slug: str, action: str) -> None:
    if not _PROM_AVAILABLE:
        return
    stream_blocks_total.labels(
        org=_safe_label(org_slug, "anonymous"),
        action=_safe_label(action, "unknown"),
    ).inc()


def record_stream_finalize_failure(org_slug: str, reason: str) -> None:
    if not _PROM_AVAILABLE:
        return
    stream_finalize_failures_total.labels(
        org=_safe_label(org_slug, "anonymous"),
        reason=_safe_label(reason, "unknown"),
    ).inc()


def inc_active_connections(delta: int = 1) -> None:
    if not _PROM_AVAILABLE:
        return
    if delta >= 0:
        gateway_active_connections.inc(delta)
    else:
        gateway_active_connections.dec(-delta)


def record_chat_completion(
    org_slug: str,
    outcome: str,
    duration_seconds: float,
    stage_metrics_ms: dict[str, float] | None = None,
) -> None:
    """Record chat-completion totals, wall time, and per-stage histograms."""
    if not _PROM_AVAILABLE:
        return
    org = _safe_label(org_slug, "anonymous")
    out = _safe_label(outcome, "unknown")
    chat_completions_total.labels(org=org, outcome=out).inc()
    try:
        chat_request_duration_seconds.labels(org=org).observe(float(duration_seconds))
    except (TypeError, ValueError):
        pass
    if not stage_metrics_ms:
        return
    for stage in _PIPELINE_STAGES:
        key = f"{stage}_ms"
        raw = stage_metrics_ms.get(key)
        if raw is None:
            continue
        try:
            seconds = float(raw) / 1000.0
        except (TypeError, ValueError):
            continue
        if seconds < 0:
            continue
        pipeline_stage_seconds.labels(org=org, stage=stage).observe(seconds)


def render_latest() -> tuple[bytes, str]:
    """Return (body, content_type) for the /metrics handler."""
    if not _PROM_AVAILABLE:
        return b"# prometheus_client not installed\n", _PROM_CONTENT_TYPE
    return generate_latest(REGISTRY), _PROM_CONTENT_TYPE
