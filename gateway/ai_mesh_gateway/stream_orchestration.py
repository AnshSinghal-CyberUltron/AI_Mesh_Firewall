"""
End-to-end streaming orchestration for /v1/chat/completions.

Lifecycle (all hard gates complete before first SSE byte):
  1. eligibility_phase  — handled in main.proxy_chat before calling here
  2. selection_phase    — routing/model choice in main before calling here
  3. stream_phase       — LiteLLM SSE + optional SecureStreamingResponse
  4. finalization_phase — circuit breaker, TPM reconciliation, Prometheus

Insertion points in main.py:
  - firewall-disabled fast path (~2521)
  - no-backend path (~2973)
  - full governance path (~3302)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from llm_router import LLMRouter, ModelSelection
    from rate_limiter import RateLimiter
    from circuit_breaker import CircuitBreaker

LOG = logging.getLogger("gateway.stream_orchestration")


class StreamScanMode(str, Enum):
    NONE = "none"
    SCANNER_ONLY = "scanner_only"
    OUTPUT_GUARD = "output_guard"


@dataclass
class StreamRunMetrics:
    """Populated during stream_phase; consumed in finalization_phase."""

    provider_start_ts: float = 0.0
    first_token_ts: float = 0.0
    completed: bool = False
    had_error: bool = False
    output_blocked: bool = False
    fallback_before_first_token: bool = False
    chunks_emitted: int = 0
    usage: dict[str, int] | None = None
    usage_estimated: bool = False

    @property
    def ttft_ms(self) -> float:
        if self.first_token_ts <= 0 or self.provider_start_ts <= 0:
            return 0.0
        return (self.first_token_ts - self.provider_start_ts) * 1000

    @property
    def duration_ms(self) -> float:
        if self.provider_start_ts <= 0:
            return 0.0
        end = time.perf_counter()
        return (end - self.provider_start_ts) * 1000


@dataclass
class StreamLaunchContext:
    """Inputs for stream_phase after preflight/selection in main."""

    body: dict
    redacted_prompt: str | None
    org_slug: str
    model: str
    key_hash: str = ""
    rate_limit_tpm: int = 0
    estimated_tokens: int = 20
    org_tpm_limit: int = 0
    route_selection: Any = None
    scan_verdict: Any = None
    scan_mode: StreamScanMode = StreamScanMode.NONE
    request_id: str = ""
    user_id: Any = None
    project_id: str = ""
    organization_id: int | None = None
    source_ip: str = ""
    start_time: float = field(default_factory=time.perf_counter)


@dataclass
class StreamFinalizeHooks:
    """Callbacks/resources for post-stream accounting."""

    circuit_breaker: CircuitBreaker | None = None
    rate_limiter: RateLimiter | None = None
    record_stream_complete: Callable[..., None] | None = None
    emit_telemetry: Callable[..., None] | None = None


def streaming_preflight_block_body(
    config: dict,
    *,
    policy_sync_loaded: bool,
) -> dict[str, str] | None:
    """
    Fail-closed gate before opening an SSE response.

    When ``stream_preflight_fail_closed`` is true (default), streaming requests
  require a loaded policy cache when ``policy_cache_require_loaded`` is true.
    """
    if not config.get("stream_preflight_fail_closed", True):
        return None
    if config.get("policy_cache_require_loaded", True) and not policy_sync_loaded:
        return {
            "error": "service_unavailable",
            "message": "Streaming preflight failed: policy cache not loaded (fail-closed).",
            "code": "stream_preflight_policy_unavailable",
        }
    return None


def policy_summary_id(summary: str) -> str:
    if not summary:
        return ""
    return hashlib.sha256(summary.encode("utf-8")).hexdigest()[:16]


def build_base_stream_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }


def enrich_stream_headers(
    headers: dict[str, str],
    *,
    route_selection: Any = None,
    scan_verdict: Any = None,
    redacted_prompt: str | None = None,
    scan_mode: StreamScanMode = StreamScanMode.NONE,
    emit_debug: bool = False,
) -> dict[str, str]:
    """Normalize routing + governance headers on the stream response."""
    out = dict(headers)
    if route_selection is not None:
        out["X-ZeroShield-Original-Model"] = getattr(route_selection, "requested_model", None) or "auto"
        out["X-ZeroShield-Routed-Model"] = getattr(route_selection, "model_name", "") or ""
        out["X-ZeroShield-Routing-Source"] = getattr(route_selection, "decision_source", "") or ""
        requested = getattr(route_selection, "requested_model", None) or "auto"
        routed = getattr(route_selection, "model_name", "") or ""
        rerouted = bool(requested and requested != "auto" and routed and routed != requested)
        out["X-ZeroShield-Rerouted"] = "true" if rerouted else "false"
        reason = getattr(route_selection, "reason", "") or ""
        if reason:
            out["X-ZeroShield-Routing-Reason"] = reason[:180]
        policy_summary = getattr(route_selection, "policy_summary", "") or ""
        if policy_summary:
            summary_hash = policy_summary_id(policy_summary)
            if summary_hash:
                out["X-ZeroShield-Routing-Policy-Id"] = summary_hash
            if emit_debug:
                out["X-ZeroShield-Routing-Policy-Summary"] = policy_summary[:180]
    if scan_verdict is not None and getattr(scan_verdict, "tier", None):
        out["X-ZeroShield-Detection-Tier"] = str(scan_verdict.tier)
    if redacted_prompt is not None:
        out["X-ZeroShield-Action"] = "redacted"
    elif scan_verdict is not None and getattr(scan_verdict, "action", None) == "flag":
        out["X-ZeroShield-Action"] = "flag"
    out["X-ZeroShield-Stream-Scan-Mode"] = scan_mode.value
    if emit_debug:
        out["X-ZeroShield-Stream-Lifecycle"] = "preflight,selection,stream,finalize"
    return out


def _extract_usage_from_sse_line(line: str) -> dict[str, int] | None:
    stripped = line.strip()
    if not stripped.startswith("data: "):
        return None
    payload = stripped[6:].strip()
    if payload in ("", "[DONE]"):
        return None
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None
    return {
        "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
        "total_tokens": int(usage.get("total_tokens", 0) or 0),
    }


async def instrumented_stream_generator(
    inner: AsyncGenerator[str, None],
    metrics: StreamRunMetrics,
) -> AsyncGenerator[str, None]:
    """Track TTFT, chunk count, and usage from SSE payloads."""
    metrics.provider_start_ts = time.perf_counter()
    try:
        async for chunk in inner:
            if metrics.first_token_ts <= 0 and chunk and "data: " in chunk:
                payload = chunk.strip()
                if payload != "data: [DONE]" and "[DONE]" not in payload[:20]:
                    metrics.first_token_ts = time.perf_counter()
                    metrics.chunks_emitted += 1
            elif chunk:
                metrics.chunks_emitted += 1
            usage = _extract_usage_from_sse_line(chunk)
            if usage:
                metrics.usage = usage
            yield chunk
        metrics.completed = True
    except Exception:
        metrics.had_error = True
        raise


async def finalize_stream(
    ctx: StreamLaunchContext,
    metrics: StreamRunMetrics,
    hooks: StreamFinalizeHooks,
    *,
    decision: str = "allowed",
) -> None:
    """finalization_phase: circuit, TPM, metrics, optional telemetry."""
    model = ctx.model or ctx.body.get("model", "")
    elapsed_ms = (time.perf_counter() - ctx.start_time) * 1000

    if hooks.circuit_breaker is not None:
        try:
            if metrics.had_error or metrics.output_blocked:
                await hooks.circuit_breaker.record_error(
                    model,
                    "stream_output_blocked" if metrics.output_blocked else "stream_error",
                )
            elif metrics.completed:
                await hooks.circuit_breaker.record_success(model)
        except Exception as exc:
            LOG.warning("Stream circuit finalize failed: %s", exc)

    actual_tokens = 0
    if metrics.usage:
        actual_tokens = int(metrics.usage.get("total_tokens", 0) or 0)
    if actual_tokens <= 0 and metrics.completed:
        actual_tokens = ctx.estimated_tokens
        metrics.usage_estimated = True

    if hooks.rate_limiter is not None and ctx.key_hash and ctx.rate_limit_tpm > 0:
        try:
            await hooks.rate_limiter.record_usage(
                ctx.key_hash,
                actual_tokens,
                ctx.estimated_tokens,
            )
        except Exception as exc:
            LOG.warning("Stream key TPM finalize failed: %s", exc)

    if hooks.rate_limiter is not None and ctx.org_slug and ctx.org_tpm_limit > 0:
        try:
            await hooks.rate_limiter.record_org_usage(
                ctx.org_slug,
                actual_tokens,
                ctx.estimated_tokens,
            )
        except Exception as exc:
            LOG.warning("Stream org TPM finalize failed: %s", exc)

    if hooks.record_stream_complete is not None:
        try:
            hooks.record_stream_complete(
                org_slug=ctx.org_slug,
                model=model,
                decision=decision,
                metrics=metrics,
                elapsed_ms=elapsed_ms,
            )
        except Exception as exc:
            LOG.warning("Stream metrics finalize failed: %s", exc)

    if hooks.emit_telemetry is not None:
        try:
            hooks.emit_telemetry(
                event_type="stream_complete",
                model=model,
                user_id=ctx.user_id,
                project_id=ctx.project_id,
                latency_ms=elapsed_ms,
                metadata={
                    "ttft_ms": round(metrics.ttft_ms, 2),
                    "chunks": metrics.chunks_emitted,
                    "completed": metrics.completed,
                    "had_error": metrics.had_error,
                    "usage": metrics.usage or {},
                    "usage_estimated": metrics.usage_estimated,
                    "output_blocked": metrics.output_blocked,
                    "request_id": ctx.request_id,
                },
            )
        except Exception as exc:
            LOG.warning("Stream telemetry finalize failed: %s", exc)


async def stream_with_finalize(
    inner: AsyncGenerator[str, None],
    ctx: StreamLaunchContext,
    hooks: StreamFinalizeHooks,
    *,
    decision: str = "allowed",
    metrics: StreamRunMetrics | None = None,
    finalize_timeout_ms: int | None = None,
) -> AsyncGenerator[str, None]:
    run_metrics = metrics if metrics is not None else StreamRunMetrics()
    if metrics is None:
        wrapped: AsyncGenerator[str, None] = instrumented_stream_generator(inner, run_metrics)
    else:
        wrapped = inner
    effective_decision = decision
    try:
        async for chunk in wrapped:
            yield chunk
    finally:
        if run_metrics.output_blocked:
            effective_decision = "blocked"
        timeout_s = None
        if finalize_timeout_ms is not None and finalize_timeout_ms > 0:
            timeout_s = finalize_timeout_ms / 1000.0
        try:
            if timeout_s is not None:
                await asyncio.wait_for(
                    finalize_stream(ctx, run_metrics, hooks, decision=effective_decision),
                    timeout=timeout_s,
                )
            else:
                await finalize_stream(ctx, run_metrics, hooks, decision=effective_decision)
        except asyncio.TimeoutError:
            LOG.error("Stream finalize timed out after %sms", finalize_timeout_ms)
            try:
                from metrics import record_stream_finalize_failure

                record_stream_finalize_failure(ctx.org_slug, "timeout")
            except Exception:
                pass
        except Exception as exc:
            LOG.error("Stream finalize failed: %s", exc)
            try:
                from metrics import record_stream_finalize_failure

                record_stream_finalize_failure(ctx.org_slug, "error")
            except Exception:
                pass


def resolve_scan_mode(
    *,
    output_scan_enabled: bool,
    input_scanner: Any,
    output_guard: Any,
) -> StreamScanMode:
    if not output_scan_enabled or input_scanner is None:
        return StreamScanMode.NONE
    if output_guard is not None:
        return StreamScanMode.OUTPUT_GUARD
    return StreamScanMode.SCANNER_ONLY


async def build_inner_llm_stream(
    llm_router: LLMRouter,
    body: dict,
    redacted_prompt: str | None,
) -> AsyncGenerator[str, None]:
    """stream_phase provider leg (LiteLLM SSE)."""
    async for chunk in llm_router.acompletion_stream(body, redacted_prompt):
        yield chunk


def wrap_secure_stream_if_needed(
    inner: AsyncGenerator[str, None],
    *,
    scan_mode: StreamScanMode,
    config: dict,
    input_scanner: Any,
    output_guard: Any,
    telemetry: Any,
    ctx: StreamLaunchContext,
    record_guard_metric: Callable[[str, str], None] | None = None,
    stream_metrics: StreamRunMetrics | None = None,
    enforcement_mode: str = "block",
) -> AsyncGenerator[str, None]:
    if scan_mode == StreamScanMode.NONE or input_scanner is None:
        return inner
    try:
        from secure_streaming import SecureStreamingResponse
    except ImportError:
        from .secure_streaming import SecureStreamingResponse

    secure = SecureStreamingResponse(
        inner_generator=inner,
        scanner=input_scanner,
        redaction_enabled=True,
        buffer_max_bytes=int(config.get("stream_max_buffer_bytes", config.get("scan_buffer_max_bytes", 4096))),
        max_buffer_chunks=int(config.get("stream_max_buffer_chunks", 64)),
        output_guard=output_guard if scan_mode == StreamScanMode.OUTPUT_GUARD else None,
        telemetry=telemetry,
        user_id=ctx.user_id,
        organization_id=ctx.organization_id,
        model=ctx.model or ctx.body.get("model", ""),
        project_id=ctx.project_id,
        source_ip=ctx.source_ip,
        request_id=ctx.request_id,
        record_guard_metric=record_guard_metric,
        org_slug=ctx.org_slug,
        stream_metrics=stream_metrics,
        enforcement_mode=enforcement_mode,
    )
    return secure.__aiter__()
