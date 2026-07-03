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

from pipeline_trace import attach_latency_breakdown

if TYPE_CHECKING:
    from llm_router import LLMRouter, ModelSelection
    from rate_limiter import RateLimiter
    from circuit_breaker import CircuitBreaker

LOG = logging.getLogger("gateway.stream_orchestration")


class StreamScanMode(str, Enum):
    NONE = "none"
    SCANNER_ONLY = "scanner_only"
    OUTPUT_GUARD = "output_guard"


# M-51: severity order used to keep only the strongest output-guard action
# observed during a stream (block > redact > flag).
_GUARD_ACTION_SEVERITY = {"": 0, "flag": 1, "redact": 2, "block": 3}


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
    # Reconstructed assistant text (capped) for the Scan Detail "Output" panel.
    output_snippet: str = ""

    def append_output(self, text: str, *, cap: int = 2000) -> None:
        """Accumulate streamed assistant text up to ``cap`` chars."""
        if not text or len(self.output_snippet) >= cap:
            return
        self.output_snippet = (self.output_snippet + text)[:cap]
    # M-51: strongest output-guard verdict observed mid-stream, consumed by the
    # terminal zeroshield trace frame (allow/redact/flag/block attribution).
    guard_action: str = ""
    guard_threat_type: str = ""
    guard_detail: str = ""
    guard_matched_patterns: list[str] = field(default_factory=list)

    def record_guard_action(
        self,
        action: str,
        *,
        threat_type: str = "",
        detail: str = "",
        matched_patterns: list[str] | None = None,
    ) -> None:
        """Record an output-guard enforcement action (keeps the strongest)."""
        action = (action or "").strip().lower()
        if _GUARD_ACTION_SEVERITY.get(action, 0) < _GUARD_ACTION_SEVERITY.get(self.guard_action, 0):
            return
        self.guard_action = action
        if threat_type:
            self.guard_threat_type = threat_type
        if detail:
            self.guard_detail = detail
        if matched_patterns:
            self.guard_matched_patterns = list(matched_patterns)

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
    # HTTP header values must be latin-1 encodable. A reroute/policy reason can
    # contain non-latin-1 chars (e.g. the unicode arrow → in a routing reason),
    # which makes Starlette's StreamingResponse raise UnicodeEncodeError -> 500
    # (H4). The non-stream path already sanitizes via main._latin1_safe_headers;
    # mirror it here for the streaming header path.
    return {k: _latin1_safe_value(v) for k, v in out.items()}


def _latin1_safe_value(value: Any) -> str:
    """Coerce a header value to a latin-1-encodable, control-char-free str."""
    import re
    s = "" if value is None else str(value)
    # B1/FD: CR/LF/NUL and other C0/DEL control chars ARE latin-1-encodable, so
    # they slip past the except branch below and reach h11/uvicorn, which raises
    # LocalProtocolError AFTER the streaming response has started (a mid-flight
    # abort). Collapse them to a space — mirror of main._latin1_safe_headers (the
    # FD fix was applied only to the non-stream helper; this is the stream half).
    s = re.sub(r"[\r\n\x00-\x1f\x7f]", " ", s)
    try:
        s.encode("latin-1")
        return s
    except UnicodeEncodeError:
        return s.encode("latin-1", "replace").decode("latin-1")


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
    pipeline_trace: dict | None = None,
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

    # Prompt for the scan-detail input panel: prefer the already-redacted prompt;
    # fall back to the last user message in the request body (clean prompts have no
    # redacted copy). Truncated for the telemetry record.
    _prompt_snip = ctx.redacted_prompt or ""
    if not _prompt_snip:
        for _m in reversed((ctx.body or {}).get("messages") or []):
            if isinstance(_m, dict) and _m.get("role") == "user":
                _c = _m.get("content")
                if isinstance(_c, str):
                    _prompt_snip = _c
                elif isinstance(_c, list):
                    _prompt_snip = " ".join(p.get("text", "") for p in _c if isinstance(p, dict))
                if _prompt_snip:
                    break

    # ISSUE B/C FIX: promote the input-scan (and any more-severe output-guard) verdict
    # to the stream_complete event's TOP-LEVEL action / risk_score / threat_type so the
    # control drain derives the right verdict, security_risk_score, threat_category and
    # pii_detected for STREAMED requests. The non-stream `request` emission already does
    # this (main.py ~5799-5812); the streaming finalizer omitted it, so a streamed input
    # redaction was stored as a clean allow / score 0 / threat NONE (scan 7686).
    if ctx.redacted_prompt is not None:
        _sv = ctx.scan_verdict
        _sc_action = "redact"
        _sc_threat = (getattr(_sv, "threat_type", "") or "pii") if _sv is not None else "pii"
        _sc_risk = float(getattr(_sv, "confidence", 0.0) or 0.0) if _sv is not None else 0.0
    else:
        _sc_action, _sc_threat, _sc_risk = "allow", "", 0.0
    # Output-guard outcome wins only when STRICTLY more severe (block > redact > flag >
    # allow) so a mid-stream block/redact is never downgraded by a clean input scan.
    _SEV = {"allow": 0, "flag": 1, "redact": 2, "block": 3}
    _guard_action = "block" if metrics.output_blocked else (metrics.guard_action or "")
    if _guard_action and _SEV.get(_guard_action, 0) > _SEV.get(_sc_action, 0):
        _sc_action = _guard_action
        if metrics.guard_threat_type:
            _sc_threat = metrics.guard_threat_type

    if hooks.emit_telemetry is not None:
        try:
            hooks.emit_telemetry(
                event_type="stream_complete",
                model=model,
                user_id=ctx.user_id,
                project_id=ctx.project_id,
                latency_ms=elapsed_ms,
                action=_sc_action,
                risk_score=_sc_risk,
                threat_type=_sc_threat,
                metadata={
                    "ttft_ms": round(metrics.ttft_ms, 2),
                    "chunks": metrics.chunks_emitted,
                    "completed": metrics.completed,
                    "had_error": metrics.had_error,
                    "usage": metrics.usage or {},
                    "usage_estimated": metrics.usage_estimated,
                    "output_blocked": metrics.output_blocked,
                    "request_id": ctx.request_id,
                    # SCAN-DETAIL ENRICHMENT: include the full pipeline trace, the
                    # (already-redacted) input, and the reconstructed assistant
                    # output so the activity/scan-detail report shows the 9-stage
                    # pipeline plus both sides of the conversation for STREAMED
                    # requests, not just a thin summary.
                    **({"pipeline_trace": pipeline_trace} if pipeline_trace else {}),
                    "prompt_snippet": _prompt_snip[:2000],
                    **(
                        {
                            "response_snippet": metrics.output_snippet[:2000],
                            "sanitized_output": metrics.output_snippet[:2000],
                        }
                        if metrics.output_snippet
                        else {}
                    ),
                },
            )
        except Exception as exc:
            LOG.warning("Stream telemetry finalize failed: %s", exc)


_DONE_FRAME = "data: [DONE]\n\n"


def _is_done_line(chunk: str) -> bool:
    stripped = chunk.strip()
    return stripped == "data: [DONE]" or stripped == "[DONE]"


def _wants_usage_chunk(body: dict | None) -> bool:
    """streaming #7: True when the client requested ``stream_options.include_usage``.

    Per the OpenAI streaming contract, a truthy ``include_usage`` obliges a final
    chunk carrying ``usage`` (with empty ``choices``) before ``[DONE]``. Defensive
    against malformed bodies (non-dict ``stream_options`` etc.).
    """
    if not isinstance(body, dict):
        return False
    opts = body.get("stream_options")
    if not isinstance(opts, dict):
        return False
    return bool(opts.get("include_usage"))


def build_usage_chunk_frame(
    ctx: StreamLaunchContext,
    metrics: StreamRunMetrics,
    *,
    stream_id: str = "",
    stream_model: str = "",
) -> str:
    """streaming #7: synthesize the terminal ``usage`` SSE chunk for clients that
    asked for ``stream_options.include_usage`` when the upstream provider did not
    emit one. Shape matches the OpenAI contract: a chat.completion.chunk with
    ``choices: []`` and a top-level ``usage`` object. Prefers the observed
    ``metrics.usage``; otherwise estimates from ``ctx.estimated_tokens``."""
    usage = metrics.usage
    if not usage:
        total = int(ctx.estimated_tokens or 0)
        usage = {
            "prompt_tokens": 0,
            "completion_tokens": total,
            "total_tokens": total,
        }
        metrics.usage_estimated = True
    frame: dict[str, Any] = {
        "id": stream_id or f"chatcmpl-{ctx.request_id or 'zs-stream'}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": stream_model or ctx.model or str(ctx.body.get("model", "") or ""),
        "choices": [],
        "usage": usage,
    }
    return f"data: {json.dumps(frame)}\n\n"


def build_stream_trace_frame(
    ctx: StreamLaunchContext,
    metrics: StreamRunMetrics,
    base_zeroshield: dict | None,
    *,
    stream_id: str = "",
    stream_model: str = "",
    error: bool = False,
    pipeline_trace_base: dict | None = None,
) -> str:
    """M-51: terminal SSE trace frame, emitted once before ``data: [DONE]``.

    Shape is a ChatCompletionChunk with ``choices: []`` plus an extra top-level
    ``zeroshield`` object so the STOCK OpenAI SDK accepts it: the SDK already
    emits empty-choices chunks itself (stream_options.include_usage) and its
    pydantic models allow extra fields. The ``zeroshield`` payload reuses the
    client-safe metadata shape produced by the non-streaming path
    (main._build_zeroshield_metadata -> _redact_for_client_response); the
    caller passes that dict as ``base_zeroshield`` and this function overlays
    the final mid-stream output-guard outcome from ``metrics``.
    """
    zs = dict(base_zeroshield or {})
    if metrics.output_blocked or metrics.guard_action == "block":
        zs["action"] = "block"
        zs["detection_tier"] = "output_guard"
        if metrics.guard_threat_type:
            zs["threat_type"] = metrics.guard_threat_type
        if metrics.guard_detail:
            zs["detail"] = metrics.guard_detail
        zs["reason"] = "Streaming response blocked by output guard."
        if metrics.guard_matched_patterns:
            zs["matched_patterns"] = metrics.guard_matched_patterns
    elif error or metrics.had_error:
        zs["action"] = "error"
        zs.setdefault("reason", "Stream terminated by an upstream error.")
    elif metrics.guard_action == "redact":
        zs["action"] = "redact"
        zs["detection_tier"] = "output_guard"
        if metrics.guard_threat_type:
            zs["threat_type"] = metrics.guard_threat_type
        if metrics.guard_detail:
            zs["detail"] = metrics.guard_detail
        zs["reason"] = "Sensitive content redacted from streaming response."
        if metrics.guard_matched_patterns:
            zs["matched_patterns"] = metrics.guard_matched_patterns
    elif metrics.guard_action == "flag" and str(zs.get("action") or "allow") == "allow":
        zs["action"] = "flag"
        zs["detection_tier"] = "output_guard"
        if metrics.guard_threat_type:
            zs["threat_type"] = metrics.guard_threat_type
        if metrics.guard_detail:
            zs["detail"] = metrics.guard_detail
    zs.setdefault("action", "allow")
    if ctx.request_id:
        zs["request_id"] = ctx.request_id
    elapsed_ms = metrics.duration_ms
    if elapsed_ms <= 0 and ctx.start_time:
        elapsed_ms = (time.perf_counter() - ctx.start_time) * 1000
    zs["processing_time_ms"] = round(elapsed_ms, 2)
    if metrics.ttft_ms > 0:
        zs["ttft_ms"] = round(metrics.ttft_ms, 2)

    frame: dict[str, Any] = {
        "id": stream_id or f"chatcmpl-{ctx.request_id or 'zs-stream'}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": stream_model or ctx.model or str(ctx.body.get("model", "") or ""),
        "choices": [],
        "zeroshield": zs,
    }
    if metrics.usage:
        frame["usage"] = metrics.usage
    # FULL-PIPELINE-ON-STREAM: attach the same 9-stage pipeline_trace the
    # non-stream path returns, so streaming clients render the complete pipeline
    # (not just the routing summary). Overlay the terminal output-guard outcome
    # onto the output_guardrail stage so a mid-stream block/redact/flag is reflected.
    if pipeline_trace_base:
        try:
            pt = dict(pipeline_trace_base)
            _final = str(zs.get("action") or "allow")
            if _final in ("block", "redact", "flag"):
                _stages = []
                for s in (pt.get("stages") or []):
                    s2 = dict(s) if isinstance(s, dict) else s
                    if isinstance(s2, dict) and s2.get("name") == "output_guardrail":
                        s2["action"] = _final
                        if zs.get("detail"):
                            s2["detail"] = zs["detail"]
                    _stages.append(s2)
                pt["stages"] = _stages
            # Reconcile totals with the completed stream wall-clock (PIPELINE-0015).
            _stage_sum = round(
                sum(float(s.get("latency_ms") or 0) for s in (pt.get("stages") or []) if isinstance(s, dict)),
                1,
            )
            _overhead = round(max(0.0, elapsed_ms - _stage_sum), 1)
            pt["stage_latency_sum_ms"] = _stage_sum
            pt["overhead_ms"] = _overhead
            pt["total_latency_ms"] = round(elapsed_ms, 2)
            if metrics.ttft_ms > 0:
                pt["ttft_ms"] = round(metrics.ttft_ms, 2)
            attach_latency_breakdown(pt)
            frame["pipeline_trace"] = pt
        except Exception:
            frame["pipeline_trace"] = pipeline_trace_base
    return f"data: {json.dumps(frame)}\n\n"


async def stream_with_finalize(
    inner: AsyncGenerator[str, None],
    ctx: StreamLaunchContext,
    hooks: StreamFinalizeHooks,
    *,
    decision: str = "allowed",
    metrics: StreamRunMetrics | None = None,
    finalize_timeout_ms: int | None = None,
    zeroshield_base: dict | None = None,
    pipeline_trace_base: dict | None = None,
    emit_trace_frame: bool | None = None,
    request: Any = None,
) -> AsyncGenerator[str, None]:
    run_metrics = metrics if metrics is not None else StreamRunMetrics()
    if metrics is None:
        wrapped: AsyncGenerator[str, None] = instrumented_stream_generator(inner, run_metrics)
    else:
        wrapped = inner
    # streaming #7: detect a client that asked for stream_options.include_usage so
    # we can synthesize a terminal usage chunk if the provider never emits one.
    want_usage = _wants_usage_chunk(ctx.body)
    saw_usage_chunk = False
    # streaming #4: optional FastAPI/Starlette request used to detect client
    # disconnect mid-stream. Backward compatible — when None, behavior is
    # unchanged (no polling). When the client goes away we stop pulling upstream
    # so generation/output-scanning/billing halts; the finally block still
    # finalizes and bills the partial usage already produced.
    _disconnect_check = getattr(request, "is_disconnected", None) if request is not None else None
    # M-51: single choke point for the terminal zeroshield trace frame — every
    # streaming path (allow/redact/flag/block/error) funnels through here. The
    # trace is injected immediately BEFORE the terminal ``data: [DONE]`` and is
    # emitted exactly once per stream. Legacy callers that do not provide a
    # zeroshield_base keep the historical chunk sequence unchanged.
    emit_trace = emit_trace_frame if emit_trace_frame is not None else zeroshield_base is not None
    trace_emitted = False
    stream_id = ""
    stream_model = ""
    saw_error_frame = False
    effective_decision = decision
    try:
        async for chunk in wrapped:
            # streaming #4: stop pulling upstream once the client has gone away.
            # Poll at most once per chunk (cheap). Breaking here halts further
            # generation/output-scanning; the finally block still finalizes and
            # bills only the chunks already produced (partial usage tracked).
            if _disconnect_check is not None:
                try:
                    if await _disconnect_check():
                        LOG.info(
                            "Client disconnected mid-stream (org=%s, request_id=%s); "
                            "halting upstream after %d chunks.",
                            ctx.org_slug,
                            ctx.request_id,
                            run_metrics.chunks_emitted,
                        )
                        break
                except Exception:
                    # Disconnect probing must never break a live stream.
                    pass
            # streaming #7: note whether the provider already emitted a usage
            # chunk so we do not duplicate it when synthesizing the fallback.
            if want_usage and not saw_usage_chunk and isinstance(chunk, str):
                if _extract_usage_from_sse_line(chunk):
                    saw_usage_chunk = True
            if not emit_trace or not isinstance(chunk, str):
                yield chunk
                continue
            if _is_done_line(chunk):
                if want_usage and not saw_usage_chunk:
                    saw_usage_chunk = True
                    yield build_usage_chunk_frame(
                        ctx,
                        run_metrics,
                        stream_id=stream_id,
                        stream_model=stream_model,
                    )
                if not trace_emitted:
                    trace_emitted = True
                    yield build_stream_trace_frame(
                        ctx,
                        run_metrics,
                        zeroshield_base,
                        stream_id=stream_id,
                        stream_model=stream_model,
                        error=saw_error_frame,
                        pipeline_trace_base=pipeline_trace_base,
                    )
                yield chunk
                continue
            line = chunk.strip()
            needs_parse = line.startswith("data: ") and (
                not stream_id or not stream_model or '"error"' in line[:96]
            )
            if needs_parse:
                try:
                    data = json.loads(line[6:])
                except (json.JSONDecodeError, TypeError):
                    data = None
                if isinstance(data, dict):
                    if data.get("error"):
                        saw_error_frame = True
                    if not stream_id and data.get("id"):
                        stream_id = str(data["id"])
                    if not stream_model and data.get("model"):
                        stream_model = str(data["model"])
            yield chunk
        if emit_trace and not trace_emitted:
            # Inner stream ended without a [DONE] sentinel (or the client
            # disconnected and we broke out): still terminate the SSE stream with
            # exactly one trace frame followed by [DONE].
            if want_usage and not saw_usage_chunk:
                saw_usage_chunk = True
                yield build_usage_chunk_frame(
                    ctx,
                    run_metrics,
                    stream_id=stream_id,
                    stream_model=stream_model,
                )
            trace_emitted = True
            yield build_stream_trace_frame(
                ctx,
                run_metrics,
                zeroshield_base,
                stream_id=stream_id,
                stream_model=stream_model,
                error=saw_error_frame or run_metrics.had_error,
                pipeline_trace_base=pipeline_trace_base,
            )
            yield _DONE_FRAME
    except (GeneratorExit, asyncio.CancelledError):
        # Client disconnect / cancellation: never yield during teardown.
        raise
    except Exception:
        run_metrics.had_error = True
        if emit_trace and not trace_emitted:
            trace_emitted = True
            yield build_stream_trace_frame(
                ctx,
                run_metrics,
                zeroshield_base,
                stream_id=stream_id,
                stream_model=stream_model,
                error=True,
                pipeline_trace_base=pipeline_trace_base,
            )
            yield _DONE_FRAME
        raise
    finally:
        if run_metrics.output_blocked:
            effective_decision = "blocked"
        timeout_s = None
        if finalize_timeout_ms is not None and finalize_timeout_ms > 0:
            timeout_s = finalize_timeout_ms / 1000.0
        try:
            if timeout_s is not None:
                await asyncio.wait_for(
                    finalize_stream(ctx, run_metrics, hooks, decision=effective_decision, pipeline_trace=pipeline_trace_base),
                    timeout=timeout_s,
                )
            else:
                await finalize_stream(ctx, run_metrics, hooks, decision=effective_decision, pipeline_trace=pipeline_trace_base)
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
    request: Any = None,
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
        request=request,
    )
    return secure.__aiter__()
