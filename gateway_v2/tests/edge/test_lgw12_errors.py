# Feature: sse-egress-pipeline
# Validates: Requirements 3.4
"""Unit tests for the single ``Error_Envelope`` (GW12, task 7.3).

Covers :mod:`gateway_v2.edge.errors` — ``render`` / ``render_http`` / ``render_sse`` /
``render_not_implemented`` / :class:`HTTPResponse` and the ``_ENVELOPE`` table. These are
example/unit tests (no ≥10k property loop, no ``hypothesis``): the property-level coverage of
the codec round-trip + confluence lives in ``test_lgw12_sse_codec.py``; this file pins the
envelope's declared HTTP/SSE renderings and the unmapped-code generic fallback (R3.4).

Key invariants asserted:

* A MAPPED value-code renders the HTTP status declared in ``_ENVELOPE`` (read from the table,
  not a hardcoded guess that could drift) with a ``{"error": {message, type, code}}`` body
  whose ``code`` echoes the originating value-code.
* An UNMAPPED code renders the declared generic ``500`` ``gateway_error`` body whose
  human-readable ``message`` does NOT contain the raw code (R3.4) — the code appears only in
  the structured ``code`` field.
* ``render(code, as_sse=True)`` returns the single-sourced SSE ``Error_Frame`` bytes
  (``data: {json}\n\n``); ``render(code, as_sse=False)`` returns an :class:`HTTPResponse`.
* ``render_not_implemented`` renders the declared ``501`` placeholder, never a raw ``404``.
"""

from __future__ import annotations

import json

from gateway_v2.domain import posture
from gateway_v2.edge import errors
from gateway_v2.edge.wire.sse import SSEEncoder

# A representative set of codes that MUST have a declared envelope entry (the task names
# these four explicitly). Each is read against the actual ``_ENVELOPE`` table below, so the
# assertion tracks the table and does not hardcode a status that could drift.
_MAPPED_CODES: tuple[str, ...] = (
    posture.STREAM_KEY_REVOKED,
    posture.STREAM_IDLE_TIMEOUT,
    posture.STREAM_BUFFER_UNAVAILABLE,
    posture.PLAN_UNAVAILABLE,
)

#: A code with no declared ``_ENVELOPE`` entry — exercises the generic fallback (R3.4). Not a
#: posture constant on purpose: an unmapped code is exactly an internal gap the renderer must
#: not leak.
_UNMAPPED_CODE = "some_unmapped_internal_code"


def _error_obj(body: bytes) -> dict[str, object]:
    """Parse an ``{"error": {...}}`` HTTP JSON body and return the inner ``error`` object."""
    outer = json.loads(body.decode("utf-8"))
    assert isinstance(outer, dict), f"HTTP body is not a JSON object: {outer!r}"
    error = outer.get("error")
    assert isinstance(error, dict), f"HTTP body has no error object: {outer!r}"
    return error


# =========================================================================== #
# Mapped codes: declared status + echoed code (R3.2).
# =========================================================================== #


def test_mapped_code_renders_declared_status_and_echoes_code() -> None:
    """A mapped code renders the table's declared status with an echoing ``code`` field (R3.2)."""
    for code in _MAPPED_CODES:
        entry = errors._ENVELOPE.get(code)
        assert entry is not None, f"expected {code!r} to have a declared envelope entry"
        declared_status, declared_type = entry

        response = errors.render_http(code)
        assert isinstance(response, errors.HTTPResponse)
        # Status comes from the ACTUAL table entry, not a hardcoded guess (no drift).
        assert response.status == declared_status, (
            f"{code!r}: rendered status {response.status} != declared {declared_status}"
        )
        error = _error_obj(response.body)
        # The structured code echoes the originating value-code (single spelling in the trace).
        assert error.get("code") == code, (
            f"{code!r}: body code field {error.get('code')!r} does not echo the value-code"
        )
        # The body ``type`` agrees with the envelope's declared SSE error_type.
        assert error.get("type") == declared_type, (
            f"{code!r}: body type {error.get('type')!r} != declared {declared_type!r}"
        )
        # The body is the declared three-field shape with a non-empty human message.
        assert isinstance(error.get("message"), str) and error["message"], (
            f"{code!r}: body missing a human-readable message: {error!r}"
        )
        # JSON content type is declared.
        assert ("content-type", "application/json") in response.headers


def test_mapped_code_specific_statuses_match_table() -> None:
    """Spot-check the declared statuses for the named codes against the ``_ENVELOPE`` table.

    These are read FROM the table (``_ENVELOPE[code][0]``) rather than asserted as literals, so
    the test documents the mapping while staying drift-proof: it only fails if ``render_http``
    disagrees with the table, not if the table itself is retuned.
    """
    for code in _MAPPED_CODES:
        table_status = errors._ENVELOPE[code][0]
        assert errors.render_http(code).status == table_status


# =========================================================================== #
# Unmapped code: generic 500, no raw code in the human message (R3.4).
# =========================================================================== #


def test_unmapped_code_renders_generic_500() -> None:
    """An unmapped code renders the declared generic ``500`` ``gateway_error`` body (R3.4)."""
    response = errors.render_http(_UNMAPPED_CODE)
    assert isinstance(response, errors.HTTPResponse)
    assert response.status == errors.GENERIC_HTTP_STATUS == 500, (
        f"unmapped code did not render the generic status: {response.status}"
    )
    error = _error_obj(response.body)
    assert error.get("type") == "gateway_error", (
        f"unmapped code body type is not the generic gateway_error: {error!r}"
    )


def test_unmapped_code_message_does_not_leak_raw_code() -> None:
    """The generic ``message`` for an unmapped code NEVER contains the raw code string (R3.4).

    The raw value-code may appear ONLY in the structured ``code`` field — not in the
    human-readable ``message`` that a client or an operator reads — so an internal gap leaks no
    internal detail.
    """
    response = errors.render_http(_UNMAPPED_CODE)
    error = _error_obj(response.body)

    message = error.get("message")
    assert isinstance(message, str)
    assert _UNMAPPED_CODE not in message, (
        f"the raw value-code leaked into the human-readable message: {message!r}"
    )
    # The structured code field still carries the raw code (single spelling for the trace).
    assert error.get("code") == _UNMAPPED_CODE, (
        f"the structured code field should echo the raw code: {error!r}"
    )


# =========================================================================== #
# render() dispatch: SSE bytes vs HTTPResponse (R3.1 / R3.2).
# =========================================================================== #


def test_render_as_sse_returns_single_sourced_error_frame_bytes() -> None:
    """``render(code, as_sse=True)`` returns the single-sourced SSE ``Error_Frame`` bytes (R3.2).

    Single-sourced via the one :class:`SSEEncoder` the streaming path uses, so the two sides of
    the stream cannot disagree on the error shape — the bytes are identical to a fresh encoder's
    ``error(code)``. The frame is the full terminal ``data: {json}\n\n`` error frame.
    """
    for code in (*_MAPPED_CODES, _UNMAPPED_CODE):
        rendered = errors.render(code, as_sse=True)
        assert isinstance(rendered, bytes), (
            f"{code!r}: as_sse=True did not return bytes: {type(rendered)!r}"
        )
        # Single-sourced through the SSE encoder — byte-identical to the encoder's own render.
        assert rendered == SSEEncoder().error(code), (
            f"{code!r}: SSE render diverged from the single-sourced encoder output"
        )
        # The declared SSE framing an SDK parses.
        assert rendered.startswith(b"data: "), f"{code!r}: SSE frame missing 'data: ' prefix"
        assert rendered.endswith(b"\n\n"), f"{code!r}: SSE frame missing blank-line terminator"
        payload = rendered[len(b"data: ") : -len(b"\n\n")]
        obj = json.loads(payload.decode("utf-8"))
        assert isinstance(obj, dict)
        error = obj.get("error")
        assert isinstance(error, dict), f"{code!r}: SSE frame is not an Error_Frame: {obj!r}"
        # The SSE frame echoes the code in its structured ``code`` field (declared contract).
        assert error.get("code") == code, (
            f"{code!r}: SSE Error_Frame code field does not echo the value-code: {error!r}"
        )


def test_render_as_http_returns_httpresponse() -> None:
    """``render(code, as_sse=False)`` returns an :class:`HTTPResponse` (not SSE bytes)."""
    for code in (*_MAPPED_CODES, _UNMAPPED_CODE):
        rendered = errors.render(code, as_sse=False)
        assert isinstance(rendered, errors.HTTPResponse), (
            f"{code!r}: as_sse=False did not return an HTTPResponse: {type(rendered)!r}"
        )
        # Equivalent to the direct render_http path.
        assert rendered == errors.render_http(code)


# =========================================================================== #
# not_implemented placeholder: declared 501, not a raw 404 (R2.3).
# =========================================================================== #


def test_render_not_implemented_is_501_not_404() -> None:
    """A registered-for-later surface renders the declared ``501``, never a raw ``404`` (R2.3)."""
    response = errors.render_not_implemented("responses")
    assert isinstance(response, errors.HTTPResponse)
    assert response.status == errors.NOT_IMPLEMENTED_HTTP_STATUS == 501, (
        f"placeholder did not render 501 Not Implemented: {response.status}"
    )
    assert response.status != 404, "placeholder must not render a raw 404"
    error = _error_obj(response.body)
    assert error.get("type") == "not_implemented", (
        f"placeholder body type is not not_implemented: {error!r}"
    )
    # The surface name is carried only in the structured code field, not the human message.
    assert error.get("code") == "not_implemented:responses", (
        f"placeholder code field does not carry the surface: {error!r}"
    )
    message = error.get("message")
    assert isinstance(message, str) and message, "placeholder missing a human message"


# =========================================================================== #
# HTTPResponse value helpers.
# =========================================================================== #


def test_httpresponse_json_helper_parses_body() -> None:
    """:meth:`HTTPResponse.json` round-trips the rendered JSON body for inspection."""
    response = errors.render_http(posture.STREAM_KEY_REVOKED)
    parsed = response.json()
    assert isinstance(parsed, dict)
    assert "error" in parsed
