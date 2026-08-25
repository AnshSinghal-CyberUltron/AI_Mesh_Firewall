"""Lightweight metrics emitter (logging-first).

This module provides simple functions to record Bedrock call metrics.
All Bedrock metrics are also forwarded to the dedicated bedrock_logger
for clean, separate log files in production.
"""
from __future__ import annotations

import logging
import os
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
# Process-local scrape uses a dedicated CollectorRegistry. When
# PROMETHEUS_MULTIPROC_DIR is set, render_latest uses MultiProcessCollector
# so gunicorn workers aggregate (RC-8 under-report × WEB_CONCURRENCY).
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


_ADDON_BUCKETS = (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)

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
        multiprocess_mode="livesum",
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
    # CHG-0087: MCP tool-call scan decisions were AUDITED (MCPEvent) but not METERED,
    # so the 1.4 guardrails (block/redact for tool-poisoning / credentials / PII / IP)
    # were invisible to Prometheus dashboards + alerting. Low-cardinality: decision is
    # block/redact/allow/monitor; tag is the bounded compliance set (SECRET/PII/INFRA/
    # HIPAA/PCI-DSS/SOC2/GDPR...).
    mcp_scan_decisions_total = Counter(
        "amf_gateway_mcp_scan_decisions_total",
        "MCP tool-call scan/enforcement decisions per org "
        "(block/redact/allow/monitor/scan_skipped/rate_limited/error).",
        ["org", "decision"],
        registry=REGISTRY,
    )
    mcp_compliance_tags_total = Counter(
        "amf_gateway_mcp_compliance_tags_total",
        "MCP scan compliance-tag hits per org (PII/SECRET/INFRA/HIPAA/PCI-DSS/...).",
        ["org", "tag"],
        registry=REGISTRY,
    )
    # CHG-0088: MCP tool-call end-to-end latency (the audit already carries latency_ms
    # but it was never exposed as a metric) — lets dashboards see MCP p50/p95/p99 under
    # load (item 20 "1.4 under peak load" monitoring).
    mcp_call_seconds = Histogram(
        "amf_gateway_mcp_call_seconds",
        "MCP tool-call end-to-end latency in seconds, labeled by org and decision.",
        ["org", "decision"],
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
        registry=REGISTRY,
    )
    # CHG-0094: MCP audit records DROPPED under backpressure (inflight >= cap), labeled
    # by priority (high = block/redact/rate_limited/error security decisions; normal =
    # allow/monitor/clean) and decision. A non-zero high-priority count means a security
    # decision was made but its audit record was lost — the ...->tag->AUDIT chain broke
    # under load; alert on it (item 13/20 audit-completeness under peak load).
    mcp_audit_dropped_total = Counter(
        "amf_gateway_mcp_audit_dropped_total",
        "MCP audit records dropped under backpressure, by priority and decision.",
        ["priority", "decision"],
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
        buckets=_ADDON_BUCKETS,
        registry=REGISTRY,
    )
    t_addon_pre_seconds = Histogram(
        "amf_gateway_t_addon_pre_seconds", "T_addon_pre seconds (includes input T2)",
        ["org"], buckets=_ADDON_BUCKETS, registry=REGISTRY,
    )
    t_addon_post_seconds = Histogram(
        "amf_gateway_t_addon_post_seconds", "T_addon_post seconds (output guard)",
        ["org"], buckets=_ADDON_BUCKETS, registry=REGISTRY,
    )
    t_t2_seconds = Histogram(
        "amf_gateway_t_t2_seconds", "Input Tier-2 seconds (inside T_addon_pre)",
        ["org"], buckets=_ADDON_BUCKETS, registry=REGISTRY,
    )
    capacity_fail_total = Counter(
        "amf_gateway_capacity_fail_total",
        "Honesty/capacity predicate failures (never a live capacity RPS).",
        ["reason"], registry=REGISTRY,
    )

_STUB_LLM_TRUTHY = frozenset({"1", "true", "yes", "on"})
_PIPELINE_STAGES = ("auth", "policy", "tier1", "tier2", "upstream", "telemetry")


def _safe_label(value: object, fallback: str = "unknown") -> str:
    if value is None:
        return fallback
    s = str(value).strip().lower()
    return s[:64] if s else fallback


def _stub_llm_env_on() -> bool:
    return os.environ.get("GATEWAY_LOADTEST_STUB_LLM", "").strip().lower() in _STUB_LLM_TRUTHY


def _ms_or_none(raw: object) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def record_capacity_fail(reason: str) -> None:
    if not _PROM_AVAILABLE:
        return
    try:
        capacity_fail_total.labels(reason=_safe_label(reason, "unspecified")).inc()
    except Exception:
        return


def _observe_addon_split(org: str, stage_metrics_ms: dict[str, float] | None, duration_seconds: float) -> None:
    metrics = stage_metrics_ms if isinstance(stage_metrics_ms, dict) else {}
    pre = _ms_or_none(metrics.get("t_addon_pre_ms"))
    post = _ms_or_none(metrics.get("t_addon_post_ms"))
    t2 = _ms_or_none(metrics.get("t_t2_ms"))
    if pre is None or post is None or t2 is None:
        try:
            from .pipeline_trace import compute_addon_split
        except ImportError:
            from pipeline_trace import compute_addon_split  # type: ignore[no-redef]
        total_ms = _ms_or_none(metrics.get("total_latency_ms"))
        if total_ms is None:
            try:
                total_ms = float(duration_seconds) * 1000.0
            except (TypeError, ValueError):
                total_ms = 0.0
        split = compute_addon_split(metrics, total_ms)
        pre = pre if pre is not None else split["t_addon_pre_ms"]
        post = post if post is not None else split["t_addon_post_ms"]
        t2 = t2 if t2 is not None else split["t_t2_ms"]
    for metric, raw_ms in (
        (t_addon_pre_seconds, pre), (t_addon_post_seconds, post), (t_t2_seconds, t2),
    ):
        try:
            seconds = float(raw_ms) / 1000.0
        except (TypeError, ValueError):
            continue
        if seconds < 0:
            continue
        try:
            metric.labels(org=org).observe(seconds)
        except Exception:
            pass


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


def record_mcp_scan_decision(org_slug: str, decision: str, compliance_tags=None,
                             latency_ms=None) -> None:
    """CHG-0087/0088: meter an MCP tool-call scan/enforcement decision, its compliance
    tags, and (CHG-0088) its end-to-end latency.

    Best-effort / fail-safe (no-op when prometheus_client is absent). Called from the
    MCP audit sink (``mcp_proxy._record_gateway_event``) so every block/redact/allow/
    monitor decision the 1.4 chain makes is visible to Prometheus, not just the MCPEvent
    audit trail. Cardinality is bounded
    (org × {block,redact,allow,monitor,scan_skipped,rate_limited,error}; org × the
    fixed compliance-tag set). ``scan_skipped`` = zero scan-control rows
    (no Tier-1/Tier-2 ran) — distinct from ``allow`` (scanned and clean)."""
    if not _PROM_AVAILABLE:
        return
    org = _safe_label(org_slug, "anonymous")
    dec = _safe_label(decision, "unknown")
    try:
        mcp_scan_decisions_total.labels(org=org, decision=dec).inc()
    except Exception:  # pragma: no cover - metrics must never break the request path
        return
    for _tag in (compliance_tags or []):
        try:
            mcp_compliance_tags_total.labels(org=org, tag=_safe_label(str(_tag), "unknown")).inc()
        except Exception:  # pragma: no cover
            pass
    # CHG-0088: observe only a REAL measured latency (skip 0/None so paths that don't
    # time the call — e.g. the tools/list metadata-scan audit — don't skew the low bucket).
    if latency_ms:
        try:
            mcp_call_seconds.labels(org=org, decision=dec).observe(float(latency_ms) / 1000.0)
        except (TypeError, ValueError):  # pragma: no cover
            pass


def record_mcp_audit_dropped(priority: str, decision: str) -> None:
    """CHG-0094: meter an MCP audit record dropped under backpressure. Best-effort /
    fail-safe (no-op when prometheus_client is absent). ``priority`` is high|normal;
    ``decision`` is the dropped record's decision. A non-zero high-priority series is a
    lost SECURITY-decision audit — the ...->tag->AUDIT chain broke under load."""
    if not _PROM_AVAILABLE:
        return
    try:
        mcp_audit_dropped_total.labels(
            priority=_safe_label(priority, "normal"),
            decision=_safe_label(decision, "unknown"),
        ).inc()
    except Exception:  # pragma: no cover - metrics must never break the request path
        return


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
    if not _PROM_AVAILABLE:
        return
    org = _safe_label(org_slug, "anonymous")
    out = _safe_label(outcome, "unknown")
    chat_completions_total.labels(org=org, outcome=out).inc()
    try:
        chat_request_duration_seconds.labels(org=org).observe(float(duration_seconds))
    except (TypeError, ValueError):
        pass
    if stage_metrics_ms:
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
    _observe_addon_split(org, stage_metrics_ms, duration_seconds)
    if _stub_llm_env_on():
        record_capacity_fail("stub_llm")


def render_latest() -> tuple[bytes, str]:
    """Return (body, content_type) for the /metrics handler."""
    if not _PROM_AVAILABLE:
        return b"# prometheus_client not installed\n", _PROM_CONTENT_TYPE
    mp_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR") or os.environ.get("prometheus_multiproc_dir")
    if mp_dir:
        try:
            from prometheus_client.multiprocess import MultiProcessCollector
            registry = CollectorRegistry()
            MultiProcessCollector(registry, path=mp_dir)
            return generate_latest(registry), _PROM_CONTENT_TYPE
        except Exception:
            LOG.exception("prometheus multiprocess scrape failed; using process-local registry")
    return generate_latest(REGISTRY), _PROM_CONTENT_TYPE
