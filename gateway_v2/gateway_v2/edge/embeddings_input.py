"""The embeddings input path: fold, scan, stage with a per-item shape check (Group A).

``edge/embeddings_input.py`` moves the ``/v1/embeddings`` input fold out of
``edge/openai_misc.py`` (task 26.3 deletes ``EmbeddingsSurface._input_as_deltas``
/ ``_input_scan`` there and calls these functions), so the embeddings surface and
chat share ONE input redactor -- :mod:`gateway_v2.edge.input_enforcement` -- rather
than each spelling its own. It is a sibling module (not more lines in
``openai_misc.py``) so both stay under ``check_sizes``.

**``embeddings_deltas``.** One ``("embeddings_input", item)`` pair per
``req.input_items``, in request order -- the exact channel spelling and order the
shipped ``_input_as_deltas`` emitted. Every other function here folds through it so
the delta shape is defined once.

**``scan_embeddings``.** Calls :func:`~gateway_v2.edge.input_enforcement.scan_deltas`
with ``join_runs=()``: each embeddings item is an INDEPENDENT scannable text, so
the chat split-across-content-parts concern (C21) does not apply -- there are no
join runs. ``scan_deltas`` already shifts each item's finding spans by that item's
base offset in the concatenated item text, so embeddings findings live in the same
request-content coordinate space chat findings do. The ``on_invoke`` hook is
REQUIRED here: the shipped code called ``exports.observe_detector_invocation()``
once per item, so the surface (26.3) passes
``on_invoke=lambda _text: exports.observe_detector_invocation()`` and the per-item
invocation metric is preserved byte-for-byte with today's behaviour.

**``stage_embeddings``.** Reuses the chat
:func:`~gateway_v2.edge.input_enforcement.stage_upstream` authorize -> verify flow
(``resolve.authorize`` -> ``dispatch/authorized.py::verified_upstream`` /
``apply_and_verify``) -- there is no second redactor. A refuse code from that flow
(``input_blocked`` from a BLOCK authorization, ``input_unmaskable`` from a
``VerificationFailure``) is propagated unchanged. Then it ADDS the embeddings SHAPE
check (Property 15) on the STAGED bytes: the staged delta count equals
``len(req.input_items)``, every channel is ``embeddings_input``, and
``req.input_is_string`` implies EXACTLY ONE delta. A shape mismatch means the staged
bytes dropped / merged / duplicated an item -- an unmaskable redaction that must not
reach the provider -- and returns
:data:`~gateway_v2.edge.input_enforcement.REFUSE_INPUT_UNMASKABLE`, the SAME code
``stage_upstream`` already returns for a ``VerificationFailure`` (both
halves land on ``input_unmaskable``, failing closed). The staged deltas are read back
out of the ``UpstreamRequest`` body under the shipped
:data:`~gateway_v2.dispatch.transform.BODY_DELTAS_KEY` with the SAME ``(channel,
text)`` validation convention ``dispatch/authorized.py::_deltas_of`` uses -- not a
fabricated key.

**Preservation (Req 3.16).** A decision with no transformations (ALLOW / FLAG /
MONITOR-only) flows through ``apply_and_verify`` unchanged, so clean items reach the
provider byte-identical, one delta per item, in request order.

**No capacity literal, no module-level mutable.** The channel spelling is a plain
module-level string; there is no module-level list / dict / set. ``edge`` is the top
of the import-linter layer order, so importing ``dispatch`` + the ``edge`` sibling
below it is contract-legal; this module mints no authorization and never reads or
rebuilds the dispatch witness, so the provenance gate stays green (``stage_upstream``
does the staging).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from gateway_v2.dispatch.provider import UpstreamRequest
from gateway_v2.dispatch.transform import BODY_DELTAS_KEY, GatewayRequest, Transform
from gateway_v2.domain import Decision, Finding
from gateway_v2.edge.input_enforcement import (
    REFUSE_INPUT_UNMASKABLE,
    InputScan,
    scan_deltas,
    stage_upstream,
)
from gateway_v2.edge.wire.openai_misc import MiscEmbeddingsRequest
from gateway_v2.egress.output_guard import OutputResolver

__all__ = (
    "embeddings_deltas",
    "scan_embeddings",
    "stage_embeddings",
)

#: The one channel spelling every embeddings input item rides under -- the exact
#: spelling the shipped ``EmbeddingsSurface._input_as_deltas`` emitted, kept here so
#: the fold + the shape check cannot drift. A channel name, not a capacity-position
#: literal (serving bounds live in ``runtime/resources.py``).
_EMBEDDINGS_CHANNEL = "embeddings_input"


def embeddings_deltas(req: MiscEmbeddingsRequest) -> tuple[tuple[str, str], ...]:
    """One ``("embeddings_input", item)`` delta per input item, in request order.

    Moves ``EmbeddingsSurface._input_as_deltas`` out of ``edge/openai_misc.py`` (task
    26.3 deletes it there and calls this). The ``Transform`` consumes the
    ``(channel, text)`` delta shape; the embeddings items ride under the
    :data:`_EMBEDDINGS_CHANNEL` channel so the request -> provider body carries the
    exact input text the provider embeds, one delta per item, in request order
    (so each vector round-trips to its input position, R6.2 / R6.3).
    """
    return tuple((_EMBEDDINGS_CHANNEL, item) for item in req.input_items)


def scan_embeddings(
    req: MiscEmbeddingsRequest,
    *,
    detector: Callable[[str], Sequence[Finding]],
    resolver: OutputResolver,
    on_invoke: Callable[[str], None],
) -> InputScan:
    """Scan the embeddings input items and resolve once (Group A, Property 4).

    Calls :func:`~gateway_v2.edge.input_enforcement.scan_deltas` over
    :func:`embeddings_deltas` with ``join_runs=()``: each item is an independent
    scannable text, so there is no split-across-parts join run (the chat C21 case
    does not apply to embeddings). ``scan_deltas`` already shifts each item's finding
    spans by its base offset in the concatenated item text, so the resolved
    ``decision`` lives in request-content coordinates like a chat decision.

    ``on_invoke`` is called with the exact text handed to ``detector`` on every
    per-item run (``scan_deltas`` invokes it per delta); the surface passes
    ``lambda _text: exports.observe_detector_invocation()`` so the shipped per-item
    invocation metric is preserved. A detector OR resolver raise fails closed to
    :data:`~gateway_v2.edge.input_enforcement.REFUSE_INPUT_SCAN_FAILURE` inside
    ``scan_deltas`` (Property 4).
    """
    return scan_deltas(
        embeddings_deltas(req),
        join_runs=(),
        detector=detector,
        resolver=resolver,
        on_invoke=on_invoke,
    )


def stage_embeddings(
    decision: Decision,
    req: MiscEmbeddingsRequest,
    *,
    destination: str,
    transform: Transform,
) -> UpstreamRequest | str:
    """Stage the verified embeddings upstream bytes + check the per-item shape (Property 15).

    Reuses the chat authorize -> verify flow unchanged: builds the
    :class:`~gateway_v2.dispatch.transform.GatewayRequest` from :func:`embeddings_deltas`
    and calls :func:`~gateway_v2.edge.input_enforcement.stage_upstream`. A refuse CODE
    (a ``str``) from that flow is propagated so a BLOCK authorization
    (:data:`~gateway_v2.edge.input_enforcement.REFUSE_INPUT_BLOCKED`) or a
    ``VerificationFailure`` (mapped to :data:`REFUSE_INPUT_UNMASKABLE` INSIDE
    ``stage_upstream``) is surfaced; there is no second redactor here.

    On a staged :class:`~gateway_v2.dispatch.provider.UpstreamRequest` it then checks
    the embeddings SHAPE (Property 15) by reading the staged deltas back out and
    asserting ALL of:

    1. the staged delta count equals ``len(req.input_items)``,
    2. every staged delta channel is :data:`_EMBEDDINGS_CHANNEL`,
    3. ``req.input_is_string`` implies EXACTLY ONE staged delta.

    A shape mismatch means the staged bytes dropped, merged or duplicated an item --
    a redaction whose per-item structure cannot be verified, which must not reach the
    provider -- so it returns :data:`REFUSE_INPUT_UNMASKABLE` (fail closed, the SAME
    code ``stage_upstream`` already returns for a ``VerificationFailure``). On success
    the staged ``UpstreamRequest`` is returned -- the only bytes that may reach the
    provider.
    """
    request = GatewayRequest(
        destination=destination,
        model=req.model,
        text_deltas=embeddings_deltas(req),
    )
    staged = stage_upstream(decision, request, transform)
    if isinstance(staged, str):
        return staged
    if not _shape_ok(staged, req):
        return REFUSE_INPUT_UNMASKABLE
    return staged


def _shape_ok(upstream: UpstreamRequest, req: MiscEmbeddingsRequest) -> bool:
    """True iff the staged deltas keep the embeddings per-item shape (Property 15).

    Reads the staged ``(channel, text)`` deltas back out of ``upstream.body`` under
    the shipped :data:`~gateway_v2.dispatch.transform.BODY_DELTAS_KEY` with the SAME
    validation convention ``dispatch/authorized.py::_deltas_of`` uses, then checks the
    three shape clauses: the staged delta count equals ``len(req.input_items)``, every
    channel is :data:`_EMBEDDINGS_CHANNEL`, and a string ``input`` (``input_is_string``)
    yields exactly one delta. A malformed body reads back as an empty tuple, which fails
    the count check for any non-empty request -- so an unreadable staged body also fails
    closed.
    """
    deltas = _staged_deltas(upstream)
    if len(deltas) != len(req.input_items):
        return False
    if any(channel != _EMBEDDINGS_CHANNEL for channel, _text in deltas):
        return False
    if req.input_is_string and len(deltas) != 1:
        return False
    return True


def _staged_deltas(upstream: UpstreamRequest) -> tuple[tuple[str, str], ...]:
    """Read the packed ``text_deltas`` back out of a staged ``UpstreamRequest`` body.

    Mirrors ``dispatch/authorized.py::_deltas_of`` exactly: narrows the
    ``Mapping[str, object]`` value stored under the shipped
    :data:`~gateway_v2.dispatch.transform.BODY_DELTAS_KEY` (the SAME key
    ``Transform.to_provider`` packs under) back to the ``(channel, text)`` delta shape,
    returning an empty tuple for a body that does not carry well-formed deltas rather
    than forwarding an ill-typed value.
    """
    raw = upstream.body.get(BODY_DELTAS_KEY, ())
    if not isinstance(raw, tuple):
        return ()
    out: list[tuple[str, str]] = []
    for item in raw:
        if (
            isinstance(item, tuple)
            and len(item) == 2
            and isinstance(item[0], str)
            and isinstance(item[1], str)
        ):
            out.append((item[0], item[1]))
        else:
            return ()
    return tuple(out)
