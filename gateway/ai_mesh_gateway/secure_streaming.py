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
# Streaming PII-race guard: on a non-final flush, hold back the trailing N bytes
# of (clean) content so a PII value that is SPLIT across a flush boundary (e.g.
# "alex@" in this flush, "example.com" in the next) is re-scanned WITH its
# completion before any part of it is released to the client. Must exceed the
# longest single PII/secret token (email/phone/SSN/credit-card/API-key < 512B).
STREAM_LOOKAHEAD_BYTES = 512


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
        org_config: dict | None = None,
        context_chunks: list[str] | None = None,
        context_resolver: "Callable[[], object] | None" = None,
        request: object | None = None,
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
        # M-05: request context so the output guard runs with org tri-state tier-2
        # gating, correct breaker org attribution, and RAG grounding (instead of
        # context-blind inspect(full_text)). All optional/defensive: when absent
        # the guard falls back to gateway-level config + empty (no-context) grounding.
        self._org_config = org_config
        # Static RAG context if the caller already resolved it; else resolved once
        # lazily via context_resolver on the first flush (Redis fetch is async).
        self._context_chunks: list[str] | None = context_chunks
        self._context_resolver = context_resolver
        self._context_resolved = context_chunks is not None or context_resolver is None
        self._content_buffer: list[str] = []
        self._content_buffer_len: int = 0
        self._chunk_queue: list[tuple[str, str]] = []
        self._stream_blocked: bool = False
        self._last_flush_reason: FlushReason | None = None
        # streaming #4: optional FastAPI/Starlette request for client-disconnect
        # detection. Optional + backward-compatible: when None (or it has no
        # is_disconnected) the loop never polls and behavior is unchanged. When
        # the client goes away we stop pulling the inner generator so upstream
        # generation/scanning halts; buffered-but-unflushed content is dropped.
        self._disconnect_check = getattr(request, "is_disconnected", None) if request is not None else None

    async def __aiter__(self) -> AsyncGenerator[str, None]:
        try:
            async for raw_sse in self._inner:
                if self._stream_blocked:
                    break

                # streaming #4: stop pulling upstream once the client has gone
                # away. Poll at most once per inner chunk (cheap). Returning ends
                # the generator cleanly; unflushed buffered content is discarded
                # (it was never released, so nothing leaks). Defensive: a probing
                # failure must never break a still-live stream.
                if self._disconnect_check is not None:
                    try:
                        if await self._disconnect_check():
                            LOG.info(
                                "Client disconnected mid-stream (request_id=%s); "
                                "halting secure stream.",
                                self._request_id,
                            )
                            self._clear_buffers()
                            return
                    except Exception:
                        pass

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
                    # M-51: surface the upstream error to the stream metrics so
                    # the terminal zeroshield trace frame reports action=error.
                    if self._stream_metrics is not None:
                        self._stream_metrics.had_error = True
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
            # M-51: mark the run errored so the terminal trace frame and the
            # finalization phase attribute this termination correctly.
            if self._stream_metrics is not None:
                self._stream_metrics.had_error = True
            if not self._stream_blocked:
                yield "data: [DONE]\n\n"

    async def _flush_buffer(self, reason: FlushReason) -> AsyncGenerator[str, None]:
        if not self._chunk_queue:
            return
        self._last_flush_reason = reason

        full_text = "".join(self._content_buffer)

        if self._output_guard is not None:
            # M-05: thread request context (RAG chunks + org_config + org_slug) into
            # the guard so streaming output gets the same tri-state tier-2 gating and
            # grounding the non-streaming path already has. Defensive throughout:
            # resolver failures fail-open to no-context (current behavior), and a
            # guard whose inspect() predates these kwargs falls back to the legacy
            # single-arg call so no caller/test is regressed.
            context_chunks = await self._resolve_context_chunks()
            try:
                verdict = await self._output_guard.inspect(
                    full_text,
                    context_chunks=context_chunks,
                    org_config=self._org_config,
                    org_slug=self._org_slug or "",
                )
            except TypeError:
                # Older/duck-typed guard: inspect(text) only.
                verdict = await self._output_guard.inspect(full_text)

            # H-03 FIX: a tier-2 output-guard OUTAGE sets verdict.scan_degraded,
            # meaning the streamed response was passed only partially / UN-scanned
            # (fail-open by design). The non-stream path (M11) emits an operator
            # 'output_scan_degraded' signal so the outage is observable — streaming
            # had NO such signal, so a silent guard outage on a streamed response
            # was completely invisible to operators. Emit the same visibility
            # telemetry here, once per stream (the flush handler can run on every
            # flush; the guard flag prevents one outage producing N duplicate events).
            if getattr(verdict, "scan_degraded", False) and not getattr(self, "_degraded_emitted", False):
                self._degraded_emitted = True
                self._emit_degraded_telemetry(verdict, flush_reason=reason)

            effective_action = verdict.action
            if verdict.action == "flag" and self._enforcement_mode == "block":
                effective_action = "block"
            # F2: 'rewrite' has no mid-stream analog — the non-stream path re-infers
            # AFTER the full response exists, which streaming cannot do. Without this,
            # a rewrite verdict matched none of the block/redact/flag handlers and
            # fell through to the clean-release path, streaming the ORIGINAL unsafe
            # content. Coerce rewrite -> block so the original is never delivered
            # (the non-stream rewrite intent is "do not deliver the original").
            if verdict.action == "rewrite":
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
                    self._record_guard_metrics("block", verdict)
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
                self._record_output(redacted_text)
                LOG.info(
                    "Output guard redacted streaming content (type=%s, patterns=%s, flush=%s)",
                    verdict.threat_type,
                    verdict.matched_patterns,
                    reason.value,
                )
                self._record_guard_metrics("redact", verdict)
                self._emit_guard_telemetry(verdict, action="redact", flush_reason=reason)
                self._record_metric("redact")
                for redacted_chunk in self._yield_redacted(redacted_text):
                    yield redacted_chunk
                self._clear_buffers()
                return

            if verdict.action == "flag":
                self._record_guard_metrics("flag", verdict)
                self._emit_guard_telemetry(verdict, action="flag", flush_reason=reason)
                self._audit_output_guard(verdict, action="flag")
                self._record_metric("flag")

            # Clean / flag: release, but on a NON-final flush hold back the
            # lookahead tail so a PII token split across this boundary cannot be
            # half-streamed before a later scan blocks it (streaming PII race).
            self._record_output(full_text)
            if reason == FlushReason.DONE:
                for original_sse, _ in self._chunk_queue:
                    yield original_sse
                self._clear_buffers()
            else:
                for original_sse in self._release_with_lookahead_tail():
                    yield original_sse
            return

        verdict = await self._scanner.scan_output(full_text)

        if verdict.threat_type in ("pii", "secret") and verdict.matched_patterns:
            redacted_text = self._scanner.redact_pii(full_text)
            self._record_output(redacted_text)
            LOG.info(
                "PII redacted in streaming output (type=%s, patterns=%s, flush=%s)",
                verdict.threat_type,
                verdict.matched_patterns,
                reason.value,
            )
            self._record_guard_metrics("redact", verdict)
            self._record_metric("redact")
            for redacted_chunk in self._yield_redacted(redacted_text):
                yield redacted_chunk
            self._clear_buffers()
        elif reason == FlushReason.DONE:
            self._record_output(full_text)
            for original_sse, _ in self._chunk_queue:
                yield original_sse
            self._clear_buffers()
        else:
            # Clean: release leading chunks, hold the lookahead tail so a PII
            # token split across this boundary is re-scanned whole next flush.
            for original_sse in self._release_with_lookahead_tail():
                yield original_sse

    async def _resolve_context_chunks(self) -> list[str]:
        """Return RAG context chunks for grounding, resolving lazily once.

        M-05: the Redis fetch for the RAG context is async, so when the caller
        could not resolve it eagerly it provides an async ``context_resolver``
        that we run on the first flush and cache. Always fail-open to ``[]`` so a
        missing/broken context never blocks or crashes the stream (matches the
        non-streaming path, which uses an empty list when no RAG context exists).
        """
        if self._context_chunks is not None:
            return self._context_chunks
        if self._context_resolved or self._context_resolver is None:
            return []
        self._context_resolved = True
        try:
            resolved = self._context_resolver()
            if asyncio.iscoroutine(resolved):
                resolved = await resolved
            self._context_chunks = list(resolved) if resolved else []
        except Exception:
            # Best-effort: context binding never breaks the stream.
            self._context_chunks = []
        return self._context_chunks

    def _record_output(self, text: str) -> None:
        """Accumulate the client-facing (post-redaction) streamed text onto the
        shared StreamRunMetrics so the Scan Detail "Output" panel can show the
        delivered response for STREAMED requests. Blocked content is never
        recorded here — only what was actually released to the client."""
        if self._stream_metrics is None or not text:
            return
        try:
            self._stream_metrics.append_output(text)
        except Exception:
            pass

    def _record_metric(self, action: str) -> None:
        if self._record_guard_metric is not None:
            try:
                self._record_guard_metric(self._org_slug, action)
            except Exception:
                pass

    def _record_guard_metrics(self, action: str, verdict) -> None:
        """M-51: persist the guard verdict onto StreamRunMetrics so the
        terminal zeroshield trace frame can report the mid-stream outcome."""
        if self._stream_metrics is None:
            return
        try:
            self._stream_metrics.record_guard_action(
                action,
                threat_type=str(getattr(verdict, "threat_type", "") or ""),
                detail=str(getattr(verdict, "detail", "") or "")[:256],
                matched_patterns=list(getattr(verdict, "matched_patterns", None) or []),
            )
        except Exception:
            # Metrics enrichment must never break the stream.
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

    def _emit_degraded_telemetry(self, verdict, *, flush_reason: FlushReason) -> None:
        """H-03: surface a tier-2 output-guard OUTAGE on the STREAMING path the
        same way the non-stream M11 branch does, so a silent guard outage on a
        streamed response is observable to operators. Fail-open (the stream is
        still delivered) is intentional, but it must never be invisible."""
        if self._telemetry is None:
            return
        try:
            from telemetry import build_telemetry_event

            self._telemetry.emit(build_telemetry_event(
                event_type="output_scan_degraded",
                action="allow",
                threat_type="scanner_degraded",
                user_id=self._user_id,
                organization_id=self._organization_id,
                model=self._model,
                project_id=self._project_id,
                source_ip=self._source_ip,
                metadata={
                    "detail": "Tier-2 output guard model unavailable — streamed output passed UNSCANNED",
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

    def _release_with_lookahead_tail(self):
        """Yield the leading (already-scanned-clean) chunks but RETAIN a trailing
        ``STREAM_LOOKAHEAD_BYTES`` window in the buffer for re-scanning on the next
        flush. This closes the streaming PII race: the current flush's full text
        was scanned clean, but a PII token could still be *starting* at the very
        end of the buffer and complete in a later chunk — so the tail that could
        hold a partial token is never released until it is re-scanned whole. When
        a later scan does detect the (now complete) PII, the block/redact paths
        operate on the FULL buffer (tail included) and the tail is dropped/redacted
        rather than streamed raw. A short buffer (< lookahead) retains everything
        and releases nothing this flush (it is released at the DONE flush)."""
        acc = 0
        keep_from = 0
        for i in range(len(self._chunk_queue) - 1, -1, -1):
            acc += len(self._chunk_queue[i][1].encode("utf-8"))
            keep_from = i
            if acc >= STREAM_LOOKAHEAD_BYTES:
                break
        release = self._chunk_queue[:keep_from]
        retain = self._chunk_queue[keep_from:]
        self._chunk_queue = retain
        self._content_buffer = [c for _, c in retain]
        self._content_buffer_len = sum(len(c.encode("utf-8")) for _, c in retain)
        for original_sse, _ in release:
            yield original_sse

    def _extract_content_delta(self, chunk_data: dict) -> str:
        """Concatenate every text-bearing field of the delta into a single str
        for scanning. The output guard must see ALL channels that stream raw to
        the client — not only ``delta.content`` but also ``reasoning_content``
        and tool-call argument/name strings — or PII/secrets in those channels
        leak unscanned. Always returns a ``str`` (list-shaped content parts are
        coerced) so the downstream buffer/lookahead byte-length logic is safe."""
        choices = chunk_data.get("choices") or []
        if not choices:
            return ""

        parts: list[str] = []
        # R12 (#14 stream parity): scan EVERY choice's delta, not just choices[0],
        # so a secret in a parallel (n>1) choice still trips the guard and the
        # buffered chunk is dropped on a bad verdict.
        for ch in choices:
            if not isinstance(ch, dict):
                continue
            delta = ch.get("delta") or {}
            if not isinstance(delta, dict):
                continue

            # FIX-C: content may be a list of content-part dicts; coerce to text.
            content = delta.get("content")
            if isinstance(content, list):
                content = "".join(
                    p.get("text") or "" for p in content if isinstance(p, dict)
                )
            elif not isinstance(content, str):
                content = ""
            parts.append(content)

            # FIX-A: reasoning_content streams raw to the client too — scan it.
            reasoning = delta.get("reasoning_content")
            if isinstance(reasoning, str):
                parts.append(reasoning)

            # FIX-B: tool-call function name + arguments stream raw — scan them.
            tool_calls = delta.get("tool_calls")
            if isinstance(tool_calls, list):
                for call in tool_calls:
                    if not isinstance(call, dict):
                        continue
                    fn = call.get("function")
                    if not isinstance(fn, dict):
                        continue
                    name = fn.get("name")
                    if isinstance(name, str):
                        parts.append(name)
                    arguments = fn.get("arguments")
                    if isinstance(arguments, str):
                        parts.append(arguments)

            # R12 (#13): legacy `function_call` delta channel (pre-tool_calls API
            # shape) streams raw too — scan name + arguments (non-stream I5 parity).
            fc = delta.get("function_call")
            if isinstance(fc, dict):
                for _k in ("name", "arguments"):
                    _v = fc.get(_k)
                    if isinstance(_v, str):
                        parts.append(_v)

            # R12 (#15): refusal channel streams raw — scan it.
            refusal = delta.get("refusal")
            if isinstance(refusal, str):
                parts.append(refusal)

            # R13 (#16 stream parity): audio-output transcript streams raw too.
            _au = delta.get("audio")
            if isinstance(_au, dict):
                _t = _au.get("transcript")
                if isinstance(_t, str):
                    parts.append(_t)

        return "".join(parts)

    @staticmethod
    def _blank_streaming_secondary_channels(delta: dict) -> None:
        """R13: blank EVERY secondary text channel that _extract_content_delta
        scans, so a redact/block rebuild never streams an un-redacted secret in a
        non-content channel. Mirrors the non-stream _neutralize_secondary_output_
        channels: reasoning_content, tool_calls fn name/args, legacy function_call,
        refusal, audio.transcript. Only string fields are rewritten so the SSE
        frame stays well-formed."""
        if not isinstance(delta, dict):
            return
        if isinstance(delta.get("reasoning_content"), str):
            delta["reasoning_content"] = ""
        for call in (delta.get("tool_calls") or []):
            if isinstance(call, dict) and isinstance(call.get("function"), dict):
                fn = call["function"]
                if isinstance(fn.get("arguments"), str):
                    fn["arguments"] = ""
                if isinstance(fn.get("name"), str):
                    fn["name"] = ""
        fc = delta.get("function_call")
        if isinstance(fc, dict):
            for _k in ("name", "arguments"):
                if isinstance(fc.get(_k), str):
                    fc[_k] = ""
        if isinstance(delta.get("refusal"), str):
            delta["refusal"] = ""
        # R14: blank audio.transcript AND audio.data — the base64 audio bytes
        # carry the spoken content, so redacting only the transcript still ships
        # the secret as audio.
        _au = delta.get("audio")
        if isinstance(_au, dict):
            if isinstance(_au.get("transcript"), str):
                _au["transcript"] = ""
            if isinstance(_au.get("data"), str):
                _au["data"] = ""

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
        # R13: the secondary text-bearing channels were scanned as part of
        # full_text; on a redact/block rebuild they must NOT stream raw. They can't
        # carry the (single) redacted content, so blank them in place across EVERY
        # choice (not just choices[0]) — reasoning_content, tool_calls, legacy
        # function_call, refusal, audio.transcript — preserving SSE structure. The
        # first choice carries the redacted content; other choices are blanked
        # (the redaction is a single concatenated stream).
        first_done = False
        for ch in choices:
            if not isinstance(ch, dict) or "delta" not in ch:
                continue
            delta = ch["delta"]
            if not isinstance(delta, dict):
                continue
            delta["content"] = new_content if not first_done else ""
            first_done = True
            self._blank_streaming_secondary_channels(delta)
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
