"""Response emission + the per-request stream stats sink, split out of ``edge/routes.py``.

``routes.py`` sat at the 800-line gate, so the three helpers every route shares live here:
:func:`emit_response` (write a rendered :class:`~gateway_v2.edge.errors.HTTPResponse` onto the ASGI
``send`` channel), :func:`emit_error_code` (render a value-code through the single
``Error_Envelope`` and emit it, optionally with the GW29 ``Retry-After`` / ``x-should-retry``
headers) and :class:`ProducedModeHolder`. ``edge/routes.py`` re-exports them.
No capacity literal, no module-level mutable.
"""

from __future__ import annotations

from gateway_v2.edge import errors
from gateway_v2.edge.asgi_sse import ASGISend
from gateway_v2.edge.console_projection import apply_projection
from gateway_v2.edge.request_holds import RequestHolds
from gateway_v2.egress.stream import StreamStats
from gateway_v2.egress.strict import StrictStats

__all__ = ("ProducedModeHolder", "emit_error_code", "emit_response")


async def emit_response(send: ASGISend, response: errors.HTTPResponse) -> None:
    """Write a framework-free :class:`errors.HTTPResponse` onto the ASGI ``send`` channel.

    The single place a rendered HTTP value becomes ASGI events: a ``http.response.start`` with
    the status + the response's ``(name, value)`` header tuple (encoded to the ASGI
    ``list[tuple[bytes, bytes]]`` header shape), then one ``http.response.body`` carrying the
    body bytes. Keeping this in one helper means every HTTP reply — the two operational
    endpoints and every ``Error_Envelope`` rendering — emits identically.
    """
    headers = [
        (name.encode("latin-1"), value.encode("latin-1")) for name, value in response.headers
    ]
    await send(
        {
            "type": "http.response.start",
            "status": response.status,
            "headers": headers,
        }
    )
    await send({"type": "http.response.body", "body": response.body})


async def emit_error_code(
    send: ASGISend, code: str, request_id: str | None = None,
    param: str | None = None, boundary: int | None = None,
    console: dict[str, object] | None = None,
    retry_after_s: float | None = None,
) -> None:
    """Render a value-code through the single ``Error_Envelope`` and emit it as HTTP.

    Every value-code that reaches a non-streaming handler passes through
    :func:`errors.render_http`, so an unmapped code renders the declared generic error rather
    than leaking raw internal detail (R3.4). The streaming chat handler uses the SSE rendering
    instead (``errors.render_sse``), so the two sides never disagree on the error shape.

    ``request_id`` is the gateway-minted per-request id (``None`` at a pre-identity site); it
    threads into the HTTP body + the ``x-request-id`` header. ``param`` names the offending field
    for a boundary parse refusal (``None`` otherwise). ``boundary`` is the permitted-maximum VALUE
    a contract-bearing caller supplies for the ``invalid_max_*`` codes (``None`` otherwise);
    ``edge/errors`` formats it into the boundary-free declared message — the capacity number is
    sourced from ``max_output_tokens_boundary()`` at the caller, never a literal here or there.
    """
    response = errors.render_http(
        code, param=param, request_id=request_id, boundary=boundary, retry_after_s=retry_after_s
    )
    await emit_response(send, apply_projection(response, console))


class ProducedModeHolder:
    """A per-request mutable sink for the pipeline's returned stats (GW13 C5).

    Both ``StreamPipeline.run`` and ``StrictWithholdPipeline.run`` return a stats
    value carrying ``produced_mode`` (and, on the INCREMENTAL side,
    ``degenerated_to_strict``). The ``_attempt`` coroutine captures that return and
    stashes it here so :meth:`ChatRoute._finish_stream` can emit the Produced_Mode
    trace frame on the wire just before the terminal ``[DONE]`` marker. It is a
    per-request LOCAL instance built in :meth:`ChatRoute._stream` (never a
    module-level mutable, gated), so nothing request-scoped is shared on the frozen
    route. ``stats`` is ``None`` until an attempt completed (a byte-less failure of
    every attempt leaves it unset, in which case no trace frame is emitted).
    """

    __slots__ = ("holds", "stats")

    def __init__(self, holds: RequestHolds | None = None) -> None:
        self.holds = holds
        self.stats: StreamStats | StrictStats | None = None
