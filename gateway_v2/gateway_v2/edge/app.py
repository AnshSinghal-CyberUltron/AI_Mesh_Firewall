"""ASGI app assembly: router, lifespan, ``/metrics``, ``/readyz`` (GW12, task 7.2).

``build_app`` assembles the serving skin as a MINIMAL raw-ASGI 3.0 application — a single
``async def app(scope, receive, send)`` callable — because the repo pulls in NO HTTP framework
(``pyproject.toml`` dependencies are ``psycopg`` + ``redis`` only; adding starlette/fastapi here
would take a framework dependency the whole package has deliberately avoided). Every HTTP reply
is a framework-free :class:`gateway_v2.edge.errors.HTTPResponse` value emitted onto the ASGI
``send`` channel via the ``emit_response`` helper (the task 7.1 pattern:
``http.response.start`` + ``http.response.body``).

What this assembles (R2.1):

* a **router** — the ``(method, path) -> handler`` table built by
  :func:`gateway_v2.edge.routes.build_routes`, with the streaming chat route wired live and the
  responses/misc/mcp/rag surfaces registered as declared not-implemented placeholders (R2.2/R2.3);
* a **lifespan** handler — drains the ASGI ``lifespan`` protocol (``startup`` / ``shutdown``) so
  a conforming server can boot and stop the app cleanly;
* **``/readyz``** — returns ``200`` when every required state dependency is available and a
  non-success ``503`` NAMING the unavailable dependency when one is missing (R2.4/R2.5), wired to
  the ``runtime.state_stamp.state_ready(view)`` contract the LGW05b handoff pins (``/readyz`` must
  call ``state_ready(view)`` and return 503 with its reason when not ready); and
* **``/metrics``** — the Prometheus text exposition computed OFF the serving loop (R2.6 / C38
  R13.2): the handler offloads the (CPU-bound, O(samples)) snapshot via ``asyncio.to_thread`` so
  a scrape never blocks the forwarding loop.

``edge`` is the top layer, so this module may import every layer below. The scanner and the
``ProviderClient`` are injected into :func:`build_app` and handed to the chat route (above
``detect`` — the correct injection site; ``egress`` must never import ``detect``, R14.1/R14.2).

**Split (noted in task 7.2).** The route table and the per-route handlers live in the sibling
:mod:`gateway_v2.edge.routes`; this module owns the ASGI protocol loop + the two operational
endpoints. The split keeps each module well under the 800-line gate and separates "how ASGI
events flow" from "what each route does".

**No capacity literal, no module-level mutable.** The route table is a function local; the HTTP
status integers here are response-status codes, not capacity-position literals; the serving
bounds all derive from the injected ``ResourceContract``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from gateway_v2.dispatch.provider import ProviderClient
from gateway_v2.dispatch.routing import DispatchRouter
from gateway_v2.domain import Finding
from gateway_v2.edge import errors
from gateway_v2.edge.cancel import InFlightControlSource, InFlightSnapshot
from gateway_v2.edge.error_frame_scan import ErrorFrameScanner
from gateway_v2.edge.routes import (
    ASGIReceive,
    ASGIScope,
    ASGISend,
    ChatRoute,
    RouteTable,
    build_routes,
    emit_response,
)
from gateway_v2.egress.output_guard import OutputResolver
from gateway_v2.egress.stream import ScanProtocol
from gateway_v2.runtime.holdback_config import load_holdback_config
from gateway_v2.runtime.resources import ResourceContract

__all__ = (
    "ASGIApplication",
    "MetricsRegistry",
    "ReadinessProbe",
    "app",
    "build_app",
)

# --------------------------------------------------------------------------- #
# Injected surfaces that have no concrete type in the package yet.
# --------------------------------------------------------------------------- #


@runtime_checkable
class MetricsRegistry(Protocol):
    """What ``/metrics`` needs from the metrics source: an off-loop text exposition (R2.6).

    ``exposition()`` returns the Prometheus text body as a ``str``. It is called inside
    ``asyncio.to_thread`` so the (O(samples), CPU-bound) snapshot+format runs OFF the serving
    loop (C38 R13.2). A registry is read-only here — the serving skin never mutates it.
    """

    def exposition(self) -> str: ...


#: The readiness probe ``/readyz`` consults (R2.4/R2.5). ``(ready, reason)``: ``reason`` is
#: ``None`` when ready and NAMES the unavailable dependency otherwise — exactly the shape
#: ``runtime.state_stamp.state_ready`` returns, so a deployment can hand that function (bound to
#: its ``StampView``) straight in. ``None`` is accepted for an unwired deployment and reports
#: ready, matching ``state_ready(None) == (True, None)``.
ReadinessProbe = Callable[[], tuple[bool, str | None]]

#: An ASGI 3.0 application: the ``async def app(scope, receive, send)`` coroutine function a
#: conforming server invokes once per connection (and once for the ``lifespan`` protocol).
ASGIApplication = Callable[[ASGIScope, ASGIReceive, ASGISend], Awaitable[None]]


# --------------------------------------------------------------------------- #
# build_app (R2.1).
# --------------------------------------------------------------------------- #


def build_app(
    *,
    contract: ResourceContract,
    provider: ProviderClient,
    scanner: ScanProtocol,
    resolver: OutputResolver,
    metrics_registry: MetricsRegistry,
    clock: Callable[[], float],
    readiness: ReadinessProbe | None = None,
    active_streams: Callable[[], int] | None = None,
    control: InFlightControlSource | None = None,
    error_scanner: ErrorFrameScanner | None = None,
) -> ASGIApplication:
    """Assemble the raw-ASGI serving skin (R2.1).

    Wires the streaming chat route to the injected ``provider`` / ``scanner`` / ``resolver`` +
    the bounded coalescer, registers the responses/misc/mcp/rag placeholders, and mounts
    ``/readyz`` + ``/metrics``. The holdback cap is loaded once at boot from the environment
    (``load_holdback_config``), matching the shipped ``load_contract`` idiom; the window and the
    per-stream buffer high-water derive from ``contract`` (no literal). ``readiness`` defaults to
    an always-ready probe for an unwired deployment (parity with ``state_ready(None)``);
    ``active_streams`` defaults to a single-stream count when a live concurrency counter is not
    yet wired (GW19 supplies the real one). ``control`` is the in-flight control source the chat
    handler polls at each chunk boundary (R10); it defaults to a fresh/available/all-clear source
    so a deployment that has not wired the kill-switch / revocation / plan feed (a GW05/GW06
    integration) still runs under the max-stream-duration deadline, which is driven by ``clock``
    vs the contract alone. ``error_scanner`` is the findings-producing detector a mid-stream
    provider error frame is scanned through before any byte is forwarded (R11); it is injected
    HERE (above ``detect``) and defaults to a no-findings scanner (forward unchanged) for a
    deployment that has not yet wired GW07/GW08's detector — a withhold only ever follows a BLOCK
    decision or a scan error, so the default is not a fail-open hole.

    Returns the ``async def app(scope, receive, send)`` callable. The function is pure assembly:
    it reads no store and starts no task — the lifespan handler is where a conforming server
    drives startup/shutdown.
    """
    cfg, _cfg_logs = load_holdback_config()
    chat = ChatRoute(
        contract=contract,
        provider=provider,
        scanner=scanner,
        resolver=resolver,
        cfg=cfg,
        router=DispatchRouter(),
        active_streams=active_streams if active_streams is not None else _one_stream,
        control=control if control is not None else _NoControlSource(),
        clock=clock,
        error_scanner=error_scanner if error_scanner is not None else _no_error_findings,
    )
    routes = build_routes(chat)
    probe: ReadinessProbe = readiness if readiness is not None else _always_ready
    skin = _ServingSkin(
        routes=routes,
        metrics_registry=metrics_registry,
        readiness=probe,
        clock=clock,
    )
    return skin.asgi


# --------------------------------------------------------------------------- #
# The serving skin: the ASGI protocol loop + the two operational endpoints.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _ServingSkin:
    """Holds the assembled routes + operational wiring; exposes the ASGI callable.

    Frozen + slotted: assembled once at boot and shared across every connection. All per-request
    state lives on the ASGI ``scope``/channels, never on the skin.
    """

    routes: RouteTable
    metrics_registry: MetricsRegistry
    readiness: ReadinessProbe
    clock: Callable[[], float]

    async def asgi(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        """The ASGI 3.0 entry point: dispatch by ``scope["type"]``.

        ``lifespan`` is drained by :meth:`_lifespan`; an ``http`` connection is routed by
        ``(method, path)``. A websocket or any other scope type is declined cleanly (the serving
        skin speaks HTTP + lifespan only).
        """
        scope_type = scope.get("type")
        if scope_type == "lifespan":
            await self._lifespan(receive, send)
            return
        if scope_type == "http":
            await self._http(scope, receive, send)
            return
        # Any other scope (e.g. websocket) is not served here; close without a protocol error.

    async def _lifespan(self, receive: ASGIReceive, send: ASGISend) -> None:
        """Drain the ASGI ``lifespan`` protocol so a server can boot/stop the app.

        Answers ``lifespan.startup`` with ``lifespan.startup.complete`` and
        ``lifespan.shutdown`` with ``lifespan.shutdown.complete``. The serving skin holds no
        store connection of its own to open or close (the state loop + provider pools are owned
        elsewhere), so the handler is a clean acknowledgement loop.
        """
        while True:
            message = await receive()
            message_type = message.get("type")
            if message_type == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message_type == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def _http(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        """Route one HTTP connection by ``(method, path)``.

        ``/readyz`` and ``/metrics`` are the two operational endpoints handled inline; every
        other path is looked up in the route table. An unknown ``(method, path)`` renders the
        declared ``not_implemented`` placeholder through the single ``Error_Envelope`` (never a
        raw framework 404), so an unmapped route leaks no internal routing shape.
        """
        method = _method(scope)
        path = _path(scope)
        if path == "/readyz":
            await self._readyz(send)
            return
        if path == "/metrics":
            await self._metrics(send)
            return
        handler = self.routes.get((method, path))
        if handler is None:
            await emit_response(send, errors.render_not_implemented(path))
            return
        await handler(scope, receive, send)

    async def _readyz(self, send: ASGISend) -> None:
        """``/readyz``: ``200`` when ready, else ``503`` naming the unavailable dep (R2.4/R2.5).

        Consults the injected :data:`ReadinessProbe` — wired by a deployment to
        ``runtime.state_stamp.state_ready(view)`` so there is ONE definition of ready (the
        LGW05b handoff pins this: ``/readyz`` must call ``state_ready(view)`` and return 503 with
        its reason when state is unverified). A ``(True, None)`` renders ``200 ok``; a
        ``(False, reason)`` renders ``503`` with the reason as the JSON body, so the 503 is
        diagnosable.
        """
        ready, reason = self.readiness()
        if ready:
            await emit_response(send, _ok_body())
            return
        await emit_response(send, _not_ready_body(reason))

    async def _metrics(self, send: ASGISend) -> None:
        """``/metrics``: Prometheus text exposition computed OFF the serving loop (R2.6).

        The snapshot+format is CPU-bound and O(samples), so it runs in ``asyncio.to_thread`` and
        the forwarding loop is never blocked by a scrape (C38 R13.2). The body is the registry's
        text exposition verbatim.
        """
        body = await asyncio.to_thread(self.metrics_registry.exposition)
        await emit_response(send, _metrics_body(body))


# --------------------------------------------------------------------------- #
# Operational-endpoint response bodies (framework-free HTTPResponse values).
# --------------------------------------------------------------------------- #

_TEXT_PLAIN = "text/plain; charset=utf-8"
_PROM_MEDIA_TYPE = "text/plain; version=0.0.4; charset=utf-8"
_READY_STATUS = 200
_NOT_READY_STATUS = 503


def _ok_body() -> errors.HTTPResponse:
    """The ``/readyz`` success body: ``200`` with a plain ``ok`` marker."""
    return errors.HTTPResponse(
        status=_READY_STATUS,
        body=b"ok",
        headers=(("content-type", _TEXT_PLAIN),),
    )


def _not_ready_body(reason: str | None) -> errors.HTTPResponse:
    """The ``/readyz`` not-ready body: ``503`` naming the unavailable dependency (R2.5).

    The ``reason`` the probe returned is the body verbatim (a non-empty string from
    ``state_ready``); a missing reason falls back to a declared generic ``not ready`` so the
    body is never empty (an empty 503 would make the condition undiagnosable).
    """
    detail = reason if reason else "not ready"
    return errors.HTTPResponse(
        status=_NOT_READY_STATUS,
        body=detail.encode("utf-8"),
        headers=(("content-type", _TEXT_PLAIN),),
    )


def _metrics_body(exposition: str) -> errors.HTTPResponse:
    """The ``/metrics`` body: ``200`` with the Prometheus text exposition."""
    return errors.HTTPResponse(
        status=_READY_STATUS,
        body=exposition.encode("utf-8"),
        headers=(("content-type", _PROM_MEDIA_TYPE),),
    )


# --------------------------------------------------------------------------- #
# Scope helpers + injected defaults.
# --------------------------------------------------------------------------- #


def _method(scope: ASGIScope) -> str:
    """The HTTP method from an ASGI ``http`` scope (upper-cased; empty when absent)."""
    value = scope.get("method")
    return value.upper() if isinstance(value, str) else ""


def _path(scope: ASGIScope) -> str:
    """The request path from an ASGI ``http`` scope (empty when absent)."""
    value = scope.get("path")
    return value if isinstance(value, str) else ""


def _always_ready() -> tuple[bool, str | None]:
    """The default readiness probe for an unwired deployment (parity with ``state_ready(None)``)."""
    return True, None


def _no_error_findings(_frame: str) -> Sequence[Finding]:
    """The default error-frame findings scanner when GW07/GW08's detector is not yet wired.

    Returns NO findings, so a mid-stream error frame resolves to ALLOW and is forwarded unchanged
    (R11.2) — the same thin-handler posture as the content path, which runs the shipped pipeline
    with ``detector=None`` (no findings) until GW15 wires the real detector. This is NOT a
    fail-open hole: a withhold only happens on a BLOCK decision or a scan error (R11.3/R11.4), and
    the default scanner never raises; a deployment that must guard error frames injects the real
    findings detector through :func:`build_app`'s ``error_scanner`` (above ``detect``), at which
    point a secret in an error frame is redacted/blocked exactly as in released content.
    """
    return ()


def _one_stream() -> int:
    """The default live-concurrency count when GW19's counter is not yet wired.

    One active stream — so the coalescer's derived per-stream high-water is computed for a single
    stream. Not a capacity literal: it is a count of in-flight streams, the quantity the live
    counter reports, not a configured bound.
    """
    return 1


class _NoControlSource:
    """The default in-flight control source when the control-plane feed is not yet wired.

    Reports a FRESH, AVAILABLE snapshot with every signal clear, so a stream runs under the
    max-duration deadline alone until GW05/GW06 inject the real kill-switch / revocation / plan
    feed. ``age_s=0.0`` keeps it inside any derived ``max_snapshot_age_s()`` (so it is not treated
    as stale), and ``available=True`` means it does not trip the fail-closed path for a deployment
    that simply has not wired the control plane yet. A real source replaces this with the signed
    snapshot read; the max-stream-duration trigger is active regardless, since that bound is purely
    the injected clock vs the contract.
    """

    __slots__ = ()

    def snapshot(self) -> InFlightSnapshot:
        return InFlightSnapshot(
            available=True,
            age_s=0.0,
            kill_switch_engaged=False,
            key_revoked=False,
            plan_changed=False,
        )


# --------------------------------------------------------------------------- #
# Module-level app sentinel.
# --------------------------------------------------------------------------- #

#: ``None`` until a deployment calls :func:`build_app` with its injected dependencies. The old
#: ``app = None`` stub is kept as the un-built default so ``from gateway_v2.edge import app``
#: still imports; a real server boots the app with ``build_app(...)``.
app: ASGIApplication | None = None
