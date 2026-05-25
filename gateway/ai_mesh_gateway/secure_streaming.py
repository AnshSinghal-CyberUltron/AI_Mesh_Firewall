"""
Secure Streaming Response (Phase 3 - Step 3.2, extended with Output Guard).

Buffers SSE chunks from the LLM, scans for PII patterns, credential exposure,
IP leakage, and hallucination markers, then yields to the client.

Buffering Strategy:
1. Accumulate content deltas until sentence boundary or buffer limit
2. If OutputGuard is available, run full output inspection (PII, credential,
   IP leakage, hallucination). Otherwise fall back to scanner.scan_output()
3. Handle verdicts: block terminates the stream, redact replaces content,
   flag emits telemetry and passes through
4. Yield original SSE chunks with (possibly redacted) content
5. On stream end ([DONE]): flush remaining buffer through scanner
"""

import json
import logging
from typing import AsyncGenerator, TYPE_CHECKING

if TYPE_CHECKING:
    from output_guard import OutputGuard
    from telemetry import TelemetryProducer

LOG = logging.getLogger("gateway.secure_streaming")

DEFAULT_BUFFER_MAX_BYTES = 4096
SENTENCE_BOUNDARIES = frozenset(".!?\n")


class SecureStreamingResponse:
    """
    Wraps an SSE async generator with output scanning.

    The inner generator yields SSE-formatted strings (e.g., "data: {...}\\n\\n").
    This class buffers content deltas, scans at sentence boundaries using
    OutputGuard (if available) or the basic scanner, and yields (possibly
    redacted) SSE chunks. Blocking verdicts terminate the stream with an
    error SSE event.
    """

    def __init__(
        self,
        inner_generator: AsyncGenerator[str, None],
        scanner: "InputScanner",
        redaction_enabled: bool = True,
        buffer_max_bytes: int = DEFAULT_BUFFER_MAX_BYTES,
        output_guard: "OutputGuard | None" = None,
        telemetry: "TelemetryProducer | None" = None,
        # Request context for telemetry enrichment
        user_id: int | str | None = None,
        organization_id: int | None = None,
        model: str = "",
        project_id: str = "",
        source_ip: str = "",
        request_id: str = "",
    ) -> None:
        self._inner = inner_generator
        self._scanner = scanner
        self._redaction_enabled = redaction_enabled
        self._buffer_max_bytes = buffer_max_bytes
        self._output_guard = output_guard
        self._telemetry = telemetry
        self._user_id = user_id
        self._organization_id = organization_id
        self._model = model
        self._project_id = project_id
        self._source_ip = source_ip
        self._request_id = request_id
        self._content_buffer: list[str] = []
        self._content_buffer_len: int = 0
        self._chunk_queue: list[tuple[str, str]] = []
        self._stream_blocked: bool = False

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
                    async for flushed in self._flush_buffer():
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

                content_delta = self._extract_content_delta(chunk_data)

                if not content_delta:
                    yield raw_sse
                    continue

                self._chunk_queue.append((raw_sse, content_delta))
                self._content_buffer.append(content_delta)
                self._content_buffer_len += len(content_delta.encode("utf-8"))

                should_flush = (
                    self._content_buffer_len >= self._buffer_max_bytes
                    or any(ch in content_delta for ch in SENTENCE_BOUNDARIES)
                )

                if should_flush:
                    async for flushed in self._flush_buffer():
                        yield flushed

        except Exception:
            LOG.exception("Error in secure streaming response")
            async for flushed in self._flush_buffer():
                yield flushed

    async def _flush_buffer(self) -> AsyncGenerator[str, None]:
        if not self._chunk_queue:
            return

        full_text = "".join(self._content_buffer)

        if self._output_guard is not None:
            verdict = await self._output_guard.inspect(full_text)

            if verdict.action == "block":
                LOG.warning(
                    "Output guard blocked streaming content (type=%s, detail=%s)",
                    verdict.threat_type,
                    verdict.detail,
                )
                self._stream_blocked = True
                if self._telemetry is not None:
                    from telemetry import build_telemetry_event

                    self._telemetry.emit(build_telemetry_event(
                        event_type="output_guard",
                        action="block",
                        threat_type=verdict.threat_type,
                        compliance_tags=verdict.compliance_tags,
                        user_id=self._user_id,
                        organization_id=self._organization_id,
                        model=self._model,
                        project_id=self._project_id,
                        source_ip=self._source_ip,
                        metadata={
                            "detail": verdict.detail,
                            "streaming": True,
                            "request_id": self._request_id,
                            "module": "1.7",
                            "module_id": "1.7",
                        },
                    ))
                error_sse = self._build_error_sse(
                    f"Response blocked: {verdict.threat_type} detected in output."
                )
                yield error_sse
                self._content_buffer.clear()
                self._content_buffer_len = 0
                self._chunk_queue.clear()
                return

            if verdict.action == "redact":
                redacted_text = self._scanner.redact_pii(full_text)
                LOG.info(
                    "Output guard redacted streaming content (type=%s, patterns=%s)",
                    verdict.threat_type,
                    verdict.matched_patterns,
                )
                for redacted_chunk in self._yield_redacted(redacted_text):
                    yield redacted_chunk
                self._clear_buffers()
                return

            if verdict.action == "flag":
                if self._telemetry is not None:
                    from telemetry import build_telemetry_event

                    self._telemetry.emit(build_telemetry_event(
                        event_type="output_guard",
                        action="flag",
                        threat_type=verdict.threat_type,
                        compliance_tags=verdict.compliance_tags,
                        user_id=self._user_id,
                        organization_id=self._organization_id,
                        model=self._model,
                        project_id=self._project_id,
                        source_ip=self._source_ip,
                        metadata={
                            "detail": verdict.detail,
                            "streaming": True,
                            "request_id": self._request_id,
                            "module": "1.7",
                            "module_id": "1.7",
                        },
                    ))

            for original_sse, _ in self._chunk_queue:
                yield original_sse
            self._clear_buffers()
            return

        verdict = await self._scanner.scan_output(full_text)

        if verdict.threat_type in ("pii", "secret") and verdict.matched_patterns:
            redacted_text = self._scanner.redact_pii(full_text)
            LOG.info(
                "PII redacted in streaming output (type=%s, patterns=%s)",
                verdict.threat_type,
                verdict.matched_patterns,
            )
            for redacted_chunk in self._yield_redacted(redacted_text):
                yield redacted_chunk
        else:
            for original_sse, _ in self._chunk_queue:
                yield original_sse

        self._clear_buffers()

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
