# Feature: chat-surface-gw15, Property 18: Every value-code renders through the single Error_Envelope  # noqa: E501
# Validates: Requirements 12.1
"""Property 18 — every value-code renders through the single ``Error_Envelope`` (task 5.7).

House idiom: a single seeded ``random.Random`` driven for ``>= 10,000`` iterations, NO
``hypothesis``; one property <-> one property-based test.

*For any* value-code reaching the wire — whether a MAPPED code drawn from the one shared
``edge/errors._ENVELOPE`` table (the declared posture vocabulary of ``domain/posture.py``) or a
RANDOM UNMAPPED code that has no declared envelope entry — the single ``Error_Envelope`` entry
point :func:`gateway_v2.edge.errors.render` produces a DECLARED, well-formed rendering through the
ONE envelope and NEVER a raw framework error (R12.1 / R3.1 / R3.4):

* non-streaming (``as_sse=False``) -> a :class:`~gateway_v2.edge.errors.HTTPResponse` carrying a
  declared HTTP status, an ``application/json`` content-type header, and a well-formed
  ``{"error": {"message", "type", "code"}}`` JSON body; and
* streaming (``as_sse=True``) -> the declared, SDK-parseable SSE ``Error_Frame`` bytes
  (``data: {json}\\n\\n``, a top-level ``{"error": {...}}`` object) produced by the single
  :class:`~gateway_v2.edge.wire.sse.SSEEncoder` the streaming path uses.

The invariant each iteration, for BOTH mapped and unmapped codes and BOTH renderings:

1. the render never raises and never returns ``None`` (a code always has A rendering — the
   envelope is total over the value-code space, R3.4);
2. a MAPPED code renders its declared ``_ENVELOPE`` HTTP status + ``error.type`` (the HTTP
   ``error.type`` is sourced from the envelope's type column), while the SSE ``error.type`` is
   the single-sourced :func:`~gateway_v2.edge.wire.sse.error_frame_for` type; an UNMAPPED code
   renders the declared generic :data:`~gateway_v2.edge.errors.GENERIC_HTTP_STATUS` (500)
   ``gateway_error`` body and NEVER substitutes the raw code string into the human-readable
   ``message`` (no internal-detail leak);
3. the structured ``code`` field echoes the originating code verbatim on BOTH renderings (the
   trace keeps the single spelling) — HTTP body ``error.code`` and SSE body ``error.code`` agree;
4. the SSE ``error.type`` + human-readable ``message`` are single-sourced from
   ``error_frame_for`` (the one place the SSE error shape is spelled); for a MAPPED code the HTTP
   body's message is single-sourced from the same ``error_frame_for``, so HTTP and SSE carry
   identical text (R3.4); an unmapped code's HTTP and SSE each carry their own declared generic
   message and neither leaks the raw code; and
5. ``render(code, as_sse=False)`` is byte-identical to the direct
   :func:`~gateway_v2.edge.errors.render_http` call and ``render(code, as_sse=True)`` is
   byte-identical to :func:`~gateway_v2.edge.errors.render_sse` (``render`` is the one dispatch
   point — there is no second envelope).

Only the public shipped render surface is exercised; nothing is mocked.
"""

from __future__ import annotations

import json
import random
import string

from gateway_v2.edge import errors
from gateway_v2.edge.wire.sse import error_frame_for

_SEED = 0x18_15_0507
_ITERATIONS = 10_000

#: The declared generic HTTP ``error.type`` an unmapped code renders (never the raw code string).
_GENERIC_ERROR_TYPE = "gateway_error"

#: Every MAPPED value-code in the one shared envelope table, with its declared ``(status, type)``.
_MAPPED_CODES: tuple[str, ...] = tuple(errors._ENVELOPE)

#: Characters an unmapped synthetic code may draw from (letters/digits/underscore — the shape of
#: a posture value-code), so a generated code resembles a real code yet is absent from the table.
_CODE_ALPHABET = string.ascii_lowercase + string.digits + "_"


def _ctx(i: int, **extra: object) -> str:
    """A reproducible failure context string (seed + iteration + relevant values)."""
    body = " ".join(f"{k}={v!r}" for k, v in extra.items())
    return f"seed={_SEED:#x} iter={i} {body}"


#: A distinctive sentinel prefix (plus an embedded digit) carried by every synthetic unmapped
#: code so a generated code can never be an INCIDENTAL substring of the fixed, English-word
#: generic messages — the "no leak" assertion is about the renderer never SUBSTITUTING the raw
#: code into the message, not about a 2-char token coincidentally occurring inside a sentence.
_UNMAPPED_PREFIX = "zz9_unmapped_"


def _random_unmapped_code(rng: random.Random) -> str:
    """A random value-code guaranteed to have NO declared ``_ENVELOPE`` entry.

    Draws a letters/digits/underscore token that looks like a posture code (carrying the
    :data:`_UNMAPPED_PREFIX` sentinel so it cannot be an incidental substring of the generic
    messages), then regenerates until it is absent from the table — so the unmapped-code fallback
    (R3.4) is exercised with a code the renderer has genuinely never seen.
    """
    while True:
        length = rng.randint(1, 24)
        suffix = "".join(rng.choice(_CODE_ALPHABET) for _ in range(length))
        code = _UNMAPPED_PREFIX + suffix
        if code not in errors._ENVELOPE:
            return code


def _http_error_obj(response: errors.HTTPResponse) -> dict[str, object]:
    """Parse an :class:`HTTPResponse` body and return its ``error`` object (asserts the shape)."""
    parsed = response.json()
    assert isinstance(parsed, dict), f"HTTP body is not a JSON object: {parsed!r}"
    error = parsed.get("error")
    assert isinstance(error, dict), f"HTTP body has no error object: {parsed!r}"
    return error


def _sse_error_obj(frame: bytes) -> dict[str, object]:
    """Parse a declared SSE ``Error_Frame`` and return its ``error`` object (asserts the shape).

    A declared error frame is ``data: {json}\\n\\n`` whose JSON is a top-level
    ``{"error": {...}}`` object — the shape the official SDKs decode into a typed error.
    """
    assert frame.startswith(b"data: "), f"SSE frame is not a data: frame: {frame!r}"
    assert frame.endswith(b"\n\n"), f"SSE frame is not terminated by a blank line: {frame!r}"
    body = frame[len(b"data: ") : -len(b"\n\n")]
    parsed = json.loads(body.decode("utf-8"))
    assert isinstance(parsed, dict), f"SSE body is not a JSON object: {parsed!r}"
    error = parsed.get("error")
    assert isinstance(error, dict), f"SSE body has no error object: {parsed!r}"
    return error


def test_property_18_every_value_code_renders_through_single_error_envelope() -> None:
    rng = random.Random(_SEED)

    for i in range(_ITERATIONS):
        # Draw EITHER a mapped code from the one shared envelope table OR a random unmapped code,
        # so both the declared-entry path and the generic fallback are exercised across the run.
        mapped = rng.random() < 0.5
        code = rng.choice(_MAPPED_CODES) if mapped else _random_unmapped_code(rng)

        # (1) The single Error_Envelope entry point renders both sides without raising; a code
        #     always has A rendering (the envelope is total over the value-code space, R3.4).
        http = errors.render(code, as_sse=False)
        sse = errors.render(code, as_sse=True)
        assert isinstance(http, errors.HTTPResponse), _ctx(
            i, note="non-streaming render did not return an HTTPResponse", code=code, got=http
        )
        assert isinstance(sse, bytes), _ctx(
            i, note="streaming render did not return SSE bytes", code=code, got=sse
        )

        # The HTTP side always declares an application/json content-type (a declared envelope,
        # never a raw framework error).
        assert ("content-type", "application/json") in http.headers, _ctx(
            i, note="HTTP envelope missing application/json content-type", code=code,
            headers=http.headers,
        )

        http_error = _http_error_obj(http)
        sse_error = _sse_error_obj(sse)

        # (3) The structured code field echoes the originating code VERBATIM on BOTH renderings
        #     (the trace keeps the single spelling), and both sides agree.
        assert http_error.get("code") == code, _ctx(
            i, note="HTTP error.code did not echo the originating code", code=code,
            got=http_error.get("code"),
        )
        assert sse_error.get("code") == code, _ctx(
            i, note="SSE error.code did not echo the originating code", code=code,
            got=sse_error.get("code"),
        )

        # (4) The SSE error.type + message are single-sourced from error_frame_for (the ONE place
        #     the SSE error shape is spelled, R1.3) — true for mapped and unmapped alike.
        expected_frame = error_frame_for(code)
        assert sse_error.get("type") == expected_frame.error_type, _ctx(
            i, note="SSE error.type is not the single-sourced declared type", code=code,
            got=sse_error.get("type"), want=expected_frame.error_type,
        )
        assert sse_error.get("message") == expected_frame.message, _ctx(
            i, note="SSE message is not the single-sourced declared message", code=code,
            got=sse_error.get("message"), want=expected_frame.message,
        )
        http_message = http_error.get("message")
        assert isinstance(http_message, str), _ctx(
            i, note="HTTP error.message is not a string", code=code, got=http_message,
        )

        if mapped:
            # (2a) A MAPPED code renders its declared _ENVELOPE HTTP status + error.type (the HTTP
            #      type is sourced from the envelope's type column).
            declared_status, declared_type = errors._ENVELOPE[code]
            assert http.status == declared_status, _ctx(
                i, note="mapped code did not render its declared HTTP status", code=code,
                got=http.status, want=declared_status,
            )
            assert http_error.get("type") == declared_type, _ctx(
                i, note="mapped code HTTP error.type is not the declared envelope type",
                code=code, got=http_error.get("type"), want=declared_type,
            )
            # The HTTP body's human-readable message is single-sourced from error_frame_for for a
            # mapped code, so HTTP and SSE carry IDENTICAL text (R3.4 — the two sides agree).
            assert http_message == sse_error.get("message"), _ctx(
                i, note="mapped code: HTTP and SSE disagree on the human-readable message",
                code=code, http_message=http_message, sse_message=sse_error.get("message"),
            )
        else:
            # (2b) An UNMAPPED code renders the declared generic 500 gateway_error body and NEVER
            #      substitutes the raw code string into the human-readable message (no leak).
            assert http.status == errors.GENERIC_HTTP_STATUS, _ctx(
                i, note="unmapped code did not render the generic HTTP status", code=code,
                got=http.status, want=errors.GENERIC_HTTP_STATUS,
            )
            assert http_error.get("type") == _GENERIC_ERROR_TYPE, _ctx(
                i, note="unmapped code HTTP error.type is not the generic type", code=code,
                got=http_error.get("type"),
            )
            assert code not in http_message, _ctx(
                i, note="unmapped code leaked the raw code into the HTTP message",
                code=code, message=http_message,
            )
            sse_message = expected_frame.message
            assert isinstance(sse_message, str) and code not in sse_message, _ctx(
                i, note="unmapped code leaked the raw code into the SSE message",
                code=code, message=sse_message,
            )

        # (5) render(...) is the ONE dispatch point: it is byte-identical to the direct render_*
        #     calls, so there is no second envelope rendering a code a different way.
        direct_http = errors.render_http(code)
        assert http == direct_http, _ctx(
            i, note="render(as_sse=False) diverged from render_http", code=code,
        )
        assert sse == errors.render_sse(code), _ctx(
            i, note="render(as_sse=True) diverged from render_sse", code=code,
        )
