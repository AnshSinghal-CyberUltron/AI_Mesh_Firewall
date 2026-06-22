"""
Non-blocking telemetry event producer for the Gateway.

Events are buffered in memory and flushed to a Redis list
(``telemetry:events``) periodically or when the buffer is full.

A Celery Beat task on the backend drains this list and batch-inserts
EnforcementEvent records into Postgres.

Design goals:
- Zero impact on request latency (in-memory append only)
- Graceful degradation on Redis failure (events dropped, logged)
- 1000+ req/sec throughput
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis

LOG = logging.getLogger("gateway.telemetry")

REDIS_TELEMETRY_KEY = "telemetry:events"
REDIS_TELEMETRY_STREAM_KEY = "telemetry:stream"
DEFAULT_FLUSH_INTERVAL = 2.0
DEFAULT_MAX_BUFFER_SIZE = 100
TELEMETRY_STREAMS_ENABLED = os.environ.get("TELEMETRY_STREAMS_ENABLED", "false").lower() in ("1", "true", "yes")


class TelemetryProducer:
    """
    Non-blocking telemetry event accumulator.

    Call ``emit(event)`` from any coroutine -- it appends to an
    in-memory list and returns immediately. A background asyncio
    task flushes the buffer to Redis every ``flush_interval`` seconds
    or when the buffer exceeds ``max_buffer_size``.
    """

    def __init__(
        self,
        redis_client: aioredis.Redis,
        flush_interval: float = DEFAULT_FLUSH_INTERVAL,
        max_buffer_size: int = DEFAULT_MAX_BUFFER_SIZE,
    ) -> None:
        self._redis = redis_client
        self._flush_interval = flush_interval
        self._max_buffer_size = max_buffer_size
        self._buffer: list[dict] = []
        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task | None = None
        self._running = False

    def emit(self, event: dict) -> None:
        """
        Append a telemetry event to the in-memory buffer.

        This method never blocks and never raises. If the buffer
        is full, the oldest event is dropped.
        """
        try:
            if "timestamp" not in event:
                event["timestamp"] = datetime.now(timezone.utc).isoformat()
            self._buffer.append(event)
            if len(self._buffer) > self._max_buffer_size * 2:
                dropped = len(self._buffer) - self._max_buffer_size
                self._buffer = self._buffer[dropped:]
                LOG.warning("Telemetry buffer overflow: dropped %d events", dropped)
        except Exception as exc:
            LOG.debug("Telemetry emit failed (non-critical): %s", exc)

    async def start(self) -> None:
        """Start the background flush loop."""
        if self._running:
            return
        self._running = True
        self._flush_task = asyncio.create_task(self._flush_loop())
        LOG.info(
            "Telemetry producer started (flush_interval=%.1fs, buffer_size=%d)",
            self._flush_interval,
            self._max_buffer_size,
        )

    async def stop(self) -> None:
        """Final flush and cancel the background task."""
        self._running = False
        if self._flush_task is not None:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        await self._flush_to_redis()
        LOG.info("Telemetry producer stopped (final flush complete)")

    async def _flush_loop(self) -> None:
        """Background coroutine: flush buffer to Redis periodically."""
        while self._running:
            try:
                await asyncio.sleep(self._flush_interval)
                await self._flush_to_redis()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                LOG.error("Telemetry flush loop error: %s", exc)

    async def _flush_to_redis(self) -> None:
        """LPUSH buffered events as JSON to the Redis telemetry list."""
        if not self._buffer:
            return

        async with self._lock:
            batch = self._buffer[:]
            self._buffer.clear()

        if not batch:
            return

        try:
            serialized = [json.dumps(evt, default=str) for evt in batch]
            async with self._redis.pipeline(transaction=False) as pipe:
                for item in serialized:
                    pipe.lpush(REDIS_TELEMETRY_KEY, item)
                    if TELEMETRY_STREAMS_ENABLED:
                        pipe.xadd(REDIS_TELEMETRY_STREAM_KEY, {"event": item})
                await pipe.execute()
            LOG.debug("Telemetry flushed %d events to Redis", len(serialized))
        except (aioredis.RedisError, OSError, ConnectionError) as exc:
            LOG.error(
                "Telemetry flush to Redis failed (dropped %d events): %s",
                len(batch),
                exc,
            )


def build_telemetry_event(
    event_type: str,
    model: str = "",
    user_id: int | None = None,
    project_id: str = "",
    key_prefix: str = "",
    prompt_hash: str = "",
    prompt_snippet: str = "",
    endpoint_id: int | None = None,
    latency_ms: float = 0.0,
    risk_score: float = 0.0,
    action: str = "allow",
    threat_type: str = "",
    tokens_used: dict[str, int] | None = None,
    compliance_tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    pipeline_stage: str = "",
    intent: str = "",
    organization_id: int | None = None,
    method: str = "POST",
    source_ip: str = "",
    user_agent: str = "",
    status_code: int = 200,
) -> dict:
    """
    Build a standardized telemetry event dict.

    Event types: request, block, redact, kill_switch, scan_hit, output_guard
    Pipeline stages: query, retriever, ranker, generator
    """
    # ── AUDIT PII SCRUB (single choke point for every telemetry path) ──
    # The dashboard governance log surfaces metadata free-text fields
    # (raw_output / response_snippet / detail / reason / …) and prompt_snippet
    # verbatim. Model output OR a guard advisory can carry RAW PII (e.g. an
    # international phone the input-redactor or a "Detect"-only output action did
    # not mask). Pass every free-text field through redact_all (a no-op on benign
    # text) so the AUDIT LOG never persists raw PII — non-stream and streaming
    # output-guard events both build their event here.
    try:
        from patterns import redact_all as _ra  # type: ignore
    except ImportError:
        try:
            from .patterns import redact_all as _ra  # type: ignore
        except Exception:
            _ra = None  # type: ignore
    _safe_snippet = prompt_snippet[:500] if prompt_snippet else ""
    _safe_meta = dict(metadata) if isinstance(metadata, dict) else (metadata or {})
    if _ra is not None:
        try:
            if _safe_snippet:
                _safe_snippet = _ra(_safe_snippet)
            _TEXT_KEYS = (
                "raw_output", "response_snippet", "sanitized_output", "final_output",
                "rewritten_output", "output", "detail", "guardrail_reasoning",
                "reason", "evidence", "original_prompt", "prompt", "preview", "message",
                # TEL-4: the non-stream lane copies the RAW prompt preview into these
                # metadata keys (main.py: _tel_md["prompt_snippet"]/["prompt_submitted"]
                # = _prompt_snippet[:2000], up to 2000 raw chars). The redacted
                # ``prompt_snippet`` *parameter* above is separate from these metadata
                # keys, which were NOT scrubbed — so unredacted PII persisted in the
                # audit log. Fold every prompt-preview key through redact_all here.
                "prompt_snippet", "prompt_submitted", "prompt_preview",
                "prompt_in", "prompt_out", "user_prompt", "input_text", "input_preview",
                "forwarded_prompt",
            )
            for _k in _TEXT_KEYS:
                _v = _safe_meta.get(_k)
                if isinstance(_v, str) and _v:
                    _safe_meta[_k] = _ra(_v)
                elif isinstance(_v, list):
                    _safe_meta[_k] = [_ra(x) if isinstance(x, str) else x for x in _v]
        except Exception:
            pass
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "model": model,
        "user_id": user_id,
        "project_id": project_id,
        "key_prefix": key_prefix,
        "prompt_hash": prompt_hash,
        "prompt_snippet": _safe_snippet,
        "endpoint_id": endpoint_id,
        "latency_ms": round(latency_ms, 2),
        "risk_score": risk_score,
        "action": action,
        "threat_type": threat_type,
        "tokens_used": tokens_used or {},
        "compliance_tags": compliance_tags or [],
        "pipeline_stage": pipeline_stage,
        "intent": intent,
        "organization_id": organization_id,
        "method": method,
        "source_ip": source_ip,
        "user_agent": user_agent,
        "status_code": status_code,
        "metadata": _safe_meta,
    }
