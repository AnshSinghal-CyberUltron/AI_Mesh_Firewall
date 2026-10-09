# Feature: sse-egress-pipeline
# Validates: Requirements 2.1, 2.4, 2.5, 2.6, 3.4
"""Unit tests for the ASGI app plumbing (GW12, task 7.3).

Drives the REAL ASGI application assembled by :func:`gateway_v2.edge.app.build_app` over an
**in-process ASGI transport** (scope/receive/send, ``asyncio.run``), reusing the ``_Transport``
idiom established by ``test_lgw12_asgi_conformance.py`` (task 15.2) so the transport is not
reinvented. These are example/unit tests — no ≥10k property loop, no ``hypothesis``.

Asserts the serving-skin plumbing:

* ``/readyz`` → ``200 ok`` when the injected readiness probe reports ready; → ``503`` NAMING the
  missing dependency when the probe reports not-ready (R2.4/R2.5).
* ``/metrics`` → ``200`` with the injected :class:`MetricsRegistry.exposition` body (R2.6).
* the chat route (``POST /v1/chat/completions``) is registered and routes to the streaming
  handler — a scripted :class:`StubProviderClient` drives a ``200`` ``text/event-stream``
  response ending in the terminal ``[DONE]`` marker (R2.1/R2.2).
* a registered-for-later placeholder (``POST /v1/responses``) → the declared ``501``
  ``not_implemented`` via the ``Error_Envelope``, NOT a raw ``404`` (R2.3/R3.4).
* an unknown route → the declared ``501`` ``not_implemented``, not a raw framework error
  (R2.3/R3.4).
* the ASGI ``lifespan`` protocol is drained: a startup/shutdown scope is acked with
  ``lifespan.startup.complete`` / ``lifespan.shutdown.complete``.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Mapping, Sequence

from gateway_v2.detect.holdback import scan as holdback_scan
from gateway_v2.dispatch.provider import StubProviderClient, UpstreamEvent
from gateway_v2.domain import Finding
from gateway_v2.edge.app import ReadinessProbe, build_app
from gateway_v2.edge.wire.sse import DONE_MARKER
from gateway_v2.egress.output_guard import MinimalOutputResolver
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

_SEED = 0x6A12
_CHAT_PATH = "/v1/chat/completions"
_METRICS_BODY = "# gateway_v2 metrics\namf_up 1\n"


# --------------------------------------------------------------------------- #
# Fixtures: the real ASGI app with every dependency injected (no socket).
# --------------------------------------------------------------------------- #


def _contract() -> ResourceContract:
    """A serviceable contract (same ``from_signals`` fixture shape as the other LGW12 tests)."""
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
    """A minimal :class:`~gateway_v2.edge.app.MetricsRegistry`: a fixed text exposition."""

    __slots__ = ()

    def exposition(self) -> str:
        return _METRICS_BODY


class _Clock:
    """A monotonic in-process clock stand-in (seconds), advanced on each read, never real."""

    __slots__ = ("_t",)

    def __init__(self) -> None:
        self._t = 0.0

    def __call__(self) -> float:
        self._t += 1e-6
        return self._t


def _no_error_findings(_frame: str) -> Sequence[Finding]:
    """The default error-frame scanner: no findings → a mid-stream error frame forwards clean."""
    return ()


def _content_script() -> tuple[UpstreamEvent, ...]:
    """A clean content stream: one event per scripted delta, the last one ``final``."""
    deltas = ("Hello", ", ", "world", "!")
    last = len(deltas) - 1
    return tuple(
        UpstreamEvent(text_deltas=(("content", text),), final=(i == last))
        for i, text in enumerate(deltas)
    )


def _build(
    *,
    readiness: ReadinessProbe | None = None,
    metrics: _StubMetrics | None = None,
    script: Sequence[UpstreamEvent] | None = None,
) -> object:
    """Assemble the real ASGI app with a scripted in-process provider (no socket).

    Mirrors the conformance harness's ``_build`` so the dependency wiring is identical: the
    shipped :class:`StubProviderClient` (in-process, seeded RNG), the pure ``detect.holdback.scan``
    content scanner, :class:`MinimalOutputResolver`, and the local metrics/clock stubs. The
    readiness probe + metrics registry are the surfaces these tests exercise.
    """
    provider = StubProviderClient(
        script=tuple(script if script is not None else _content_script()),
        rng=random.Random(_SEED),
    )
    return build_app(
        contract=_contract(),
        provider=provider,
        scanner=holdback_scan,
        resolver=MinimalOutputResolver(),
        metrics_registry=metrics if metrics is not None else _StubMetrics(),
        clock=_Clock(),
        readiness=readiness,
        error_scanner=_no_error_findings,
    )


# --------------------------------------------------------------------------- #
# In-process ASGI transport (the task 15.2 idiom, generalised to any method/path).
# --------------------------------------------------------------------------- #


class _Transport:
    """A one-connection in-process ASGI transport: no socket, records every send event.

    The same shape as ``test_lgw12_asgi_conformance._Transport``: a scripted ``receive`` queue
    (request body then ``http.disconnect``) and a recording ``send`` sink. Generalised to take an
    arbitrary ``(method, path)`` so a GET ``/readyz`` and a POST chat request drive the same way.
    """

    __slots__ = ("events", "_incoming")

    def __init__(self, *, body: bytes) -> None:
        self.events: list[Mapping[str, object]] = []
        self._incoming: list[Mapping[str, object]] = [
            {"type": "http.request", "body": body, "more_body": False},
            {"type": "http.disconnect"},
        ]

    @staticmethod
    def http_scope(method: str, path: str) -> dict[str, object]:
        """A minimal ASGI 3.0 ``http`` scope for ``method`` + ``path``."""
        return {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [(b"content-type", b"application/json")],
        }

    async def receive(self) -> Mapping[str, object]:
        if self._incoming:
            return self._incoming.pop(0)
        return {"type": "http.disconnect"}

    async def send(self, event: Mapping[str, object]) -> None:
        self.events.append(dict(event))

    # -- recorded-wire accessors --------------------------------------------- #

    def start_event(self) -> Mapping[str, object]:
        starts = [e for e in self.events if e.get("type") == "http.response.start"]
        assert len(starts) == 1, f"expected exactly one response.start, got {len(starts)}"
        return starts[0]

    def status(self) -> int:
        status = self.start_event().get("status")
        assert isinstance(status, int), f"response.start carries no int status: {status!r}"
        return status

    def headers(self) -> dict[str, str]:
        raw = self.start_event().get("headers")
        assert isinstance(raw, list)
        return {
            name.decode("latin-1").lower(): value.decode("latin-1") for name, value in raw
        }

    def body_bytes(self) -> bytes:
        """Concatenate every ``http.response.body`` payload (the full response body)."""
        out = bytearray()
        for event in self.events:
            if event.get("type") != "http.response.body":
                continue
            body = event.get("body")
            if isinstance(body, (bytes, bytearray)):
                out.extend(body)
        return bytes(out)

    def body_frames(self) -> list[bytes]:
        """Every non-empty ``http.response.body`` payload, in order (the SSE frames)."""
        frames: list[bytes] = []
        for event in self.events:
            if event.get("type") != "http.response.body":
                continue
            body = event.get("body")
            if isinstance(body, (bytes, bytearray)) and body:
                frames.append(bytes(body))
        return frames


def _drive_http(app: object, method: str, path: str, *, body: bytes = b"{}") -> _Transport:
    """Drive one in-process HTTP request through ``app`` and return the recording transport."""
    transport = _Transport(body=body)

    async def _run() -> None:
        await app(transport.http_scope(method, path), transport.receive, transport.send)  # type: ignore[operator]

    asyncio.run(_run())
    return transport


# --------------------------------------------------------------------------- #
# /readyz — 200 ready, 503 naming the missing dep (R2.4/R2.5).
# --------------------------------------------------------------------------- #


def test_readyz_ready_returns_200_ok() -> None:
    """``/readyz`` returns ``200 ok`` when the injected probe reports ready (R2.4)."""
    app = _build(readiness=lambda: (True, None))
    transport = _drive_http(app, "GET", "/readyz")

    assert transport.status() == 200, "ready probe did not yield 200"
    assert transport.body_bytes() == b"ok", (
        f"ready /readyz body is not the ok marker: {transport.body_bytes()!r}"
    )


def test_readyz_not_ready_returns_503_naming_the_dep() -> None:
    """``/readyz`` returns ``503`` NAMING the unavailable dependency when not ready (R2.5).

    The probe reports ``(False, "some_dep")``; the ``503`` body must carry ``some_dep`` so the
    unready condition is diagnosable (an empty 503 would be undiagnosable).
    """
    missing_dep = "some_dep"
    app = _build(readiness=lambda: (False, missing_dep))
    transport = _drive_http(app, "GET", "/readyz")

    assert transport.status() == 503, (
        f"not-ready probe did not yield 503: {transport.status()}"
    )
    body = transport.body_bytes().decode("utf-8")
    assert missing_dep in body, (
        f"the 503 body does not name the missing dependency {missing_dep!r}: {body!r}"
    )


def test_readyz_default_probe_is_ready() -> None:
    """An unwired deployment (no probe) reports ready — parity with ``state_ready(None)`` (R2.4)."""
    app = _build(readiness=None)
    transport = _drive_http(app, "GET", "/readyz")
    assert transport.status() == 200


# --------------------------------------------------------------------------- #
# /metrics — 200 with the injected exposition body (R2.6).
# --------------------------------------------------------------------------- #


def test_metrics_returns_injected_exposition() -> None:
    """``/metrics`` returns ``200`` with the registry's exposition body verbatim (R2.6)."""
    app = _build(metrics=_StubMetrics())
    transport = _drive_http(app, "GET", "/metrics")

    assert transport.status() == 200, f"/metrics did not return 200: {transport.status()}"
    assert transport.body_bytes() == _METRICS_BODY.encode("utf-8"), (
        f"/metrics body is not the injected exposition: {transport.body_bytes()!r}"
    )


# --------------------------------------------------------------------------- #
# Chat route is registered and streams (R2.1/R2.2).
# --------------------------------------------------------------------------- #


def test_chat_route_registered_streams_sse_200() -> None:
    """``POST /v1/chat/completions`` routes to the streaming handler: ``200`` SSE + ``[DONE]``.

    Proves the route is registered and wired to the live streaming handler (R2.1/R2.2): the
    scripted :class:`StubProviderClient` drives content frames and the recorded wire opens
    ``200 text/event-stream`` and ends with the terminal ``[DONE]`` marker.
    """
    app = _build(script=_content_script())
    transport = _drive_http(app, "POST", _CHAT_PATH)

    assert transport.status() == 200, f"chat route did not start 200: {transport.status()}"
    assert transport.headers().get("content-type") == "text/event-stream", (
        f"chat route content-type is not text/event-stream: {transport.headers()!r}"
    )
    frames = transport.body_frames()
    assert DONE_MARKER in frames, f"chat stream never emitted the terminal [DONE]: {frames!r}"


# --------------------------------------------------------------------------- #
# Placeholders + unknown routes → declared 501, never a raw 404 (R2.3/R3.4).
# --------------------------------------------------------------------------- #


def _assert_not_implemented(transport: _Transport) -> None:
    """Assert a response is the declared ``501`` ``not_implemented`` Error_Envelope rendering."""
    assert transport.status() == 501, (
        f"expected the declared 501 not_implemented, got {transport.status()}"
    )
    assert transport.status() != 404, "a registered placeholder must not be a raw 404"
    obj = json.loads(transport.body_bytes().decode("utf-8"))
    assert isinstance(obj, dict)
    error = obj.get("error")
    assert isinstance(error, dict), f"response is not an Error_Envelope body: {obj!r}"
    assert error.get("type") == "not_implemented", (
        f"placeholder body type is not not_implemented: {error!r}"
    )


def test_registered_placeholder_returns_501_not_404() -> None:
    """``POST /v1/responses`` renders the declared ``501`` placeholder via the envelope (R2.3)."""
    app = _build()
    transport = _drive_http(app, "POST", "/v1/responses")
    _assert_not_implemented(transport)


def test_unknown_route_returns_501_not_raw_framework_error() -> None:
    """An unknown ``(method, path)`` renders the declared ``501``, not a raw framework error."""
    app = _build()
    transport = _drive_http(app, "GET", "/this/route/does/not/exist")
    _assert_not_implemented(transport)


# --------------------------------------------------------------------------- #
# lifespan protocol is drained (startup/shutdown acks).
# --------------------------------------------------------------------------- #


def test_lifespan_startup_and_shutdown_are_acked() -> None:
    """A ``lifespan`` scope is drained: startup/shutdown each receive their ``.complete`` ack."""
    app = _build()

    incoming: list[Mapping[str, object]] = [
        {"type": "lifespan.startup"},
        {"type": "lifespan.shutdown"},
    ]
    acks: list[Mapping[str, object]] = []

    async def _receive() -> Mapping[str, object]:
        assert incoming, "lifespan handler pulled more events than were scripted"
        return incoming.pop(0)

    async def _send(event: Mapping[str, object]) -> None:
        acks.append(dict(event))

    async def _run() -> None:
        await app({"type": "lifespan"}, _receive, _send)  # type: ignore[operator]

    asyncio.run(_run())

    ack_types = [e.get("type") for e in acks]
    assert ack_types == ["lifespan.startup.complete", "lifespan.shutdown.complete"], (
        f"lifespan protocol not drained correctly: {ack_types!r}"
    )
