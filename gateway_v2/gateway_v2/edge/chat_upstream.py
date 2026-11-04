"""The single chat upstream-open seam (GW28 design C6).

Both chat open sites (streaming ``routes.py::_next_attempt._attempt`` and non-streaming
``openai_nonstream._collect_output_channels``) call :func:`open_chat_upstream`. With
``deps.routing is None`` it returns exactly ``deps.provider.open(request)`` (today's behaviour).
With routing set it builds the out-of-band ``RoutingContext``, attaches a fresh per-request
``RoutingTrace`` to the request's ``RequestHolds`` and returns ``RoutedProvider.open_routed``.
This module is the ONLY caller of ``open_routed`` (AST-tested, P12), so enforcement on both sides
of the open (input before, output guard + error-frame scan after) applies to every attempt.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from typing import TYPE_CHECKING

from gateway_v2.dispatch.openai_upstream import BODY_OPENAI_KEY
from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.routed_provider import RoutedProvider
from gateway_v2.dispatch.routing_types import RoutingContext, RoutingDecision
from gateway_v2.domain.plan import ExecutionPlan
from gateway_v2.edge.routing_trace import RoutingTrace

if TYPE_CHECKING:
    from gateway_v2.edge.request_holds import RequestHolds
    from gateway_v2.edge.routes import ChatRoute

__all__ = ("open_chat_upstream", "routing_context")

_NONE = "-"


def _requested_model(body: Mapping[str, object]) -> str | None:
    openai = body.get(BODY_OPENAI_KEY)
    if not isinstance(openai, Mapping):
        return None
    model = openai.get("model")
    return model if isinstance(model, str) else None


def routing_context(
    plan: ExecutionPlan | None,
    body: Mapping[str, object],
    request_key: str,
    sink: Callable[[RoutingDecision], None],
    *,
    max_ident_len: int,
) -> RoutingContext | None:
    """The routing inputs for a verified body, or None (-> ``routing_context_missing``)."""
    model = _requested_model(body)
    if plan is None or model is None:
        return None
    return RoutingContext.from_plan(
        plan,
        request_key=request_key,
        requested_model=model,
        sink=sink,
        max_ident_len=max_ident_len,
    )


def open_chat_upstream(
    deps: ChatRoute,
    plan: ExecutionPlan | None,
    request: UpstreamRequest,
    holds: RequestHolds,
) -> AsyncIterator[UpstreamEvent]:
    """Open the chat upstream: plain provider, or the routed one when ``deps.routing`` is set."""
    # ``getattr``: shipped tests drive this path with duck-typed deps lacking ``routing``.
    routing: RoutedProvider | None = getattr(deps, "routing", None)
    if routing is None:
        return deps.provider.open(request)
    contract = deps.contract
    model = _requested_model(request.body)
    alias = model if model is not None and routing.serves_alias(model) else _NONE
    trace = RoutingTrace(cap=contract.routing_max_attempts() + 1, alias=alias)
    holds.note_routing(trace)
    ctx = routing_context(
        plan,
        request.body,
        holds.request_id,
        trace.append,
        max_ident_len=contract.routing_max_identifier_len(),
    )
    return routing.open_routed(request, ctx)
