# Feature: chat-surface-gw16-misc, Property 15: Every value-code renders through the single Error_Envelope  # noqa: E501
# Validates: Requirements 9.1
"""Property 15 — every GW16 value-code renders through the single ``Error_Envelope`` (task 8.3).

House idiom: a single seeded ``random.Random`` driven for ``>= 10,000`` iterations, NO
``hypothesis``; one property <-> one property-based test. This is the GW16-scoped sibling of
GW15's Property 18 (``tests/edge/test_lgw15_errors.py``), focused on the GW16-relevant code set
rather than the whole shared vocabulary.

*For any* GW16 value-code reaching the wire — the two NEW misc codes
(:data:`~gateway_v2.edge.wire.openai_misc.MISSING_REQUIRED_PARAMETER` /
:data:`~gateway_v2.edge.wire.openai_misc.MODEL_NOT_FOUND`), the three malformed-body codes
(:data:`~gateway_v2.edge.wire.openai.MALFORMED_BODY` /
:data:`~gateway_v2.edge.wire.openai.MALFORMED_NOT_AN_OBJECT` /
:data:`~gateway_v2.edge.wire.openai.MALFORMED_WRONG_SHAPE`), OR a RANDOM UNMAPPED synthetic code
with no declared envelope entry — the single ``Error_Envelope`` entry point
:func:`gateway_v2.edge.errors.render` produces a DECLARED, well-formed HTTP body through the ONE
envelope and NEVER a raw framework error (R9.1 / R3.1 / R3.4).

The invariant each iteration, for every code in the GW16 set and both ``render``/``render_http``:

1. ``render(code, as_sse=False)`` and ``render_http(code)`` return an
   :class:`~gateway_v2.edge.errors.HTTPResponse` carrying an ``application/json`` content-type
   header and a well-formed ``{"error": {"message", "type", "code"}}`` JSON body — never a raw
   framework error (the render never raises, never returns ``None``);
2. the ACTUAL declared mapping per code, read off the authoritative
   :data:`~gateway_v2.edge.errors._ENVELOPE` table: a MAPPED code renders its declared
   ``(status, type)`` (``missing_required_parameter`` -> ``(400, invalid_request_error)``;
   ``model_not_found`` -> ``(404, not_found_error)``), while a code with NO ``_ENVELOPE`` entry
   (the ``MALFORMED_*`` codes are declared value-codes but have no envelope row, so they render
   the generic fallback — the correct declared behaviour for an unmapped code) renders the
   declared generic :data:`~gateway_v2.edge.errors.GENERIC_HTTP_STATUS` (500) ``gateway_error``
   body;
3. the structured ``error.code`` field echoes the originating code VERBATIM (the trace keeps the
   single spelling), mapped or generic;
4. an UNMAPPED code never substitutes the raw code string into the human-readable ``message`` —
   no internal-detail leak (the synthetic unmapped codes carry a distinctive sentinel prefix, so
   a short code can never be an INCIDENTAL substring of the fixed English generic message); and
5. ``render(code, as_sse=False)`` is byte-identical to the direct
   :func:`~gateway_v2.edge.errors.render_http` call (``render`` is the ONE dispatch point — there
   is no second envelope).

Only the public shipped render surface is exercised; nothing is mocked.
"""

from __future__ import annotations

import random
import string

from gateway_v2.edge import errors
from gateway_v2.edge.wire.openai import (
    MALFORMED_BODY,
    MALFORMED_NOT_AN_OBJECT,
    MALFORMED_WRONG_SHAPE,
)
from gateway_v2.edge.wire.openai_misc import (
    MISSING_REQUIRED_PARAMETER,
    MODEL_NOT_FOUND,
)

_SEED = 0x16_15_0803
_ITERATIONS = 10_000

#: The declared generic HTTP ``error.type`` an unmapped code renders (never the raw code string).
_GENERIC_ERROR_TYPE = "gateway_error"

#: The GW16-relevant value-code set this property exercises: the two NEW misc codes plus the
#: three malformed-body codes the misc parser raises. Drawn randomly each iteration alongside the
#: synthetic unmapped codes, so BOTH the declared-entry path (the two new codes) AND the generic
#: fallback path (the ``MALFORMED_*`` codes, which have no ``_ENVELOPE`` row) are exercised.
_GW16_CODES: tuple[str, ...] = (
    MISSING_REQUIRED_PARAMETER,
    MODEL_NOT_FOUND,
    MALFORMED_BODY,
    MALFORMED_NOT_AN_OBJECT,
    MALFORMED_WRONG_SHAPE,
)

#: Characters an unmapped synthetic code may draw from (letters/digits/underscore — the shape of
#: a posture/misc value-code), so a generated code resembles a real code yet is absent from the
#: envelope table.
_CODE_ALPHABET = string.ascii_lowercase + string.digits + "_"

#: A distinctive sentinel prefix (plus an embedded digit) carried by every synthetic unmapped
#: code so a generated code can never be an INCIDENTAL substring of the fixed, English-word
#: generic message — the "no leak" assertion is about the renderer never SUBSTITUTING the raw
#: code into the message, not about a short token coincidentally occurring inside a sentence
#: (mirrors GW15 Property 18's sentinel-prefix technique).
_UNMAPPED_PREFIX = "zz9_gw16_unmapped_"


def _ctx(i: int, **extra: object) -> str:
    """A reproducible failure context string (seed + iteration + relevant values)."""
    body = " ".join(f"{k}={v!r}" for k, v in extra.items())
    return f"seed={_SEED:#x} iter={i} {body}"


def _random_unmapped_code(rng: random.Random) -> str:
    """A random value-code guaranteed to have NO declared ``_ENVELOPE`` entry.

    Draws a letters/digits/underscore token that looks like a value-code (carrying the
    :data:`_UNMAPPED_PREFIX` sentinel so it cannot be an incidental substring of the generic
    message), then regenerates until it is absent from the table — so the unmapped-code fallback
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


def test_property_15_every_gw16_value_code_renders_through_single_error_envelope() -> None:
    rng = random.Random(_SEED)

    # Sanity: the two NEW codes carry their declared envelope entries (task 3.1), so the
    # mapped-path assertions below are exercising the real table, not an accidental generic match.
    assert errors._ENVELOPE[MISSING_REQUIRED_PARAMETER] == (400, "invalid_request_error")
    assert errors._ENVELOPE[MODEL_NOT_FOUND] == (404, "not_found_error")

    for i in range(_ITERATIONS):
        # Draw EITHER a GW16-relevant code (the two new codes + the three MALFORMED_* codes) OR a
        # random unmapped synthetic code, so both the declared-entry path and the generic fallback
        # are exercised across the run.
        from_gw16_set = rng.random() < 0.5
        code = rng.choice(_GW16_CODES) if from_gw16_set else _random_unmapped_code(rng)

        # (1) The single Error_Envelope entry point renders without raising; a code always has A
        #     declared rendering (the envelope is total over the value-code space, R3.4), and the
        #     HTTP side always declares an application/json content-type (a declared envelope,
        #     never a raw framework error).
        http = errors.render(code, as_sse=False)
        assert isinstance(http, errors.HTTPResponse), _ctx(
            i, note="non-streaming render did not return an HTTPResponse", code=code, got=http
        )
        assert ("content-type", "application/json") in http.headers, _ctx(
            i, note="HTTP envelope missing application/json content-type", code=code,
            headers=http.headers,
        )

        http_error = _http_error_obj(http)
        http_message = http_error.get("message")
        assert isinstance(http_message, str), _ctx(
            i, note="HTTP error.message is not a string", code=code, got=http_message,
        )

        # (3) The structured code field echoes the originating code VERBATIM (single spelling).
        assert http_error.get("code") == code, _ctx(
            i, note="HTTP error.code did not echo the originating code", code=code,
            got=http_error.get("code"),
        )

        # (2) The ACTUAL declared mapping, read off the authoritative _ENVELOPE table: a code WITH
        #     a row renders that (status, type); a code with NO row (the MALFORMED_* codes among
        #     the GW16 set, and every synthetic unmapped code) renders the declared generic 500
        #     gateway_error fallback — the correct declared behaviour for an unmapped code.
        entry = errors._ENVELOPE.get(code)
        if entry is not None:
            declared_status, declared_type = entry
            assert http.status == declared_status, _ctx(
                i, note="mapped code did not render its declared HTTP status", code=code,
                got=http.status, want=declared_status,
            )
            assert http_error.get("type") == declared_type, _ctx(
                i, note="mapped code HTTP error.type is not the declared envelope type",
                code=code, got=http_error.get("type"), want=declared_type,
            )
        else:
            assert http.status == errors.GENERIC_HTTP_STATUS, _ctx(
                i, note="unmapped code did not render the generic HTTP status", code=code,
                got=http.status, want=errors.GENERIC_HTTP_STATUS,
            )
            assert http_error.get("type") == _GENERIC_ERROR_TYPE, _ctx(
                i, note="unmapped code HTTP error.type is not the generic type", code=code,
                got=http_error.get("type"),
            )
            # (4) An unmapped code NEVER substitutes the raw code string into the human-readable
            #     message (no internal-detail leak). True for the synthetic sentinel-prefixed
            #     codes AND for the MALFORMED_* codes (whose "malformed_request_*" spelling never
            #     appears in the fixed generic English message).
            assert code not in http_message, _ctx(
                i, note="unmapped code leaked the raw code into the HTTP message",
                code=code, message=http_message,
            )

        # (5) render(..., as_sse=False) is the ONE dispatch point: byte-identical to the direct
        #     render_http call, so there is no second envelope rendering a code a different way.
        assert http == errors.render_http(code), _ctx(
            i, note="render(as_sse=False) diverged from render_http", code=code,
        )
