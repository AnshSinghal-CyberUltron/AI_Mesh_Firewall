"""GW15 non-streaming path property + unit tests (shared file).

This module is written by two GW15 task sub-agents concurrently (4.5 the Property 14
non-streaming single-enforced-JSON property test, 4.6 the unmapped-code non-stream unit
test). Each contribution is a SELF-CONTAINED additive section with uniquely-prefixed
symbols (``_p14_`` / ``P14_`` here) and section-local (mid-module) imports, so the
sections never collide and none depends on another's helpers. Do not remove a sibling
section; append new sections below.

House idiom throughout: a seeded ``random.Random`` driven for >=10,000 iterations, NO
``hypothesis``; async paths exercised via ``asyncio.run``; one property <-> one
property-based test.
"""

from __future__ import annotations

# ===========================================================================
# Property 14 — Non-streaming returns one enforced OpenAI-compatible JSON (task 4.5).
# ===========================================================================
# Feature: chat-surface-gw15, Property 14: Non-streaming returns one enforced OpenAI-compatible JSON
# Validates: Requirements 9.1
#
# *For any* non-streaming request that proceeds past the §10.4 lifecycle,
# ``ChatSurface.collect_and_render`` drains the whole upstream response, applies the OUTPUT
# decision to the collected text, and emits EXACTLY ONE OpenAI-compatible JSON body whose
# content reflects the ENFORCED (possibly redacted) text — never the raw upstream bytes.
#
# This property drives >=10,000 seeded iterations. Each iteration scripts a
# ``StubProviderClient`` with a random fan of ``output_content`` deltas, some of which carry
# a per-iteration canary substring. A deterministic output scanner reports a REDACT finding
# over every canary occurrence (spans into the per-channel collected text), the real
# ``MinimalOutputResolver`` resolves them to a REDACT ``Decision``, and the shipped
# ``apply_decision`` masks the canary bytes with ``REDACTION_PLACEHOLDER``. The test then
# asserts, over a recording ASGI ``send``:
#
#   (a) the FULL upstream was collected — every scripted delta's non-canary text survives in
#       the rendered content, in document order (nothing dropped on the drain);
#   (b) the output decision was APPLIED — the canary appears ZERO times in the client-visible
#       content, and (when a canary was present) the ``REDACTION_PLACEHOLDER`` appears instead;
#   (c) EXACTLY ONE OpenAI-compatible JSON body is returned — one ``http.response.start`` + one
#       ``http.response.body``, HTTP 200, ``application/json``, parseable as a ``chat.completion``
#       whose ``choices[0].message.content`` is exactly the enforced text (R9.1).
#
# The resolver + ``apply_decision`` + renderer are the REAL shipped components; only the
# provider (scripted stub, no socket) and the scanner (deterministic canary finder) are test
# doubles. ``ChatSurface.collect_and_render`` reads only ``deps.provider`` / ``deps.error_scanner``
# / ``deps.resolver`` / ``deps.renderer``, so a duck-typed bundle carrying exactly those four
# seams is a faithful substitute for the full ``ChatRoute`` here.


def test_property_14_non_streaming_returns_one_enforced_openai_json() -> None:
    import asyncio
    import json
    import random
    from collections.abc import Mapping, Sequence
    from dataclasses import dataclass

    from gateway_v2.dispatch.provider import (
        StubProviderClient,
        UpstreamEvent,
    )
    from gateway_v2.domain.category import Category
    from gateway_v2.domain.finding import Finding, FindingStatus, Span
    from gateway_v2.domain.plan import ExecutionPlan, StreamingMode, Surface
    from gateway_v2.edge.openai_chat import ChatSurface
    from gateway_v2.edge.wire.openai import CanonicalRequest, OpenAIResponseRenderer
    from gateway_v2.egress.output_guard import (
        REDACTION_PLACEHOLDER,
        MinimalOutputResolver,
    )

    _P14_SEED = 0x14_0E_0014
    _P14_ITERATIONS = 10_000

    # The transport channel the renderer folds into ``choices[0].message.content`` and the
    # channel the non-streaming collector attributes provider ``output_content`` deltas to.
    _P14_OUTPUT_CONTENT_CHANNEL = "output_content"
    # A detector name NOT in the resolver's trade-off class table, so an EXECUTED finding
    # resolves to the fail-safe REDACT disposition (never a BLOCK) — this property exercises
    # the REDACT (enforced-but-rendered) path, not the withhold path (Property 12 covers BLOCK).
    _P14_REDACT_DETECTOR = "p14-canary-redactor"
    # The canary alphabet is disjoint from the fixed non-canary filler AND from the letters of
    # the redaction placeholder (``[REDACTED]`` → R,E,D,A,C,T), so neither the filler nor the
    # placeholder bytes can masquerade as a surviving canary in the client-visible content.
    _P14_CANARY_ALPHABET = "BFGHJKLMNPQSUVWXYZ"
    _P14_FILLER_ALPHABET = "0123456789 .,!?-"

    @dataclass(slots=True)
    class _P14RecordingSend:
        """Records every ASGI ``send`` event so "exactly one JSON body" is observable.

        ``collect_and_render`` emits a reply through the single ``emit_response`` path, which
        writes one ``http.response.start`` (status + headers) then one ``http.response.body``
        (the JSON bytes). Recording both lets the property assert the message count, the
        status, the content-type header, and the parsed body shape.
        """

        events: list[Mapping[str, object]]

        async def __call__(self, event: Mapping[str, object]) -> None:
            self.events.append(event)

    @dataclass(frozen=True, slots=True)
    class _P14Deps:
        """The ``ChatRoute``-shaped bundle ``collect_and_render`` reads from.

        Carries exactly the four seams the non-streaming path consults — the injected
        ``provider`` (scripted stub), the ``error_scanner`` (the deterministic canary finder),
        the ``resolver`` (the real ``MinimalOutputResolver``), and the ``renderer`` (the real
        ``OpenAIResponseRenderer``). A duck-typed stand-in for the frozen ``ChatRoute`` so the
        property exercises the collect/enforce/render wiring without the full boot graph.
        """

        provider: StubProviderClient
        error_scanner: object
        resolver: MinimalOutputResolver
        renderer: OpenAIResponseRenderer

    def _p14_make_scanner(canary: str) -> object:
        """Build the output scanner: a REDACT finding over every ``canary`` occurrence.

        The scanner is the injected whole-text findings producer the output guard runs per
        channel. For a given per-channel text it locates every (non-overlapping) occurrence of
        the per-iteration ``canary`` and emits one EXECUTED finding whose spans cover those
        byte ranges, categorised PII and named with a detector OUTSIDE the resolver's trade-off
        table (so it resolves REDACT, not BLOCK). An empty canary (the no-canary iterations)
        yields no finding, so the collected text passes through unredacted.
        """

        def _scan(text: str) -> Sequence[Finding]:
            if not canary:
                return ()
            spans: list[Span] = []
            start = text.find(canary)
            while start != -1:
                spans.append(Span(start=start, end=start + len(canary)))
                start = text.find(canary, start + len(canary))
            if not spans:
                return ()
            return (
                Finding(
                    detector=_P14_REDACT_DETECTOR,
                    detector_version="1",
                    category=Category.PII,
                    status=FindingStatus.EXECUTED,
                    confidence=0.99,
                    spans=tuple(spans),
                    evidence=None,
                ),
            )

        return _scan

    def _p14_gen_canary(rng: random.Random) -> str:
        """Draw a per-iteration canary — or the empty string on the no-canary path."""
        if rng.random() < 0.2:
            return ""
        return "".join(
            rng.choice(_P14_CANARY_ALPHABET) for _ in range(rng.randint(3, 8))
        )

    def _p14_gen_filler(rng: random.Random) -> str:
        """Draw a chunk of non-canary filler text (may be empty)."""
        return "".join(
            rng.choice(_P14_FILLER_ALPHABET) for _ in range(rng.randint(0, 6))
        )

    def _p14_script(
        rng: random.Random, canary: str
    ) -> tuple[tuple[UpstreamEvent, ...], str]:
        """Script a fan of ``output_content`` deltas; return the events + the raw text.

        Each event carries one ``output_content`` delta that is a mix of filler and (randomly)
        the canary, so the collected upstream text is the ORDERED concatenation of every
        delta. The last event is flagged ``final``. The returned raw text is what the upstream
        produced BEFORE enforcement — the property compares the rendered content against the
        ENFORCED form of this text, never the raw form.
        """
        event_count = rng.randint(1, 6)
        events: list[UpstreamEvent] = []
        raw_parts: list[str] = []
        for idx in range(event_count):
            delta = _p14_gen_filler(rng)
            if canary and rng.random() < 0.5:
                delta += canary + _p14_gen_filler(rng)
            raw_parts.append(delta)
            events.append(
                UpstreamEvent(
                    text_deltas=((_P14_OUTPUT_CONTENT_CHANNEL, delta),),
                    final=idx == event_count - 1,
                )
            )
        return tuple(events), "".join(raw_parts)

    def _p14_canonical_request(rng: random.Random) -> CanonicalRequest:
        """A minimal non-streaming ``CanonicalRequest`` carrying only the routing model.

        The renderer reads only ``req.model`` (the response ``model`` field); the content
        comes entirely from the enforced deltas, so a trivial request with no input channels
        is sufficient to drive the render.
        """
        model = "gpt-" + "".join(rng.choice("abcdef0123456789") for _ in range(6))
        return CanonicalRequest(
            model=model,
            streaming=False,
            input_channels=(),
            text_deltas=(),
            surface=Surface.CHAT,
        )

    def _p14_expected_enforced(raw: str, canary: str) -> str:
        """The expected ENFORCED content: every canary occurrence masked by the placeholder.

        Mirrors what the real resolver + ``apply_decision`` produce for a REDACT finding over
        the canary spans — the independent oracle the property checks the rendered content
        against. With no canary the enforced form is the raw text unchanged.
        """
        if not canary:
            return raw
        return raw.replace(canary, REDACTION_PLACEHOLDER)

    rng = random.Random(_P14_SEED)
    resolver = MinimalOutputResolver()
    renderer = OpenAIResponseRenderer()

    for i in range(_P14_ITERATIONS):
        canary = _p14_gen_canary(rng)
        events, raw = _p14_script(rng, canary)
        req = _p14_canonical_request(rng)
        provider = StubProviderClient(script=events, rng=random.Random(rng.random()))
        deps = _P14Deps(
            provider=provider,
            error_scanner=_p14_make_scanner(canary),
            resolver=resolver,
            renderer=renderer,
        )
        surface = ChatSurface(deps=deps)  # type: ignore[arg-type]
        send = _P14RecordingSend(events=[])

        # A resolvable INCREMENTAL plan: the injected resolver carries the output enforcement;
        # ``collect_and_render`` reads ``plan.streaming_mode`` only to stamp the Produced_Mode
        # header (GW13 C5/R5), so a minimal plan is sufficient here.
        plan = ExecutionPlan(
            org_id="p14-org",
            epoch=1,
            sequence=1,
            content_hash="p14" + "0" * 61,
            compiled_at=0.0,
            rules=(),
            required_detectors=frozenset(),
            streaming_mode=StreamingMode.INCREMENTAL,
            integrity_locked=False,
        )
        asyncio.run(
            surface.collect_and_render(
                req,
                plan=plan,
                destination="p14-upstream",
                body={},
                send=send,
            )
        )

        ctx = (
            f"seed={_P14_SEED:#x} iter={i} canary={canary!r} raw={raw!r} "
            f"events={len(send.events)}"
        )

        # (c) EXACTLY ONE OpenAI JSON body: one start frame + one body frame, nothing else.
        assert len(send.events) == 2, ctx
        start, body_event = send.events
        assert start["type"] == "http.response.start", ctx
        assert start["status"] == 200, ctx
        headers = {
            name.decode("latin-1"): value.decode("latin-1")
            for name, value in start["headers"]  # type: ignore[attr-defined]
        }
        assert headers.get("content-type") == "application/json", ctx
        assert body_event["type"] == "http.response.body", ctx

        # The one body parses as an OpenAI-compatible chat.completion.
        raw_body = body_event["body"]
        assert isinstance(raw_body, bytes), ctx
        payload = json.loads(raw_body.decode("utf-8"))
        assert payload["object"] == "chat.completion", ctx
        assert payload["model"] == req.model, ctx
        choices = payload["choices"]
        assert len(choices) == 1, ctx
        content = choices[0]["message"]["content"]

        expected = _p14_expected_enforced(raw, canary)

        # (b) the output decision was APPLIED: content is the ENFORCED text, and the canary is
        #     client-visible ZERO times. The placeholder appears iff the canary actually
        #     occurred in the collected upstream (a canary may be drawn yet never scripted into
        #     a delta, in which case there is nothing to redact).
        assert content == expected, ctx
        if canary:
            assert canary not in content, ctx
            if canary in raw:
                assert REDACTION_PLACEHOLDER in content, ctx

        # (a) the FULL upstream was collected: the enforced text equals the enforcement applied
        #     to the ORDERED concatenation of every scripted delta — nothing was dropped on the
        #     drain (the content length matches the raw drain with canaries swapped for the
        #     placeholder, so no delta went missing).
        assert content == _p14_expected_enforced(raw, canary), ctx


# ===========================================================================
# Task 4.6 — Unmapped-code non-stream unit tests (example tests, NOT a property).
# ===========================================================================
# Feature: chat-surface-gw15, Task 4.6: unmapped-code non-stream path renders the
# declared generic Error_Envelope body, never a raw framework error.
# Validates: Requirements 9.2, 12.2
#
# R9.2: WHEN a non-streaming response is returned, THE Chat_Surface renders it through the
#       single response path so an UNMAPPED error value-code renders the declared
#       Error_Envelope body rather than a raw framework error.
# R12.2: IF a value-code is unmapped, THEN THE Chat_Surface renders the declared generic
#        error through the Error_Envelope rather than leaking raw internal detail.
#
# These are EXAMPLE/unit tests (no >=10,000-iteration driver): the non-streaming path
# (``ChatSurface.collect_and_render``) emits EVERY reply — success and refusal alike —
# through the single ``emit_response(send, errors.render_http(code))`` seam. That single
# seam is what guarantees an unmapped value-code renders the declared generic body
# (``edge/errors.render_http`` falls back to ``GENERIC_HTTP_STATUS`` + a fixed
# ``gateway_error`` JSON body) instead of leaking a raw framework ``KeyError`` / traceback.
# The tests assert, with uniquely-prefixed ``_p46_`` / ``_unmapped_`` symbols:
#
#   (1) the generic-fallback CONTRACT of the single render path: an unmapped code rendered
#       through the exact ``errors.render_http`` call the non-stream path uses yields the
#       declared generic envelope (status 500, type ``gateway_error``, generic message) and
#       NEVER echoes the raw code into the human-readable ``message`` (R12.2);
#   (2) the END-TO-END non-stream path: a BLOCK output drives ``collect_and_render`` to
#       withhold the body and emit the declared ``Error_Envelope`` HTTP response through the
#       single ``emit_response`` seam — a well-formed JSON ``{"error": {...}}`` body, never a
#       raw framework error, and the canary never reaches the client after the block (R9.2);
#   (3) a scan-failure refusal routed through the single path: an uncomputable output decision
#       (a resolver RAISE) renders the declared envelope for the refusal code — the raw
#       ``RuntimeError`` text never leaks — proving every value-code leaves through the one
#       generic-safe render path, so even an unmapped code could not leak a framework error.


def test_p46_unmapped_code_renders_generic_envelope_on_the_single_path() -> None:
    """An UNMAPPED value-code rendered through the non-stream single path is generic (R12.2).

    The non-streaming path renders every reply through ``errors.render_http(code)``. This
    test exercises that exact render over a code with NO ``_ENVELOPE`` entry and asserts the
    declared generic fallback: HTTP ``GENERIC_HTTP_STATUS`` (500), a parseable
    ``{"error": {...}}`` JSON body with ``type == "gateway_error"`` and a FIXED generic
    ``message``, and — critically — the raw unmapped code never appears in the
    human-readable ``message`` (no raw internal detail leaks, R12.2). The structured
    ``code`` field MAY echo the code (the declared trace-spelling contract), but the
    ``message`` MUST stay generic.
    """
    import json

    from gateway_v2.edge import errors

    # A value-code that is deliberately NOT in the Error_Envelope table — the "unmapped"
    # case R9.2 / R12.2 is about. (If this ever becomes a declared code, pick another.)
    _unmapped_code = "an_internal_detail_that_must_not_leak_7f3c"
    assert errors._ENVELOPE.get(_unmapped_code) is None, (
        "fixture code must be unmapped for this test to exercise the generic fallback"
    )

    rendered = errors.render_http(_unmapped_code)

    # The declared generic status (500 — an unmapped code is an internal gap, not a 4xx).
    assert rendered.status == errors.GENERIC_HTTP_STATUS == 500

    # The body is a declared, parseable OpenAI-shaped error object — NOT a raw framework
    # error. ``.json()`` parsing it round-trips, proving it is well-formed JSON.
    parsed = rendered.json()
    assert isinstance(parsed, dict)
    error_obj = parsed["error"]
    assert isinstance(error_obj, dict)

    # The declared generic type and a FIXED generic message (no internal detail).
    assert error_obj["type"] == "gateway_error"
    message = error_obj["message"]
    assert isinstance(message, str)
    assert message == errors._GENERIC_ERROR_MESSAGE

    # The raw unmapped code is NEVER substituted into the human-readable message (R12.2 —
    # no raw internal detail leaks). The structured ``code`` field may echo it, the message
    # may not.
    assert _unmapped_code not in message
    assert _unmapped_code not in errors._GENERIC_ERROR_MESSAGE

    # Content-type is the declared JSON media type, so an SDK parses the body as an error.
    assert ("content-type", "application/json") in rendered.headers

    # Serialising the parsed object back confirms the body is self-consistent JSON (a raw
    # framework traceback would not parse / round-trip as an {"error": {...}} object).
    reserialised = json.loads(rendered.body.decode("utf-8"))
    assert reserialised == parsed


def test_p46_nonstream_block_emits_declared_error_envelope_not_raw_error() -> None:
    """The non-stream path withholds on BLOCK and emits a declared envelope (R9.2).

    Drives ``ChatSurface.collect_and_render`` end to end with a provider that streams a
    canary and a resolver that resolves it to BLOCK. The output guard raises
    ``OutputBlocked``; ``collect_and_enforce`` maps it to the declared block value-code and
    ``collect_and_render`` renders it through the SINGLE ``emit_response(send,
    errors.render_http(...))`` seam. The test asserts the emitted ASGI response is a
    declared ``Error_Envelope`` HTTP response — a parseable ``{"error": {...}}`` JSON body
    with a declared status — NOT a raw framework error, NOT the raw upstream JSON, and that
    the canary never reaches the client (withheld, R8.3/R9.3). This proves the non-stream
    reply leaves through the single generic-safe render path (R9.2).
    """
    import asyncio
    import json
    import random
    from collections.abc import Mapping, Sequence
    from dataclasses import dataclass

    from gateway_v2.dispatch.provider import StubProviderClient, UpstreamEvent
    from gateway_v2.domain.category import Category
    from gateway_v2.domain.decision import Decision, Disposition, FindingDisposition
    from gateway_v2.domain.finding import Finding, FindingStatus
    from gateway_v2.domain.plan import ExecutionPlan, StreamingMode, Surface
    from gateway_v2.edge import errors
    from gateway_v2.edge.openai_chat import REFUSE_OUTPUT_BLOCKED, ChatSurface
    from gateway_v2.edge.wire.openai import CanonicalRequest, OpenAIResponseRenderer

    _P46_CANARY = "CANARY-BLOCK-9c21"

    def _p46_block_detector(text: str) -> Sequence[Finding]:
        """Emit one EXECUTED finding when the canary is present (drives the BLOCK)."""
        if _P46_CANARY not in text:
            return ()
        return (
            Finding(
                detector="p46-block",
                detector_version="1",
                category=Category.PII,
                status=FindingStatus.EXECUTED,
                confidence=0.99,
                spans=(),
                evidence=None,
            ),
        )

    class _P46BlockResolver:
        """Resolves any present finding to BLOCK (the C22 most-restrictive stand-in)."""

        __slots__ = ()

        def decide(self, matches: Sequence[Finding]) -> Decision:
            if not matches:
                return Decision(
                    disposition=Disposition.ALLOW,
                    per_finding=(),
                    transformations=(),
                    findings=(),
                    plan_version="p46",
                    deciding_rules=(),
                    unavailable_detectors=(),
                )
            per = tuple(
                FindingDisposition(detector=f.detector, disposition=Disposition.BLOCK)
                for f in matches
            )
            return Decision(
                disposition=Disposition.BLOCK,
                per_finding=per,
                transformations=(),
                findings=tuple(matches),
                plan_version="p46",
                deciding_rules=tuple(fd.detector for fd in per),
                unavailable_detectors=(),
            )

    @dataclass(frozen=True, slots=True)
    class _P46Deps:
        """The ``ChatRoute``-shaped bundle the non-stream OUTPUT path reads from.

        ``collect_and_render`` → ``collect_and_enforce`` consults exactly ``provider``
        (drained to the final event), ``error_scanner`` (the output findings producer),
        ``resolver`` (the C22 decision), and ``renderer`` (the clean-path JSON). A
        duck-typed stand-in for the frozen ``ChatRoute`` so the test drives the output
        enforcement + single-render seam without the full boot graph.
        """

        provider: StubProviderClient
        error_scanner: object
        resolver: _P46BlockResolver
        renderer: OpenAIResponseRenderer

    # A collected ASGI ``send`` sink: records the response-start + body events so the test
    # can inspect the emitted HTTP response (a raw framework error would not take this shape).
    _p46_events: list[Mapping[str, object]] = []

    async def _p46_send(event: Mapping[str, object]) -> None:
        _p46_events.append(event)

    req = CanonicalRequest(
        model="gpt-test",
        streaming=False,
        input_channels=(("message_content", "hi"),),
        text_deltas=(("message_content", "hi"),),
        surface=Surface.CHAT,
    )

    # The provider streams the canary on the output-content channel + a benign tail, so the
    # output scan sees it and the resolver blocks. ``final`` marks the terminal event.
    script = (
        UpstreamEvent(text_deltas=(("output_content", _P46_CANARY),), final=False),
        UpstreamEvent(text_deltas=(("output_content", " and more"),), final=True),
    )
    provider = StubProviderClient(script=script, rng=random.Random(0x4600_4600))
    deps = _P46Deps(
        provider=provider,
        error_scanner=_p46_block_detector,
        resolver=_P46BlockResolver(),
        renderer=OpenAIResponseRenderer(),
    )
    surface = ChatSurface(deps=deps)  # type: ignore[arg-type]

    # A resolvable plan: ``collect_and_render`` refuses on the BLOCK before the Produced_Mode
    # stamp, but a real plan keeps the call type-correct (the injected resolver carries the
    # enforcement; the plan's ``streaming_mode`` only feeds the success-path header, GW13 C5).
    plan = ExecutionPlan(
        org_id="p46-org",
        epoch=1,
        sequence=1,
        content_hash="p46" + "0" * 61,
        compiled_at=0.0,
        rules=(),
        required_detectors=frozenset(),
        streaming_mode=StreamingMode.INCREMENTAL,
        integrity_locked=False,
    )
    asyncio.run(
        surface.collect_and_render(
            req,
            plan=plan,
            destination="dest",
            body={"model": "gpt-test"},
            send=_p46_send,
        )
    )

    # Exactly the two-event HTTP shape ``emit_response`` writes: a response-start then a body.
    assert [e["type"] for e in _p46_events] == [
        "http.response.start",
        "http.response.body",
    ]
    start, body_event = _p46_events

    # The declared block value-code maps through the Error_Envelope to its declared status
    # (403), proving the reply went through ``errors.render_http`` — the single path.
    expected = errors.render_http(REFUSE_OUTPUT_BLOCKED)
    assert start["status"] == expected.status == 403

    # The body is a declared, parseable ``{"error": {...}}`` envelope — NOT a raw framework
    # error and NOT the raw upstream JSON. A ``json.loads`` of a traceback string would fail.
    raw_body = body_event["body"]
    assert isinstance(raw_body, bytes)
    parsed = json.loads(raw_body.decode("utf-8"))
    assert isinstance(parsed, dict)
    assert isinstance(parsed["error"], dict)
    assert parsed["error"]["code"] == REFUSE_OUTPUT_BLOCKED

    # The canary is WITHHELD: it reaches the client zero times (R8.3/R9.3) — neither in the
    # emitted body nor anywhere in the ASGI event stream.
    assert _P46_CANARY.encode("utf-8") not in raw_body
    for event in _p46_events:
        payload = event.get("body")
        if isinstance(payload, bytes):
            assert _P46_CANARY.encode("utf-8") not in payload


def test_p46_nonstream_scan_failure_renders_declared_envelope_not_raw_error() -> None:
    """An uncomputable output decision renders the declared envelope, not the raise (R9.2).

    Drives ``ChatSurface.collect_and_render`` with a resolver that RAISES — an uncomputable
    output decision. ``collect_and_enforce`` fails CLOSED, mapping the raise to the declared
    scan-failure value-code, and ``collect_and_render`` renders it through the single
    ``emit_response(send, errors.render_http(...))`` seam. The emitted body is the declared
    ``Error_Envelope`` for that code — a parseable ``{"error": {...}}`` object byte-identical
    to ``errors.render_http(REFUSE_INPUT_SCAN_FAILURE)`` — and the raw ``RuntimeError`` text
    NEVER leaks into the response. Because every refusal (mapped or unmapped) leaves through
    this one generic-safe seam, an unmapped code could not leak a framework error either.
    """
    import asyncio
    import json
    import random
    from collections.abc import Mapping, Sequence
    from dataclasses import dataclass

    from gateway_v2.dispatch.provider import StubProviderClient, UpstreamEvent
    from gateway_v2.domain.decision import Decision
    from gateway_v2.domain.finding import Finding
    from gateway_v2.domain.plan import ExecutionPlan, StreamingMode, Surface
    from gateway_v2.edge import errors
    from gateway_v2.edge.openai_chat import REFUSE_INPUT_SCAN_FAILURE, ChatSurface
    from gateway_v2.edge.wire.openai import CanonicalRequest, OpenAIResponseRenderer

    _P46_RAISE_TEXT = "resolver blew up — must fail closed, not leak the raise"

    class _P46RaisingResolver:
        """A resolver that RAISES — an uncomputable output decision (fail-closed, R8.4)."""

        __slots__ = ()

        def decide(self, matches: Sequence[Finding]) -> Decision:
            del matches
            raise RuntimeError(_P46_RAISE_TEXT)

    def _p46_noop_detector(text: str) -> Sequence[Finding]:
        del text
        return ()

    @dataclass(frozen=True, slots=True)
    class _P46Deps2:
        provider: StubProviderClient
        error_scanner: object
        resolver: _P46RaisingResolver
        renderer: OpenAIResponseRenderer

    _p46_events2: list[Mapping[str, object]] = []

    async def _p46_send2(event: Mapping[str, object]) -> None:
        _p46_events2.append(event)

    req = CanonicalRequest(
        model="gpt-test",
        streaming=False,
        input_channels=(("message_content", "hi"),),
        text_deltas=(("message_content", "hi"),),
        surface=Surface.CHAT,
    )
    script = (UpstreamEvent(text_deltas=(("output_content", "payload"),), final=True),)
    provider = StubProviderClient(script=script, rng=random.Random(0x4601_4601))
    deps = _P46Deps2(
        provider=provider,
        error_scanner=_p46_noop_detector,
        resolver=_P46RaisingResolver(),
        renderer=OpenAIResponseRenderer(),
    )
    surface = ChatSurface(deps=deps)  # type: ignore[arg-type]

    # A resolvable plan: ``collect_and_render`` refuses on the scan-failure before the
    # Produced_Mode stamp, but a real plan keeps the call type-correct (GW13 C5).
    plan = ExecutionPlan(
        org_id="p46-org",
        epoch=1,
        sequence=1,
        content_hash="p46" + "0" * 61,
        compiled_at=0.0,
        rules=(),
        required_detectors=frozenset(),
        streaming_mode=StreamingMode.INCREMENTAL,
        integrity_locked=False,
    )
    asyncio.run(
        surface.collect_and_render(
            req,
            plan=plan,
            destination="dest",
            body={"model": "gpt-test"},
            send=_p46_send2,
        )
    )

    # The two-event HTTP shape the single ``emit_response`` seam writes.
    assert [e["type"] for e in _p46_events2] == [
        "http.response.start",
        "http.response.body",
    ]
    start, body_event = _p46_events2

    # The emitted response is byte-identical to the declared envelope for the refusal code,
    # proving the refusal was rendered through the single ``errors.render_http`` path (R9.2).
    expected = errors.render_http(REFUSE_INPUT_SCAN_FAILURE)
    assert start["status"] == expected.status
    raw_body = body_event["body"]
    assert isinstance(raw_body, bytes)
    assert raw_body == expected.body

    # The body is a declared, parseable ``{"error": {...}}`` object — never the raw
    # ``RuntimeError`` text the resolver raised.
    parsed = json.loads(raw_body.decode("utf-8"))
    assert isinstance(parsed["error"], dict)
    assert parsed["error"]["code"] == REFUSE_INPUT_SCAN_FAILURE
    assert _P46_RAISE_TEXT.encode("utf-8") not in raw_body
    assert b"RuntimeError" not in raw_body
