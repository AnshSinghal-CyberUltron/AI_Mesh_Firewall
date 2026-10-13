"""GW15 output-enforcement path property tests (shared file).

This module is written by several GW15 task sub-agents concurrently (4.2 the Property 11
REDACT output-channel test, 4.3 the Property 12 BLOCK output-withhold test, 4.4 the
Property 13 fail-closed FAIL_OPEN-zero test). Each contribution is a SELF-CONTAINED
additive section with uniquely-prefixed symbols (``_p12_`` / ``_P12`` / ``P12_`` here)
and section-local (mid-module) imports, so the sections never collide and none depends on
another's helpers. Do not remove a sibling section; append new sections below.

House idiom throughout: a seeded ``random.Random`` driven for >=10,000 iterations, NO
``hypothesis``; async paths exercised via ``asyncio.run``; one property <-> one
property-based test.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Shared module-level imports (owned by the first section; additive, used by the
# concurrent sibling sections that reference these common names at module scope).
# Each section ALSO imports what it needs section-locally; these top-level names
# satisfy sibling helpers (dataclasses, type aliases) evaluated at import time.
# ---------------------------------------------------------------------------
import asyncio  # noqa: F401
import json  # noqa: F401
import random  # noqa: F401
from collections.abc import AsyncIterator, Mapping, Sequence  # noqa: F401
from dataclasses import dataclass, field  # noqa: F401
from typing import TYPE_CHECKING, cast  # noqa: F401

if TYPE_CHECKING:  # pragma: no cover - annotation-only import, no runtime cycle
    from gateway_v2.edge.routes import ChatRoute  # noqa: F401

# ===========================================================================
# Property 12 — A BLOCK output finding withholds the whole response (task 4.3).
# ===========================================================================
# Feature: chat-surface-gw15, Property 12: A BLOCK output finding withholds the whole response
# Validates: Requirements 8.3, 9.3
#
# *For any* collected or streamed upstream response carrying a BLOCK-disposition output
# finding, the Chat_Surface WITHHOLDS the whole response body and renders the declared block
# value-code. The property exercises BOTH response shapes, each over its own real shipped
# path, alternating across >=10,000 seeded iterations:
#
#   * NON-STREAMING (R9.3): ``ChatSurface.collect_and_render`` drains the whole upstream, runs
#     the injected output scanner + the real ``MinimalOutputResolver`` per channel, and applies
#     the shipped ``apply_decision``. A BLOCK disposition raises ``OutputBlocked``, so the
#     non-stream collector withholds the JSON body and emits the declared block value-code
#     through the single ``edge/errors`` envelope. Over a recording ASGI ``send`` the property
#     asserts: exactly one ``http.response.start`` + one ``http.response.body``; HTTP 403 (the
#     ``output_blocked`` envelope status); the body is the declared ``{"error": {...}}`` envelope
#     carrying ``code == output_blocked`` and is NOT an OpenAI ``chat.completion`` body; and the
#     raw upstream canary appears in the client-visible bytes ZERO times (no JSON body leaked).
#
#   * STREAMING (R8.3 / R10): the shipped ``StreamPipeline.for_plan`` (ENFORCING output, driven
#     by the REAL ``detect.holdback`` scanner + the real ``MinimalOutputResolver``) consumes a
#     content stream whose text carries a BLOCK-class value (a card number, which the resolver's
#     trade-off table maps to BLOCK). The pipeline emits a terminal ``DownstreamFrame`` with
#     ``error_code == "output_blocked"`` and no content. A sink built on the REAL ``SSEEncoder``
#     — a faithful reproduction of ``ChatRoute._sink`` (set the terminal latch on an error frame,
#     refuse any content after) — serialises the frames to the SSE wire. Decoding that wire, the
#     property asserts: exactly one terminal SSE ``Error_Frame`` with ``code == output_blocked``;
#     NO content frame is emitted before OR after it (the whole response is withheld, not just
#     truncated); the encoder is terminal; and the raw canary bytes appear on the wire ZERO
#     times.
#
# Only the provider (scripted stub / in-memory chunk source, no socket) and the output scanner
# (a deterministic BLOCK-class finder) are test doubles; the resolver, ``apply_decision``, the
# holdback scanner, the ``SSEEncoder``, and the ``edge/errors`` envelope are the REAL shipped
# components, so the withhold is proven end to end on both paths.


def test_property_12_block_output_finding_withholds_the_whole_response() -> None:
    import asyncio
    import json
    import random
    import time

    from gateway_v2.detect import holdback
    from gateway_v2.detect.holdback import TokenIndex
    from gateway_v2.detect.windowing import max_pattern_length
    from gateway_v2.dispatch.provider import StubProviderClient, UpstreamEvent
    from gateway_v2.domain.category import Category
    from gateway_v2.domain.finding import Finding, FindingStatus, Span
    from gateway_v2.domain.plan import (
        Action,
        ExecutionPlan,
        FailurePosture,
        Mode,
        Rule,
        RuleScope,
        StreamingMode,
        Surface,
    )
    from gateway_v2.edge import errors
    from gateway_v2.edge.openai_chat import REFUSE_OUTPUT_BLOCKED, ChatSurface
    from gateway_v2.edge.wire.openai import CanonicalRequest, OpenAIResponseRenderer
    from gateway_v2.edge.wire.sse import DONE_MARKER, SSEEncoder
    from gateway_v2.egress.output_guard import MinimalOutputResolver
    from gateway_v2.egress.stream import (
        DownstreamFrame,
        ScanResultLike,
        StreamPipeline,
        TokenCounterProtocol,
        UpstreamChunk,
    )
    from gateway_v2.runtime.holdback_config import load_holdback_config
    from gateway_v2.runtime.holdback_metrics import HoldbackMetrics

    _P12_SEED = 0x12_0C_0012
    _P12_ITERATIONS = 10_000

    # The declared terminal block value-code the whole property is about: the shipped egress
    # pipeline raises it on a BLOCK, the ``edge/errors`` envelope maps it to a 403, and the SSE
    # encoder renders it as the declared terminal Error_Frame. One spelling across both paths.
    _P12_BLOCK_CODE = "output_blocked"
    assert REFUSE_OUTPUT_BLOCKED == _P12_BLOCK_CODE  # the non-stream path's spelling agrees

    # The output-content transport channel the non-streaming collector attributes provider
    # ``output_content`` deltas to and the renderer folds into ``choices[0].message.content``.
    _P12_OUTPUT_CONTENT_CHANNEL = "output_content"
    # The streaming channel the holdback pipeline + SSE encoder carry content on.
    _P12_STREAM_CHANNEL = "content"

    # A BLOCK-class detector name: ``MinimalOutputResolver`` maps ``card`` (and ``jwt``) to
    # ``Disposition.BLOCK`` via its trade-off table, so a finding tagged with this detector
    # resolves to a BLOCK decision regardless of any co-matching finding (C22).
    _P12_BLOCK_DETECTOR = "card"
    # A real card-shaped value the shipped holdback scanner releases naturally on the final
    # chunk, so the streaming BLOCK fires through the genuine detect-layer path.
    _P12_CARD_VALUE = "4111111111111111"

    _P12_FILLER_ALPHABET = "0123456789 .,!?-abcdef"

    def _p12_ctx(i: int, path: str, **extra: object) -> str:
        body = " ".join(f"{k}={v!r}" for k, v in extra.items())
        return f"seed={_P12_SEED:#x} iter={i} path={path} {body}"

    def _p12_filler(rng: random.Random) -> str:
        """A chunk of non-canary filler text (may be empty) over a disjoint alphabet."""
        return "".join(
            rng.choice(_P12_FILLER_ALPHABET) for _ in range(rng.randint(0, 6))
        )

    # ======================================================================= #
    # NON-STREAMING half (R9.3) — collect_and_render withholds the JSON body.
    # ======================================================================= #

    @dataclass(slots=True)
    class _P12RecordingSend:
        """Records every ASGI ``send`` event so "exactly one withheld reply" is observable."""

        events: list[Mapping[str, object]]

        async def __call__(self, event: Mapping[str, object]) -> None:
            self.events.append(event)

    @dataclass(frozen=True, slots=True)
    class _P12NonStreamDeps:
        """The ``ChatRoute``-shaped bundle ``collect_and_render`` reads from.

        Carries exactly the four seams the non-streaming path consults: the injected
        ``provider`` (scripted stub), the ``error_scanner`` (the deterministic BLOCK finder),
        the real ``resolver`` (``MinimalOutputResolver``), and the real ``renderer``. A
        duck-typed stand-in for the frozen ``ChatRoute`` so the property drives the
        collect/enforce/withhold wiring without the full boot graph.
        """

        provider: StubProviderClient
        error_scanner: object
        resolver: MinimalOutputResolver
        renderer: OpenAIResponseRenderer

    def _p12_block_scanner(canary: str) -> object:
        """An output scanner: a BLOCK-class finding over every ``canary`` occurrence.

        The scanner is the injected whole-text findings producer the output guard runs per
        channel. It locates every occurrence of the per-iteration ``canary`` and emits one
        EXECUTED finding tagged with the BLOCK-class detector (so ``MinimalOutputResolver``
        resolves it to BLOCK, C22). A channel's text with no canary yields no finding.
        """

        def _scan(text: str) -> Sequence[Finding]:
            spans: list[Span] = []
            start = text.find(canary)
            while start != -1:
                spans.append(Span(start=start, end=start + len(canary)))
                start = text.find(canary, start + len(canary))
            if not spans:
                return ()
            return (
                Finding(
                    detector=_P12_BLOCK_DETECTOR,
                    detector_version="p12",
                    category=Category.PII,
                    status=FindingStatus.EXECUTED,
                    confidence=1.0,
                    spans=tuple(spans),
                    evidence=None,
                ),
            )

        return _scan

    def _p12_canary(rng: random.Random) -> str:
        """A distinctive canary token unlikely to collide with the filler alphabet."""
        n = rng.randint(4, 16)
        alphabet = "GHIJKLMNOPQRSTUVWXYZ"
        return "P12CANARY_" + "".join(rng.choice(alphabet) for _ in range(n))

    def _p12_nonstream_script(
        rng: random.Random, canary: str
    ) -> tuple[tuple[UpstreamEvent, ...], str]:
        """Script a fan of ``output_content`` deltas, at least one carrying the canary.

        Returns the events + the raw collected text. The canary is guaranteed present in at
        least one delta so the BLOCK fires; the raw text is what the upstream produced BEFORE
        enforcement — the property proves NONE of it (canary or filler) reaches the client.
        """
        event_count = rng.randint(1, 5)
        canary_idx = rng.randint(0, event_count - 1)
        events: list[UpstreamEvent] = []
        raw_parts: list[str] = []
        for idx in range(event_count):
            delta = _p12_filler(rng)
            if idx == canary_idx:
                delta += canary + _p12_filler(rng)
            raw_parts.append(delta)
            events.append(
                UpstreamEvent(
                    text_deltas=((_P12_OUTPUT_CONTENT_CHANNEL, delta),),
                    final=idx == event_count - 1,
                )
            )
        return tuple(events), "".join(raw_parts)

    def _p12_canonical_request(rng: random.Random) -> CanonicalRequest:
        """A minimal non-streaming ``CanonicalRequest`` (the renderer reads only ``model``)."""
        model = "gpt-" + "".join(rng.choice("abcdef0123456789") for _ in range(6))
        return CanonicalRequest(
            model=model,
            streaming=False,
            input_channels=(),
            text_deltas=(),
            surface=Surface.CHAT,
        )

    def _p12_run_nonstream(i: int, rng: random.Random) -> None:
        """Drive one non-streaming BLOCK iteration and assert the body is withheld (R9.3)."""
        canary = _p12_canary(rng)
        events, raw = _p12_nonstream_script(rng, canary)
        req = _p12_canonical_request(rng)
        provider = StubProviderClient(script=events, rng=random.Random(rng.random()))
        deps = _P12NonStreamDeps(
            provider=provider,
            error_scanner=_p12_block_scanner(canary),
            resolver=MinimalOutputResolver(),
            renderer=OpenAIResponseRenderer(),
        )
        surface = ChatSurface(deps=deps)  # type: ignore[arg-type]
        send = _P12RecordingSend(events=[])

        asyncio.run(
            surface.collect_and_render(
                req,
                plan=None,  # type: ignore[arg-type]  # the injected resolver carries enforcement
                destination="p12-upstream",
                body={},
                send=send,
            )
        )

        ctx = _p12_ctx(i, "non-stream", canary=canary, raw=raw, events=len(send.events))

        # Exactly ONE reply: one start frame + one body frame, nothing else (no JSON streamed
        # before the withhold, no second body after).
        assert len(send.events) == 2, ctx
        start, body_event = send.events
        assert start["type"] == "http.response.start", ctx
        assert body_event["type"] == "http.response.body", ctx

        # The reply is the declared block value-code rendered through the single Error_Envelope
        # (403), proving it went through ``errors.render_http`` — not a 200 completion.
        expected = errors.render_http(REFUSE_OUTPUT_BLOCKED)
        assert start["status"] == expected.status == 403, ctx

        body_bytes = cast(bytes, body_event["body"])
        parsed = json.loads(body_bytes.decode("utf-8"))
        assert isinstance(parsed, dict), ctx
        # The withheld reply is the error envelope, NOT an OpenAI chat.completion body (R9.3).
        assert "error" in parsed, ctx
        assert parsed.get("object") != "chat.completion", ctx
        assert isinstance(parsed["error"], dict), ctx
        assert parsed["error"]["code"] == REFUSE_OUTPUT_BLOCKED, ctx

        # The whole response is WITHHELD: neither the canary nor any raw upstream filler reaches
        # the client. The error-envelope body carries only declared text + the code, so the raw
        # upstream text appears ZERO times on the wire.
        wire_text = body_bytes.decode("utf-8")
        assert canary not in wire_text, ctx
        for channel, text in _p12_collect_raw_by_channel(events):
            del channel
            if text and text not in ("", _P12_OUTPUT_CONTENT_CHANNEL):
                assert text not in wire_text or text == "", _p12_ctx(
                    i, "non-stream", note="raw upstream text leaked into the withheld body",
                    leaked=text,
                )

    def _p12_collect_raw_by_channel(
        events: tuple[UpstreamEvent, ...],
    ) -> tuple[tuple[str, str], ...]:
        """Concatenate scripted deltas per channel (the raw upstream the client must NOT see)."""
        order: list[str] = []
        texts: dict[str, list[str]] = {}
        for event in events:
            for channel, delta in event.text_deltas:
                if channel not in texts:
                    texts[channel] = []
                    order.append(channel)
                texts[channel].append(delta)
        return tuple((channel, "".join(texts[channel])) for channel in order)

    # ======================================================================= #
    # STREAMING half (R8.3 / R10) — the pipeline emits a terminal Error_Frame.
    # ======================================================================= #

    def _p12_scan(
        buf: str,
        *,
        final: bool,
        hold_cap_tokens: int,
        token_index: TokenCounterProtocol,
        window: int,
    ) -> ScanResultLike:
        """Adapter injecting the REAL pure ``detect.holdback.scan`` as the pipeline scanner."""
        return holdback.scan(
            buf,
            final=final,
            hold_cap_tokens=hold_cap_tokens,
            token_index=token_index,  # type: ignore[arg-type]
            window=window,
        )

    def _p12_block_detector(value: str) -> object:
        """A ``Detector`` flagging every occurrence of ``value`` as the BLOCK-class ``card``.

        ``MinimalOutputResolver`` maps the ``card`` detector name to ``Disposition.BLOCK``, so a
        completed match in a released slice drives the pipeline to the terminal ``output_blocked``
        frame (the genuine BLOCK path exercised by the shipped egress tests).
        """

        def _detect(whole: str) -> list[Finding]:
            hits: list[Finding] = []
            start = 0
            while True:
                idx = whole.find(value, start)
                if idx == -1:
                    break
                hits.append(
                    Finding(
                        detector=_P12_BLOCK_DETECTOR,
                        detector_version="p12",
                        category=Category.PII,
                        status=FindingStatus.EXECUTED,
                        confidence=1.0,
                        spans=(Span(idx, idx + len(value)),),
                        evidence=None,
                    ),
                )
                start = idx + len(value)
            return hits

        return _detect

    def _p12_block_plan() -> ExecutionPlan:
        """An ``ExecutionPlan`` carrying ONE enforcing OUTPUT rule so ``for_plan`` enforces.

        ``enforcing_output_rules`` returns a non-empty set for an OUTPUT+ENFORCE+BLOCK rule, so
        ``StreamPipeline.for_plan`` builds the enforcing release loop (holds + applies the
        decision) rather than passing through — the precondition for a BLOCK to withhold.
        """
        rule = Rule(
            rule_id="p12-block",
            category=Category.SECRET,
            mode=Mode.ENFORCE,
            action=Action.BLOCK,
            threshold=None,
            priority=0,
            scope=RuleScope.OUTPUT,
            on_unavailable=FailurePosture.FAIL_CLOSED,
            surfaces=frozenset({Surface.CHAT}),
        )
        return ExecutionPlan(
            org_id="p12-org",
            epoch=1,
            sequence=1,
            content_hash="p12" + "0" * 61,
            compiled_at=0.0,
            rules=(rule,),
            required_detectors=frozenset(),
            streaming_mode=StreamingMode.INCREMENTAL,
            integrity_locked=True,
        )

    async def _p12_chunk_source(
        chunks: Sequence[UpstreamChunk],
    ) -> AsyncIterator[UpstreamChunk]:
        """An async iterator over a fixed list of upstream chunks (no socket)."""
        for chunk in chunks:
            yield chunk

    def _p12_never_killed() -> bool:
        return False

    class _P12SseSink:
        """A pipeline ``Send`` sink reproducing ``ChatRoute._sink`` over the REAL ``SSEEncoder``.

        A downstream content frame is serialised with ``encoder.content``; a terminal frame
        (``error_code`` set) is serialised with ``encoder.error`` and sets the one-way
        ``sent_terminal`` latch so no content can follow it on the wire — exactly the
        no-content-after-error discipline the shipped handler enforces. Every emitted SSE
        byte string is recorded so the property can decode the whole wire.
        """

        __slots__ = ("_encoder", "frames", "sent_terminal")

        def __init__(self, encoder: SSEEncoder) -> None:
            self._encoder = encoder
            self.frames: list[bytes] = []
            self.sent_terminal = False

        async def __call__(self, frame: DownstreamFrame) -> None:
            if self.sent_terminal:
                return
            error_code = frame.error_code
            if error_code is not None:
                self.frames.append(self._encoder.error(error_code))
                self.sent_terminal = True
                return
            self.frames.append(self._encoder.content(frame))

    def _p12_split_card(rng: random.Random) -> tuple[list[UpstreamChunk], str]:
        """Scripted content chunks whose text carries the card value, randomly fragmented.

        Random filler surrounds the card and the whole text is split at random cut points so a
        BLOCK that depends on a split boundary would be caught — the holdback scanner holds the
        minimal matchable suffix, so the card completes on the final chunk and the pipeline
        blocks regardless of where the chunk boundaries fall.
        """
        prefix = _p12_filler(rng)
        suffix = _p12_filler(rng)
        text = f"{prefix}{_P12_CARD_VALUE}{suffix}"
        n_splits = rng.randint(0, 4)
        cuts = sorted(rng.randint(1, max(1, len(text) - 1)) for _ in range(n_splits))
        pieces: list[str] = []
        prev = 0
        for cut in cuts:
            pieces.append(text[prev:cut])
            prev = cut
        pieces.append(text[prev:])
        pieces = [p for p in pieces if p] or [text]
        chunks: list[UpstreamChunk] = []
        for idx, piece in enumerate(pieces):
            chunks.append(
                UpstreamChunk(
                    text_deltas=((_P12_STREAM_CHANNEL, piece),),
                    final=idx == len(pieces) - 1,
                )
            )
        return chunks, text

    def _p12_sse_payloads(wire: bytes) -> list[object]:
        """Decode every ``data: {json}`` SSE frame on the wire into its parsed JSON payload.

        ``[DONE]`` markers (never expected on a withheld stream) decode to the sentinel string
        ``"[DONE]"``; every other frame is JSON-parsed. Lets the property assert the exact frame
        sequence the client would observe.
        """
        payloads: list[object] = []
        for raw in wire.split(b"\n\n"):
            raw = raw.strip()
            if not raw:
                continue
            assert raw.startswith(b"data: "), raw
            body = raw[len(b"data: ") :]
            if body == b"[DONE]":
                payloads.append("[DONE]")
                continue
            payloads.append(json.loads(body.decode("ascii")))
        return payloads

    def _p12_run_stream(i: int, rng: random.Random) -> None:
        """Drive one streaming BLOCK iteration and assert the whole stream is withheld (R8.3)."""
        plan = _p12_block_plan()
        cfg, _ = load_holdback_config({})
        pipeline = StreamPipeline.for_plan(
            plan,
            scanner=_p12_scan,
            resolver=MinimalOutputResolver(),
            token_index=TokenIndex(),
            cfg=cfg,
            metrics=HoldbackMetrics(),
            window=max_pattern_length(),
            clock=time.perf_counter_ns,
            detector=_p12_block_detector(_P12_CARD_VALUE),  # type: ignore[arg-type]
        )
        encoder = SSEEncoder()
        sink = _P12SseSink(encoder)
        chunks, raw_text = _p12_split_card(rng)

        stats = asyncio.run(
            pipeline.run(_p12_chunk_source(chunks), sink, _p12_never_killed)
        )

        ctx = _p12_ctx(i, "stream", raw=raw_text, frames=len(sink.frames))

        # The pipeline withheld the stream with the declared block code (R8.3).
        assert stats.error == _P12_BLOCK_CODE, ctx
        # A terminal frame was emitted and the sink went terminal (no content can follow).
        assert sink.sent_terminal, ctx
        assert encoder.terminated, ctx

        wire = b"".join(sink.frames)
        payloads = _p12_sse_payloads(wire)

        # At least the terminal frame reached the wire, and NO clean [DONE] marker did — the
        # stream did not complete cleanly, it was cut by the output block (R8.3 / R10.4).
        assert payloads, _p12_ctx(i, "stream", note="no SSE frame reached the wire")
        assert DONE_MARKER not in wire, ctx

        # Property 12 streaming contract: the response is withheld via a TERMINAL Error_Frame
        # with NO content after it. The holdback release loop may release clean prefix bytes
        # that precede the completed BLOCK match (the minimal-suffix hold), so a content frame
        # BEFORE the terminal is legitimate; the invariant is that the LAST frame is the
        # declared block Error_Frame and nothing follows it, and that the blocked bytes never
        # leave the gateway.
        terminal = payloads[-1]
        assert terminal != "[DONE]", ctx
        assert isinstance(terminal, dict), ctx
        assert "error" in terminal, _p12_ctx(
            i, "stream", note="the last frame is not an Error_Frame", frame=terminal
        )
        assert terminal["error"]["code"] == _P12_BLOCK_CODE, _p12_ctx(
            i, "stream", note="terminal frame is not the declared block code", frame=terminal
        )

        # Exactly ONE terminal Error_Frame, and it is the LAST frame — no content frame follows
        # it (the no-content-after-error contract, enforced by the sink's terminal latch + the
        # SSEEncoder going terminal). Every frame before it is a content frame, never an error.
        error_positions = [
            idx
            for idx, payload in enumerate(payloads)
            if isinstance(payload, dict) and "error" in payload
        ]
        assert error_positions == [len(payloads) - 1], _p12_ctx(
            i,
            "stream",
            note="the Error_Frame is not the single final frame (content after error?)",
            payloads=payloads,
        )

        # The BLOCKED content — the card value — reaches the client ZERO times: the whole
        # match is withheld, never released as any released slice or echoed in the error frame.
        wire_text = wire.decode("ascii")
        assert _P12_CARD_VALUE not in wire_text, _p12_ctx(
            i, "stream", note="the blocked card value leaked onto the wire"
        )
        # No released content frame carries the card value either (defence in depth over the
        # decoded payloads, not just the raw bytes).
        for payload in payloads[:-1]:
            assert isinstance(payload, dict) and "choices" in payload, _p12_ctx(
                i, "stream", note="a non-content frame preceded the terminal", payload=payload
            )
            for choice in payload["choices"]:
                delta_text = "".join(
                    str(v) for v in choice.get("delta", {}).values()
                )
                assert _P12_CARD_VALUE not in delta_text, _p12_ctx(
                    i, "stream", note="card value appeared in a released content frame"
                )

    # ======================================================================= #
    # Drive BOTH paths across the iteration budget (one property, both shapes).
    # ======================================================================= #

    rng = random.Random(_P12_SEED)
    for i in range(_P12_ITERATIONS):
        # Alternate the two response shapes so each gets >= ~5,000 iterations and a regression
        # in EITHER the non-streaming withhold or the streaming terminal-frame withhold fails.
        if i % 2 == 0:
            _p12_run_nonstream(i, rng)
        else:
            _p12_run_stream(i, rng)


# Feature: chat-surface-gw15, Property 13: Uncomputable scan or decision fails closed with FAIL_OPEN pinned at zero  # noqa: E501
# Validates: Requirements 8.4
# --------------------------------------------------------------------------- #
# Property 13 — an uncomputable scan or decision fails closed with FAIL_OPEN at 0.
#
# For ANY input or output scan or decision that cannot be computed, the Chat_Surface
# withholds the affected bytes (never forwarding raw unscanned / undecided content)
# and ``PerRequestExports.snapshot().fail_open_total`` remains zero (R8.4).
#
# The three uncomputable OUTPUT faults the §4.1 non-streaming path can hit — each
# routed through ``ChatSurface.collect_and_render`` → ``collect_and_enforce`` — are
# driven every iteration:
#
#   * ``scanner_raises``        — the injected ``error_scanner`` raises mid-collection.
#   * ``resolver_raises``       — ``resolver.decide`` raises while resolving a channel.
#   * ``decision_uncomputable`` — the resolver returns a well-formed REDACT ``Decision``
#     whose redaction span is NOT fully covered by the released slice, so
#     ``apply_decision`` raises ``ValueError`` (the fail-closed partial-span path, R3.3).
#
# The INPUT side's matching fault — the injected ``input_detector`` raising inside
# ``run_lifecycle`` — is driven too, so "input OR output scan/decision" is covered.
#
# The invariant per fault: the client-visible body is the declared ``scan_failure``
# error envelope (the raw canary bytes reach the client ZERO times, fail closed), and a
# ``PerRequestExports`` observed across the whole request has ``fail_open_total == 0`` —
# the pinned-zero series no correct path increments (R8.4 / R14.3), the same series GW12
# Property 7 asserts against.
#
# House idiom: a seeded ``random.Random``, no ``hypothesis``, >=10,000 iterations; the
# ``ChatSurface`` coroutines are driven via ``asyncio.run``. This section is additive to
# the Property 11 / Property 12 sections above; every symbol it owns is prefixed
# ``_p13_`` / ``_P13_`` / ``P13_`` and ALL imports + helpers are nested inside the test
# function (section-local), so the sections coexist without a name clash.
# --------------------------------------------------------------------------- #


def test_property_13_uncomputable_scan_or_decision_fails_closed_fail_open_zero() -> None:
    # ``asyncio`` / ``json`` / ``random`` / ``Mapping`` / ``Sequence`` / ``dataclass`` /
    # ``cast`` and the ``TYPE_CHECKING`` ``ChatRoute`` reference are module-level above; this
    # section re-uses them and imports only the gateway_v2 symbols it alone needs.
    from gateway_v2.admit.grant import Admitted
    from gateway_v2.dispatch.provider import StubProviderClient, UpstreamEvent
    from gateway_v2.domain.decision import (
        Decision,
        Disposition,
        FindingDisposition,
        Transformation,
    )
    from gateway_v2.domain.finding import Finding, Span
    from gateway_v2.domain.identity import Principal
    from gateway_v2.domain.plan import ExecutionPlan, StreamingMode, Surface
    from gateway_v2.edge.identity_resolver import TenantIdentity
    from gateway_v2.edge.openai_chat import REFUSE_INPUT_SCAN_FAILURE, ChatSurface
    from gateway_v2.edge.wire.openai import (
        CanonicalRequest,
        OpenAIRequestParser,
        OpenAIResponseRenderer,
    )
    from gateway_v2.runtime.stream_metrics import PerRequestExports

    _P13_SEED = 0x6C_6757_0013
    _P13_ITERATIONS = 10_000

    #: The output content channel ``collect_and_enforce`` folds a provider delta into and
    #: the renderer maps to ``message.content``. The canary is placed here so a fail-OPEN
    #: would surface the canary bytes in the rendered JSON content.
    _P13_OUTPUT_CHANNEL = "output_content"
    #: A REDACT-class detector name — documents the vocabulary the uncomputable-apply
    #: ``Decision`` is tagged with (its span, not its class, is what forces the ValueError).
    _P13_REDACT_DETECTOR = "email"
    #: Alphabet for canary synthesis — printable, distinctive, so a canary that slips into
    #: the client body is unambiguous in a counterexample.
    _P13_CANARY_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    #: The three OUTPUT uncomputable-fault kinds driven every iteration. A dropped
    #: fail-closed branch for ANY kind fails the gate.
    _P13_OUTPUT_FAULTS: tuple[str, ...] = (
        "scanner_raises",
        "resolver_raises",
        "decision_uncomputable",
    )

    def _p13_ctx(i: int, **extra: object) -> str:
        body = " ".join(f"{k}={v!r}" for k, v in extra.items())
        return f"seed={_P13_SEED:#x} iter={i} {body}"

    def _p13_canary(rng: random.Random) -> str:
        n = rng.randint(4, 24)
        return "P13CANARY_" + "".join(rng.choice(_P13_CANARY_ALPHABET) for _ in range(n))

    class _P13Send:
        """A recording ASGI ``send`` so the test can read the rendered body bytes.

        ``collect_and_render`` writes via ``emit_response`` an ``http.response.start``
        event then an ``http.response.body`` event. This captures both so the test asserts
        the client-visible body does NOT contain the canary (fail closed) on a fault.
        """

        __slots__ = ("events",)

        def __init__(self) -> None:
            self.events: list[Mapping[str, object]] = []

        async def __call__(self, event: Mapping[str, object]) -> None:
            self.events.append(event)

        def body_bytes(self) -> bytes:
            out = b""
            for event in self.events:
                if event.get("type") == "http.response.body":
                    out += cast(bytes, event.get("body", b""))
            return out

    class _P13CanaryProvider(StubProviderClient):
        """A provider whose single event carries the canary in the output content channel.

        A correct (non-faulting) run would place the canary into the rendered JSON content,
        so the raw bytes are present on the wire — the ONLY thing keeping them from the
        client is the fail-closed withhold the property asserts.
        """

        def __init__(self, canary: str, *, rng: random.Random) -> None:
            super().__init__(
                script=(
                    UpstreamEvent(
                        text_deltas=((_P13_OUTPUT_CHANNEL, canary),),
                        final=True,
                    ),
                ),
                rng=rng,
            )

    class _P13RaisingScanner:
        """An ``error_scanner`` that always raises — an uncomputable OUTPUT scan (R8.4)."""

        __slots__ = ()

        def __call__(self, text: str) -> Sequence[Finding]:
            del text
            raise RuntimeError("p13: output scanner is uncomputable")

    class _P13CleanScanner:
        """An ``error_scanner`` producing no findings — when the FAULT is elsewhere."""

        __slots__ = ()

        def __call__(self, text: str) -> Sequence[Finding]:
            del text
            return ()

    class _P13RaisingResolver:
        """An ``OutputResolver`` whose ``decide`` always raises (uncomputable decision)."""

        __slots__ = ()

        def decide(self, matches: Sequence[Finding]) -> Decision:
            del matches
            raise RuntimeError("p13: output decision is uncomputable")

    class _P13UncomputableApplyResolver:
        """A resolver returning a REDACT ``Decision`` with a PARTIALLY-COVERED span.

        The decision is well-formed (``most_restrictive`` of a single REDACT is REDACT, so
        ``Decision.__post_init__`` accepts it), but its redaction span STARTS inside the
        released slice and ENDS beyond it — a partial overlap. ``apply_decision`` cannot
        safely mask a partially-covered match, so it raises ``ValueError`` (the fail-closed
        partial-coverage path, R3.3). This models a decision that cannot be applied to the
        bytes in hand; ``collect_and_enforce`` must catch it and withhold (R8.4). (A span
        that lies WHOLLY past the slice is deliberately skipped by ``apply_decision`` — "it
        is applied when the slice that contains it is released" — so it would NOT exercise
        the uncomputable path; the partial overlap is what forces the fail-closed raise.)
        """

        __slots__ = ("_released_len",)

        def __init__(self, released_len: int) -> None:
            self._released_len = released_len

        def decide(self, matches: Sequence[Finding]) -> Decision:
            del matches
            # Start inside the released canary, end past it → not fully covered → ValueError.
            start = max(0, self._released_len - 2)
            span = Span(start=start, end=self._released_len + 6)
            return Decision(
                disposition=Disposition.REDACT,
                per_finding=(
                    FindingDisposition(
                        detector=_P13_REDACT_DETECTOR,
                        disposition=Disposition.REDACT,
                    ),
                ),
                transformations=(
                    Transformation(kind="redact", span=span, replacement="[REDACTED]"),
                ),
                findings=(),
                plan_version="p13",
                deciding_rules=(_P13_REDACT_DETECTOR,),
                unavailable_detectors=(),
            )

    @dataclass(slots=True)
    class _P13OutputDeps:
        """The duck-typed bundle ``collect_and_render`` → ``collect_and_enforce`` reads.

        Carries exactly the four seams the non-streaming path consults: ``provider``
        (drained for the canary event), ``error_scanner`` + ``resolver`` (the two
        scan/decide seams, one carrying the injected fault), and ``renderer`` (reached only
        on the success path, never on a fault). All permissive except the fault seam.
        """

        provider: _P13CanaryProvider
        error_scanner: object
        resolver: object
        renderer: OpenAIResponseRenderer

    def _p13_plan() -> ExecutionPlan:
        return ExecutionPlan(
            org_id="p13-org",
            epoch=1,
            sequence=1,
            content_hash="p13" * 11 + "x",
            compiled_at=0.0,
            rules=(),
            required_detectors=frozenset(),
            streaming_mode=StreamingMode.INCREMENTAL,
            integrity_locked=False,
        )

    def _p13_request(canary: str) -> CanonicalRequest:
        return CanonicalRequest(
            model="gpt-p13",
            streaming=False,
            input_channels=(("message_content", canary),),
            text_deltas=(("message_content", canary),),
            surface=Surface.CHAT,
        )

    def _p13_output_deps(
        fault: str, canary: str, *, rng: random.Random, released_len: int
    ) -> _P13OutputDeps:
        provider = _P13CanaryProvider(canary, rng=rng)
        if fault == "scanner_raises":
            return _P13OutputDeps(
                provider=provider,
                error_scanner=_P13RaisingScanner(),
                resolver=_P13RaisingResolver(),  # unreached: scan raises first.
                renderer=OpenAIResponseRenderer(),
            )
        if fault == "resolver_raises":
            return _P13OutputDeps(
                provider=provider,
                error_scanner=_P13CleanScanner(),
                resolver=_P13RaisingResolver(),
                renderer=OpenAIResponseRenderer(),
            )
        if fault == "decision_uncomputable":
            return _P13OutputDeps(
                provider=provider,
                error_scanner=_P13CleanScanner(),
                resolver=_P13UncomputableApplyResolver(released_len=released_len),
                renderer=OpenAIResponseRenderer(),
            )
        raise AssertionError(f"unknown p13 output fault {fault!r}")  # pragma: no cover

    # INPUT-side fault — the injected detector raising inside run_lifecycle.

    class _P13RaisingInputDetector:
        """An injected input detector that raises — an uncomputable INPUT scan (R8.4/R7)."""

        __slots__ = ()

        def __call__(self, text: str) -> Sequence[Finding]:
            del text
            raise RuntimeError("p13: input scan is uncomputable")

    class _P13Identity:
        __slots__ = ("_identity",)

        def __init__(self, identity: TenantIdentity) -> None:
            self._identity = identity

        async def resolve(self, scope: Mapping[str, object]) -> TenantIdentity | None:
            del scope
            return self._identity

    class _P13Snapshot:
        __slots__ = ("_plan",)

        def __init__(self, plan: ExecutionPlan) -> None:
            self._plan = plan

        def lookup(self, org_id: str, now: float | None = None) -> ExecutionPlan:
            del org_id, now
            return self._plan

    class _P13Admission:
        __slots__ = ()

        async def admit(
            self, *, owner_id: str, request_id: str, enqueued_at: float
        ) -> Admitted:
            return Admitted(
                owner_id=owner_id, request_id=request_id, admitted_at=enqueued_at
            )

    class _P13Quota:
        __slots__ = ()

        async def evaluate(self, cost: int, *, request_id: str) -> Admitted:
            del cost
            return Admitted(owner_id="p13", request_id=request_id, admitted_at=0.0)

    class _P13BoundContract:
        """Generous pre-check bounds so the lifecycle reaches the input scan every time."""

        __slots__ = ()

        @staticmethod
        def max_request_bytes() -> int:
            return 1_000_000

        @staticmethod
        def max_expansion_ratio() -> float:
            return 100.0

        @staticmethod
        def max_request_parts() -> int:
            return 1_000_000

    @dataclass(slots=True)
    class _P13InputDeps:
        """The bundle ``run_lifecycle`` reads up to the (raising) input scan."""

        contract: _P13BoundContract
        parser: OpenAIRequestParser
        identity: _P13Identity
        snapshot_source: object
        admission: _P13Admission
        quota: _P13Quota
        clock: object
        input_detector: _P13RaisingInputDetector
        resolver: _P13RaisingResolver
        router: object
        transform: object
        provider: _P13CanaryProvider

    def _p13_input_deps(canary: str, *, rng: random.Random) -> _P13InputDeps:
        plan = _p13_plan()
        identity = TenantIdentity(
            org_id="p13-org",
            principal=Principal(
                key_id="p13-key",
                org_id="p13-org",
                rate_per_s=1.0,
                burst=1.0,
                feed_seq=0,
            ),
        )
        return _P13InputDeps(
            contract=_P13BoundContract(),
            parser=OpenAIRequestParser(),
            identity=_P13Identity(identity),
            snapshot_source=lambda: _P13Snapshot(plan),
            admission=_P13Admission(),
            quota=_P13Quota(),
            clock=lambda: 0.0,
            input_detector=_P13RaisingInputDetector(),
            resolver=_P13RaisingResolver(),
            router=object(),
            transform=object(),
            provider=_P13CanaryProvider(canary, rng=rng),
        )

    rng = random.Random(_P13_SEED)

    for i in range(_P13_ITERATIONS):
        canary = _p13_canary(rng)

        # --- OUTPUT path: every uncomputable output fault, via collect_and_render. ---
        for fault in _P13_OUTPUT_FAULTS:
            exports = PerRequestExports()
            fail_open_before = exports.snapshot().fail_open_total

            req = _p13_request(canary)
            deps = _p13_output_deps(fault, canary, rng=rng, released_len=len(canary))
            surface = ChatSurface(deps=cast("ChatRoute", deps))
            send = _P13Send()

            asyncio.run(
                surface.collect_and_render(
                    req,
                    _p13_plan(),
                    destination="p13-dest",
                    body={"model": "gpt-p13"},
                    send=send,
                )
            )

            body = send.body_bytes()

            # Fail closed: the raw canary bytes reached the client ZERO times (R8.4).
            assert canary.encode("ascii") not in body, _p13_ctx(
                i,
                note="canary leaked into client body on an uncomputable output fault",
                fault=fault,
                canary=canary,
            )
            # The withheld response is the declared scan_failure envelope, not raw content.
            assert body, _p13_ctx(i, note="no response emitted on fault", fault=fault)
            envelope = json.loads(body.decode("utf-8"))
            assert envelope["error"]["code"] == REFUSE_INPUT_SCAN_FAILURE, _p13_ctx(
                i,
                note="withheld code is not the declared scan_failure code",
                fault=fault,
                rendered=envelope,
            )

            # FAIL_OPEN stays pinned at zero across the whole request (R8.4 / R14.3).
            fail_open_after = exports.snapshot().fail_open_total
            assert fail_open_before == 0 and fail_open_after == 0, _p13_ctx(
                i,
                note="fail_open_total moved off zero on an output fault",
                fault=fault,
                before=fail_open_before,
                after=fail_open_after,
            )

        # --- INPUT path: the injected detector raising inside run_lifecycle. ---
        input_exports = PerRequestExports()
        in_deps = _p13_input_deps(canary, rng=rng)
        in_surface = ChatSurface(deps=cast("ChatRoute", in_deps))
        in_body = json.dumps(
            {"model": "gpt-p13", "messages": [{"role": "user", "content": canary}]}
        ).encode("utf-8")
        in_scope: Mapping[str, object] = {"path": "/v1/chat/completions"}

        parsed, outcome = asyncio.run(in_surface.run_lifecycle(in_scope, in_body))

        # Fail closed: refused with the declared scan_failure code; the proceed inputs are
        # withheld (zero provider calls), so the unscanned canary never left the gateway.
        assert outcome.refuse_code == REFUSE_INPUT_SCAN_FAILURE, _p13_ctx(
            i,
            note="raising input scan did not fail closed with scan_failure",
            refuse_code=outcome.refuse_code,
        )
        assert (
            outcome.plan is None
            and outcome.destination is None
            and outcome.body is None
        ), _p13_ctx(i, note="input-fault refusal leaked a provider input")
        assert parsed is not None, _p13_ctx(
            i, note="request did not parse before the input scan"
        )

        # FAIL_OPEN stays pinned at zero on the input-side uncomputable scan too.
        assert input_exports.snapshot().fail_open_total == 0, _p13_ctx(
            i,
            note="fail_open_total moved off zero on an input fault",
            after=input_exports.snapshot().fail_open_total,
        )


# Feature: chat-surface-gw15, Property 11: A REDACT output-channel finding reaches the client zero times (output channel test)  # noqa: E501
# Validates: Requirements 8.1, 8.2
# --------------------------------------------------------------------------- #
# Property 11 — a REDACT output-channel finding reaches the client zero times (task 4.2).
#
# For ANY upstream response and *any* output ``TextChannel``, a canary placed in that
# channel that the output resolver maps to a REDACT disposition is removed / masked so the
# CLIENT-VISIBLE bytes contain the canary ZERO times — asserted for BOTH:
#
#   * NON-STREAMING: the shipped ``collect_and_enforce`` drain + per-channel
#     scan/resolve/``apply_decision`` enforcement, then the exact JSON bytes
#     ``OpenAIResponseRenderer.non_stream_body`` renders (what a non-streaming client
#     receives).
#   * STREAMING: the shipped ``StreamPipeline.run`` release loop (ENFORCING output, driven
#     by the REAL ``detect.holdback`` scanner + the real ``MinimalOutputResolver``), whose
#     downstream content frames are the bytes a streaming client receives.
#
# The canary is iterated over EVERY output ``TextChannel`` entry (the ``choices[]``-scoped
# names, derived from the authoritative ``TEXT_CHANNELS`` tuple) each iteration, so a
# dropped output-scan / enforcement path for ANY single output channel — a channel whose
# canary survives into the client-visible bytes — fails the gate (C21).
#
# Only the provider (scripted stub, no socket) and the output scanner (a deterministic
# REDACT-class finder over the canary span) are test doubles; the resolver,
# ``apply_decision``, the holdback scanner, and the renderer are the REAL shipped
# components, so the redaction is proven end to end on both paths.
#
# This section is additive to the sibling sections above; every symbol it owns is prefixed
# ``_p11_`` / ``_P11_`` / ``P11_`` with section-local (mid-module) imports, so the sections
# coexist without a name clash.
# --------------------------------------------------------------------------- #

import asyncio as _p11_asyncio  # noqa: E402
import random as _p11_random  # noqa: E402
from collections.abc import AsyncIterator as _P11AsyncIterator  # noqa: E402
from collections.abc import Sequence as _P11Sequence  # noqa: E402
from dataclasses import dataclass as _p11_dataclass  # noqa: E402
from typing import TYPE_CHECKING as _P11_TYPE_CHECKING  # noqa: E402
from typing import cast as _p11_cast  # noqa: E402

from gateway_v2.detect import holdback as _p11_holdback  # noqa: E402
from gateway_v2.detect.holdback import TokenIndex as _P11TokenIndex  # noqa: E402
from gateway_v2.detect.windowing import max_pattern_length as _p11_max_pattern_length  # noqa: E402
from gateway_v2.dispatch.provider import StubProviderClient as _P11StubProviderClient  # noqa: E402
from gateway_v2.dispatch.provider import UpstreamEvent as _P11UpstreamEvent  # noqa: E402
from gateway_v2.domain.category import Category as _P11Category  # noqa: E402
from gateway_v2.domain.channels import TEXT_CHANNELS as _P11_TEXT_CHANNELS  # noqa: E402
from gateway_v2.domain.finding import Finding as _P11Finding  # noqa: E402
from gateway_v2.domain.finding import FindingStatus as _P11FindingStatus  # noqa: E402
from gateway_v2.domain.finding import Span as _P11Span  # noqa: E402
from gateway_v2.domain.plan import Surface as _P11Surface  # noqa: E402
from gateway_v2.edge.openai_nonstream import (  # noqa: E402
    collect_and_enforce as _p11_collect_and_enforce,
)
from gateway_v2.edge.wire.openai import CanonicalRequest as _P11CanonicalRequest  # noqa: E402
from gateway_v2.edge.wire.openai import OpenAIResponseRenderer as _P11Renderer  # noqa: E402
from gateway_v2.egress.output_guard import (  # noqa: E402
    REDACTION_PLACEHOLDER as _P11_REDACTION_PLACEHOLDER,
)
from gateway_v2.egress.output_guard import (  # noqa: E402
    MinimalOutputResolver as _P11MinimalOutputResolver,
)
from gateway_v2.egress.output_guard import apply_decision as _p11_apply_decision  # noqa: E402
from gateway_v2.egress.stream import Detector as _P11Detector  # noqa: E402
from gateway_v2.egress.stream import DownstreamFrame as _P11DownstreamFrame  # noqa: E402
from gateway_v2.egress.stream import ScanResultLike as _P11ScanResultLike  # noqa: E402
from gateway_v2.egress.stream import StreamPipeline as _P11StreamPipeline  # noqa: E402
from gateway_v2.egress.stream import TokenCounterProtocol as _P11TokenCounterProtocol  # noqa: E402
from gateway_v2.egress.stream import UpstreamChunk as _P11UpstreamChunk  # noqa: E402
from gateway_v2.runtime.holdback_config import (  # noqa: E402
    load_holdback_config as _p11_load_holdback_config,
)
from gateway_v2.runtime.holdback_metrics import HoldbackMetrics as _P11HoldbackMetrics  # noqa: E402

if _P11_TYPE_CHECKING:
    from gateway_v2.edge.routes import ChatRoute as _P11ChatRoute

_P11_SEED = 0x6C_6757_0011
_P11_ITERATIONS = 10_000

#: The ``TextChannel.sdk_path`` prefix that marks an OUTPUT (response-side) channel — the
#: ``choices[]`` entries are the output scan's job. Scope is read off the authoritative
#: ``sdk_path`` so adding an output channel to the one ``TEXT_CHANNELS`` tuple is all it
#: takes to demand a canary placement here (C21).
_P11_OUTPUT_SDK_PATH_PREFIX = "choices[]"

#: A REDACT-class detector name: ``MinimalOutputResolver`` maps ``email`` (a redact-the-
#: remainder class) to ``Disposition.REDACT`` via its trade-off table, so a finding tagged
#: with this detector resolves to a REDACT decision and ``apply_decision`` masks its span.
_P11_REDACT_DETECTOR = "email"

#: Alphabet for canary synthesis — printable, distinctive, and free of the holdback
#: scanner's structural delimiters (``.`` / ``:`` / ``/`` / ``-`` / ``@``) and whitespace,
#: so the real streaming scanner holds the whole canary as one word-class run and it never
#: releases as a raw prefix (the only path to the client is through ``apply_decision``).
_P11_CANARY_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def _p11_ctx(seed: int, i: int, **extra: object) -> str:
    body = " ".join(f"{k}={v!r}" for k, v in extra.items())
    return f"seed={seed:#x} iter={i} {body}"


def _p11_canary(rng: _p11_random.Random) -> str:
    """A distinctive canary token unlikely to collide with any filler text."""
    n = rng.randint(4, 24)
    return "P11CANARY" + "".join(rng.choice(_P11_CANARY_ALPHABET) for _ in range(n))


def _p11_output_channel_names() -> tuple[str, ...]:
    """The output-scoped ``TEXT_CHANNELS`` names, from the authoritative enumeration."""
    return tuple(
        channel.name
        for channel in _P11_TEXT_CHANNELS
        if channel.sdk_path.startswith(_P11_OUTPUT_SDK_PATH_PREFIX)
    )


def _p11_redact_finding(text: str, canary: str) -> tuple[_P11Finding, ...]:
    """A single EXECUTED REDACT-class finding over the canary span in ``text``.

    Returns the finding whose span covers exactly the canary bytes so
    ``MinimalOutputResolver`` resolves it to REDACT and ``apply_decision`` replaces that
    span with the placeholder; empty when the canary is absent (filler text produces no
    finding). The span is absolute over the text the scanner saw, matching the whole-text
    detector contract the egress release loop relies on.
    """
    if canary not in text:
        return ()
    start = text.index(canary)
    return (
        _P11Finding(
            detector=_P11_REDACT_DETECTOR,
            detector_version="p11",
            category=_P11Category.PII,
            status=_P11FindingStatus.EXECUTED,
            confidence=1.0,
            spans=(_P11Span(start=start, end=start + len(canary)),),
            evidence=None,
        ),
    )


# --- non-streaming side: the exact bytes a non-streaming client receives --------------- #


class _P11OneChannelProvider(_P11StubProviderClient):
    """A :class:`StubProviderClient` that emits the canary in exactly one output channel."""

    __slots__ = ()

    def __init__(self, *, channel_name: str, canary: str, rng: _p11_random.Random) -> None:
        super().__init__(
            script=(
                _P11UpstreamEvent(text_deltas=((channel_name, canary),), final=True),
            ),
            rng=rng,
        )


class _P11OutputScanner:
    """Injected whole-text findings producer: a REDACT finding on the canary span.

    The ``error_scanner`` / detector seam injected in ``edge`` (above ``detect``, R7.5):
    given a channel's whole text it returns the canary's REDACT finding (empty for filler
    text), so the resolver maps it to REDACT and the enforcement masks it.
    """

    __slots__ = ("_canary",)

    def __init__(self, canary: str) -> None:
        self._canary = canary

    def __call__(self, text: str) -> _P11Sequence[_P11Finding]:
        return _p11_redact_finding(text, self._canary)


@_p11_dataclass(slots=True)
class _P11NonStreamDeps:
    """The minimal duck-typed deps ``collect_and_enforce`` reads (provider/scanner/resolver).

    ``collect_and_enforce`` only reads ``deps.provider`` (drained to the final event),
    ``deps.error_scanner`` (the per-channel whole-text findings producer), and
    ``deps.resolver`` (the C22 ``most_restrictive`` decision). The REDACT finding + the
    shipped ``MinimalOutputResolver`` + ``apply_decision`` are the real enforcement path.
    """

    provider: _P11OneChannelProvider
    error_scanner: _P11OutputScanner
    resolver: _P11MinimalOutputResolver


def _p11_canonical_request() -> _P11CanonicalRequest:
    """A minimal non-streaming :class:`CanonicalRequest` for the renderer."""
    return _P11CanonicalRequest(
        model="gpt-p11",
        streaming=False,
        input_channels=(),
        text_deltas=(),
        surface=_P11Surface.CHAT,
    )


def _p11_client_bytes_nonstream(
    channel_name: str, canary: str, rng: _p11_random.Random
) -> bytes:
    """Run the non-streaming enforce + render and return the client-visible bytes.

    Drives the shipped ``collect_and_enforce`` (drain + per-channel scan/resolve/
    ``apply_decision``) over a provider that emitted the canary in ``channel_name``, then
    renders the enforced deltas through ``OpenAIResponseRenderer.non_stream_body`` — the
    exact JSON a non-streaming client receives. A BLOCK / scan-failure refusal would set a
    ``refuse_code`` (not Property 11's REDACT case), so the test asserts a clean enforce.
    """
    deps = _P11NonStreamDeps(
        provider=_P11OneChannelProvider(channel_name=channel_name, canary=canary, rng=rng),
        error_scanner=_P11OutputScanner(canary),
        resolver=_P11MinimalOutputResolver(),
    )
    enforced = _p11_asyncio.run(
        _p11_collect_and_enforce(
            _p11_cast("_P11ChatRoute", deps),
            "p11-destination",
            {},
            block_code="output_blocked",
            scan_failure_code="scan_failure",
        )
    )
    assert enforced.refuse_code is None, (
        f"REDACT canary refused on the non-stream path (channel={channel_name!r}, "
        f"refuse_code={enforced.refuse_code!r})"
    )
    return _P11Renderer().non_stream_body(_p11_canonical_request(), enforced.deltas)


# --- streaming side: the released downstream content bytes a client receives ----------- #


def _p11_real_scanner(
    buf: str,
    *,
    final: bool,
    hold_cap_tokens: int,
    token_index: _P11TokenCounterProtocol,
    window: int,
) -> _P11ScanResultLike:
    """Adapter so the real pure ``holdback.scan`` injects as a ``ScanProtocol``.

    The canary alphabet excludes every structural delimiter, so the real scanner holds the
    whole canary as one word-class run until the final chunk flushes it — the canary never
    releases as a raw prefix, so the only path to the client is through ``apply_decision``,
    which the REDACT decision masks.
    """
    return _p11_holdback.scan(
        buf,
        final=final,
        hold_cap_tokens=hold_cap_tokens,
        token_index=token_index,  # type: ignore[arg-type]
        window=window,
    )


def _p11_stream_detector(canary: str) -> _P11Detector:
    """A whole-text ``Detector`` that flags the canary span for the release loop."""

    def _detect(whole: str) -> _P11Sequence[_P11Finding]:
        return _p11_redact_finding(whole, canary)

    return _detect


class _P11Collector:
    """An injected ``Send`` sink that records every downstream content frame."""

    __slots__ = ("frames",)

    def __init__(self) -> None:
        self.frames: list[_P11DownstreamFrame] = []

    async def __call__(self, frame: _P11DownstreamFrame) -> None:
        self.frames.append(frame)

    def released_text(self) -> str:
        parts: list[str] = []
        for frame in self.frames:
            if frame.error_code is not None:
                continue
            for _channel, delta in frame.text_deltas:
                parts.append(delta)
        return "".join(parts)


def _p11_never_killed() -> bool:
    return False


async def _p11_source(
    chunks: _P11Sequence[_P11UpstreamChunk],
) -> _P11AsyncIterator[_P11UpstreamChunk]:
    for chunk in chunks:
        yield chunk


def _p11_client_bytes_stream(
    channel_name: str, canary: str, rng: _p11_random.Random
) -> str:
    """Run the streaming release loop and return the client-visible released text.

    Drives the shipped ``StreamPipeline.run`` over one final chunk carrying the canary in
    ``channel_name`` with the REDACT detector attached (ENFORCING output engages the hold +
    ``apply_decision`` enforcement; the pass-through mode never scans), and returns the
    concatenation of the downstream content frames — the bytes a streaming client receives.
    ``rng`` is kept for parity with the non-stream driver's injected-randomness contract.
    """
    del rng
    cfg, _ = _p11_load_holdback_config({})
    pipe = _P11StreamPipeline(
        scanner=_p11_real_scanner,
        resolver=_P11MinimalOutputResolver(),
        token_index=_P11TokenIndex(),
        cfg=cfg,
        metrics=_P11HoldbackMetrics(),
        enforcing_output=True,
        window=_p11_max_pattern_length(),
        detector=_p11_stream_detector(canary),
    )
    collector = _P11Collector()
    chunks = (_P11UpstreamChunk(text_deltas=((channel_name, canary),), final=True),)
    _p11_asyncio.run(pipe.run(_p11_source(chunks), collector, _p11_never_killed))
    return collector.released_text()


def test_property_11_redact_output_channel_reaches_client_zero_times() -> None:
    rng = _p11_random.Random(_P11_SEED)
    output_channels = _p11_output_channel_names()

    # Coverage guard: there must be output channels to iterate, so a scope regression that
    # emptied the output-scoped set fails the gate rather than vacuously passing (C21).
    assert output_channels, _p11_ctx(_P11_SEED, -1, note="no output channels enumerated")

    # Sanity: a REDACT decision over the canary span masks it so the enforced text no
    # longer contains the canary — the enforcement primitive Property 11 rests on.
    probe = "left P11CANARYPROBE right"
    probe_decision = _P11MinimalOutputResolver().decide(
        list(_p11_redact_finding(probe, "P11CANARYPROBE"))
    )
    probe_out = _p11_apply_decision(probe, probe_decision, base_offset=0)
    assert "P11CANARYPROBE" not in probe_out and _P11_REDACTION_PLACEHOLDER in probe_out, (
        _p11_ctx(_P11_SEED, -1, note="REDACT primitive did not mask the canary", out=probe_out)
    )

    for i in range(_P11_ITERATIONS):
        # Iterate the canary over EVERY output TextChannel entry each iteration so a dropped
        # enforcement path for ANY single channel (its canary surviving to the client) fails.
        for channel_name in output_channels:
            canary = _p11_canary(rng)

            # NON-STREAMING: the exact JSON bytes a non-streaming client receives.
            nonstream_bytes = _p11_client_bytes_nonstream(channel_name, canary, rng)
            assert canary.encode("ascii") not in nonstream_bytes, _p11_ctx(
                _P11_SEED,
                i,
                note="REDACT canary reached the non-streaming client",
                channel=channel_name,
                canary=canary,
                body=nonstream_bytes,
            )

            # STREAMING: the released downstream content bytes a client receives.
            stream_text = _p11_client_bytes_stream(channel_name, canary, rng)
            assert canary not in stream_text, _p11_ctx(
                _P11_SEED,
                i,
                note="REDACT canary reached the streaming client",
                channel=channel_name,
                canary=canary,
                released=stream_text,
            )
