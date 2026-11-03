"""Shared render helpers + response-shape constants for the misc OpenAI surfaces (GW16).

Split out of ``edge/openai_misc.py`` so that module stays under the 800-line module gate
(the same one-directional split that kept ``edge/routes.py`` small via ``edge/chat_deps.py``
and ``edge/openai_chat.py`` small via ``edge/chat_lifecycle.py``). This sibling owns the two
module-level render helpers both misc handlers share — :func:`_refuse` (the one value-code →
HTTP ``Error_Envelope`` path) and :func:`_emit_json` (the one 200 ``application/json`` path) —
the response-shape constants they read, and the fail-closed :class:`_VectorTransportError`
signal the embeddings drain raises. ``edge/openai_misc.py`` imports them back, so every
handler keeps calling the SAME single render path and the surfaces never disagree (R9).

The split is one-directional: ``openai_misc`` imports from ``openai_misc_shared``;
``openai_misc_shared`` imports nothing from ``openai_misc`` (no cycle). The ``edge/errors`` +
``edge/routes`` imports stay DEFERRED inside each helper — the ``routes -> openai_misc`` cycle
task 7.1's ``build_routes`` wiring closes also reaches this sibling, so a module-level import
of ``edge/routes`` here would reintroduce it. No capacity literal (the values are
response-shape status / media-type / header / model-echo strings, not serving bounds — those
live in ``runtime/resources.py``); no module-level mutable.
"""

from __future__ import annotations

from gateway_v2.edge.asgi_sse import ASGISend
from gateway_v2.edge.identity_resolver import refusal_request_id
from gateway_v2.edge.outcome_metrics import OutcomeObserver, observe_refusal

__all__ = (
    "_DEFAULT_MODERATION_MODEL",
    "_emit_json",
    "_refuse",
    "_VectorTransportError",
)


# --------------------------------------------------------------------------- #
# Response-shape constants + the fail-closed vector-transport signal.
# --------------------------------------------------------------------------- #
#: The OpenAI success response status / media type / header name for a non-streaming misc
#: body. Response-shape values, NOT capacity-position literals (the serving bounds live in
#: ``runtime/resources.py``); mirrors ``edge/openai_chat.py``'s non-stream constants.
_OK_HTTP_STATUS = 200
_JSON_MEDIA_TYPE = "application/json"
_CONTENT_TYPE_HEADER = "content-type"

#: The model echoed in a ``POST /v1/moderations`` body when the request omits ``model``
#: (OpenAI's moderations ``model`` is optional). A plain response-shape string, NOT a
#: capacity-position literal (serving bounds live in ``runtime/resources.py``): the renderer
#: only echoes it and the SDK accepts whatever ``model`` the body carries, so a stable
#: default keeps the render deterministic.
_DEFAULT_MODERATION_MODEL = "text-moderation-latest"


class _VectorTransportError(Exception):
    """A provider delivery could not be read as an embedding vector (fail-closed signal).

    Raised while draining the provider stream when an event carries an ``error_frame`` or a
    delta text that is not a JSON float array. The surface catches it and WITHHOLDS the body
    with the declared ``scan_failure`` code — no partial or raw provider body is ever emitted
    (R6.1 / R10). A plain internal exception, never surfaced to the client as itself.
    """


# --------------------------------------------------------------------------- #
# Shared emit helpers (the single ``edge/errors`` render path, R9).
# --------------------------------------------------------------------------- #
#
# Both misc handlers render every refusal through the ONE value-code → HTTP
# ``Error_Envelope`` and every 200 success through the ONE ``application/json``
# ``HTTPResponse``. These two module-level functions ARE that single render path,
# lifted here so neither handler re-spells it and the surfaces never disagree.
# ``emit_response`` + ``edge/errors`` are deferred-imported INSIDE each helper to keep
# this module free of the cycle task 7.1's ``routes -> openai_misc`` wiring would
# otherwise close — the discipline ``ChatRoute.handle`` uses.


async def _refuse(
    send: ASGISend, code: str, observer: OutcomeObserver | None = None, *, decided: bool = False
) -> None:
    """Render a declared refuse-code through the single ``Error_Envelope`` (R9).

    Every refusal leaves through ``emit_response(send, errors.render_http(code))`` — the
    one value-code → HTTP path — so an unmapped code still renders the declared generic
    error rather than a raw framework 500 (R9.2). Touches no exports counter;
    ``fail_open_total`` is never incremented (R10.3) and the withholding sites record a
    content-free withholding on the exports producer BEFORE refusing. ``observer`` counts it.
    """
    from gateway_v2.edge import errors
    from gateway_v2.edge.routes import emit_response

    observe_refusal(observer, code, decided=decided)
    await emit_response(send, errors.render_http(code, request_id=refusal_request_id(code)))


async def _emit_json(send: ASGISend, body: bytes) -> None:
    """Emit a rendered JSON body as an HTTP 200 ``application/json`` response (R9).

    The single success-emit path shared by both misc handlers: a byte-stable renderer
    output goes out through the ONE ``emit_response`` + ``HTTPResponse`` boundary with the
    OpenAI success status + media type. Deferred-imports for the same cycle reason as
    :func:`_refuse`.
    """
    from gateway_v2.edge import errors
    from gateway_v2.edge.routes import emit_response

    await emit_response(
        send,
        errors.HTTPResponse(
            status=_OK_HTTP_STATUS,
            body=body,
            headers=((_CONTENT_TYPE_HEADER, _JSON_MEDIA_TYPE),),
        ),
    )
