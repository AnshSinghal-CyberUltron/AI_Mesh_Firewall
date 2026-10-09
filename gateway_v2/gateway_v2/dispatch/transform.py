"""Byte-verified request/response shaping between wire and provider formats (GW12).

``Transform`` is the dispatch-layer codec that shapes a :class:`GatewayRequest`
into an :class:`~gateway_v2.dispatch.provider.UpstreamRequest` for the provider
(``to_provider``) and shapes a provider
:class:`~gateway_v2.dispatch.provider.UpstreamEvent` back into the egress
:class:`~gateway_v2.egress.stream.DownstreamFrame` (``from_provider``).

**Pure.** No clock, no randomness, no I/O — ``to_provider`` and ``from_provider``
are deterministic functions of their argument alone, so the round-trip is
directly testable (Design: Components §5; Requirements 8.4).

**Round-trip (Property 8, transform half).** For a well-formed payload ``x``,
``from_provider(echo(to_provider(x))) ≡ x`` byte-for-byte. ``echo`` models the
provider reflecting a submitted request back as a single terminal event — the
only step a *pure* transform can stand in for the real network. It carries the
request's text deltas through unchanged so the equivalence is a byte identity on
the content, verified by task 4.4's property test.

**Layering.** ``dispatch`` sits ABOVE ``egress`` in the import-linter layer
order (``edge > admit > plan > detect > resolve > dispatch > egress > audit >
runtime > contracts > domain``), so this module may import ``egress``
(``DownstreamFrame``) and its sibling ``dispatch.provider``
(``UpstreamRequest`` / ``UpstreamEvent``). The two provider value types are
OWNED by ``dispatch/provider.py`` (task 4.3) and imported here — never
redefined — so there is exactly one spelling of each.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from gateway_v2.egress.stream import DownstreamFrame

__all__ = (
    "BODY_DELTAS_KEY",
    "BODY_MODEL_KEY",
    "GatewayRequest",
    "Transform",
    "echo",
)

#: Stable keys under which ``to_provider`` packs the request into
#: ``UpstreamRequest.body``. Named constants (not inline literals) so the pack
#: and unpack sides cannot drift and the round-trip stays a byte identity.
BODY_DELTAS_KEY = "text_deltas"
BODY_MODEL_KEY = "model"


@dataclass(frozen=True, slots=True)
class GatewayRequest:
    """The Gateway wire-format request the Transform consumes (R8.4).

    Minimal frozen-slotted value type: a request carries its routing
    ``destination``, the upstream ``model`` to call, and the text payload as
    ``text_deltas`` — the same ``(channel_name, delta_text)`` shape the egress
    ``DownstreamFrame`` / ``UpstreamChunk`` carry, so the content survives the
    request → provider → event → downstream path without reshaping.

    Defined here because no ``GatewayRequest`` type exists yet elsewhere in the
    package (``domain/`` has ``RequestContext`` / ``ExecutionPlan`` but no wire
    request). It lives in ``dispatch`` alongside its only consumer; a later card
    that needs it in a lower layer can relocate it with a re-export shim.
    """

    destination: str
    model: str
    text_deltas: tuple[tuple[str, str], ...]


class Transform:
    """Byte-verified request/response shaping (R8.4).

    Stateless and pure: a single shared instance is safe to reuse across streams
    because neither method touches instance or module state.
    """

    __slots__ = ()

    def to_provider(self, req: GatewayRequest) -> UpstreamRequest:
        """Shape a wire ``GatewayRequest`` into a provider ``UpstreamRequest``.

        The text payload is packed into an immutable ``body`` under
        :data:`BODY_DELTAS_KEY` / :data:`BODY_MODEL_KEY`; ``destination`` carries
        the routing selection through unchanged. The body is a
        ``MappingProxyType`` so the returned ``UpstreamRequest`` is as immutable
        as its frozen dataclass promises.
        """
        body: MappingProxyType[str, object] = MappingProxyType(
            {
                BODY_MODEL_KEY: req.model,
                BODY_DELTAS_KEY: req.text_deltas,
            }
        )
        return UpstreamRequest(destination=req.destination, body=body)

    def from_provider(self, ev: UpstreamEvent) -> DownstreamFrame:
        """Shape a provider ``UpstreamEvent`` into an egress ``DownstreamFrame``.

        An event carrying a mid-stream ``error_frame`` becomes a terminal
        ``DownstreamFrame`` whose ``error_code`` is set and whose ``text_deltas``
        are empty — the SSE codec renders it as an ``Error_Frame`` and emits no
        content after it (R1.3). Otherwise the event's text deltas pass through
        unchanged into a content frame.
        """
        if ev.error_frame is not None:
            return DownstreamFrame(text_deltas=(), error_code=ev.error_frame)
        return DownstreamFrame(text_deltas=ev.text_deltas)


def echo(req: UpstreamRequest) -> UpstreamEvent:
    """Reflect a submitted ``UpstreamRequest`` back as a terminal ``UpstreamEvent``.

    Models the provider echoing the request's text payload back as the sole
    terminal event, so a *pure* transform can exercise the full
    request → event path for the round-trip property (Property 8, transform
    half). The packed deltas are read back out under :data:`BODY_DELTAS_KEY`;
    a well-formed body always carries that key with the original tuple, so
    ``from_provider(echo(to_provider(x)))`` reproduces ``x``'s deltas byte for
    byte.
    """
    raw = req.body.get(BODY_DELTAS_KEY, ())
    deltas = _as_text_deltas(raw)
    return UpstreamEvent(text_deltas=deltas, final=True)


def _as_text_deltas(raw: object) -> tuple[tuple[str, str], ...]:
    """Narrow a packed body value back to the ``text_deltas`` shape.

    ``UpstreamRequest.body`` is typed ``Mapping[str, object]``, so the packed
    tuple comes back as ``object``; this re-establishes the static shape for a
    well-formed body (the only case the round-trip claims) and returns an empty
    tuple for anything else rather than forwarding an ill-typed value.
    """
    if not isinstance(raw, tuple):
        return ()
    deltas: list[tuple[str, str]] = []
    for item in raw:
        if (
            isinstance(item, tuple)
            and len(item) == 2
            and isinstance(item[0], str)
            and isinstance(item[1], str)
        ):
            deltas.append((item[0], item[1]))
        else:
            return ()
    return tuple(deltas)
