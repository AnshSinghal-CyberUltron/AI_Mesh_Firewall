# Feature: sse-egress-pipeline
# Validates: Requirements 16.2
"""LGW12-7 LOCAL EQUIVALENT: in-process ASGI conformance harness (GW12, task 15.2).

This is the **LGW12-7 local equivalent** of the recorded-frame SSE conformance gate: it
drives the REAL ASGI application assembled by
:func:`gateway_v2.edge.app.build_app` over an **in-process ASGI transport** (no real
socket, no HTTP server) and does recorded-frame conformance checks on the streaming chat
response. The full real-TCP both-SDK conformance — replaying the gateway's stream through
the stock **OpenAI Python SDK** AND the **OpenAI Node SDK** against a live listening socket
— is a **DEFERRED cloud gate** (design: Deferred gates); it needs a real network listener
and both language runtimes, which this local harness deliberately does not stand up. What
the local equivalent proves is that the bytes the ASGI app emits onto the ``send`` channel
conform to the declared SSE wire shapes an SDK parses: the ``chat.completion.chunk`` content
frames, the terminal ``data: [DONE]`` marker, the declared ``Error_Frame`` shape, and the
split-surrogate decode confluence.

**How the in-process transport works.** A conforming ASGI server would, per connection,
build an ``http`` ``scope``, call the app's ``async def app(scope, receive, send)`` once,
feed request body + ``http.disconnect`` through ``receive``, and collect the app's
``http.response.start`` + ``http.response.body`` events through ``send``. This harness is
exactly that server, in-process: :class:`_Transport` holds a scripted ``receive`` queue and
a ``send`` sink that RECORDS every emitted ASGI event. ``asyncio.run`` drives one request to
completion, then the recorded events are asserted frame-by-frame. No socket is opened; the
app under test is the genuine ``build_app`` output with every dependency injected.

**What is recorded + asserted (LGW12-7 shape).**

1. ``test_response_starts_200_text_event_stream`` — the ``http.response.start`` carries
   status ``200`` and a ``content-type: text/event-stream`` header (R1 wire shape / R2.2).
2. ``test_content_frames_are_parseable_chat_completion_chunks`` — each content body frame is
   a ``data: {json}\\n\\n`` SSE frame whose JSON parses to the ``chat.completion.chunk``
   shape and carries the scripted deltas, in order (R1.1).
3. ``test_terminal_done_marker_is_last`` — the terminal ``data: [DONE]\\n\\n`` marker is the
   last SSE frame emitted, with no content after it (R1.2).
4. ``test_error_frame_path_blocks_mid_stream`` — the Error_Frame path: the provider is
   scripted to emit a mid-stream ``error_frame`` that the injected error scanner drives to a
   terminal BLOCK, and the recorded wire ends with the declared SSE ``Error_Frame`` shape
   (parseable ``{"error": {...}}``), with NO content frame after it (R11.3 / R1.3).
5. ``test_split_surrogate_decode_confluence`` — an astral-codepoint content frame fed to the
   shipped :class:`~gateway_v2.edge.wire.sse.SSEDecoder` split across EVERY chunk boundary
   (including a boundary that splits the JSON ``\\uXXXX`` surrogate-pair escape) decodes to
   the SAME, correct code points — the confluence idea (Property 9) checked at the
   conformance level (R1.4).

The content scanner injected into ``build_app`` is the shipped pure
``detect.holdback.scan`` (a no-findings holdback core — a test may inject it directly since
test files are outside the import-linter layer contract); the resolver is the shipped
:class:`~gateway_v2.egress.output_guard.MinimalOutputResolver`; the metrics registry is a
stub returning a text exposition; the clock is a monotonic in-process stand-in. The contract
comes from ``from_signals`` with the same fixture shape the other LGW12 edge tests use. No
``hypothesis``; async drive is ``asyncio.run`` (house idiom).
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from gateway_v2.detect.holdback import scan as holdback_scan
from gateway_v2.dispatch.provider import StubProviderClient, UpstreamEvent
from gateway_v2.domain import (
    Category,
    Finding,
    FindingStatus,
    Span,
)
from gateway_v2.edge.app import build_app
from gateway_v2.edge.wire.sse import (
    CHUNK_OBJECT,
    DONE_MARKER,
    SSEDecoder,
    SSEEncoder,
)
from gateway_v2.egress.output_guard import MinimalOutputResolver
from gateway_v2.egress.stream import DownstreamFrame
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

# --------------------------------------------------------------------------- #
# Fixtures: the real ASGI app with every dependency injected (no socket).
# --------------------------------------------------------------------------- #

_SEED = 0x1507
_CHAT_PATH = "/v1/chat/completions"


def _contract() -> ResourceContract:
    """A serviceable contract (same ``from_signals`` fixture shape as the other LGW12 tests).

    The serving bounds all derive from this contract — no capacity literal in the test. The
    ``floor(cpu_quota * utilization_cap)`` scanner pool is 3, matching the shipped
    ``test_lgw12_serving_loop`` fixture, so the C38 executor the app builds is real.
    """
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=400 * 1024 * 1024 * 8,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals,
        target_p99_ms=20.0,
        utilization_cap=0.75,
        per_worker_rss=400 * 1024 * 1024,
    )


class _StubMetrics:
    """A minimal :class:`~gateway_v2.edge.app.MetricsRegistry`: a fixed text exposition.

    ``build_app`` only reads ``exposition()`` for ``/metrics`` (off the serving loop); the
    chat path never touches it, so a constant body is all the conformance harness needs.
    """

    __slots__ = ()

    def exposition(self) -> str:
        return "# gateway_v2 metrics\n"


class _Clock:
    """A monotonic in-process clock stand-in (seconds), advanced by the harness, never real.

    The serving loop reads it for the max-stream-duration / timeout bounds. A conformance
    run completes in a handful of advances well inside any derived bound, so the stream never
    trips a duration cut and the recorded frames are the clean content + ``[DONE]`` path.
    """

    __slots__ = ("_t",)

    def __init__(self) -> None:
        self._t = 0.0

    def __call__(self) -> float:
        self._t += 1e-6
        return self._t


def _no_error_findings(_frame: str) -> Sequence[Finding]:
    """The default error-frame scanner: no findings → a mid-stream error frame forwards clean."""
    return ()


@dataclass(frozen=True, slots=True)
class _BlockingErrorScanner:
    """An error-frame scanner that finds a BLOCK-class match over the whole frame (R11.3).

    Produces ONE EXECUTED finding whose ``detector`` names a terminate-the-stream class
    (``card``), so :class:`MinimalOutputResolver` resolves it to a BLOCK disposition and the
    error-frame scan withholds the frame with ``output_blocked`` — the terminal code the
    handler renders as the declared SSE ``Error_Frame``. The span covers the whole frame so
    the finding is well-formed; the resolver blocks regardless of the span.
    """

    detector_class: str = "card"
    category: Category = field(default=Category.PII)

    def __call__(self, frame: str) -> Sequence[Finding]:
        return (
            Finding(
                detector=self.detector_class,
                detector_version="asgi-conformance-test",
                category=self.category,
                status=FindingStatus.EXECUTED,
                confidence=1.0,
                spans=(Span(start=0, end=len(frame)),),
                evidence=None,
            ),
        )


def _build(
    *,
    script: Sequence[UpstreamEvent],
    error_scanner: object | None = None,
) -> object:
    """Assemble the real ASGI app with a scripted in-process provider (no socket).

    The provider is the shipped :class:`StubProviderClient` (in-process, seeded RNG); the
    content scanner is the shipped pure ``detect.holdback.scan``; the resolver is the shipped
    :class:`MinimalOutputResolver`; metrics + clock are the local stubs above. ``build_app``
    returns the genuine ``async def app(scope, receive, send)`` callable the transport drives.
    """
    provider = StubProviderClient(script=tuple(script), rng=random.Random(_SEED))
    return build_app(
        contract=_contract(),
        provider=provider,
        scanner=holdback_scan,
        resolver=MinimalOutputResolver(),
        metrics_registry=_StubMetrics(),
        clock=_Clock(),
        error_scanner=error_scanner if error_scanner is not None else _no_error_findings,
    )


# --------------------------------------------------------------------------- #
# The in-process ASGI transport: scripted receive + recording send.
# --------------------------------------------------------------------------- #


class _Transport:
    """A one-connection in-process ASGI transport: no socket, records every send event.

    Mimics exactly what a conforming server does for one HTTP connection: hands the app an
    ``http`` scope, feeds a request-body event then ``http.disconnect`` through ``receive``,
    and collects the app's ``http.response.start`` + ``http.response.body`` events through
    ``send``. The recorded ``events`` list is the "wire" the conformance assertions read.
    """

    __slots__ = ("events", "_incoming")

    def __init__(self, *, body: bytes) -> None:
        self.events: list[Mapping[str, object]] = []
        # The request body, then a disconnect — the two events the app's receive channel
        # pulls. A conforming server delivers the body with more_body cleared, then signals
        # the client disconnect once the response has drained.
        self._incoming: list[Mapping[str, object]] = [
            {"type": "http.request", "body": body, "more_body": False},
            {"type": "http.disconnect"},
        ]

    @staticmethod
    def scope(path: str = _CHAT_PATH) -> dict[str, object]:
        """A minimal ASGI 3.0 ``http`` scope for a ``POST`` to the chat route."""
        return {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [(b"content-type", b"application/json")],
        }

    async def receive(self) -> Mapping[str, object]:
        """Pull the next scripted incoming event; after the script, hold at disconnect."""
        if self._incoming:
            return self._incoming.pop(0)
        return {"type": "http.disconnect"}

    async def send(self, event: Mapping[str, object]) -> None:
        """Record one emitted ASGI event (the recording side of the in-process transport)."""
        self.events.append(dict(event))

    # -- recorded-wire accessors --------------------------------------------- #

    def start_event(self) -> Mapping[str, object]:
        """The single ``http.response.start`` event (the response head)."""
        starts = [e for e in self.events if e.get("type") == "http.response.start"]
        assert len(starts) == 1, f"expected exactly one response.start, got {len(starts)}"
        return starts[0]

    def body_frames(self) -> list[bytes]:
        """Every non-empty ``http.response.body`` payload, in emission order (the SSE frames)."""
        frames: list[bytes] = []
        for event in self.events:
            if event.get("type") != "http.response.body":
                continue
            body = event.get("body")
            if isinstance(body, (bytes, bytearray)) and body:
                frames.append(bytes(body))
        return frames


def _drive(app: object, *, body: bytes = b"{}") -> _Transport:
    """Drive one in-process request through ``app`` and return the recording transport.

    ``asyncio.run`` runs the ASGI callable to completion against the scripted
    receive + recording send, exactly as a server would for one connection.
    """
    transport = _Transport(body=body)

    async def _run() -> None:
        await app(transport.scope(), transport.receive, transport.send)  # type: ignore[operator]

    asyncio.run(_run())
    return transport


# --------------------------------------------------------------------------- #
# Scripted provider streams.
# --------------------------------------------------------------------------- #

#: The scripted content deltas a clean conformance stream carries, in order.
_SCRIPTED_DELTAS: tuple[str, ...] = ("Hello", ", ", "world", "!")


def _content_script() -> tuple[UpstreamEvent, ...]:
    """A clean content stream: one event per scripted delta, last one ``final``."""
    events: list[UpstreamEvent] = []
    last = len(_SCRIPTED_DELTAS) - 1
    for i, text in enumerate(_SCRIPTED_DELTAS):
        events.append(
            UpstreamEvent(text_deltas=(("content", text),), final=(i == last)),
        )
    return tuple(events)


def _error_frame_script() -> tuple[UpstreamEvent, ...]:
    """A stream that delivers content, then a mid-stream provider ``error_frame`` (R11).

    The error frame carries a secret-looking body; the injected
    :class:`_BlockingErrorScanner` drives it to a terminal BLOCK, so the recorded wire ends
    with the declared SSE ``Error_Frame`` and no content after it.
    """
    return (
        UpstreamEvent(text_deltas=(("content", "partial"),), final=False),
        UpstreamEvent(
            text_deltas=(),
            final=False,
            error_frame="upstream failure: card 4111111111111111 leaked in a stack trace",
        ),
        UpstreamEvent(text_deltas=(("content", "unreachable"),), final=True),
    )


# --------------------------------------------------------------------------- #
# SSE frame parsing over the recorded wire.
# --------------------------------------------------------------------------- #


def _parse_data_json(frame: bytes) -> dict[str, object]:
    """Parse a ``data: {json}\\n\\n`` SSE content/error frame into its JSON object.

    Asserts the exact SSE framing (``data: `` prefix, ``\\n\\n`` terminator) an SDK parses,
    then returns the decoded JSON object. A ``[DONE]`` marker is not JSON and must not be
    passed here.
    """
    assert frame.startswith(b"data: "), f"frame missing 'data: ' prefix: {frame!r}"
    assert frame.endswith(b"\n\n"), f"frame missing blank-line terminator: {frame!r}"
    payload = frame[len(b"data: ") : -len(b"\n\n")]
    obj = json.loads(payload.decode("utf-8"))
    assert isinstance(obj, dict), f"SSE data payload is not a JSON object: {obj!r}"
    return obj


def _deltas_of(chunk_obj: Mapping[str, object]) -> list[str]:
    """Pull the ``choices[].delta.content`` texts from a ``chat.completion.chunk`` object."""
    choices = chunk_obj.get("choices")
    assert isinstance(choices, list), f"chunk has no choices list: {chunk_obj!r}"
    out: list[str] = []
    for choice in choices:
        assert isinstance(choice, dict)
        delta = choice.get("delta")
        assert isinstance(delta, dict)
        content = delta.get("content")
        if isinstance(content, str):
            out.append(content)
    return out


# =========================================================================== #
# 1. Response head: 200 + text/event-stream (R1 wire shape / R2.2).
# =========================================================================== #


def test_response_starts_200_text_event_stream() -> None:
    """The recorded ``http.response.start`` is ``200`` with a ``text/event-stream`` body."""
    transport = _drive(_build(script=_content_script()))
    start = transport.start_event()

    assert start["status"] == 200, f"streaming response did not start 200: {start!r}"
    headers = start.get("headers")
    assert isinstance(headers, list)
    decoded = {
        name.decode("latin-1").lower(): value.decode("latin-1")
        for name, value in headers
    }
    assert decoded.get("content-type") == "text/event-stream", (
        f"streaming response content-type is not text/event-stream: {decoded!r}"
    )


# =========================================================================== #
# 2. Content frames parse as chat.completion.chunk + carry the scripted deltas (R1.1).
# =========================================================================== #


def test_content_frames_are_parseable_chat_completion_chunks() -> None:
    """Each content body frame is a parseable ``chat.completion.chunk`` with the deltas (R1.1)."""
    transport = _drive(_build(script=_content_script()))
    frames = transport.body_frames()

    # Every SSE frame before the terminal [DONE] is a content frame.
    content_frames = [f for f in frames if f != DONE_MARKER]
    assert content_frames, f"no content frames recorded: {frames!r}"

    recovered: list[str] = []
    for frame in content_frames:
        obj = _parse_data_json(frame)
        assert obj.get("object") == CHUNK_OBJECT, (
            f"content frame object is not {CHUNK_OBJECT!r}: {obj!r}"
        )
        # SDK-required identity fields are present and well-typed.
        assert isinstance(obj.get("id"), str) and obj["id"], f"chunk missing id: {obj!r}"
        assert isinstance(obj.get("model"), str) and obj["model"], f"chunk missing model: {obj!r}"
        assert isinstance(obj.get("created"), int), f"chunk missing created: {obj!r}"
        recovered.extend(_deltas_of(obj))

    assert recovered == list(_SCRIPTED_DELTAS), (
        f"recorded content deltas {recovered!r} != scripted {list(_SCRIPTED_DELTAS)!r}"
    )


# =========================================================================== #
# 3. Terminal data: [DONE] marker is emitted last, nothing after it (R1.2).
# =========================================================================== #


def test_terminal_done_marker_is_last() -> None:
    """The ``data: [DONE]\\n\\n`` terminal marker is the LAST SSE frame, no content after it."""
    transport = _drive(_build(script=_content_script()))
    frames = transport.body_frames()

    assert DONE_MARKER in frames, f"terminal [DONE] marker never emitted: {frames!r}"
    trailing = frames[frames.index(DONE_MARKER) :]
    assert frames[-1] == DONE_MARKER, (
        f"[DONE] marker is not the last SSE frame; trailing frames={trailing!r}"
    )
    # Exactly one terminal marker, and no content frame follows it.
    assert frames.count(DONE_MARKER) == 1, f"more than one [DONE] marker: {frames!r}"


# =========================================================================== #
# 4. Error_Frame path: a mid-stream block ends with the declared Error_Frame (R11.3 / R1.3).
# =========================================================================== #


def test_error_frame_path_blocks_mid_stream() -> None:
    """A blocked mid-stream error frame terminates with the declared SSE ``Error_Frame`` (R11.3).

    The provider delivers one content delta, then a mid-stream ``error_frame`` carrying a
    secret-looking body. The injected :class:`_BlockingErrorScanner` + ``MinimalOutputResolver``
    drive it to a terminal BLOCK (``output_blocked``), so the recorded wire ends with a
    parseable ``{"error": {...}}`` frame and NO content frame follows the error — the no-splice
    / no-content-after-error contract checked at the conformance level.
    """
    transport = _drive(
        _build(script=_error_frame_script(), error_scanner=_BlockingErrorScanner()),
    )
    frames = transport.body_frames()
    assert frames, "no SSE frames recorded for the error-frame path"

    # The last SSE frame is the declared Error_Frame — a parseable {"error": {...}} object.
    terminal = frames[-1]
    assert terminal != DONE_MARKER, (
        f"a blocked stream must NOT end with [DONE]; frames={frames!r}"
    )
    err_obj = _parse_data_json(terminal)
    error = err_obj.get("error")
    assert isinstance(error, dict), f"terminal frame is not an Error_Frame: {err_obj!r}"
    # The declared Error_Frame shape: message / type / code string fields.
    assert isinstance(error.get("message"), str) and error["message"], (
        f"Error_Frame missing message: {error!r}"
    )
    assert isinstance(error.get("type"), str) and error["type"], (
        f"Error_Frame missing type: {error!r}"
    )
    assert error.get("code") == "output_blocked", (
        f"Error_Frame code is not the terminal block code: {error!r}"
    )
    # The raw secret body of the error frame was withheld — it never reached the wire.
    joined = b"".join(frames)
    assert b"4111111111111111" not in joined, (
        "the blocked error frame's raw secret bytes leaked onto the wire"
    )
    # No content frame follows the terminal Error_Frame (no splice after the cut).
    content_after: list[bytes] = []
    saw_error = False
    for frame in frames:
        if frame is terminal:
            saw_error = True
            continue
        if saw_error and frame != DONE_MARKER:
            content_after.append(frame)
    assert not content_after, (
        f"content frames emitted AFTER the terminal Error_Frame: {content_after!r}"
    )


# =========================================================================== #
# 5. Split-surrogate decode confluence at the conformance level (R1.4).
# =========================================================================== #

#: An astral (supplementary-plane) code point: U+1F680 ROCKET. ``ensure_ascii`` JSON
#: encodes it as a UTF-16 SURROGATE PAIR escape (``\\ud83d\\ude80``), so a chunk boundary
#: can fall between — or inside — the two ``\\uXXXX`` escapes.
_ASTRAL = "\U0001f680\U0001f4a1 split me \U0001f525"


def _astral_wire() -> bytes:
    """The encoder's ASCII wire bytes for a content frame carrying astral code points.

    ``SSEEncoder.content`` serialises with ``ensure_ascii=True``, so every astral code point
    becomes a ``\\uXXXX`` surrogate-pair escape on the wire — the exact bytes the decoder's
    split-surrogate buffering path must reassemble regardless of chunk boundaries.
    """
    frame = DownstreamFrame(text_deltas=(("content", _ASTRAL),))
    return SSEEncoder().content(frame)


def _decode_in_chunks(wire: bytes, boundary: int) -> tuple[DownstreamFrame, ...]:
    """Feed ``wire`` to a fresh :class:`SSEDecoder` split at ``boundary`` (two chunks)."""
    decoder = SSEDecoder()
    frames = list(decoder.feed(wire[:boundary]))
    frames.extend(decoder.feed(wire[boundary:]))
    assert not decoder.malformed(), (
        f"decoder latched malformed on well-formed astral wire at boundary={boundary}"
    )
    return tuple(frames)


def test_split_surrogate_decode_confluence() -> None:
    """An astral frame decodes to the SAME correct code points at EVERY chunk boundary (R1.4).

    The confluence idea (Property 9) checked at the conformance level: the shipped
    :class:`SSEDecoder` BUFFERS a partial event, so feeding the SAME astral content-frame wire
    split at every possible byte boundary — including boundaries that split the JSON
    ``\\ud83d\\ude80`` surrogate-pair escape — must yield one content frame carrying exactly
    the original astral text. A single-shot decode gives the reference code points.
    """
    wire = _astral_wire()

    # Reference: a single-shot decode of the whole wire.
    reference = _decode_in_chunks(wire, len(wire))
    assert len(reference) == 1, f"astral wire did not decode to one frame: {reference!r}"
    assert reference[0].text_deltas == (("content", _ASTRAL),), (
        f"single-shot astral decode lost code points: {reference[0].text_deltas!r}"
    )

    # Confluence: every split point yields the identical decoded frame.
    for boundary in range(len(wire) + 1):
        frames = _decode_in_chunks(wire, boundary)
        assert len(frames) == 1, (
            f"boundary={boundary}: astral wire decoded to {len(frames)} frames, expected 1"
        )
        assert frames[0].text_deltas == reference[0].text_deltas, (
            f"boundary={boundary}: split decode diverged from reference: "
            f"{frames[0].text_deltas!r} != {reference[0].text_deltas!r}"
        )
