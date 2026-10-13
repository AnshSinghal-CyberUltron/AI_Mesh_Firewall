# Feature: chat-surface-gw15, Property 15: Clean streaming ends with exactly one terminal [DONE]
# Validates: Requirements 10.1
"""Property 15 — a cleanly completing stream ends with exactly one terminal ``[DONE]`` (task 5.4).

House idiom: a single seeded ``random.Random`` driven for ``>= 10,000`` iterations, NO
``hypothesis``; the async streaming orchestration runs through ``asyncio.run``.

*For any* cleanly completing streaming response, the Chat_Surface's shipped streaming
orchestration (:meth:`~gateway_v2.edge.routes.ChatRoute._stream_response` →
``_stream`` → ``_finish_stream``, driving the shipped
:class:`~gateway_v2.egress.stream.StreamPipeline` and
:class:`~gateway_v2.edge.wire.sse.SSEEncoder`) emits EXACTLY ONE terminal
``data: [DONE]`` marker (:data:`~gateway_v2.edge.wire.sse.DONE_MARKER`) and emits NO SSE
``data:`` frame after it (R10.1).

The orchestration is exercised end to end against the real shipped seams — the only injected
substitute is the :class:`~gateway_v2.dispatch.provider.StubProviderClient` standing in for a
real socket (R8.5), scripted each iteration with a RANDOM clean content stream (a random number
of content deltas across a random number of upstream events, the LAST event carrying
``final=True``). Everything else is the production object: the real holdback
:func:`~gateway_v2.detect.holdback.scan` + :class:`~gateway_v2.detect.holdback.TokenIndex`, the
:class:`~gateway_v2.egress.output_guard.MinimalOutputResolver`, a resolvable pass-through
:class:`~gateway_v2.domain.plan.ExecutionPlan`, the real :class:`~gateway_v2.edge.cancel.
InFlightControl` over a fresh/available snapshot (nothing fires), the real
:class:`~gateway_v2.edge.stream_timeouts.StreamTimeouts` under a monotone clock that never
breaches, and the single terminal ``_finish_stream`` site.

The invariant checked each iteration, over the captured ASGI ``send`` events, is threefold:

1. the terminal ``DONE_MARKER`` appears EXACTLY ONCE across every emitted SSE ``data:`` frame;
2. that terminal marker is the LAST SSE ``data:`` frame — no content frame and no error frame
   follows it on the wire (the ``SSEEncoder`` one-way "no content after a terminal" contract and
   the handler's single terminal site, R10.1); and
3. no declared SSE ``Error_Frame`` is emitted at all (a clean completion carries no error code).

Everything this test owns is uniquely prefixed ``_p15_`` / ``_P15`` / ``P15_`` so tasks 5.5 and
5.6 (Property 16 / Property 17) can add their own sections to this same shared
``test_lgw15_stream.py`` file without clashing.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Mapping
from typing import cast

from gateway_v2.detect.holdback import scan
from gateway_v2.dispatch.provider import StubProviderClient, UpstreamEvent
from gateway_v2.dispatch.routing import DispatchRouter
from gateway_v2.domain import Finding
from gateway_v2.domain.plan import ExecutionPlan, StreamingMode
from gateway_v2.edge.cancel import InFlightSnapshot
from gateway_v2.edge.executor import ScanExecutor
from gateway_v2.edge.routes import ChatRoute
from gateway_v2.edge.wire.sse import DONE_MARKER
from gateway_v2.egress.output_guard import MinimalOutputResolver
from gateway_v2.egress.stream import ScanProtocol
from gateway_v2.runtime.holdback_config import HoldbackConfig
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

_SEED = 0x15_15_0015
_ITERATIONS = 10_000

#: The plan-derived destination is resolved from the plan by ``DispatchRouter.select``; the stub
#: provider replays its script regardless of destination, so the handler drives a clean stream.
_P15_CHAT_PATH = "/v1/chat/completions"

#: A tiny corpus of clean content deltas. None carries a secret / pattern that the real holdback
#: scanner would hold or redact, so a pass-through plan releases every byte and the stream
#: completes cleanly — exactly the Property 15 precondition ("a cleanly completing stream").
_P15_DELTAS: tuple[str, ...] = (
    "Hello",
    " world",
    "!",
    " The answer",
    " is 42",
    " café",  # non-ASCII: forces the SSE encoder's \\uXXXX escape path on the wire
    " naïve",
    " done.",
)

#: A large per-worker RSS + CPU quota so the derived SCANNER pool has real workers and every
#: streaming bound (window, holdback cap, buffer high-water, timeouts, max duration) is generous
#: enough that a clean stream never trips a cut. These are TEST signals on a fake contract, not
#: production capacity literals.
_P15_PER_WORKER_RSS = 400 * 1024 * 1024


def _p15_ctx(i: int, **extra: object) -> str:
    """A reproducible failure context string (seed + iteration + relevant values)."""
    body = " ".join(f"{k}={v!r}" for k, v in extra.items())
    return f"seed={_SEED:#x} iter={i} {body}"


def _p15_contract() -> ResourceContract:
    """A serviceable contract fixture (the LGW12 ``from_signals`` idiom).

    ``target_p99_ms`` is kept comfortably large so the derived ``max_stream_duration_s`` and the
    inter-chunk / idle / write timeouts never fire during a clean replay; the monotone test clock
    (below) never advances, so no C27 timeout and no R10 max-duration cut can trip. The SCANNER
    pool is ``floor(cpu_quota * utilization_cap) = floor(4.0 * 0.75) = 3`` workers.
    """
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=_P15_PER_WORKER_RSS * 8,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals,
        target_p99_ms=20.0,
        utilization_cap=0.75,
        per_worker_rss=_P15_PER_WORKER_RSS,
    )


def _p15_plan() -> ExecutionPlan:
    """A resolvable pass-through :class:`ExecutionPlan` (no rules → no output enforcement).

    ``StreamPipeline.for_plan`` resolves the enforcing-output set from
    ``enforcing_output_rules(plan)``; an empty rule set means pass-through, so every released byte
    reaches the wire and the stream completes cleanly on the ``final`` chunk — the Property 15
    precondition. The plan still resolves (it is a real ``ExecutionPlan``, not an unresolved
    marker), so ``for_plan`` never raises ``EnforcementUndetermined``.
    """
    return ExecutionPlan(
        org_id="p15-org",
        epoch=1,
        sequence=1,
        content_hash="p15" + "0" * 61,
        compiled_at=0.0,
        rules=(),
        required_detectors=frozenset(),
        streaming_mode=StreamingMode.INCREMENTAL,
        integrity_locked=False,
    )


def _p15_fresh_snapshot() -> InFlightSnapshot:
    """A fresh, available in-flight snapshot with every cut signal clear (nothing fires).

    The real :class:`~gateway_v2.edge.cancel.InFlightControl` polls this at each chunk boundary;
    an available, zero-age snapshot with the kill switch / revocation / plan-change all clear
    leaves the stream running, so no in-flight cut can shorten a clean completion.
    """
    return InFlightSnapshot(
        available=True,
        age_s=0.0,
        kill_switch_engaged=False,
        key_revoked=False,
        plan_changed=False,
    )


class _P15FreshControlSource:
    """An :class:`~gateway_v2.edge.cancel.InFlightControlSource` returning a fresh snapshot."""

    __slots__ = ()

    def snapshot(self) -> InFlightSnapshot:
        return _p15_fresh_snapshot()


def _p15_no_findings(_text: str) -> tuple[Finding, ...]:
    """An error-frame scanner that finds nothing (a clean stream carries no error frame)."""
    return ()


def _p15_clock() -> float:
    """A frozen monotone seconds clock: it never advances, so no timeout / max-duration fires."""
    return 0.0


def _p15_route(provider: StubProviderClient) -> ChatRoute:
    """Build a real :class:`ChatRoute` wired to production seams, with ``provider`` injected.

    Every field but ``provider`` is a production object: the real holdback ``scan`` + ``TokenIndex``
    + ``max_pattern_length`` window (threaded by the handler), the ``MinimalOutputResolver``, the
    real ``InFlightControl`` source (fresh snapshot), the real ``StreamTimeouts`` under the frozen
    clock, and the real ``ScanExecutor``. The scripted :class:`StubProviderClient` is the only
    injected substitute for a real socket (R8.5), so each iteration drives a fresh clean stream.
    """
    contract = _p15_contract()
    return ChatRoute(
        contract=contract,
        provider=provider,
        scanner=cast(ScanProtocol, scan),
        resolver=MinimalOutputResolver(),
        cfg=HoldbackConfig(hold_cap_tokens=3, relaxed_ceiling_tokens=6),
        router=DispatchRouter(),
        active_streams=lambda: 1,
        control=_P15FreshControlSource(),
        clock=_p15_clock,
        error_scanner=_p15_no_findings,
        executor=ScanExecutor(contract=contract),
    )


def _p15_script(rng: random.Random) -> tuple[UpstreamEvent, ...]:
    """A RANDOM clean content stream whose LAST event carries ``final=True``.

    A random number of upstream events (1..5), each carrying a random number of content deltas
    (0..3) drawn from the clean corpus, with the final event flagged. No ``error_frame`` is ever
    set, so the stream completes cleanly and the orchestration emits the terminal ``[DONE]``.
    """
    event_count = rng.randint(1, 5)
    events: list[UpstreamEvent] = []
    for position in range(event_count):
        delta_count = rng.randint(0, 3)
        deltas = tuple(
            ("content", rng.choice(_P15_DELTAS)) for _ in range(delta_count)
        )
        events.append(
            UpstreamEvent(text_deltas=deltas, final=position == event_count - 1)
        )
    return tuple(events)


class _P15Capture:
    """Capture the SSE ``data:`` frame bodies written onto the ASGI ``send`` channel, in order.

    The handler opens the SSE response (``http.response.start``), writes each content / terminal
    frame as a non-terminal ``http.response.body`` event, and closes with an empty terminal body
    event. Only the non-empty ``http.response.body`` payloads are the SSE frames Property 15
    reasons about; the empty terminal close (``more_body=False``, ``body == b""``) is the ASGI
    stream close, not an SSE ``data:`` frame, so it is excluded.
    """

    __slots__ = ("frames", "started")

    def __init__(self) -> None:
        self.frames: list[bytes] = []
        self.started = False

    async def send(self, event: Mapping[str, object]) -> None:
        kind = event.get("type")
        if kind == "http.response.start":
            self.started = True
            return
        if kind != "http.response.body":
            return
        body = event.get("body")
        if isinstance(body, (bytes, bytearray)) and body:
            self.frames.append(bytes(body))


async def _p15_drive(route: ChatRoute, capture: _P15Capture) -> None:
    """Drive the shipped streaming orchestration for one clean stream via ``_stream_response``."""
    plan = _p15_plan()
    destination = route.router.select(plan)
    await route._stream_response(capture.send, plan, destination, body={})


def _p15_run_once(rng: random.Random) -> list[bytes]:
    """Replay one random clean stream end to end; return the captured SSE ``data:`` frames."""
    provider = StubProviderClient(script=_p15_script(rng), rng=rng)
    route = _p15_route(provider)
    capture = _P15Capture()
    try:
        asyncio.run(_p15_drive(route, capture))
    finally:
        route.executor.shutdown()
    assert capture.started, "the streaming handler never opened the SSE response"
    return capture.frames


def _p15_count_terminal(frames: list[bytes]) -> int:
    """How many captured SSE frames are the terminal ``DONE_MARKER`` (expected exactly one)."""
    return sum(1 for frame in frames if frame == DONE_MARKER)


def test_property_15_clean_streaming_ends_with_exactly_one_terminal_done() -> None:
    rng = random.Random(_SEED)

    for i in range(_ITERATIONS):
        frames = _p15_run_once(rng)

        # (1) Exactly ONE terminal [DONE] marker across every emitted SSE data: frame (R10.1).
        terminal_count = _p15_count_terminal(frames)
        assert terminal_count == 1, _p15_ctx(
            i,
            note="a cleanly completing stream did not emit exactly one terminal [DONE]",
            terminal_count=terminal_count,
            frame_count=len(frames),
        )

        # (2) The terminal [DONE] is the LAST SSE data: frame — no frame follows it (R10.1).
        assert frames[-1] == DONE_MARKER, _p15_ctx(
            i,
            note="a frame was emitted after the terminal [DONE] marker",
            last_frame=frames[-1],
            frame_count=len(frames),
        )

        # (3) No declared SSE Error_Frame is emitted on a clean completion. The encoder renders an
        # error as a `data: {"error": ...}` frame; a clean stream carries none, so no captured
        # content frame is an error body.
        for position, frame in enumerate(frames[:-1]):
            assert b'"error"' not in frame, _p15_ctx(
                i,
                note="a clean completion emitted an SSE Error_Frame",
                position=position,
                frame=frame,
            )


# ===========================================================================
# Property 17 — No-splice invariant after the first content byte (task 5.6).
# ===========================================================================
# Feature: chat-surface-gw15, Property 17: No-splice invariant after the first content byte
# Validates: Requirements 10.3
#
# A fallback/retry attempt is permitted ONLY while the ``FirstByteLatch`` is unset; once the
# first content byte of one upstream response has been released downstream, NO second upstream
# response is spliced in — the client-visible bytes carry exactly one upstream response.
#
# This section is self-contained with uniquely-prefixed symbols (``_p17_`` / ``_P17_``) and
# section-local (in-function + section-local helper) imports, so it coexists with the Property
# 15 / Property 16 sections above without colliding.
#
# Driver under test (the real shipped seam): ``edge/stream_control.run_with_no_splice``
# takes a one-way ``FirstByteLatch`` and an ``attempts()`` factory yielding the next signed-safe
# attempt or ``None`` when exhausted. Each attempt here opens a REAL ``StubProviderClient``
# (the in-process ``ProviderClient`` the ``ChatRoute`` injects) and forwards its scripted
# ``UpstreamEvent`` content bytes downstream through a sink that drives the latch — so the
# no-splice invariant is checked against the actual provider the orchestration opens, not a mock
# of the driver. The attempt kinds are: fail-before-first-byte (byte-less, retry-eligible while
# the latch is unset, R10.3), release-then-clean, and release-then-fail (a post-first-byte
# failure that must propagate / terminate, never splice).


def test_property_17_no_splice_after_first_content_byte() -> None:
    import asyncio
    import logging
    from collections.abc import Awaitable

    from gateway_v2.dispatch.provider import (
        StubProviderClient,
        UpstreamEvent,
        UpstreamRequest,
    )
    from gateway_v2.edge.stream_control import (
        FirstByteLatch,
        StreamAttempt,
        run_with_no_splice,
    )

    _LOG = logging.getLogger(__name__)
    _SEED = 0x17_15_0506
    _ITERATIONS = 10_000

    # The upstream channel the stub streams its content deltas on.
    _P17_CHANNEL = "assistant.content"

    # The attempt kinds the sweep draws from. ``fail_before_byte`` produces NO content before
    # failing (retry-eligible while the latch is unset, R10.3); the two ``release_*`` kinds
    # release >= 1 content byte and therefore MUST close the no-splice gate.
    _P17_FAIL_BEFORE_BYTE = "fail_before_byte"
    _P17_RELEASE_THEN_CLEAN = "release_then_clean"
    _P17_RELEASE_THEN_FAIL = "release_then_fail"
    _P17_KINDS: tuple[str, ...] = (
        _P17_FAIL_BEFORE_BYTE,
        _P17_RELEASE_THEN_CLEAN,
        _P17_RELEASE_THEN_FAIL,
    )
    _P17_RELEASING_KINDS = frozenset({_P17_RELEASE_THEN_CLEAN, _P17_RELEASE_THEN_FAIL})

    class _P17UpstreamFailure(RuntimeError):
        """Models an upstream (provider) failure for one attempt.

        Raised AFTER the first byte by ``release_then_fail`` (must propagate, never splice) and
        BEFORE any byte by ``fail_before_byte`` (a byte-less failure the driver may follow with a
        retry while the latch is still unset).
        """

    def _p17_run(coro: Awaitable[bool]) -> bool:
        return asyncio.run(coro)  # type: ignore[arg-type]

    def _p17_random_payload(rng: random.Random) -> str:
        alphabet = "abcdefghijklmnopqrstuvwxyz0123456789 "
        return "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 12)))

    def _p17_script(rng: random.Random, payload: str) -> tuple[UpstreamEvent, ...]:
        """Fragment ``payload`` into a scripted ``UpstreamEvent`` stream (last ``final=True``)."""
        if not payload:
            return (UpstreamEvent(text_deltas=(), final=True),)
        parts = max(1, min(len(payload), rng.randint(1, 3)))
        bounds = sorted(rng.sample(range(1, len(payload)), parts - 1)) if parts > 1 else []
        cuts = [0, *bounds, len(payload)]
        fragments = [payload[a:b] for a, b in zip(cuts, cuts[1:], strict=False)]
        return tuple(
            UpstreamEvent(
                text_deltas=((_P17_CHANNEL, frag),),
                final=(n == len(fragments) - 1),
            )
            for n, frag in enumerate(fragments)
        )

    class _P17Sink:
        """Records client-visible bytes per upstream and drives the latch on the first byte."""

        __slots__ = ("latch", "downstream")

        def __init__(self, latch: FirstByteLatch) -> None:
            self.latch = latch
            self.downstream: list[tuple[int, int]] = []

        def release(self, attempt_index: int, byte: int) -> None:
            self.latch.set_on_release()
            self.downstream.append((attempt_index, byte))

    def _p17_make_attempt(
        *, kind: str, attempt_index: int, provider: StubProviderClient, sink: _P17Sink
    ) -> StreamAttempt:
        async def attempt() -> bool:
            released_any = False
            request = UpstreamRequest(destination="p17", body={})
            async for event in provider.open(request):
                for _channel, delta in event.text_deltas:
                    for byte in delta.encode("utf-8"):
                        sink.release(attempt_index, byte)
                        released_any = True
            if kind == _P17_RELEASE_THEN_FAIL:
                raise _P17UpstreamFailure(f"upstream {attempt_index} failed after first byte")
            if kind == _P17_FAIL_BEFORE_BYTE:
                raise _P17UpstreamFailure(f"upstream {attempt_index} failed before any byte")
            return released_any

        return attempt

    def _p17_random_plan(rng: random.Random) -> tuple[str, ...]:
        return tuple(rng.choice(_P17_KINDS) for _ in range(rng.randint(0, 6)))

    _LOG.info("Property 17 no-splice-after-first-byte seed=%#x", _SEED)
    rng = random.Random(_SEED)

    for i in range(_ITERATIONS):
        plan = _p17_random_plan(rng)
        latch = FirstByteLatch()
        sink = _P17Sink(latch)

        payloads: list[str] = [
            _p17_random_payload(rng) if kind in _P17_RELEASING_KINDS else ""
            for kind in plan
        ]
        providers: list[StubProviderClient] = [
            StubProviderClient(
                script=_p17_script(rng, payloads[k]),
                rng=random.Random(rng.random()),
            )
            for k in range(len(plan))
        ]

        # The first upstream that would release a content byte — the ONLY response whose bytes
        # may legitimately reach the client (R10.3).
        first_releasing: int | None = next(
            (k for k, kind in enumerate(plan) if kind in _P17_RELEASING_KINDS),
            None,
        )

        handed_out: list[int] = []

        def _factory() -> StreamAttempt | None:
            idx = len(handed_out)
            if idx >= len(plan):
                return None
            handed_out.append(idx)
            return _p17_make_attempt(
                kind=plan[idx],
                attempt_index=idx,
                provider=providers[idx],
                sink=sink,
            )

        raised: _P17UpstreamFailure | None = None
        try:
            released: bool | None = _p17_run(run_with_no_splice(latch, _factory))
        except _P17UpstreamFailure as exc:  # a post-first-byte failure propagates (R10.3)
            raised = exc
            released = None

        ctx = f"i={i} seed={_SEED:#x} plan={plan!r}"

        # ---- A post-first-byte failure MUST propagate, never splice. --------- #
        expect_raise = (
            first_releasing is not None and plan[first_releasing] == _P17_RELEASE_THEN_FAIL
        )
        if expect_raise:
            assert raised is not None, (
                f"{ctx}: a post-first-byte upstream failure was swallowed "
                f"(expected propagation, R10.3)"
            )
        else:
            assert raised is None, f"{ctx}: unexpected upstream failure propagated: {raised!r}"

        # ---- No-splice: all client-visible bytes came from ONE upstream. ----- #
        source_indices = {idx for idx, _ in sink.downstream}
        assert len(source_indices) <= 1, (
            f"{ctx}: client-visible bytes carried from MULTIPLE upstreams "
            f"{sorted(source_indices)!r} — a splice (R10.3)"
        )

        if first_releasing is None:
            # No upstream ever releases a byte: downstream empty, latch unset, driver returned
            # False, and EVERY byte-less upstream was retried in turn (a fallback runs only while
            # the latch is unset, R10.3).
            assert sink.downstream == [], (
                f"{ctx}: bytes released with no releasing upstream: {sink.downstream!r}"
            )
            assert not latch.is_set(), f"{ctx}: latch set with no byte released"
            assert released is False, (
                f"{ctx}: driver returned {released!r}, want False when nothing was released"
            )
            assert handed_out == list(range(len(plan))), (
                f"{ctx}: byte-less upstreams were not all retried pre-first-byte: "
                f"handed_out={handed_out!r} (R10.3)"
            )
            continue

        # ---- A byte WAS released: it is exactly the first releasing upstream. - #
        released_bytes = bytes(byte for _, byte in sink.downstream)
        expected_single = payloads[first_releasing].encode("utf-8")
        assert source_indices == {first_releasing}, (
            f"{ctx}: client bytes came from upstream {sorted(source_indices)!r}, "
            f"want only {first_releasing}"
        )
        assert released_bytes == expected_single, (
            f"{ctx}: client-visible bytes {released_bytes!r} are not exactly the single upstream "
            f"response {expected_single!r} — a splice (R10.3)"
        )

        # ---- The latch gates further attempts after it is set. --------------- #
        assert handed_out == list(range(first_releasing + 1)), (
            f"{ctx}: factory handed out {handed_out!r}, want exactly "
            f"{list(range(first_releasing + 1))!r} — a second upstream was attempted after the "
            f"first byte (splice, R10.3)"
        )
        assert latch.is_set(), f"{ctx}: latch not set after a byte released"

        if not expect_raise:
            assert released is True, (
                f"{ctx}: driver returned {released!r}, want True after a byte was released"
            )


# ===========================================================================
# Property 16 — Tool-call fragment reassembly round-trip (task 5.5).
# ===========================================================================
# Feature: chat-surface-gw15, Property 16: Tool-call fragment reassembly round-trip
# Validates: Requirements 10.2
#
# *For any* tool-call ``function.arguments`` string and *any* fragmentation of it across
# upstream deltas, the concatenation of the forwarded fragments reconstructs the IDENTICAL
# ``arguments`` string, so the official SDK rebuilds the exact tool call.
#
# Transport under test (the real shipped path, no mock of the codec):
#
#   fragmented ``function.arguments``
#     -> ride the ``text_deltas`` channel on a scripted ``UpstreamEvent`` (the shape the
#        ``StubProviderClient`` yields and the egress loop reads)
#     -> ``Transform.from_provider(event)`` shapes each event into a ``DownstreamFrame`` carrying
#        the SAME ``text_deltas`` unchanged (content pass-through)
#     -> ``SSEEncoder.content(frame)`` serialises each frame to the SDK-parseable
#        ``data: {json}\n\n`` wire bytes (the tool-arguments channel maps to a non-``content``
#        delta key verbatim), terminated by one ``done()`` ``[DONE]`` marker
#     -> ``SSEDecoder.feed`` decodes the concatenated wire back into ``DownstreamFrame``s whose
#        per-channel deltas are byte-identical to what the encoder serialised
#
# The property asserts the concatenation of the forwarded (decoded) fragments on the
# tool-arguments channel equals the original ``arguments`` string, over any fragmentation —
# including empty fragments, boundaries that split JSON ``\uXXXX`` surrogate escapes
# (``ensure_ascii`` on the encoder forces astral code points onto the split-surrogate buffering
# path of the decoder), and arbitrary fed-chunk boundaries on the wire. Only the provider event
# is a scripted stub; ``Transform``, ``SSEEncoder``, and ``SSEDecoder`` are the REAL shipped
# components, so the reassembly is proven end to end.
#
# This section is self-contained with uniquely-prefixed symbols (``_p16_`` / ``_P16_``) and
# section-local (in-function) imports, so it coexists with the sibling sections above.


def test_property_16_tool_call_fragment_reassembly_round_trip() -> None:
    import random as _p16_random

    from gateway_v2.dispatch.provider import UpstreamEvent
    from gateway_v2.dispatch.transform import Transform
    from gateway_v2.edge.wire.sse import SSEDecoder, SSEEncoder
    from gateway_v2.egress.stream import DownstreamFrame

    _P16_SEED = 0x16_15_0505
    _P16_ITERATIONS = 10_000

    # The channel a tool call's ``function.arguments`` ride on. It is NOT the default ``content``
    # channel, so the encoder uses the channel name verbatim as the SDK delta key and the decoder
    # maps it back to the same channel name — the exact non-default round-trip under test.
    _P16_ARGS_CHANNEL = "output_tool_arguments"

    # A corpus of ``arguments``-string fragments that stress byte-for-byte reassembly: empties,
    # realistic JSON tool-call argument bodies, JSON-significant characters (quotes, backslashes,
    # braces, colons, commas), unicode/non-ASCII, and astral emoji (surrogate PAIR escapes on the
    # ``ensure_ascii`` wire -> the decoder's split-surrogate buffering path).
    _P16_FRAGMENT_CORPUS: tuple[str, ...] = (
        "",
        "{",
        '{"location":"Paris"}',
        '{"query":"SSN 123-45-6789","limit":10}',
        'escapes \\ " } { : ,',
        "café — naïve © Ω λ",
        "日本語の引数テキスト",
        "emoji 🙂🚀 end",
        "line1\nline2\ttab\r\n",
        "x" * 48,
    )

    def _p16_fragment(text: str, rng: _p16_random.Random) -> tuple[str, ...]:
        """Split ``text`` at arbitrary code-point boundaries; the concatenation is exactly ``text``.

        The number of cut points and their positions are random, so empty fragments and
        single-fragment splits both occur; an empty ``text`` yields a single empty fragment so at
        least one delta is always forwarded.
        """
        if not text:
            return ("",)
        cut_count = rng.randint(0, len(text))
        cuts = sorted(rng.randint(0, len(text)) for _ in range(cut_count))
        bounds = [0, *cuts, len(text)]
        return tuple(text[a:b] for a, b in zip(bounds[:-1], bounds[1:], strict=True))

    def _p16_feed_in_chunks(
        decoder: SSEDecoder, wire: bytes, rng: _p16_random.Random
    ) -> tuple[DownstreamFrame, ...]:
        """Feed ``wire`` into ``decoder`` in arbitrary byte-chunk boundaries, in order.

        The chunk boundaries are random (they may fall mid-event, mid-UTF-8, or mid-surrogate-
        escape), which is exactly what the decoder's buffering must survive (R1.4). Returns the
        frames decoded across all feeds, in order.
        """
        frames: list[DownstreamFrame] = []
        offset = 0
        size = len(wire)
        while offset < size:
            step = rng.randint(1, size - offset)
            frames.extend(decoder.feed(wire[offset : offset + step]))
            offset += step
        return tuple(frames)

    transform = Transform()
    rng = _p16_random.Random(_P16_SEED)

    for i in range(_P16_ITERATIONS):
        # Build an arbitrary ``arguments`` string from the corpus (0..4 pieces), then fragment it
        # at arbitrary cut points into an arbitrary number of deltas. Both the string AND its
        # fragmentation are drawn randomly, so "any arguments string, any fragmentation" is
        # exercised across the run (empty, single-fragment, and many-fragment cases included).
        arguments = "".join(
            rng.choice(_P16_FRAGMENT_CORPUS) for _ in range(rng.randint(0, 4))
        )
        fragments = _p16_fragment(arguments, rng)
        ctx = f"seed={_P16_SEED:#x} iter={i} arguments={arguments!r} fragments={fragments!r}"

        # Fragment -> UpstreamEvent -> Transform.from_provider -> DownstreamFrame -> encoder bytes.
        encoder = SSEEncoder()
        wire = bytearray()
        for position, fragment in enumerate(fragments):
            is_last = position == len(fragments) - 1
            event = UpstreamEvent(
                text_deltas=((_P16_ARGS_CHANNEL, fragment),),
                final=is_last,
            )
            frame = transform.from_provider(event)
            # The transform forwards the deltas unchanged (content pass-through, no error frame).
            assert frame.error_code is None, ctx
            assert frame.text_deltas == ((_P16_ARGS_CHANNEL, fragment),), ctx
            wire.extend(encoder.content(frame))
        wire.extend(encoder.done())

        # Decode the concatenated wire back, feeding it in arbitrary chunk boundaries so the
        # split-surrogate / split-multibyte buffering path is exercised; the decoder is confluent.
        decoder = SSEDecoder()
        decoded_frames = _p16_feed_in_chunks(decoder, bytes(wire), rng)
        assert not decoder.malformed(), ctx

        # Concatenate the forwarded fragments on the tool-arguments channel across every decoded
        # frame: the reassembled ``arguments`` must be byte-identical to the original (R10.2).
        reassembled = "".join(
            text
            for frame in decoded_frames
            for channel, text in frame.text_deltas
            if channel == _P16_ARGS_CHANNEL
        )
        assert reassembled == arguments, ctx
        # No stray content on any other channel leaked into the reassembly.
        assert all(
            channel == _P16_ARGS_CHANNEL
            for frame in decoded_frames
            for channel, _ in frame.text_deltas
        ), ctx
