"""Small route helper split out of ``edge/routes.py`` (size gate): the registered-for-later
placeholder handler.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gateway_v2.edge import errors
from gateway_v2.edge.asgi_sse import ASGIReceive, ASGIScope, ASGISend

if TYPE_CHECKING:
    from gateway_v2.edge.routes import RouteHandler

__all__ = ("placeholder",)


def placeholder(surface: str) -> RouteHandler:
    """Build a registered-for-later handler that renders a declared ``not_implemented`` code.

    The surface is *registered* so the route table is complete and a later card (GW15/16/17/18)
    wires the real handler in place. Until then it renders the ``not_implemented`` value-code
    through the single ``Error_Envelope`` (an HTTP ``501`` JSON body via the unmapped-code
    generic path), NOT a raw ``404``-with-detail that would leak which surfaces exist.
    """

    async def _handle(scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        from gateway_v2.edge.route_emit import emit_response

        await emit_response(send, errors.render_not_implemented(surface))

    return _handle
