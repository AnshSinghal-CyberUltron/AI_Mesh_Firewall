"""The ONE place egress/dispatch value-codes are rendered to HTTP/SSE (GW12, task 7.1).

``edge/errors.py`` is the single ``Error_Envelope`` (R3.1): every component below
``edge`` returns a *value-code* from the one shared vocabulary
(``domain/posture.py``) and NEVER constructs an HTTP object, and this module maps
that code to a declared HTTP status + a declared, SDK-parseable SSE ``Error_Frame``
(R3.2). The import-linter layer order puts ``edge`` at the top and the AST gate
``lint/check_http_outside_edge_resolve.py`` forbids ``HTTPException`` /
``JSONResponse`` / ``status_code=4xx`` *outside* ``edge`` and ``resolve`` -- so the
HTTP-construction that is forbidden one layer down is permitted HERE, because this
is the render site (R3.3).

**HTTP-response representation.** ``edge/app.py`` is still the ``app = None`` stub
(GW12 task 7.2 assembles the real ASGI app) and the repo pulls in no HTTP framework
(``pyproject.toml`` dependencies are ``psycopg`` + ``redis`` only -- no
starlette/fastapi). There is therefore no framework ``Response`` type to render
into. Rather than take a premature framework dependency, this module defines a
minimal, framework-free, frozen slotted :class:`HTTPResponse` value (``status`` +
``headers`` + ``body`` bytes). The ASGI app assembled in task 7.2 emits it directly
onto the ASGI ``send`` channel (``http.response.start`` with ``status``/``headers``
then ``http.response.body`` with ``body``). Keeping the rendered result a plain
value -- not a bound framework object -- also keeps the renderer pure and unit-
testable (task 7.3) and leaves the framework choice to the app-assembly task.

**Single-sourced SSE shape.** The SSE half does NOT re-spell the error frame: it
reuses :func:`gateway_v2.edge.wire.sse.error_frame_for` (which maps a posture code
to the declared :class:`SSEErrorFrame` and already renders a generic frame for an
unmapped code) and serialises it with the same :class:`SSEEncoder` the streaming
path uses, so the two sides of the stream cannot disagree on the error shape
(R1.3 / R3.4).

**Unmapped code (R3.4).** A code with no entry in :data:`_ENVELOPE` renders a
declared GENERIC error -- HTTP ``500`` with a fixed ``gateway_error`` JSON body, or
the generic SSE frame -- and NEVER echoes the raw code string into the HTTP body or
leaks any internal detail. (The SSE frame echoes the code only in the structured
``code`` field, matching ``error_frame_for``'s declared contract; the human-readable
``message`` stays generic.)

**No capacity literal, no module-level mutable.** The one module-level table
(:data:`_ENVELOPE`, code -> ``(http_status, sse_error_type)``) is a
``MappingProxyType``; the HTTP status integers are response-status codes, not
capacity-position literals (the serving skin's bounds live in
``runtime/resources.py``).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from gateway_v2.domain import posture
from gateway_v2.edge.wire.sse import SSEEncoder, error_frame_for

__all__ = (
    "GENERIC_HTTP_STATUS",
    "NOT_IMPLEMENTED_HTTP_STATUS",
    "HTTPResponse",
    "render",
    "render_http",
    "render_not_implemented",
    "render_sse",
)


# --------------------------------------------------------------------------- #
# Media types + generic fallback (plain strings -- not capacity literals).
# --------------------------------------------------------------------------- #

_JSON_MEDIA_TYPE = "application/json"

#: Status rendered for a value-code with no declared envelope entry (R3.4). A
#: ``500`` (not a ``4xx``) because an unmapped code is an internal gap, not a
#: client error; the body stays generic so no internal detail leaks.
GENERIC_HTTP_STATUS = 500

#: The declared generic error body. Fixed strings -- the raw value-code is NEVER
#: substituted into the HTTP ``message`` (R3.4). ``type`` matches the generic SSE
#: ``error_type`` the wire codec renders, so HTTP and SSE agree.
_GENERIC_ERROR_TYPE = "gateway_error"
_GENERIC_ERROR_MESSAGE = "The gateway could not complete the request."

#: Status rendered for a surface that is REGISTERED-for-later but not yet wired (R2.3). A
#: ``501 Not Implemented`` (not a ``404``) because the route exists and is declared — the
#: placeholder is an honest "this surface is not served here yet", not a routing miss. The body
#: stays generic so no internal routing shape leaks.
NOT_IMPLEMENTED_HTTP_STATUS = 501
_NOT_IMPLEMENTED_ERROR_TYPE = "not_implemented"
_NOT_IMPLEMENTED_ERROR_MESSAGE = "This surface is not served by the gateway yet."


# --------------------------------------------------------------------------- #
# HTTP-response value type (framework-free; the ASGI app emits it in task 7.2).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class HTTPResponse:
    """A rendered HTTP response as a plain, framework-free value.

    ``status`` is the HTTP status code, ``headers`` the response headers as an
    immutable tuple of ``(name, value)`` pairs (lower-cased names, matching the
    ASGI convention), and ``body`` the raw response bytes. The ASGI app assembled
    in task 7.2 writes this straight onto the ASGI ``send`` channel. Frozen +
    slotted so the rendered result is an immutable value, not a mutable object
    carried down a layer.
    """

    status: int
    body: bytes
    headers: tuple[tuple[str, str], ...] = field(default=())

    def json(self) -> object:
        """Parse the body as JSON (test/inspection helper; not on the hot path)."""
        return json.loads(self.body.decode("utf-8"))


# --------------------------------------------------------------------------- #
# The envelope table (R3.2): posture value-code -> (http_status, sse_error_type).
# --------------------------------------------------------------------------- #

# One declared mapping per value-code in the shared vocabulary. The ``sse_error_type``
# column is the declared SSE ``error.type`` the wire codec renders for the code;
# it is kept here so the HTTP ``error.type`` body field agrees with the SSE frame's
# ``type`` (single vocabulary across both renderings). The ACTUAL SSE frame bytes
# are produced by ``error_frame_for`` + ``SSEEncoder.error`` -- this column is NOT a
# second spelling of the frame, only the HTTP-body ``type``.
#
# Status choices (R3.2):
#   * timeouts -> 504 Gateway Timeout (upstream stalls) / 408 Request Timeout
#     (the downstream client went idle -- a client-side timeout);
#   * key revocation -> 401 Unauthorized (the credential is no longer valid);
#   * malformed upstream -> 502 Bad Gateway (the upstream sent an undecodable body);
#   * buffer/plan/snapshot/kill-switch/plan-unavailable/shared-state/overload/budget
#     -> 503 Service Unavailable (a transient capacity / control-plane condition);
#   * output_blocked / scan_failure -> 403 Forbidden (the guard withheld the
#     response -- a policy decision, not an upstream fault);
#   * max-duration / stream_killed -> 503 (the gateway cut an in-flight stream).

_ENVELOPE: Mapping[str, tuple[int, str]] = MappingProxyType(
    {
        # -- GW12 stream timeouts (R9) ----------------------------------------
        posture.STREAM_INTER_CHUNK_TIMEOUT: (504, "timeout_error"),
        posture.STREAM_IDLE_TIMEOUT: (408, "timeout_error"),
        posture.STREAM_WRITE_TIMEOUT: (504, "timeout_error"),
        # -- GW12 in-flight control / max duration (R10) ----------------------
        posture.STREAM_MAX_DURATION: (503, "timeout_error"),
        posture.STREAM_KEY_REVOKED: (401, "authentication_error"),
        posture.STREAM_PLAN_CHANGED: (503, "service_unavailable"),
        posture.STREAM_SNAPSHOT_STALE: (503, "service_unavailable"),
        posture.STREAM_MALFORMED_UPSTREAM: (502, "gateway_error"),
        posture.STREAM_BUFFER_UNAVAILABLE: (503, "service_unavailable"),
        # -- Pre-existing egress terminal codes (reused unchanged) ------------
        "stream_killed": (503, "service_unavailable"),
        "scan_failure": (403, "gateway_error"),
        "output_blocked": (403, "gateway_error"),
        # -- Pre-existing posture codes (R2 cards) ----------------------------
        posture.PLAN_UNAVAILABLE: (503, "service_unavailable"),
        posture.KILL_SWITCH_UNAVAILABLE: (503, "service_unavailable"),
        posture.SHARED_STATE_UNAVAILABLE: (503, "service_unavailable"),
        posture.BUDGET_UNAVAILABLE: (503, "service_unavailable"),
        posture.OVERLOAD_SHED: (503, "service_unavailable"),
    }
)


# --------------------------------------------------------------------------- #
# Rendering (R3.2 / R3.4).
# --------------------------------------------------------------------------- #


def _http_body(error_type: str, code: str, message: str) -> bytes:
    """Serialise the declared ``{"error": {...}}`` HTTP JSON body.

    Mirrors the SSE ``SSEErrorFrame.as_json_obj`` shape (``message`` / ``type`` /
    ``code``) so a client that parses either rendering sees the same structure.
    """
    body = {"error": {"message": message, "type": error_type, "code": code}}
    return json.dumps(body, separators=(",", ":")).encode("utf-8")


def render_http(code: str) -> HTTPResponse:
    """Render a value-code to a declared :class:`HTTPResponse` (R3.2).

    A code with a declared :data:`_ENVELOPE` entry renders that status; the JSON
    body's ``message`` is taken from the single-sourced :func:`error_frame_for`
    shape so HTTP and SSE never disagree on the human-readable text. An UNMAPPED
    code renders the declared generic error -- HTTP :data:`GENERIC_HTTP_STATUS`
    with a fixed ``gateway_error`` body -- and NEVER substitutes the raw code into
    the ``message`` (R3.4). In both cases the structured ``code`` field echoes the
    originating code so the trace keeps the single spelling.
    """
    entry = _ENVELOPE.get(code)
    if entry is None:
        body = _http_body(_GENERIC_ERROR_TYPE, code, _GENERIC_ERROR_MESSAGE)
        return HTTPResponse(
            status=GENERIC_HTTP_STATUS,
            body=body,
            headers=(("content-type", _JSON_MEDIA_TYPE),),
        )
    status, error_type = entry
    # Reuse the single-sourced declared frame for the human-readable message so
    # the HTTP body and the SSE frame carry identical text for the same code.
    frame = error_frame_for(code)
    body = _http_body(error_type, code, frame.message)
    return HTTPResponse(
        status=status,
        body=body,
        headers=(("content-type", _JSON_MEDIA_TYPE),),
    )


def render_sse(code: str) -> bytes:
    """Render a value-code to the declared SSE ``Error_Frame`` bytes (R3.2).

    Single-sourced: delegates to :class:`SSEEncoder` + :func:`error_frame_for`
    (the one place the SSE error shape is spelled, R1.3). An unmapped code renders
    the generic SSE frame via ``error_frame_for``'s own fallback (R3.4). The
    returned bytes are the full ``data: {json}\\n\\n`` terminal error frame; no
    content frame follows it (the encoder goes terminal on ``error()``).
    """
    return SSEEncoder().error(code)


def render_not_implemented(surface: str) -> HTTPResponse:
    """Render the declared ``not_implemented`` placeholder response for a surface (R2.3).

    A registered-for-later route (responses/misc/mcp/rag) renders this: HTTP
    :data:`NOT_IMPLEMENTED_HTTP_STATUS` with a declared generic body. ``surface`` is echoed only
    in the structured ``code`` field (``not_implemented:<surface>``) so a trace keeps the single
    spelling; the human-readable ``message`` stays generic and leaks no internal routing shape.
    """
    body = _http_body(
        _NOT_IMPLEMENTED_ERROR_TYPE,
        f"{_NOT_IMPLEMENTED_ERROR_TYPE}:{surface}",
        _NOT_IMPLEMENTED_ERROR_MESSAGE,
    )
    return HTTPResponse(
        status=NOT_IMPLEMENTED_HTTP_STATUS,
        body=body,
        headers=(("content-type", _JSON_MEDIA_TYPE),),
    )


def render(code: str, *, as_sse: bool) -> HTTPResponse | bytes:
    """Map a value-code to a declared HTTP status + SSE ``Error_Frame`` (R3.2).

    ``as_sse=False`` returns an :class:`HTTPResponse` (the framework-free rendered
    value the ASGI app emits); ``as_sse=True`` returns the SSE ``Error_Frame``
    bytes. An unmapped code renders a declared generic error either way, never raw
    internal detail (R3.4). This is the single ``Error_Envelope`` entry point
    (R3.1); every code reaching the wire passes through here.
    """
    if as_sse:
        return render_sse(code)
    return render_http(code)
