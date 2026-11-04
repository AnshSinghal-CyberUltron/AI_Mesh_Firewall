"""Small route helpers split out of ``edge/routes.py`` (size gate): the per-request produced-mode
holder and the registered-for-later placeholder handler.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gateway_v2.edge import errors
from gateway_v2.edge.asgi_sse import ASGIReceive, ASGIScope, ASGISend

if TYPE_CHECKING:
    from gateway_v2.edge.request_holds import RequestHolds
    from gateway_v2.edge.routes import RouteHandler
    from gateway_v2.egress.stream import StreamStats
    from gateway_v2.egress.strict import StrictStats

__all__ = ("ProducedModeHolder", "placeholder")


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


def placeholder(surface: str) -> RouteHandler:
    """Build a registered-for-later handler that renders a declared ``not_implemented`` code.

    The surface is *registered* so the route table is complete and a later card (GW15/16/17/18)
    wires the real handler in place. Until then it renders the ``not_implemented`` value-code
    through the single ``Error_Envelope`` (an HTTP ``501`` JSON body via the unmapped-code
    generic path), NOT a raw ``404``-with-detail that would leak which surfaces exist.
    """

    async def _handle(scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        from gateway_v2.edge.routes import emit_response

        await emit_response(send, errors.render_not_implemented(surface))

    return _handle
