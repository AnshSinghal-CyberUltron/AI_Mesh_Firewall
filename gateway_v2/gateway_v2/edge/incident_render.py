"""GW29 incident refusal rendering, split out of ``edge/routes.py`` (799 lines).

A refusal whose code is in :data:`~gateway_v2.edge.errors.INCIDENT_HTTP_CODES` renders as HTTP 503
with ``Retry-After`` and ``x-should-retry: false`` even when the request asked to stream: the gate
refuses before any SSE head is sent, so a real HTTP status can carry the retry hint (6.4a). This
deliberately differs from other post-parse refusals. HTTP objects exist only here, in ``edge``.
"""

from __future__ import annotations

from gateway_v2.edge.asgi_sse import ASGISend
from gateway_v2.edge.console_projection import console_from_holds
from gateway_v2.edge.request_holds import RequestHolds
from gateway_v2.edge.route_emit import emit_error_code

__all__ = ("render_incident_refusal",)


async def render_incident_refusal(
    send: ASGISend,
    code: str,
    request_id: str | None,
    holds: RequestHolds | None,
    retry_after_s: float | None,
) -> None:
    """Emit the 503 JSON envelope for an incident ``code`` (never an SSE head)."""
    console = (
        None
        if holds is None
        else console_from_holds(holds, output_action=None, provider_called=False)
    )
    await emit_error_code(
        send, code, request_id, None, None, console, retry_after_s
    )
