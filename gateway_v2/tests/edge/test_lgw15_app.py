# Feature: chat-surface-gw15
# Validates: Requirements 11.2
"""Boot-wiring + ProviderClient injection unit tests for the GW15 chat surface (task 6.3).

Example/unit tests (NO ≥10k property loop, NO ``hypothesis``) over the REAL boot path:
:func:`gateway_v2.edge.app.build_app` constructs the single :class:`ChatRoute` with every §10.4
GW15 dependency and mounts it in the raw-ASGI serving skin. These assert the *wiring*, not the
pipeline behaviour (that is covered by the lifecycle / stream / output property tests):

* ``build_app`` builds exactly ONE :class:`ChatRoute` carrying every GW15 dependency, and accepts
  the injected :class:`StubProviderClient` as its ``ProviderClient`` with NO real socket — the
  stub is an in-process, scripted substitute (R11.2); the real HTTP/SSE socket is a deferred live
  gate and is never constructed here.
* the two chat/completions routes — ``("POST", "/v1/chat/completions")`` and
  ``("POST", "/v1/completions")`` — RESOLVE to the live chat handler (they do NOT render the
  declared ``not_implemented`` placeholder).
* the GW16/17/18 placeholders (``("POST", "/v1/responses")`` and siblings) stay registered as the
  declared ``501 not_implemented`` via the single ``Error_Envelope`` (not a raw 404).

The route table is assembled through the same :func:`gateway_v2.edge.routes.build_routes` the boot
path calls, so introspecting the table it returns from the boot-built :class:`ChatRoute` observes
exactly what ``build_app`` mounts. The ASGI-level 501 assertions reuse the ``_Transport`` idiom
established by ``test_lgw12_app.py`` so the in-process transport is not reinvented.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Mapping, Sequence

from gateway_v2.detect.holdback import scan as holdback_scan
from gateway_v2.dispatch.provider import ProviderClient, StubProviderClient, UpstreamEvent
from gateway_v2.dispatch.routing import DispatchRouter
from gateway_v2.dispatch.transform import Transform
from gateway_v2.domain import Finding
from gateway_v2.edge.app import build_app
from gateway_v2.edge.routes import ChatRoute, build_routes
from gateway_v2.edge.wire.openai import OpenAIRequestParser, OpenAIResponseRenderer
from gateway_v2.egress.output_guard import MinimalOutputResolver
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals
from gateway_v2.runtime.stream_metrics import PerRequestExports

_SEED = 0x6A12

#: The two chat/completions routes GW15 wires to the live handler (R2.2).
_CHAT_ROUTES: tuple[tuple[str, str], ...] = (
    ("POST", "/v1/chat/completions"),
    ("POST", "/v1/completions"),
)

#: GW16/17/18 (and misc) surfaces registered as declared not-implemented placeholders (R2.3).
_PLACEHOLDER_ROUTES: tuple[tuple[str, str], ...] = (
    ("POST", "/v1/responses"),
    ("POST", "/v1/embeddings"),
    ("GET", "/v1/models"),
    ("POST", "/v1/moderations"),
    ("POST", "/v1/mcp"),
    ("POST", "/v1/rag/query"),
    ("POST", "/v1/vector/query"),
)


# --------------------------------------------------------------------------- #
# Fixtures: a serviceable contract + a scripted, socket-free provider.
# --------------------------------------------------------------------------- #


def _contract() -> ResourceContract:
    """A serviceable contract (same ``from_signals`` fixture shape as the LGW12 app tests)."""
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


def _stub_provider(script: Sequence[UpstreamEvent] | None = None) -> StubProviderClient:
    """The in-process, scripted :class:`StubProviderClient` — no real socket (R11.2)."""
    return StubProviderClient(
        script=tuple(script if script is not None else ()),
        rng=random.Random(_SEED),
    )


class _StubMetrics:
    """A minimal metrics registry: a fixed text exposition (plain registry, no directory)."""

    __slots__ = ()

    def exposition(self) -> str:
        return "# gateway_v2 metrics\n"


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


def _build_app(provider: ProviderClient) -> object:
    """Assemble the REAL ASGI app with the injected ``provider`` (no socket)."""
    return build_app(
        contract=_contract(),
        provider=provider,
        scanner=holdback_scan,
        resolver=MinimalOutputResolver(),
        metrics_registry=_StubMetrics(),
        clock=_Clock(),
        error_scanner=_no_error_findings,
    )


# --------------------------------------------------------------------------- #
# ChatRoute wiring: one route, every dep, StubProviderClient injected (R11.2).
# --------------------------------------------------------------------------- #


def test_build_routes_from_boot_chatroute_wires_the_two_chat_paths_to_one_handler() -> None:
    """``build_app`` mounts ONE :class:`ChatRoute` and both chat paths resolve to its handler.

    The boot path builds the single frozen :class:`ChatRoute` and hands it to
    :func:`build_routes`; the two chat/completions routes bind to the SAME live
    ``ChatRoute.handle`` bound method, proving one route serves both OpenAI chat surfaces (R2.2).
    """
    provider = _stub_provider()
    route = _boot_chatroute(provider)
    assert isinstance(route, ChatRoute), "boot did not build a ChatRoute"

    table = build_routes(route)
    for key in _CHAT_ROUTES:
        assert key in table, f"chat route {key!r} is not registered"
        assert table[key] == route.handle, (
            f"chat route {key!r} is not wired to the live ChatRoute.handle"
        )
    # Both chat surfaces share the SAME live handler (one route serves both).
    assert table[_CHAT_ROUTES[0]] == table[_CHAT_ROUTES[1]], (
        "the two chat/completions routes do not resolve to the one handler"
    )


def test_boot_chatroute_carries_every_gw15_dependency() -> None:
    """The boot-built :class:`ChatRoute` carries every §10.4 GW15 dependency (R11.1).

    Asserts each injected/constructed dep is present and of the expected shape so no lifecycle
    phase has a missing seam: the parser, renderer, transform, dispatch router, the input
    detector, identity resolver, snapshot source, admission + budget deciders, and the
    per-request exports producer.
    """
    provider = _stub_provider()
    route = _boot_chatroute(provider)

    assert isinstance(route.parser, OpenAIRequestParser), "parser not wired"
    assert isinstance(route.renderer, OpenAIResponseRenderer), "renderer not wired"
    assert isinstance(route.transform, Transform), "transform not wired"
    assert isinstance(route.router, DispatchRouter), "dispatch router not wired"
    assert isinstance(route.exports, PerRequestExports), "per-request exports not wired"
    # The lifecycle seams are present (callable / protocol-shaped), fail-closed by default.
    assert callable(route.input_detector), "input detector seam missing"
    assert hasattr(route.identity, "resolve"), "identity resolver seam missing (no resolve)"
    assert callable(route.snapshot_source), "snapshot source seam missing"
    assert route.admission is not None, "admission decider seam missing"
    assert route.quota is not None, "budget decider seam missing"
    assert callable(route.clock), "clock seam missing"


def test_boot_injects_stub_provider_with_no_real_socket() -> None:
    """``build_app`` accepts the :class:`StubProviderClient` as the ``ProviderClient`` (R11.2).

    The boot-built route carries the EXACT injected stub instance — the in-process substitute,
    not a freshly-constructed real client — and the stub satisfies the ``ProviderClient``
    protocol while holding no socket (its only state is the scripted events + the abort latch).
    """
    provider = _stub_provider()
    route = _boot_chatroute(provider)

    assert route.provider is provider, "boot did not inject the given StubProviderClient"
    assert isinstance(route.provider, StubProviderClient), "provider is not the in-process stub"
    assert isinstance(route.provider, ProviderClient), (
        "the injected stub does not satisfy the ProviderClient protocol"
    )
    # No real socket: the stub's only attributes are the scripted replay + the abort latch.
    assert set(StubProviderClient.__slots__) == {"_aborted", "rng", "script"}, (
        "StubProviderClient grew socket-bearing state"
    )


def _boot_chatroute(provider: ProviderClient) -> ChatRoute:
    """Recover the single :class:`ChatRoute` the boot path mounts, to introspect its wiring.

    ``build_app`` returns an opaque ASGI callable wrapping the frozen ``_ServingSkin`` + its route
    table, so this assembles the real app with the injected ``provider`` and recovers the mounted
    :class:`ChatRoute` by resolving the live chat handler back to its bound ``__self__`` — the
    EXACT route the boot path built, not a reconstruction.
    """
    app = _build_app(provider)
    table = _mounted_table(app)
    handler = table[("POST", "/v1/chat/completions")]
    bound_self = getattr(handler, "__self__", None)
    assert isinstance(bound_self, ChatRoute), "could not recover the mounted ChatRoute from boot"
    return bound_self


def _mounted_table(app: object) -> Mapping[tuple[str, str], object]:
    """Recover the route table the boot ``_ServingSkin`` holds (introspection for the wiring test).

    ``build_app`` wraps the table in the frozen ``_ServingSkin``; the returned ASGI callable is the
    skin's bound ``asgi`` method, so ``handler.__self__`` is the skin and ``skin.routes`` is the
    exact mounted table. This reads the boot-assembled table without reconstructing it.
    """
    skin = getattr(app, "__self__", None)
    routes = getattr(skin, "routes", None)
    assert isinstance(routes, Mapping), "could not recover the mounted route table from build_app"
    return routes


# --------------------------------------------------------------------------- #
# In-process ASGI transport (the test_lgw12_app.py idiom).
# --------------------------------------------------------------------------- #


class _Transport:
    """A one-connection in-process ASGI transport: no socket, records every send event."""

    __slots__ = ("events", "_incoming")

    def __init__(self, *, body: bytes) -> None:
        self.events: list[Mapping[str, object]] = []
        self._incoming: list[Mapping[str, object]] = [
            {"type": "http.request", "body": body, "more_body": False},
            {"type": "http.disconnect"},
        ]

    @staticmethod
    def http_scope(method: str, path: str) -> dict[str, object]:
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

    def start_event(self) -> Mapping[str, object]:
        starts = [e for e in self.events if e.get("type") == "http.response.start"]
        assert len(starts) == 1, f"expected exactly one response.start, got {len(starts)}"
        return starts[0]

    def status(self) -> int:
        status = self.start_event().get("status")
        assert isinstance(status, int), f"response.start carries no int status: {status!r}"
        return status

    def body_bytes(self) -> bytes:
        out = bytearray()
        for event in self.events:
            if event.get("type") != "http.response.body":
                continue
            body = event.get("body")
            if isinstance(body, (bytes, bytearray)):
                out.extend(body)
        return bytes(out)


def _drive_http(app: object, method: str, path: str, *, body: bytes = b"{}") -> _Transport:
    """Drive one in-process HTTP request through ``app`` and return the recording transport."""
    transport = _Transport(body=body)

    async def _run() -> None:
        await app(transport.http_scope(method, path), transport.receive, transport.send)  # type: ignore[operator]

    asyncio.run(_run())
    return transport


def _is_not_implemented(transport: _Transport) -> bool:
    """True iff the response is the declared ``501 not_implemented`` Error_Envelope rendering."""
    if transport.status() != 501:
        return False
    try:
        obj = json.loads(transport.body_bytes().decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False
    error = obj.get("error") if isinstance(obj, dict) else None
    return isinstance(error, dict) and error.get("type") == "not_implemented"


# --------------------------------------------------------------------------- #
# Route resolution: chat routes resolve (NOT 501); placeholders stay 501.
# --------------------------------------------------------------------------- #


def test_chat_routes_resolve_and_do_not_render_not_implemented() -> None:
    """The two chat/completions routes resolve to the live handler, not the 501 placeholder (R2.2).

    A streaming-shaped empty script drives the lifecycle to a (fail-closed) refusal, which is the
    live chat handler's own render path — crucially NOT the ``not_implemented`` placeholder a
    GW16/17/18 surface returns. The assertion is that these routes are WIRED to the chat handler
    (they never produce the registered-placeholder 501), independent of the refusal code.
    """
    for method, path in _CHAT_ROUTES:
        app = _build_app(_stub_provider())
        transport = _drive_http(app, method, path)
        assert not _is_not_implemented(transport), (
            f"chat route {(method, path)!r} rendered the not_implemented placeholder — "
            "it is not wired to the live chat handler"
        )


def test_placeholder_routes_stay_not_implemented() -> None:
    """Every GW16/17/18 (+ misc) placeholder renders the declared ``501 not_implemented`` (R2.3)."""
    app = _build_app(_stub_provider())
    for method, path in _PLACEHOLDER_ROUTES:
        transport = _drive_http(app, method, path)
        assert transport.status() == 501, (
            f"placeholder {(method, path)!r} did not render 501: {transport.status()}"
        )
        assert transport.status() != 404, (
            f"placeholder {(method, path)!r} rendered a raw 404, not the declared 501"
        )
        assert _is_not_implemented(transport), (
            f"placeholder {(method, path)!r} is not the not_implemented Error_Envelope"
        )


def test_responses_placeholder_is_not_implemented_not_404() -> None:
    """``POST /v1/responses`` (the GW16/17/18 surface) stays ``501 not_implemented`` (R2.3)."""
    app = _build_app(_stub_provider())
    transport = _drive_http(app, "POST", "/v1/responses")
    assert _is_not_implemented(transport), (
        f"/v1/responses did not render the declared 501 not_implemented: {transport.status()}"
    )
