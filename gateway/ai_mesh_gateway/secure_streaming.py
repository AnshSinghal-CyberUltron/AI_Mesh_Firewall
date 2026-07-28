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
# E14 long-secret split fix: characters that can appear inside a high-entropy
# secret/API-key/JWT body. A trailing run of these abutting a just-redacted
# secret is treated as a "secret in progress" and carried across the flush so
# the un-anchored continuation cannot egress verbatim.
_SECRET_CHARSET = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-."
)
# Minimum trailing-run length to treat as a secret-in-progress anchor. Bounded
# tokens (email/SSN/phone/credit-card — all < 32 chars) are NOT carried, so the
# 18 bounded-token streaming cases that already reassemble correctly are
# unaffected; only a LONG high-entropy run that abuts the buffer edge (an
# unbounded API key/JWT body truncated mid-token) arms the anchor.
_SECRET_ANCHOR_MIN = 32

# G40: cap on how long the buffer will HOLD an unclosed markdown media/link opener
# (``![alt](url…`` / ``[text](url…`` whose closing ``)`` has not arrived) so the
# COMPLETED beacon is scanned + defanged whole instead of having its prefix released
# early on a BUFFER_LIMIT flush. An unclosed opener cannot render; it only becomes a
# (possibly zero-click) exfil beacon once its ``)`` arrives — but a base64/hex exfil
# payload contains no SENTENCE_BOUNDARIES char, so a >buffer_max_bytes payload forces
# a mid-URL flush and the clean-release path would ship the beacon prefix before the
# ``)`` is seen. Holding to this cap keeps the opener buffered until it closes (then
# the G13/G36 redact path neutralizes it) or, at the cap, we fail closed and defang
# in place. Sized well above any real inline-image URL yet bounded so a never-closing
# opener cannot grow the buffer without limit.
MAX_OPEN_MEDIA_HOLDBACK = 8192


def _open_media_opener_start(text: str) -> int | None:
    """Return the char index of the start of an UNCLOSED markdown media/link opener
    that abuts the buffer edge (``![alt](url…`` or ``[text](url…`` whose ``)`` has
    not yet arrived), else ``None``.

    Cheap + linear (no backtracking regex): only the rightmost ``](`` can be the
    open tail. If a ``)`` follows it the construct is already closed (no open tail);
    otherwise the opener starts at the ``[`` that pairs with that ``](`` (extended
    left one char to include a leading ``!`` so the zero-click IMAGE form is held as
    one unit)."""
    j = text.rfind("](")
    if j == -1:
        return None
    if text.find(")", j + 2) != -1:  # a ')' after '(' => construct already closed
        return None
    lb = text.rfind("[", 0, j)       # the '[' paired with this '](' ']'
    if lb == -1:
        return None
    return lb - 1 if lb > 0 and text[lb - 1] == "!" else lb


def _defang_open_media(text: str) -> str:
    """Fail-closed neutralization for an unclosed media opener that has grown past
    ``MAX_OPEN_MEDIA_HOLDBACK``: drop the opener + its in-progress URL so no
    (zero-click or one-click) beacon can reassemble client-side. The trailing URL
    bytes that arrive in later deltas carry no opener and render as inert text."""
    start = _open_media_opener_start(text)
    if start is None:
        return text
    return text[:start] + "[exfil-redacted]"


def _trailing_secret_run(text: str) -> str:
    """Return the trailing contiguous ``_SECRET_CHARSET`` run of ``text`` (after
    ignoring a trailing run of sentence-boundary/whitespace flush-trigger chars)
    if it is long enough to be a secret-in-progress, capped at
    ``STREAM_LOOKAHEAD_BYTES`` chars; else ``""``. Used to carry a secret anchor
    across a redact boundary so a >lookahead key that sheds its prefix anchor on
    the redacted-prefix flush cannot release its un-anchored tail raw."""
    # Strip trailing flush-trigger chars (e.g. the '\n'/'.' that fired this flush)
    # so a secret that runs right up to the boundary char is still recognised as
    # abutting the edge.
    end = len(text)
    while end > 0 and text[end - 1] in SENTENCE_BOUNDARIES:
        end -= 1
    i = end
    while i > 0 and text[i - 1] in _SECRET_CHARSET:
        i -= 1
    run = text[i:end]
    if len(run) < _SECRET_ANCHOR_MIN:
        return ""
    return run[-STREAM_LOOKAHEAD_BYTES:]


class _ContinuationVerdict:
    """Minimal verdict-shaped object for metrics when masking a carried-over
    secret continuation (no real OutputGuard verdict exists for the tail)."""

    threat_type = "secret"
    detail = "secret continuation masked across flush boundary"
    matched_patterns = ["secret_continuation"]


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
        # Latch: a WHOLE-RESPONSE replacement (hallucination / class-less policy
        # redact) yields a FIXED string. _flush_buffer runs once per buffered
        # segment, so without this latch that constant was re-emitted on every
        # flush and the client received it 2-3 times concatenated.
        self._response_replaced = False
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
        # E14 long-secret split fix: a "secret-in-progress" anchor carried across
        # flushes. When a non-final redact flush masks a secret that abuts the END
        # of the buffer (its match runs to the buffer edge), the trailing high-
        # entropy run is retained here and PREPENDED to the next flush's scan text
        # so the continuation re-anchors and re-detects as ONE secret span — even
        # if the continuation, on its own, has shed the prefix anchor (e.g. an
        # OpenAI key longer than STREAM_LOOKAHEAD_BYTES split so a boundary flush
        # ships the 'sk-' prefix and a later flush carries only the un-anchored
        # body). The anchor is SCAN-ONLY: it is never re-emitted to the client (it
        # was already delivered masked on the redact flush that set it), so the
        # continuation is masked while no raw bytes are duplicated.
        self._secret_anchor: str = ""
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
                # Phase-6 (P6-STREAM-error): the HTTP status is already 200 (the stream
                # started), so a stock-SDK client would otherwise read a clean EMPTY
                # success on an upstream failure. Emit an OpenAI-parseable error event
                # in-band before [DONE] so the failure is visible to the client.
                yield self._build_error_sse(
                    "The inference provider failed before completing the response.",
                    error_type="server_error", code="upstream_error",
                )
                yield "data: [DONE]\n\n"

    async def _flush_buffer(self, reason: FlushReason) -> AsyncGenerator[str, None]:
        if not self._chunk_queue:
            return
        self._last_flush_reason = reason

        full_text = "".join(self._content_buffer)
        try:
            from output_guard import normalize_output_scan_text
        except ImportError:
            from .output_guard import normalize_output_scan_text
        full_text = normalize_output_scan_text(full_text)
        # STREAM RAW-OUTPUT FIDELITY: capture the PRE-redaction model text (this flush's
        # full buffer) onto the shared StreamRunMetrics BEFORE any redact/block branch
        # below, so the Scan Detail "Raw model output" panel shows what the model produced
        # (``_record_output`` accumulates only the POST-redaction, client-facing text).
        # Recorded even for content that is about to be blocked/redacted — it is scrubbed
        # through redact_all at the telemetry audit choke, so no raw PII persists.
        self._record_raw_output(full_text)
        if not full_text and self._output_guard is not None:
            # L7: whitespace-only / empty model output — release without guard scan.
            if reason == FlushReason.DONE:
                for original_sse, _ in self._chunk_queue:
                    yield original_sse
                self._clear_buffers()
            else:
                for original_sse in self._release_with_lookahead_tail():
                    yield original_sse
            return

        # E14 long-secret split fix (fix_hint option 2): if the PREVIOUS flush
        # redacted a secret that ran to the buffer edge, a "secret-in-progress"
        # anchor is pending. The deterministic secret regexes are PREFIX-anchored
        # (e.g. sk-…), so the un-anchored continuation of a >lookahead key matches
        # NO pattern and would egress verbatim. Mask the leading contiguous
        # secret-charset continuation here, anchor-independently, BEFORE it can be
        # released — so a long key split across a redact boundary cannot shed its
        # anchor and release its tail raw. Consuming the continuation may re-arm
        # the anchor if the secret-charset run still runs to the buffer edge.
        if self._secret_anchor and full_text:
            async for masked_sse in self._consume_secret_continuation(full_text, reason):
                yield masked_sse
            return

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

            # FAIL-CLOSED PARITY (2026-07-21): route the streamed verdict through the
            # SAME enforce_output resolver the non-stream path uses. enforcement.py
            # already names SecureStreamingResponse._flush_buffer as a caller and
            # carries an `is_streaming` flag, but streaming never actually called it,
            # so the fail-closed rules applied ONLY to non-stream responses:
            #   * scan_degraded (tier-2 guard outage) -> redact. Streaming merely
            #     emitted an observability event and then honoured verdict.action, so
            #     an outage FAILED OPEN and the unscanned response streamed out.
            #   * a guard exception -> block.
            # rewrite/flag are preserved by the resolver (F-002 / F-003 UI honesty),
            # and every per-detector action the operator selected is passed through
            # untouched, so this cannot override a selection — it only adds the
            # safety floor that non-stream already had.
            try:
                from enforcement import enforce_output as _enforce_output  # noqa: PLC0415
            except ImportError:  # pragma: no cover - packaging fallback
                from .enforcement import enforce_output as _enforce_output  # noqa: PLC0415
            _decision = _enforce_output(
                verdict_action=verdict.action,
                verdict_threat_type=getattr(verdict, "threat_type", "") or "",
                scan_degraded=bool(getattr(verdict, "scan_degraded", False)),
                enforcement_mode=self._enforcement_mode,
                is_streaming=True,
            )
            effective_action = _decision.action
            # F-003: §1.7 Flag means deliver + flag telemetry. Do NOT escalate
            # flag→block when org enforcement_mode is "block" — that made the UI
            # dishonest (Flag looked like Block at runtime).
            # F-002: rewrite is honored at DONE (hold mid-stream; rewrite then).
            # Never coerce rewrite→block (that withheld a rewritten completion).

            if effective_action == "rewrite":
                if reason != FlushReason.DONE:
                    # Hold the original — do not release unsafe bytes. Keep
                    # buffers so the final DONE flush sees the full response.
                    LOG.info(
                        "Output guard rewrite pending until stream end "
                        "(type=%s, flush=%s, request_id=%s)",
                        verdict.threat_type,
                        reason.value,
                        self._request_id,
                    )
                    return
                try:
                    from output_guard import rewrite_output_response_text
                except ImportError:
                    from .output_guard import rewrite_output_response_text
                rewritten = rewrite_output_response_text(
                    verdict.threat_type or "",
                    getattr(verdict, "detail", None),
                    original_text=full_text,
                )
                # Deterministic safety net: never let a residual PII/secret slip
                # through a static/LLM rewrite on the stream path.
                if self._scanner is not None and hasattr(self._scanner, "redact_pii"):
                    rewritten = self._scanner.redact_pii(rewritten)
                LOG.info(
                    "Output guard rewrote streaming content (type=%s, flush=%s)",
                    verdict.threat_type,
                    reason.value,
                )
                self._record_output(rewritten)
                self._record_guard_metrics("rewrite", verdict)
                self._emit_guard_telemetry(verdict, action="rewrite", flush_reason=reason)
                self._audit_output_guard(verdict, action="rewrite")
                self._record_metric("rewrite")
                for rewritten_chunk in self._yield_redacted(rewritten):
                    yield rewritten_chunk
                self._clear_buffers()
                return

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

            # H-03: branch on the RESOLVED action, not the raw verdict. A tier-2
            # guard outage (scan_degraded) resolves allow->redact in enforce_output;
            # branching on verdict.action here meant that resolved action matched no
            # branch at all and the raw text fell through to the client — the exact
            # fail-open this fix exists to close.
            if effective_action == "redact":
                # G36: mirror the non-stream sanitize_output_for_verdict defense-in-
                # depth on the streamed egress — neutralize output-side data-exfil
                # channels (G13 markdown-image/link beacons) and encoded-PII runs
                # (G35 HTML-entity/percent that decode to PII) BEFORE the PII
                # redactor, so the beacon/encoded payload is seen unmasked and can be
                # defanged. Streaming previously used redact_pii ALONE, so a streamed
                # exfil beacon or encoded-PII rode out un-neutralized while the non-
                # stream path defanged it. Lazy import avoids a circular dependency;
                # only runs on redact verdicts (not the clean-release hot path).
                # STREAM/NON-STREAM PARITY (2026-07-20): delegate to the SAME
                # sanitizer the non-stream path uses. It already performs the
                # neutralize passes (exfil beacons / encoded PII / markdown-split),
                # the G10 semantic-span masking, the hallucination branch and the
                # class-less whole-response replacement — and, critically, it honours
                # verdict.redact_classes.
                #
                # The previous inline implementation called self._scanner.redact_pii()
                # (a BLANKET redact_all) and so masked EVERY detector class on any
                # redact verdict. Measured over the 27 delivered pii x cred x ip
                # combinations: 14 STRICT VIOLATIONS and 14 stream/non-stream parity
                # breaks — e.g. PII=flag + Credential=redact + IP=allow masked the
                # email and the internal IP too, while non-stream masked only the
                # key. For a class-less Policy=redact it masked three allow-classes
                # AND still streamed the jailbreak text verbatim, because the
                # class-less replacement branch lived only in the non-stream core.
                from output_guard import (  # noqa: PLC0415
                    sanitize_output_for_verdict as _sanitize_for_verdict,
                )
                # V3-01 (HIGH): hand the sanitizer the RESOLVED action, not the raw
                # verdict. ``enforce_output`` resolves allow->redact under a guard
                # outage (scan_degraded) and the branch above correctly keys on
                # ``effective_action`` — but passing the UNRESOLVED verdict made
                # ``_sanitize_output_core`` short-circuit on
                # ``if action != "redact": return response_text`` (verdict.action was
                # still "allow"), so the scrub was a strict NO-OP and the branch
                # released the UNSCANNED bytes. Measured on one guard flush, identical
                # config, verdict=OutputVerdict(action="allow", threat_type="",
                # scan_degraded=True):
                #     non-stream -> 'Contact j***@a***.com about SSN ***-**-9083'
                #     stream     -> 'Contact john.doe@acme.com about SSN 412-55-9083'
                # i.e. the fail-closed floor for a guard OUTAGE was inert on exactly
                # the path it exists for.
                #
                # Synthesising a verdict (rather than adding an action parameter) keeps
                # ONE source of truth: _sanitize_output_core reads verdict.action in
                # several places, and the non-stream path already passes a verdict whose
                # .action IS the resolved action — so both transports now feed the
                # sanitizer identically, which is parity by construction.
                # Duck-typed verdicts reach this path too, so fall back rather than
                # letting dataclasses.replace raise on a non-dataclass.
                _sanitize_verdict = verdict
                if getattr(verdict, "action", None) != effective_action:
                    try:
                        import dataclasses as _dc  # noqa: PLC0415
                        _sanitize_verdict = _dc.replace(verdict, action=effective_action)
                    except Exception:  # noqa: BLE001 - non-dataclass / frozen-shim verdict
                        try:
                            import copy as _copy  # noqa: PLC0415
                            _sanitize_verdict = _copy.copy(verdict)
                            _sanitize_verdict.action = effective_action
                        except Exception:  # noqa: BLE001 - last resort: original verdict
                            _sanitize_verdict = verdict
                redacted_text = _sanitize_for_verdict(
                    full_text, _sanitize_verdict, redact_pii_fn=self._scanner.redact_pii
                )
                # Threat classes with no spans of their own (hallucination, and the
                # class-less policy/jailbreak/toxic family) are sanitized by a
                # WHOLE-RESPONSE replacement rather than an in-place mask. That
                # constant must reach the client exactly once, no matter how many
                # buffer segments the stream flushes.
                from output_guard import (  # noqa: PLC0415
                    _REDACTABLE_OUTPUT_CATEGORIES as _REDACTABLE_CATS,
                )
                _whole_response = (
                    verdict.threat_type == "hallucination"
                    or (
                        verdict.threat_type not in _REDACTABLE_CATS
                        and verdict.threat_type != "ip_leakage"
                    )
                )
                if _whole_response:
                    if self._response_replaced:
                        # Already delivered the replacement on an earlier flush —
                        # drop this segment instead of repeating it.
                        self._clear_buffers()
                        return
                    self._response_replaced = True
                # G46 semantic-span masking (redaction_spans + matched_values) is now
                # performed inside _sanitize_output_core, so it is no longer repeated
                # here — doing it twice could double-mask an already-masked span.
                # Telemetry honesty (mirror of non-stream main.py:1566 / 7289):
                # only claim action="redact" when the bytes actually changed. A
                # tier-2 (semantic) verdict can target content the deterministic
                # regex redactor has no pattern for (e.g. a free-text person
                # name), leaving the streamed output verbatim — that is a "flag",
                # not a redaction, so the §1.7 dashboard, the guard-metric
                # counter, and the StreamRunMetrics terminal trace frame must not
                # record a phantom redaction for a response delivered unchanged.
                _redact_noop = redacted_text == full_text
                # FAIL-CLOSED PARITY: the operator selected redact, but nothing could
                # be masked. Non-stream resolves this via
                # enforce_output(redaction_possible=False) -> BLOCK, because
                # delivering would ship the very bytes the operator asked to remove.
                # Streaming instead downgraded to "flag" and released the response
                # verbatim — e.g. a tier-2 semantic PII verdict over free text the
                # deterministic regexes cannot match ("patient Margarethe
                # Villanueva-Okonkwo") streamed the raw name. Only escalate for
                # maskable classes: a class-less/hallucination verdict is sanitized by
                # whole-response replacement, so a no-op there is not a redaction gap.
                if _redact_noop and not _whole_response:
                    _noop_decision = _enforce_output(
                        verdict_action="redact",
                        verdict_threat_type=getattr(verdict, "threat_type", "") or "",
                        redaction_possible=False,
                        enforcement_mode=self._enforcement_mode,
                        is_streaming=True,
                    )
                    if _noop_decision.action == "block":
                        LOG.warning(
                            "Output guard BLOCKED streaming content: redact selected but "
                            "nothing could be masked (type=%s, flush=%s)",
                            verdict.threat_type, reason.value,
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
                _emit_action = "flag" if _redact_noop else "redact"
                self._record_output(redacted_text)
                LOG.info(
                    "Output guard %s streaming content (type=%s, patterns=%s, flush=%s)",
                    "flagged (redact no-op)" if _redact_noop else "redacted",
                    verdict.threat_type,
                    verdict.matched_patterns,
                    reason.value,
                )
                self._record_guard_metrics(_emit_action, verdict)
                self._emit_guard_telemetry(
                    verdict, action=_emit_action, flush_reason=reason, redact_noop=_redact_noop
                )
                self._audit_output_guard(verdict, action=_emit_action)
                self._record_metric(_emit_action)
                # STREAM/NON-STREAM REDACT PARITY: on a NON-final real redaction,
                # retain the lookahead tail and redact+release only the prefix so a
                # SHORT token straddling this flush boundary is not split and
                # de-prefixed into a raw egress.
                # Mutually exclusive with E14 AT THE EDGE: if a >= _SECRET_ANCHOR_MIN
                # secret-charset run runs to the buffer edge, the split token is a
                # LONG key and E14 (arm anchor from full_text, emit whole, clear)
                # already carries it — keep that path untouched. Retention covers the
                # complementary case the finding exposed: the edge token is SHORT
                # (an IP head "10", a key prefix "s", an email fragment) so E14's
                # trailing-run anchor is empty and the head would otherwise stream.
                # Skip both for _whole_response classes (hallucination / class-less
                # policy) — they emit a single replacement constant, and for
                # _redact_noop (nothing masked => no split risk).
                _e14_run_to_edge = (
                    reason != FlushReason.DONE and bool(_trailing_secret_run(full_text))
                )
                if (
                    not _redact_noop
                    and not _whole_response
                    and reason != FlushReason.DONE
                    and not _e14_run_to_edge
                ):
                    def _guard_redact(_s, _v=_sanitize_verdict):
                        return _sanitize_for_verdict(
                            _s, _v, redact_pii_fn=self._scanner.redact_pii
                        )
                    for sse in self._release_redacted_with_lookahead_tail(
                        _guard_redact, arm_anchor=True
                    ):
                        yield sse
                    return
                # E14: a real redaction on a NON-final flush whose secret runs to the
                # buffer edge arms the secret-in-progress anchor so the next flush
                # masks the un-anchored continuation (long-key split).
                if not _redact_noop and reason != FlushReason.DONE:
                    self._secret_anchor = _trailing_secret_run(full_text)
                for redacted_chunk in self._yield_redacted(redacted_text):
                    yield redacted_chunk
                self._clear_buffers()
                return

            # V3-05 (HIGH): a WHOLE-RESPONSE replacement is a statement about the
            # WHOLE response, so it must suppress everything that follows — not just
            # later REDACT segments. ``_response_replaced`` was consulted only inside
            # the redact branch above, so a later CLEAN/flag flush fell through to the
            # release path below and appended the ORIGINAL text verbatim, immediately
            # after the sentence telling the caller the output had been rewritten.
            # Realistic trigger: a tier-2 breaker OPEN on the first flush and
            # recovered afterwards. Measured at 24 and 8 chars/delta, the stock SDK
            # reassembled the replacement sentence followed by the raw SSN and the raw
            # internal IP. That is strictly worse than a block: the caller is told the
            # response was sanitized while receiving the unsanitized bytes.
            if self._response_replaced:
                self._clear_buffers()
                return

            if effective_action == "flag":
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

        # G40 defense-in-depth: the no-OutputGuard fallback lacks the guard's
        # exfil-channel + encoded-PII neutralization (G13/G35), so a streamed
        # markdown-image/link beacon or encoded-PII run would ride out here
        # un-neutralized even when the G40 buffer retention held it whole. Apply
        # the same sanitizers before release. Strict no-op on benign text
        # (neutralize_* early-return without a URL / encoded run), so the clean
        # hot path and the "clean stream delivered intact" invariant are preserved.
        from output_guard import (  # noqa: PLC0415
            neutralize_encoded_pii,
            neutralize_exfil_channels,
            neutralize_markdown_split_pii,
        )
        neutralized = neutralize_encoded_pii(neutralize_exfil_channels(full_text))
        neutralized = neutralize_markdown_split_pii(neutralized)  # G45: streaming parity

        if verdict.threat_type in ("pii", "secret") and verdict.matched_patterns:
            redacted_text = self._scanner.redact_pii(neutralized)
            # Telemetry honesty (mirror of non-stream main.py:1566 / 7289): a
            # matched-pattern verdict whose redactor leaves the bytes verbatim is
            # a "flag", not a redaction — never claim "redact" on a verbatim
            # delivery so the §1.7 dashboard / StreamRunMetrics stay honest.
            _redact_noop = redacted_text == full_text
            _emit_action = "flag" if _redact_noop else "redact"
            self._record_output(redacted_text)
            LOG.info(
                "PII %s in streaming output (type=%s, patterns=%s, flush=%s)",
                "flagged (redact no-op)" if _redact_noop else "redacted",
                verdict.threat_type,
                verdict.matched_patterns,
                reason.value,
            )
            self._record_guard_metrics(_emit_action, verdict)
            self._record_metric(_emit_action)
            # STREAM/NON-STREAM REDACT PARITY (same fix as the guard path above): on
            # a non-final real redaction, retain the lookahead tail and redact+release
            # only the prefix so a SHORT token straddling the flush boundary is
            # re-scanned whole next flush instead of splitting and egressing raw. E14
            # (long secret-charset run to the edge) keeps its own path below.
            _e14_run_to_edge = (
                reason != FlushReason.DONE and bool(_trailing_secret_run(full_text))
            )
            if not _redact_noop and reason != FlushReason.DONE and not _e14_run_to_edge:
                def _fallback_redact(_s):
                    from output_guard import (  # noqa: PLC0415
                        neutralize_encoded_pii,
                        neutralize_exfil_channels,
                        neutralize_markdown_split_pii,
                    )
                    _n = neutralize_encoded_pii(neutralize_exfil_channels(_s))
                    _n = neutralize_markdown_split_pii(_n)
                    return self._scanner.redact_pii(_n)
                for sse in self._release_redacted_with_lookahead_tail(
                    _fallback_redact, arm_anchor=True
                ):
                    yield sse
                return
            # E14: arm the secret-in-progress anchor on a non-final real redaction
            # whose secret runs to the buffer edge (long-key split), same as the
            # guard path above.
            if not _redact_noop and reason != FlushReason.DONE:
                self._secret_anchor = _trailing_secret_run(full_text)
            for redacted_chunk in self._yield_redacted(redacted_text):
                yield redacted_chunk
            self._clear_buffers()
        elif neutralized != full_text:
            # G40: an exfil beacon / encoded-PII run was defanged though the
            # scanner returned no PII/secret verdict (arbitrary-data beacon). Emit
            # the neutralized text (single rebuilt chunk) so no auto-render / raw
            # payload reaches the client on the fallback path.
            self._record_output(neutralized)
            self._record_metric("redact")
            for chunk in self._yield_redacted(neutralized):
                yield chunk
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

    def _record_raw_output(self, text: str) -> None:
        """Accumulate the PRE-redaction (raw model) streamed text onto the shared
        StreamRunMetrics for the Scan Detail "Raw model output" panel. Distinct from
        ``_record_output`` (which records only the post-redaction, client-facing text).
        Safe to record content about to be blocked/redacted: build_telemetry_event scrubs
        ``raw_output`` through redact_all before it reaches the audit log."""
        if self._stream_metrics is None or not text:
            return
        try:
            self._stream_metrics.append_raw_output(text)
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

    def _emit_guard_telemetry(
        self,
        verdict,
        *,
        action: str,
        flush_reason: FlushReason,
        redact_noop: bool = False,
    ) -> None:
        if self._telemetry is None:
            return
        try:
            from telemetry import build_telemetry_event

            metadata = {
                "detail": (verdict.detail or "")[:256],
                "streaming": True,
                "request_id": self._request_id,
                "flush_reason": flush_reason.value,
                "module": "1.7",
                "module_id": "1.7",
            }
            # Mirror non-stream main.py:1574 — record when a redact verdict left
            # the bytes verbatim (downgraded to "flag") so the dashboard can
            # distinguish a real masking from a no-op tier-2 flag.
            if redact_noop:
                metadata["redact_noop"] = True

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
                metadata=metadata,
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

    async def _consume_secret_continuation(
        self, full_text: str, reason: FlushReason
    ) -> AsyncGenerator[str, None]:
        """Mask the leading secret-charset continuation of a secret that the
        PREVIOUS flush began redacting (``self._secret_anchor`` is set).

        The deterministic secret regexes are prefix-anchored, so once a redact
        flush ships a long key's ``sk-`` prefix and clears the buffer, the trailing
        key body matches no pattern and would egress verbatim. Here we mask that
        un-anchored leading run anchor-INDEPENDENTLY (fix_hint option 2). If the
        run reaches the buffer edge the secret is still in progress, so the anchor
        is re-armed and the remainder (if any, after the run) is re-scanned through
        the normal flush path for fresh secrets in the clean tail."""
        # Length of the leading contiguous secret-charset run = the continuation.
        i = 0
        n = len(full_text)
        while i < n and full_text[i] in _SECRET_CHARSET:
            i += 1
        continuation = full_text[:i]
        remainder = full_text[i:]

        run_to_edge = i == n  # the secret-charset run still runs to the buffer end

        if continuation:
            # Emit a masked frame for the consumed continuation. Nothing raw of the
            # continuation reaches the client. The remainder (clean, non-secret-
            # charset prefix char onward) is re-scanned below, so its own secrets
            # are still caught.
            self._record_output("[REDACTED]")
            self._record_guard_metrics("redact", _ContinuationVerdict())
            self._record_metric("redact")
            # Use the first queued frame as the carrier for the redaction marker.
            first_sse, _ = self._chunk_queue[0]
            yield self._rebuild_sse_content(first_sse, "[REDACTED]")

        if run_to_edge:
            # Still mid-secret: keep the anchor armed and drop the buffer (the
            # continuation was masked, nothing to release).
            self._secret_anchor = _trailing_secret_run(continuation) or self._secret_anchor
            self._clear_buffers()
            return

        # The secret ended inside this buffer. Anchor consumed. Re-scan ONLY the
        # clean remainder through the normal flush machinery so any new secret in
        # the tail is still caught (and nothing already-emitted is re-released).
        self._secret_anchor = ""
        self._content_buffer = [remainder]
        self._content_buffer_len = len(remainder.encode("utf-8"))
        # Rebuild a single synthetic chunk carrying the remainder so the release
        # path has a frame to rebuild; reuse the last original frame as carrier.
        last_sse, _ = self._chunk_queue[-1]
        self._chunk_queue = [(last_sse, remainder)]
        if remainder:
            async for sse in self._flush_buffer(reason):
                yield sse
        else:
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
        and releases nothing this flush (it is released at the DONE flush).

        G40: the same "never release a partial that could complete into something
        dangerous" rule applies to an UNCLOSED markdown media/link opener at the
        buffer tail. A base64/hex exfil payload has no SENTENCE_BOUNDARIES char, so
        a >buffer_max_bytes beacon forces a mid-URL BUFFER_LIMIT flush; the unclosed
        ``![alt](url…`` matches no exfil pattern (clean verdict), so absent this
        guard its prefix would be released and the client would reassemble the full
        auto-render beacon. We extend the retained window to cover the whole open
        opener so the completed beacon is scanned + defanged whole. If it grows past
        MAX_OPEN_MEDIA_HOLDBACK (a never-closing opener), we fail closed: defang the
        opener in place and flush the neutralized buffer."""
        min_retain = STREAM_LOOKAHEAD_BYTES
        full_text = "".join(c for _, c in self._chunk_queue)
        open_start = _open_media_opener_start(full_text)
        if open_start is not None:
            open_tail_bytes = len(full_text[open_start:].encode("utf-8"))
            if open_tail_bytes > MAX_OPEN_MEDIA_HOLDBACK:
                # Pathological never-closing opener: neutralize + flush, then clear.
                neutralized = _defang_open_media(full_text)
                for chunk in self._yield_redacted(neutralized):
                    yield chunk
                self._clear_buffers()
                return
            # Hold the whole open opener so the completed beacon is scanned whole.
            min_retain = max(min_retain, open_tail_bytes)
        acc = 0
        keep_from = 0
        for i in range(len(self._chunk_queue) - 1, -1, -1):
            acc += len(self._chunk_queue[i][1].encode("utf-8"))
            keep_from = i
            if acc >= min_retain:
                break
        release = self._chunk_queue[:keep_from]
        retain = self._chunk_queue[keep_from:]
        self._chunk_queue = retain
        self._content_buffer = [c for _, c in retain]
        self._content_buffer_len = sum(len(c.encode("utf-8")) for _, c in retain)
        for original_sse, _ in release:
            yield original_sse

    def _release_redacted_with_lookahead_tail(self, redact_fn, *, arm_anchor: bool):
        """Redact-branch counterpart of ``_release_with_lookahead_tail``.

        STREAM/NON-STREAM REDACT PARITY (2026-07-22): the redact branches used to
        release the ENTIRE redacted buffer and clear on every flush, relying only on
        the E14 ``_secret_anchor`` (a trailing SECRET-charset run >= 32 chars) to
        carry a split token across the boundary. That left a hole the non-stream
        path does not have: a NON-final flush fires as soon as ANY token in the
        buffer is maskable (e.g. an email), and then releases up to the buffer edge —
        splitting a DIFFERENT token that straddles that edge. Its head streams (raw
        or masked), its tail lands in the next buffer DE-PREFIXED, matches no
        pattern (the deterministic regexes are prefix-anchored: ``sk-…``, ``10.…``)
        and egresses verbatim. Measured: an internal IP ``10.0.0.5`` and a
        ``sk-proj-…`` key streamed raw across SSE frames while non-stream on the same
        bytes masked both. E14 missed them because ``10`` / ``s`` are not >= 32-char
        secret runs.

        Fix: on a non-final redact flush, release only a WHITESPACE-DELIMITED
        prefix (redacted whole) and retain a >= STREAM_LOOKAHEAD_BYTES raw tail for a
        whole re-scan next flush.

        WHY WHITESPACE, NOT CHUNK BOUNDARIES (2026-07-23): the first version of this
        helper released ``_chunk_queue[:keep_from]`` and redacted that raw fragment in
        isolation. Under token-by-token streaming (1-8 chars/delta) the default
        ``max_buffer_chunks`` forces repeated flushes, ``keep_from`` advances one tiny
        chunk at a time, and ``redact_fn("j")`` / ``redact_fn("o")`` on a single
        character matches no pattern — so an email/SSN/key was released one RAW char at
        a time and egressed verbatim while non-stream masked it. A sensitive token
        (email, key, IP, SSN) contains NO whitespace, so cutting the release at a
        whitespace boundary guarantees no token straddles the cut: every token in the
        released prefix is COMPLETE and is masked by the re-scanning redactor, and the
        retained tail always begins after a whitespace. A run with no whitespace in the
        releasable region (a single long token) retains everything until a whitespace
        arrives or DONE — the E14 long-secret path is handled by the caller before this
        helper, and a pathological > MAX_OPEN_MEDIA_HOLDBACK no-whitespace run fails
        closed by redacting and releasing the whole buffer (never raw).
        """
        full_text = "".join(c for _, c in self._chunk_queue)
        if len(full_text.encode("utf-8")) <= STREAM_LOOKAHEAD_BYTES:
            # Whole buffer within the lookahead window: retain everything, release
            # nothing now. Delivered whole at a later flush / DONE. No split possible.
            return
        # Walk left from the end until >= STREAM_LOOKAHEAD_BYTES of raw tail is held.
        tail_start = len(full_text)
        acc = 0
        while tail_start > 0 and acc < STREAM_LOOKAHEAD_BYTES:
            tail_start -= 1
            acc += len(full_text[tail_start].encode("utf-8"))
        # Snap the cut LEFT to the last whitespace at or before tail_start so no
        # sensitive token (which never contains whitespace) can straddle it.
        cut = tail_start
        while cut > 0 and not full_text[cut - 1].isspace():
            cut -= 1
        if cut == 0:
            # No safe whitespace boundary in the releasable region.
            if len(full_text.encode("utf-8")) > MAX_OPEN_MEDIA_HOLDBACK:
                # Pathological single long run: fail closed — redact the WHOLE buffer
                # and release it (masked), never hold unboundedly nor ship raw.
                first_sse, _ = self._chunk_queue[0]
                yield self._rebuild_sse_content(first_sse, redact_fn(full_text))
                for original_sse, _ in self._chunk_queue[1:]:
                    yield self._rebuild_sse_content(original_sse, "")
                self._clear_buffers()
                return
            # Otherwise retain everything and wait for a whitespace boundary / DONE.
            return
        released = full_text[:cut]
        retained = full_text[cut:]
        redacted_release = redact_fn(released)
        # Arm the E14 anchor from the released prefix so a > lookahead secret whose
        # head just streamed has its retained continuation masked next flush.
        if arm_anchor:
            self._secret_anchor = _trailing_secret_run(released) or self._secret_anchor
        # Rebuild the buffer as a single synthetic chunk carrying the retained raw tail
        # (re-scanned whole next flush), reusing the last original frame as carrier.
        first_sse, _ = self._chunk_queue[0]
        carrier_sse, _ = self._chunk_queue[-1]
        self._chunk_queue = [(carrier_sse, retained)]
        self._content_buffer = [retained]
        self._content_buffer_len = len(retained.encode("utf-8"))
        # Emit the redacted released prefix as a single rebuilt frame.
        yield self._rebuild_sse_content(first_sse, redacted_release)

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
            # G62: a bare DICT content (non-conforming) — fold its str `text` value too
            # (stream parity with the non-stream _content_to_text) so it isn't skipped.
            content = delta.get("content")
            if isinstance(content, list):
                content = "".join(
                    p.get("text") or "" for p in content if isinstance(p, dict)
                )
            elif isinstance(content, dict):
                _ct = content.get("text")
                content = _ct if isinstance(_ct, str) else ""
            elif not isinstance(content, str):
                content = ""
            parts.append(content)

            # FIX-A: reasoning_content streams raw to the client too — scan it.
            # G61: coerce a non-str (structured list/dict) reasoning channel to JSON so
            # PII in a structured reasoning block is scanned (stream parity with the
            # non-stream _tool_arg_to_text coercion).
            reasoning = delta.get("reasoning_content")
            if isinstance(reasoning, str):
                parts.append(reasoning)
            elif reasoning is not None:
                try:
                    parts.append(json.dumps(reasoning))
                except (TypeError, ValueError):
                    pass

            # FIX-B: tool-call text streams raw — scan it. A-01 (stream parity): the
            # OpenAI SDK has TWO tool-call shapes — {"type":"function","function":
            # {name,arguments}} AND {"type":"custom","custom":{name,input}}. Reading
            # only ``function`` (and worse, ``continue``-ing when it is absent) let a
            # secret in ``custom.input`` stream UNSCANNED — the exact leak the
            # non-stream path fixed via _tool_call_text_channels. Fold BOTH shapes'
            # text; never early-skip a custom-only call.
            tool_calls = delta.get("tool_calls")
            if isinstance(tool_calls, list):
                for call in tool_calls:
                    if not isinstance(call, dict):
                        continue
                    for _cont, _keys in ((call.get("function"), ("name", "arguments")),
                                         (call.get("custom"), ("name", "input"))):
                        if not isinstance(_cont, dict):
                            continue
                        for _k in _keys:
                            _v = _cont.get(_k)
                            if isinstance(_v, str):
                                parts.append(_v)
                            # G58: coerce a non-str (dict) value to JSON text (a
                            # non-conforming provider may stream parsed args/input) —
                            # parity with the non-stream _tool_arg_to_text coercion.
                            elif _v is not None:
                                try:
                                    parts.append(json.dumps(_v))
                                except (TypeError, ValueError):
                                    pass

            # R12 (#13): legacy `function_call` delta channel (pre-tool_calls API
            # shape) streams raw too — scan name + arguments (non-stream I5 parity).
            fc = delta.get("function_call")
            if isinstance(fc, dict):
                for _k in ("name", "arguments"):
                    _v = fc.get(_k)
                    if isinstance(_v, str):
                        parts.append(_v)
                    elif _v is not None:
                        try:
                            parts.append(json.dumps(_v))
                        except (TypeError, ValueError):
                            pass

            # R12 (#15): refusal channel streams raw — scan it.
            # G61: coerce a non-str (structured) refusal to JSON too.
            refusal = delta.get("refusal")
            if isinstance(refusal, str):
                parts.append(refusal)
            elif refusal is not None:
                try:
                    parts.append(json.dumps(refusal))
                except (TypeError, ValueError):
                    pass

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
        # G61: blank a TRUTHY reasoning of ANY type (structured list/dict too).
        if delta.get("reasoning_content"):
            delta["reasoning_content"] = ""
        for call in (delta.get("tool_calls") or []):
            if not isinstance(call, dict):
                continue
            # A-01 (stream parity): blank BOTH tool-call shapes — function{name,
            # arguments} AND custom{name,input} — or a redact/block rebuild leaves a
            # secret in custom.input streaming raw.
            for _cont, _keys in ((call.get("function"), ("name", "arguments")),
                                 (call.get("custom"), ("name", "input"))):
                if not isinstance(_cont, dict):
                    continue
                for _k in _keys:
                    # G58: blank a TRUTHY value of ANY type — a dict-shaped value
                    # (parsed JSON from a non-conforming provider) was left verbatim
                    # by a str-only check, streaming its secret after a redact rebuild.
                    if _cont.get(_k):
                        _cont[_k] = ""
        fc = delta.get("function_call")
        if isinstance(fc, dict):
            for _k in ("name", "arguments"):
                if fc.get(_k):
                    fc[_k] = ""
        if delta.get("refusal"):  # G61: blank any-type refusal
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
    def _build_error_sse(message: str, error_type: str = "output_blocked",
                         code: str = "output_blocked") -> str:
        """Build an SSE event indicating the stream was blocked or failed."""
        error_data = {
            "error": {
                "message": message,
                "type": error_type,
                "code": code,
            }
        }
        return f"data: {json.dumps(error_data)}\n\n"
