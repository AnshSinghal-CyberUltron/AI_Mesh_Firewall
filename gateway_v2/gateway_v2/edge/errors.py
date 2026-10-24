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

#: The response header the stock ``openai`` SDK reads ``err.request_id`` from (the
#: ``x-request-id`` RESPONSE header, NOT the body). The body ALSO carries a
#: ``request_id`` field (the shared wire contract checks the body envelope; the SDK
#: reads the header), so this module sets BOTH consistently from the one id when a
#: render site has one. A plain header-name string, not a capacity-position literal.
_REQUEST_ID_HEADER = "x-request-id"

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
#     response -- a policy decision, not an upstream fault). These STAY 403.
#   * input_blocked / input_unmaskable -> 400 invalid_request_error (the REQUEST
#     carried content the gateway cannot forward safely -- an input content
#     refusal in the frozen OpenAI BadRequest family, v1 GATEWAY_BLOCK_STATUS
#     parity, default 400); NOT the 403 the output-side guard uses;
#   * invalid_max_tokens / invalid_max_completion_tokens -> 400 invalid_request_error
#     (v1's boundary validator refuses an output-budget field above the parse
#     boundary with a 400, same client-error family as an input content refusal);
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
        # -- GW13 STRICT_WITHHOLD buffer overflow (R9.5) ----------------------
        posture.STREAM_STRICT_BUFFER_OVERFLOW: (503, "service_unavailable"),
        # -- Pre-existing egress terminal codes (reused unchanged) ------------
        "stream_killed": (503, "service_unavailable"),
        "scan_failure": (403, "gateway_error"),
        "output_blocked": (403, "gateway_error"),
        # -- GW15/GW16 INPUT content block (openai-conformance-gw01-local, §10.2.2) --
        # The §10.2.2 deliberate-diff table makes v2's input/streaming block a CHOSEN
        # behaviour whose disposition the conformance suite asserts via the
        # Expected_Diff_Ledger WITHOUT forcing v1's. The frozen OpenAI wire contract
        # for a CONTENT block is HTTP 400 + ``invalid_request_error`` (the stock SDK's
        # ``BadRequestError`` category); the v2 posture value-code is ``input_blocked``.
        # Reconciled here: the HTTP STATUS is the frozen 400 and the ``error.type`` is
        # the frozen ``invalid_request_error``, while the body ``code`` stays the v2
        # posture spelling ``input_blocked`` (what the v2 cells assert and what the
        # ledger's v2 side represents). ``output_blocked`` keeps its existing 403
        # mapping unchanged. The 400 is a response-status code, not a capacity literal.
        #
        # ``input_unmaskable`` (edge/input_enforcement.py ``REFUSE_INPUT_UNMASKABLE``)
        # is the SAME HTTP family: a REDACT input whose outbound bytes cannot be
        # verified (a detected value survives, or the transformation cannot be
        # applied) is content the gateway cannot forward safely, exactly like an
        # input block. GW01 §10.2.2 and v1's ``_build_safe_block_response`` ->
        # ``GATEWAY_BLOCK_STATUS`` (default 400) make an unmaskable input REDACT the
        # same client-error disposition as a block, so it renders 400
        # ``invalid_request_error`` with the v2 posture ``code`` ``input_unmaskable``.
        "input_blocked": (400, "invalid_request_error"),
        "input_unmaskable": (400, "invalid_request_error"),
        # -- GW16 misc-surface client-facing codes (Decision 1, R7.3/R9.1) ----
        # Value-codes declared in edge/wire/openai_misc.py (MISSING_REQUIRED_PARAMETER
        # / MODEL_NOT_FOUND); keyed here as literal strings (matching the existing
        # literal-string keys above) so this render table takes no upward import.
        # The 400/404 are response-status codes, not capacity-position literals.
        "missing_required_parameter": (400, "invalid_request_error"),
        "model_not_found": (404, "not_found_error"),
        # -- Group F output-budget parse refusals (v1 boundary validator) -----
        # Value-codes declared in edge/wire/openai.py (INVALID_MAX_TOKENS /
        # INVALID_MAX_COMPLETION_TOKENS); keyed here as literal strings (matching the
        # existing literal-string keys above) so this render table takes no upward
        # import. v1's boundary validator (``MAX_OUTPUT_TOKENS_CEILING``,
        # gateway/ai_mesh_gateway/main.py ~L3399) refuses a ``max_tokens`` /
        # ``max_completion_tokens`` ABOVE the parse boundary with HTTP 400
        # ``invalid_request_error`` and ``param`` naming the field; v2 keeps that
        # 400 ``invalid_request_error`` family with the v2 ``code`` spelling. The
        # boundary number itself is NOT embedded here (it is a capacity position,
        # ``runtime/resources.py`` only): it reaches the rendered message via the
        # ``boundary`` argument the contract-bearing render site supplies (see
        # :func:`render_http` + :data:`_BOUNDARY_CODES`). The 400 is a
        # response-status code, not a capacity literal.
        "invalid_max_tokens": (400, "invalid_request_error"),
        "invalid_max_completion_tokens": (400, "invalid_request_error"),
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


def _http_body(
    error_type: str,
    code: str,
    message: str,
    *,
    param: str | None = None,
    request_id: str | None = None,
) -> bytes:
    """Serialise the declared ``{"error": {...}}`` HTTP JSON body.

    The body carries the full OpenAI error-object shape a stock SDK caller reads:
    ``message`` / ``type`` / ``code`` (mirrored from the SSE
    ``SSEErrorFrame.as_json_obj`` shape so either rendering parses the same) PLUS
    ``param`` and ``request_id``. ``param`` is OpenAI-nullable — JSON ``null`` when the
    error is not field-specific — and ``request_id`` is the request correlation id,
    rendered as JSON ``null`` at a render site that genuinely has no id yet (a
    pre-identity failure). The SDK reads ``err.param`` / ``err.code`` / ``err.type`` /
    ``err.message`` from this body and ``err.request_id`` from the ``x-request-id``
    RESPONSE header (set alongside, see :func:`render_http`).
    """
    body = {
        "error": {
            "message": message,
            "type": error_type,
            "code": code,
            "param": param,
            "request_id": request_id,
        }
    }
    return json.dumps(body, separators=(",", ":")).encode("utf-8")


def _error_headers(request_id: str | None) -> tuple[tuple[str, str], ...]:
    """Build the HTTP response headers for an error body, SDK-faithfully.

    Always carries ``content-type: application/json``; when ``request_id`` is present
    it ALSO carries the ``x-request-id`` response header the stock ``openai`` SDK reads
    ``err.request_id`` from, so the header and the body ``request_id`` field agree. A
    render site with no id (a pre-identity failure) sets no header (and the body field
    is JSON ``null``).
    """
    headers: tuple[tuple[str, str], ...] = (("content-type", _JSON_MEDIA_TYPE),)
    if request_id is not None:
        headers = (*headers, (_REQUEST_ID_HEADER, request_id))
    return headers


#: The two Group F output-budget parse-refusal codes whose declared message must name the
#: permitted maximum. They are value-codes (plain strings, the same literal-string keying the
#: ``_ENVELOPE`` entries use so this module takes no upward import of the
#: ``edge/wire/openai`` constants), NOT capacity-position literals. The boundary NUMBER is never
#: embedded here -- it arrives at runtime as the ``boundary`` argument sourced from
#: ``ResourceContract.max_output_tokens_boundary()`` at the contract-bearing caller.
_BOUNDARY_CODES = frozenset({"invalid_max_tokens", "invalid_max_completion_tokens"})


def render_http(
    code: str,
    *,
    param: str | None = None,
    request_id: str | None = None,
    boundary: int | None = None,
) -> HTTPResponse:
    """Render a value-code to a declared :class:`HTTPResponse` (R3.2).

    A code with a declared :data:`_ENVELOPE` entry renders that status; the JSON
    body's ``message`` is taken from the single-sourced :func:`error_frame_for`
    shape so HTTP and SSE never disagree on the human-readable text. An UNMAPPED
    code renders the declared generic error -- HTTP :data:`GENERIC_HTTP_STATUS`
    with a fixed ``gateway_error`` body -- and NEVER substitutes the raw code into
    the ``message`` (R3.4). In both cases the structured ``code`` field echoes the
    originating code so the trace keeps the single spelling.

    The body carries ``param`` (OpenAI-nullable; ``None`` -> JSON ``null`` when the
    error is not field-specific) and ``request_id`` (``None`` -> JSON ``null`` at a
    render site that has no id yet). When a ``request_id`` is supplied, the
    ``x-request-id`` response header is set alongside the body field so the stock SDK
    reads it from the header and the two never disagree.

    ``boundary`` is the permitted-maximum VALUE a CONTRACT-BEARING caller supplies
    (``None`` everywhere else). The boundary codes :data:`_BOUNDARY_CODES`
    (``invalid_max_tokens`` / ``invalid_max_completion_tokens``) must name the
    permitted maximum in their message, but that boundary is a capacity position
    (``ResourceContract.max_output_tokens_boundary()``) that may NOT appear as a
    literal in this render module (``check_capacity_literals`` forbids it outside
    ``runtime/resources.py``). The declared :data:`ERROR_SHAPES` message therefore
    stays boundary-free, and the caller that HAS a contract (``edge/routes.py``
    ``ChatRoute._render_refusal``) passes the concrete ``boundary`` here; this
    function appends the clause for a boundary code ONLY. So the real boundary
    reaches the wire while the NUMBER lives only in the contract -- the gate stays
    green because no capacity literal appears in this module.
    """
    headers = _error_headers(request_id)
    entry = _ENVELOPE.get(code)
    if entry is None:
        body = _http_body(
            _GENERIC_ERROR_TYPE,
            code,
            _GENERIC_ERROR_MESSAGE,
            param=param,
            request_id=request_id,
        )
        return HTTPResponse(status=GENERIC_HTTP_STATUS, body=body, headers=headers)
    status, error_type = entry
    # Reuse the single-sourced declared frame for the human-readable message so the
    # HTTP body and the SSE frame carry identical text. For a boundary code, append
    # the contract-supplied boundary VALUE (never a literal here) so the message
    # names the permitted maximum.
    message = error_frame_for(code).message
    if code in _BOUNDARY_CODES and boundary is not None:
        message = f"{message} The permitted maximum is {boundary}."
    body = _http_body(error_type, code, message, param=param, request_id=request_id)
    return HTTPResponse(status=status, body=body, headers=headers)


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


def render(
    code: str,
    *,
    as_sse: bool,
    param: str | None = None,
    request_id: str | None = None,
    boundary: int | None = None,
) -> HTTPResponse | bytes:
    """Map a value-code to a declared HTTP status + SSE ``Error_Frame`` (R3.2).

    ``as_sse=False`` returns an :class:`HTTPResponse` (the framework-free rendered
    value the ASGI app emits); ``as_sse=True`` returns the SSE ``Error_Frame``
    bytes. An unmapped code renders a declared generic error either way, never raw
    internal detail (R3.4). This is the single ``Error_Envelope`` entry point
    (R3.1); every code reaching the wire passes through here.

    ``param`` / ``request_id`` / ``boundary`` thread into the HTTP body (and the
    ``x-request-id`` header for ``request_id``); they are HTTP-body fields only —
    the SSE ``Error_Frame`` keeps the frozen OpenAI streaming-error shape
    (``message`` / ``type`` / ``code``) the v1 streaming-error contract pins, so
    they are not injected into the SSE frame. ``boundary`` is the contract-sourced
    permitted maximum the boundary codes carry (see :func:`render_http`); the SSE
    frame's boundary-free declared message is intentional (a streaming boundary
    refusal keeps the frozen shape).
    """
    if as_sse:
        return render_sse(code)
    return render_http(code, param=param, request_id=request_id, boundary=boundary)
