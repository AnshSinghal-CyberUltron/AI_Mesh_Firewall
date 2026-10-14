"""B4 task 5.1 — local ``Fake_Upstream`` + ``Fake_Guard`` test harness (R6.1, R6.2).

TEST-ONLY infrastructure. This module is NOT a test module (the leading
underscore keeps pytest from collecting it); it is the shared helper the B4
integration tests (``test_b4_http_integration.py`` §5.2/§5.3,
``test_b4_guard_integration.py`` §5.4) import from. It scaffolds NO cloud
behaviour — the real BYOK provider socket and the real GPU guard fleet socket
stay deferred live gates; this harness exercises the shipped
``HttpProviderClient`` end to end over httpx's real streaming machinery WITHOUT
a network socket.

What it provides
----------------
* :class:`FakeUpstream` — speaks the ``Generation_Provider``'s OpenAI SSE shape
  (``data: {chat.completion.chunk json}\\n\\n`` content frames + the terminal
  ``data: [DONE]\\n\\n`` marker) over an ``httpx.MockTransport``. The transport
  routes ``client.stream("POST", destination, json=body)`` — exactly what
  :meth:`HttpProviderClient.open` calls — to a REAL ``httpx`` streaming response
  whose body is produced by an async byte generator, so ``response.aiter_bytes``
  and ``response.aclose`` (which :meth:`HttpProviderClient.abort` calls) run
  through httpx's genuine streaming/cancel path. Fault controls are selectable
  via :class:`FaultKind`: a mid-stream ``{"error": ...}`` frame, a slow/stalling
  stream (a long ``await`` so ``abort`` can cut it), an abrupt disconnect, and
  malformed/undecodable SSE bytes.
* :class:`FakeGuard` — a minimal in-memory reachable guard-tier destination used
  to exercise the ``GuardRouter`` selection + guard-egress seam WITHOUT the
  GW07/GW08 detector. It just records that its destination was selected / hit.
* Helpers the integration tests reuse: :func:`real_decoder_factory` (returns the
  REAL ``edge/wire/sse.py SSEDecoder`` so the tests inject the real decoder into
  ``HttpProviderClient``), :func:`FakeUpstream.client_factory` (bound to the
  fake transport), :func:`sse_content_frame` / :func:`build_sse_stream` (frame
  scripting from text deltas), and :class:`DeterministicClock`.

Layering note. Test files are NOT under the import-linter layer contract, so
this helper may import ``edge/wire/sse.py`` directly (to BUILD the OpenAI SSE
bytes the fake serves and to supply the REAL decoder the tests inject) and the
``dispatch`` value types directly. ``httpx`` is a ``conformance``-extra
dependency; a tree without it cannot run these integration tests, so the import
is top-level here (the integration test modules ``pytest.importorskip('httpx')``
before importing this helper).

Typed for ``mypy --strict``. (mypy's configured ``files`` are
``gateway_v2``/``state_control``/``audit_control``/``lint`` — ``tests`` is not in
scope by default — but the helper is written strict-clean regardless so a
``mypy tests/dispatch/_b4_fakes.py`` run is clean.)
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx

from gateway_v2.edge.wire.sse import (
    DONE_MARKER,
    SSEDecoder,
    SSEEncoder,
)

__all__ = (
    "DEFAULT_DESTINATION",
    "DeterministicClock",
    "FakeGuard",
    "FakeUpstream",
    "FaultKind",
    "ScriptFrame",
    "build_sse_stream",
    "real_decoder_factory",
    "sse_content_frame",
)

#: The default local upstream URL the fake answers. ``MockTransport`` ignores the
#: host (it never dials a socket); the URL only has to be a valid absolute URL so
#: ``httpx`` builds the request. The integration tests pass this as
#: ``UpstreamRequest.destination``.
DEFAULT_DESTINATION = "http://fake-upstream.local/v1/chat/completions"


# --------------------------------------------------------------------------- #
# Deterministic clock (parity with the shipped injected-clock discipline).
# --------------------------------------------------------------------------- #


class DeterministicClock:
    """A monotonic, injectable clock the tests hand to ``HttpProviderClient``.

    ``HttpProviderClient`` retains the injected ``clock`` for parity with the
    shipped injected-clock discipline and reads NO wall-clock itself, so this
    clock only has to be a ``Callable[[], float]`` that advances deterministically
    on each read. Tests may seed the start and the per-tick step.
    """

    __slots__ = ("_now", "_step")

    def __init__(self, *, start: float = 0.0, step: float = 0.001) -> None:
        self._now = start
        self._step = step

    def __call__(self) -> float:
        value = self._now
        self._now += self._step
        return value


# --------------------------------------------------------------------------- #
# Frame scripting — build the OpenAI SSE bytes from text deltas.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ScriptFrame:
    """One scripted content frame: the ``(channel, text)`` deltas it carries.

    ``build_sse_stream`` turns each ``ScriptFrame`` into one
    ``data: {chat.completion.chunk json}\\n\\n`` content frame using the REAL
    ``SSEEncoder``, so the bytes the fake serves are byte-identical to what the
    gateway's own encoder would produce (the decoder on the client side therefore
    exercises its genuine split-surrogate / split-UTF-8 buffering path).
    """

    text_deltas: tuple[tuple[str, str], ...]


def _frame_to_bytes(encoder: SSEEncoder, frame: ScriptFrame) -> bytes:
    """Serialise one :class:`ScriptFrame` to a ``data: {json}\\n\\n`` content frame.

    Uses the shipped ``edge/wire/sse.py SSEEncoder`` so the wire bytes are the
    production shape (``ensure_ascii`` → astral code points become ``\\uXXXX``
    surrogate pairs on the wire). Imported locally as a ``DownstreamFrame`` to
    avoid duplicating the egress transport type.
    """
    from gateway_v2.egress.stream import DownstreamFrame

    return encoder.content(DownstreamFrame(text_deltas=frame.text_deltas))


def sse_content_frame(*deltas: tuple[str, str]) -> ScriptFrame:
    """Build a :class:`ScriptFrame` from ``(channel, text)`` pairs.

    Convenience for the common case ``sse_content_frame(("content", "Hello"))``.
    """
    return ScriptFrame(text_deltas=tuple(deltas))


def build_sse_stream(
    frames: Sequence[ScriptFrame],
    *,
    done: bool = True,
    stream_id: str = "chatcmpl-fake",
    model: str = "fake-model",
) -> bytes:
    """Build the full OpenAI SSE byte stream for ``frames`` (+ the ``[DONE]`` marker).

    Each :class:`ScriptFrame` becomes one content frame via the real
    ``SSEEncoder``; when ``done`` is set the terminal ``data: [DONE]\\n\\n`` marker
    is appended (clean termination the client's decoder treats as end-of-stream
    yielding no frame). Returning the full concatenated bytes lets a caller feed
    them to the fake in one chunk or re-slice them at arbitrary boundaries to
    exercise the decoder's buffering.
    """
    encoder = SSEEncoder(stream_id=stream_id, model=model)
    out = bytearray()
    for frame in frames:
        out.extend(_frame_to_bytes(encoder, frame))
    if done:
        out.extend(DONE_MARKER)
    return bytes(out)


def error_sse_frame(code: str, *, message: str = "fake upstream error") -> bytes:
    """Build a mid-stream ``{"error": {...}}`` SSE frame the decoder reads as terminal.

    The client's ``SSEDecoder`` maps a ``{"error": {...}}`` event to a terminal
    ``DownstreamFrame`` whose ``error_code`` is the event's ``code`` (so the
    client surfaces it as ``UpstreamEvent.error_frame`` for R11 scan-before-
    forward). The shape mirrors what the upstream recorder would emit.
    """
    body = json.dumps(
        {"error": {"message": message, "type": "upstream_error", "code": code}},
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return b"data: " + body.encode("ascii") + b"\n\n"


# --------------------------------------------------------------------------- #
# Fault controls.
# --------------------------------------------------------------------------- #


class FaultKind(Enum):
    """Selectable fault behaviour for :class:`FakeUpstream`.

    * ``NONE`` — a clean stream: the scripted content frames then ``[DONE]``.
    * ``MID_STREAM_ERROR`` — emit the configured content prefix, then a
      ``{"error": ...}`` frame (terminal; the client surfaces ``error_frame``).
    * ``STALL`` — emit the content prefix, then await a long sleep that never
      completes within the test, so ``abort`` can cut the in-flight read
      (``response.aclose`` interrupts ``aiter_bytes``).
    * ``DISCONNECT`` — emit the content prefix, then abruptly stop the body
      WITHOUT the ``[DONE]`` marker (an abrupt upstream disconnect).
    * ``MALFORMED`` — emit the content prefix, then a complete-but-undecodable
      SSE event, so the client's decoder trips its ``malformed()`` latch and the
      client fails closed with ``STREAM_MALFORMED_UPSTREAM``.
    """

    NONE = "none"
    MID_STREAM_ERROR = "mid_stream_error"
    STALL = "stall"
    DISCONNECT = "disconnect"
    MALFORMED = "malformed"


#: A deliberately long stall (seconds) for :attr:`FaultKind.STALL`. The test
#: never waits this out — it awaits ``abort`` instead, which closes the response
#: and cuts the stall. Not a capacity bound (this is test-only harness code).
_STALL_SECONDS = 3600.0

#: A complete SSE event whose ``data:`` payload is not valid JSON — the decoder
#: reads a whole event, fails to parse it, and sets ``malformed()``.
_MALFORMED_EVENT = b"data: {not-json<<<\n\n"


# --------------------------------------------------------------------------- #
# Fake_Upstream (R6.1).
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _StreamPlan:
    """What the fake's body generator emits for one request.

    ``prefix_chunks`` is the content prefix split into ONE chunk per content
    frame (not one blob) so the client's one-behind buffering releases an event
    before a trailing fault — a single-blob prefix would keep the only frame
    ``pending`` and never yield it before a STALL. ``fault`` selects the tail.
    """

    prefix_chunks: tuple[bytes, ...]
    fault: FaultKind


class FakeUpstream:
    """A local OpenAI-SSE ``Generation_Provider`` stand-in over ``httpx.MockTransport``.

    Construct it with the scripted content frames (and an optional fault), then
    hand :meth:`client_factory` to ``HttpProviderClient(client_factory=...)``.
    When the client opens its streaming POST, the mock transport returns a real
    ``httpx`` streaming response whose body comes from :class:`_FakeByteStream`
    — so the client drains it through genuine ``aiter_bytes`` and cuts it through genuine
    ``aclose`` (what ``abort`` calls). The fake records the last request it saw
    (``last_request_url`` / ``last_request_body``) so the tests can assert the
    client connected to ``UpstreamRequest.destination`` and sent the
    ``Transform``-packed body.

    This is socket-free: ``MockTransport`` never dials; it invokes the handler in
    process. The real BYOK provider socket stays a deferred live gate.
    """

    __slots__ = (
        "_frames",
        "_prefix_frames",
        "fault",
        "last_request_body",
        "last_request_url",
        "requests_seen",
    )

    def __init__(
        self,
        frames: Sequence[ScriptFrame] | None = None,
        *,
        fault: FaultKind = FaultKind.NONE,
        prefix_frames: Sequence[ScriptFrame] | None = None,
    ) -> None:
        self._frames: tuple[ScriptFrame, ...] = tuple(frames or ())
        # For a faulting stream, the frames emitted BEFORE the fault (a content
        # prefix the client should still receive). Defaults to ``frames`` so the
        # common case (``frames`` then a fault) needs no extra wiring.
        self._prefix_frames: tuple[ScriptFrame, ...] = tuple(
            prefix_frames if prefix_frames is not None else self._frames
        )
        self.fault = fault
        self.requests_seen = 0
        self.last_request_url: str | None = None
        self.last_request_body: object | None = None

    # -- httpx wiring -------------------------------------------------------- #

    def client_factory(self) -> httpx.AsyncClient:
        """Return an ``httpx.AsyncClient`` whose transport routes to this fake.

        This is what the integration tests inject as
        ``HttpProviderClient(client_factory=fake.client_factory)``. Each call
        returns a fresh client bound to one ``MockTransport`` over
        :meth:`_handle`, exactly matching the real client's per-stream
        ``client_factory()`` call.
        """
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        """The ``MockTransport`` handler: record the request, return a stream.

        Records what the client sent (URL + decoded JSON body) and returns a
        streaming ``httpx.Response`` whose body is produced by :meth:`_body`. The
        response status is 200 for every fault kind — the faults are IN the SSE
        body (a mid-stream error frame / malformed bytes / an abrupt end / a
        stall), matching how a real OpenAI-compatible stream surfaces mid-stream
        faults after a 200 header.
        """
        self.requests_seen += 1
        self.last_request_url = str(request.url)
        self.last_request_body = _decode_json_body(request)
        plan = _StreamPlan(prefix_chunks=self._prefix_chunks(), fault=self.fault)
        return httpx.Response(200, stream=_FakeByteStream(plan))

    def _prefix_chunks(self) -> tuple[bytes, ...]:
        """The content frames before any fault, ONE wire chunk per frame.

        For a clean stream the frames are the full script; for a faulting stream
        they are the content prefix that should arrive before the fault. Each
        frame is a separate chunk so the client's one-behind buffering can
        release an event before a trailing STALL / error / disconnect.
        """
        source = self._prefix_frames if self.fault is not FaultKind.NONE else self._frames
        encoder = SSEEncoder(stream_id="chatcmpl-fake", model="fake-model")
        return tuple(_frame_to_bytes(encoder, frame) for frame in source)


class _FakeByteStream(httpx.AsyncByteStream):
    """A real ``httpx`` async byte stream driving the fake's body + fault.

    ``httpx`` iterates this through ``response.aiter_bytes`` and closes it through
    ``response.aclose`` (what ``HttpProviderClient.abort`` triggers), so the
    client's open/abort path runs over httpx's genuine streaming machinery. The
    body is: the content prefix (emitted as SEPARATE per-frame chunks so the
    client's one-behind buffering releases an event before any fault), then the
    fault-specific tail.

    The STALL fault models a stall that a connection close genuinely interrupts:
    the body generator parks on an internal ``asyncio.Event`` that :meth:`aclose`
    sets, so when ``HttpProviderClient.abort`` calls ``response.aclose`` the
    parked read is released and the stream ends — the real-socket semantic where
    closing the transport unblocks a pending read, reproduced without a socket.
    """

    def __init__(self, plan: _StreamPlan) -> None:
        self._plan = plan
        # Lazily created on first use so construction stays loop-agnostic (the
        # stream is only ever iterated inside the test's running event loop).
        self._closed: object | None = None

    async def __aiter__(self) -> AsyncIterator[bytes]:
        import asyncio

        plan = self._plan
        for chunk in plan.prefix_chunks:
            yield chunk
        fault = plan.fault
        if fault is FaultKind.NONE:
            yield DONE_MARKER
        elif fault is FaultKind.MID_STREAM_ERROR:
            yield error_sse_frame("stream_killed")
        elif fault is FaultKind.MALFORMED:
            yield _MALFORMED_EVENT
        elif fault is FaultKind.DISCONNECT:
            # Abrupt end: no [DONE], no further bytes. The generator simply ends.
            return
        elif fault is FaultKind.STALL:
            # Park until the stream is closed (``abort`` → ``aclose``) OR the
            # safety timeout fires, whichever comes first. Closing the response
            # releases this wait, so the in-flight read is cut — the real-socket
            # close semantic without a socket.
            closed = self._closed_event()
            try:
                await asyncio.wait_for(closed.wait(), timeout=_STALL_SECONDS)
            except TimeoutError:  # pragma: no cover - the test aborts well before
                return

    def _closed_event(self) -> Any:
        import asyncio

        if self._closed is None:
            self._closed = asyncio.Event()
        return self._closed

    async def aclose(self) -> None:
        """Release the stream — ``HttpProviderClient.abort`` triggers this.

        Setting the close event unblocks a STALL fault parked in
        :meth:`__aiter__`, so closing the in-flight response actually cuts the
        read (not merely flips a latch). Idempotent: calling it twice (the client
        may ``aclose`` out of band in ``abort`` and again in its ``finally``) just
        re-sets an already-set event.
        """
        if self._closed is not None:
            event = self._closed
            # ``asyncio.Event.set`` is sync and idempotent; narrow via duck-type.
            event.set()  # type: ignore[attr-defined]


def _decode_json_body(request: httpx.Request) -> object:
    """Decode the request JSON body the client sent (best-effort, test-only)."""
    raw = request.content
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


# --------------------------------------------------------------------------- #
# Fake_Guard (R6.2) — minimal reachable guard destination, no detector.
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class FakeGuard:
    """A minimal in-memory guard-tier stand-in for the ``GuardRouter`` seam (R6.2).

    B4 builds the guard ROUTING selection + the guard-egress seam, NOT the
    GW07/GW08 detector model call. So the fake only needs to be a reachable
    destination the routing can point at and that records it was hit — no
    scanning, no findings. A test selects a destination with ``GuardRouter`` and
    then dispatches to the matching ``FakeGuard`` to prove the seam is
    exercisable without a detector.
    """

    destination: str
    hits: int = field(default=0)
    last_payload: object | None = field(default=None)

    def dispatch(self, payload: object = None) -> str:
        """Record a dispatch to this guard and echo its destination.

        The ``payload`` is whatever the seam would hand a real guard (unused by
        the fake beyond being recorded). Returns ``destination`` so a test can
        assert the ``GuardRouter``-selected destination reached this guard.
        """
        self.hits += 1
        self.last_payload = payload
        return self.destination


class FakeGuardPool:
    """A small registry mapping guard destinations to :class:`FakeGuard`s.

    Lets a §5.4 test build a pool, feed the destinations into a ``Load_Snapshot``
    for ``GuardRouter.select``, then route the selected destination back to the
    right ``FakeGuard`` — confirming the routing selection + guard-egress seam is
    exercisable with NO real detector present.
    """

    __slots__ = ("_guards",)

    def __init__(self, destinations: Sequence[str]) -> None:
        self._guards: dict[str, FakeGuard] = {
            dest: FakeGuard(destination=dest) for dest in destinations
        }

    @property
    def destinations(self) -> tuple[str, ...]:
        """The guard destinations in registration order."""
        return tuple(self._guards)

    def get(self, destination: str) -> FakeGuard:
        """The :class:`FakeGuard` for ``destination`` (``KeyError`` if unknown)."""
        return self._guards[destination]

    def dispatch(self, destination: str, payload: object = None) -> FakeGuard:
        """Route a dispatch to the guard named ``destination`` and return it."""
        guard = self._guards[destination]
        guard.dispatch(payload)
        return guard


# --------------------------------------------------------------------------- #
# Decoder factory — inject the REAL edge/wire/sse.py SSEDecoder.
# --------------------------------------------------------------------------- #


def real_decoder_factory() -> Callable[[], SSEDecoder]:
    """Return a ``decoder_factory`` that builds a FRESH real ``SSEDecoder`` per stream.

    This is what the integration tests inject as
    ``HttpProviderClient(decoder_factory=real_decoder_factory())`` — the REAL
    ``edge/wire/sse.py SSEDecoder`` (NOT a stub), so the client's SSE parsing,
    split-surrogate buffering, and ``malformed()`` fail-closed latch are the
    production ones. A fresh decoder per ``open`` keeps two concurrent streams
    from sharing the buffer / latch.
    """
    return SSEDecoder
