"""
Secure Streaming Response (Phase 3 - Step 3.2, extended with Output Guard).

Buffers SSE chunks from the LLM, scans for PII patterns, credential exposure,
IP leakage, and hallucination markers, then yields to the client.
"""
from __future__ import annotations

import asyncio
import json
import logging
from enum import Enum
from typing import AsyncGenerator, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from output_guard import OutputGuard
    from stream_orchestration import StreamRunMetrics
    from telemetry import TelemetryProducer

LOG = logging.getLogger("gateway.secure_streaming")

DEFAULT_BUFFER_MAX_BYTES = 4096
DEFAULT_MAX_BUFFER_CHUNKS = 64
SENTENCE_BOUNDARIES = frozenset(".!?\n")


class FlushReason(str, Enum):
    BOUNDARY = "boundary"
    BUFFER_LIMIT = "buffer_limit"
    DONE = "done"
    ERROR = "error"


class SecureStreamingResponse:
    """
    Wraps an SSE async generator with output scanning.

    The inner generator yields SSE-formatted strings (e.g., "data: {...}\\n\\n").
    This class buffers content deltas, scans at sentence boundaries using
    OutputGuard (if available) or the basic scanner, and yields (possibly
    redacted) SSE chunks. Blocking verdicts terminate the stream with an
    error SSE event followed by [DONE].
    """

    def __init__(
        self,
        inner_generator: AsyncGenerator[str, None],
        scanner: "InputScanner",
        redaction_enabled: bool = True,
        buffer_max_bytes: int = DEFAULT_BUFFER_MAX_BYTES,
        max_buffer_chunks: int = DEFAULT_MAX_BUFFER_CHUNKS,
        output_guard: "OutputGuard | None" = None,
        telemetry: "TelemetryProducer | None" = None,
        user_id: int | str | None = None,
        organization_id: int | None = None,
        model: str = "",
        project_id: str = "",
        source_ip: str = "",
        request_id: str = "",
        record_guard_metric: Callable[[str, str], None] | None = None,
        org_slug: str = "",
        stream_metrics: "StreamRunMetrics | None" = None,
        enforcement_mode: str = "block",
    ) -> None:
        self._inner = inner_generator
        self._scanner = scanner
        self._redaction_enabled = redaction_enabled
        self._buffer_max_bytes = buffer_max_bytes
        self._max_buffer_chunks = max(1, max_buffer_chunks)
        self._output_guard = output_guard
        self._telemetry = telemetry
        self._user_id = user_id
        self._organization_id = organization_id
        self._model = model
        self._project_id = project_id
        self._source_ip = source_ip
        self._request_id = request_id
        self._record_guard_metric = record_guard_metric
        self._org_slug = org_slug
        self._stream_metrics = stream_metrics
        self._enforcement_mode = (enforcement_mode or "block").strip().lower()
        self._content_buffer: list[str] = []
        self._content_buffer_len: int = 0
        self._chunk_queue: list[tuple[str, str]] = []
        self._stream_blocked: bool = False
        self._last_flush_reason: FlushReason | None = None

    async def __aiter__(self) -> AsyncGenerator[str, None]:
        try:
            async for raw_sse in self._inner:
                if self._stream_blocked:
                    break

                if not self._redaction_enabled:
                    yield raw_sse
                    continue

                line = raw_sse.strip()

                if line == "data: [DONE]" or line == "[DONE]":
                    async for flushed in self._flush_buffer(FlushReason.DONE):
                        yield flushed
                    if not self._stream_blocked:
                        yield raw_sse
                    continue

                if not line.startswith("data: "):
                    yield raw_sse
                    continue

                json_str = line[6:]
                try:
                    chunk_data = json.loads(json_str)
                except (json.JSONDecodeError, TypeError):
                    yield raw_sse
                    continue

                if isinstance(chunk_data, dict) and chunk_data.get("error"):
                    yield raw_sse
                    yield "data: [DONE]\n\n"
                    self._stream_blocked = True
                    break

                content_delta = self._extract_content_delta(chunk_data)

                if not content_delta:
                    yield raw_sse
                    continue

                if len(self._chunk_queue) >= self._max_buffer_chunks:
                    async for flushed in self._flush_buffer(FlushReason.BUFFER_LIMIT):
                        yield flushed
                    if self._stream_blocked:
                        break

                self._chunk_queue.append((raw_sse, content_delta))
                self._content_buffer.append(content_delta)
                self._content_buffer_len += len(content_delta.encode("utf-8"))

                flush_reason = None
                if self._content_buffer_len >= self._buffer_max_bytes:
                    flush_reason = FlushReason.BUFFER_LIMIT
                elif any(ch in content_delta for ch in SENTENCE_BOUNDARIES):
                    flush_reason = FlushReason.BOUNDARY

                if flush_reason is not None:
                    async for flushed in self._flush_buffer(flush_reason):
                        yield flushed

        except Exception:
            LOG.exception("Error in secure streaming response")
            # Fail-closed: do not flush buffered content that was not scanned.
            self._clear_buffers()
            if not self._stream_blocked:
                yield "data: [DONE]\n\n"

    async def _flush_buffer(self, reason: FlushReason) -> AsyncGenerator[str, None]:
        if not self._chunk_queue:
            return
        self._last_flush_reason = reason

        full_text = "".join(self._content_buffer)

        if self._output_guard is not None:
            verdict = await self._output_guard.inspect(full_text)

            effective_action = verdict.action
            if verdict.action == "flag" and self._enforcement_mode == "block":
                effective_action = "block"

            if effective_action == "block":
                LOG.warning(
                    "Output guard blocked streaming content (type=%s, detail=%s, flush=%s)",
                    verdict.threat_type,
                    verdict.detail,
                    reason.value,
                )
                self._stream_blocked = True
                if self._stream_metrics is not None:
                    self._stream_metrics.output_blocked = True
                    self._stream_metrics.completed = True
                self._emit_guard_telemetry(verdict, action="block", flush_reason=reason)
                self._audit_output_guard(verdict, action="block")
                self._record_metric("block")
                yield self._build_error_sse(
                    f"Response blocked: {verdict.threat_type} detected in output."
                )
                yield "data: [DONE]\n\n"
                self._clear_buffers()
                return

            if verdict.action == "redact":
                redacted_text = self._scanner.redact_pii(full_text)
                LOG.info(
                    "Output guard redacted streaming content (type=%s, patterns=%s, flush=%s)",
                    verdict.threat_type,
                    verdict.matched_patterns,
                    reason.value,
                )
                self._emit_guard_telemetry(verdict, action="redact", flush_reason=reason)
                self._record_metric("redact")
                for redacted_chunk in self._yield_redacted(redacted_text):
                    yield redacted_chunk
                self._clear_buffers()
                return

            if verdict.action == "flag":
                self._emit_guard_telemetry(verdict, action="flag", flush_reason=reason)
                self._audit_output_guard(verdict, action="flag")
                self._record_metric("flag")

            for original_sse, _ in self._chunk_queue:
                yield original_sse
            self._clear_buffers()
            return

        verdict = await self._scanner.scan_output(full_text)

        if verdict.threat_type in ("pii", "secret") and verdict.matched_patterns:
            redacted_text = self._scanner.redact_pii(full_text)
            LOG.info(
                "PII redacted in streaming output (type=%s, patterns=%s, flush=%s)",
                verdict.threat_type,
                verdict.matched_patterns,
                reason.value,
            )
            self._record_metric("redact")
            for redacted_chunk in self._yield_redacted(redacted_text):
                yield redacted_chunk
        else:
            for original_sse, _ in self._chunk_queue:
                yield original_sse

        self._clear_buffers()

    def _record_metric(self, action: str) -> None:
        if self._record_guard_metric is not None:
            try:
                self._record_guard_metric(self._org_slug, action)
            except Exception:
                pass

    def _audit_output_guard(self, verdict, *, action: str) -> None:
        if not self._org_slug:
            return
        try:
            from telemetry_ops import emit_query_audit_event, _log_task_exception  # type: ignore

            task = asyncio.create_task(
                emit_query_audit_event(
                    org_slug=self._org_slug,
                    decision="block" if action == "block" else action,
                    rule_code=str(getattr(verdict, "threat_type", None) or "output_guard"),
                    metadata={
                        "streaming": True,
                        "request_id": self._request_id,
                        "model": self._model,
                        "detail": (getattr(verdict, "detail", None) or "")[:256],
                    },
                )
            )
            task.add_done_callback(_log_task_exception)
        except Exception:
            pass

    def _emit_guard_telemetry(self, verdict, *, action: str, flush_reason: FlushReason) -> None:
        if self._telemetry is None:
            return
        try:
            from telemetry import build_telemetry_event

            self._telemetry.emit(build_telemetry_event(
                event_type="output_guard",
                action=action,
                threat_type=verdict.threat_type,
                compliance_tags=verdict.compliance_tags,
                user_id=self._user_id,
                organization_id=self._organization_id,
                model=self._model,
                project_id=self._project_id,
                source_ip=self._source_ip,
                metadata={
                    "detail": (verdict.detail or "")[:256],
                    "streaming": True,
                    "request_id": self._request_id,
                    "flush_reason": flush_reason.value,
                    "module": "1.7",
                    "module_id": "1.7",
                },
            ))
        except Exception:
            pass

    def _yield_redacted(self, redacted_text: str):
        """Yield redacted content: first chunk gets all text, rest get empty."""
        if len(self._chunk_queue) == 1:
            original_sse, _ = self._chunk_queue[0]
            yield self._rebuild_sse_content(original_sse, redacted_text)
        else:
            first_sse, _ = self._chunk_queue[0]
            yield self._rebuild_sse_content(first_sse, redacted_text)
            for i in range(1, len(self._chunk_queue)):
                original_sse, _ = self._chunk_queue[i]
                yield self._rebuild_sse_content(original_sse, "")

    def _clear_buffers(self) -> None:
        """Reset all internal buffers."""
        self._content_buffer.clear()
        self._content_buffer_len = 0
        self._chunk_queue.clear()

    def _extract_content_delta(self, chunk_data: dict) -> str:
        choices = chunk_data.get("choices") or []
        if not choices:
            return ""
        delta = choices[0].get("delta") or {}
        return delta.get("content") or ""

    def _rebuild_sse_content(self, original_sse: str, new_content: str) -> str:
        line = original_sse.strip()
        if not line.startswith("data: "):
            return original_sse
        json_str = line[6:]
        try:
            chunk_data = json.loads(json_str)
        except (json.JSONDecodeError, TypeError):
            return original_sse
        choices = chunk_data.get("choices") or []
        if choices and "delta" in choices[0]:
            choices[0]["delta"]["content"] = new_content
        return f"data: {json.dumps(chunk_data)}\n\n"

    @staticmethod
    def _build_error_sse(message: str) -> str:
        """Build an SSE event indicating the stream was blocked."""
        error_data = {
            "error": {
                "message": message,
                "type": "output_blocked",
                "code": "output_blocked",
            }
        }
        return f"data: {json.dumps(error_data)}\n\n"
