# Validates: Requirements 3.1, 3.2, 3.3, 3.4, 8.2, 8.4, 8.5
"""Envelope rows for the GW15 ``/v1/responses`` adapter codes."""

from __future__ import annotations

import pytest

from gateway_v2.edge import errors
from gateway_v2.edge.wire.responses import (
    RESPONSES_INVALID_MODEL,
    RESPONSES_MALFORMED_BODY,
    RESPONSES_MISSING_INPUT,
    UNSUPPORTED_CONTENT_PART,
)
from gateway_v2.edge.wire.sse import ERROR_SHAPES

_EXPECTED: tuple[tuple[str, int, str], ...] = (
    (RESPONSES_MALFORMED_BODY, 400, "invalid_request_error"),
    (RESPONSES_INVALID_MODEL, 400, "invalid_request_error"),
    (RESPONSES_MISSING_INPUT, 400, "invalid_request_error"),
    (UNSUPPORTED_CONTENT_PART, 400, "invalid_request_error"),
    ("response_not_found", 404, "not_found_error"),
    ("response_store_unavailable", 503, "service_unavailable"),
)


@pytest.mark.parametrize(("code", "status", "error_type"), _EXPECTED)
def test_responses_code_renders_declared_status(code: str, status: int, error_type: str) -> None:
    """Each adapter code renders its status, type, and a declared message."""
    response = errors.render_http(code, request_id="zs-test")
    body = response.json()
    assert isinstance(body, dict)
    error = body["error"]
    assert response.status == status
    assert error["type"] == error_type
    assert error["code"] == code
    assert error["message"] == ERROR_SHAPES[code][1]
    assert ERROR_SHAPES[code][0] == error_type
    assert ("x-request-id", "zs-test") in response.headers


@pytest.mark.parametrize(
    ("code", "param"),
    (
        (RESPONSES_INVALID_MODEL, "model"),
        (RESPONSES_MISSING_INPUT, "input"),
        (UNSUPPORTED_CONTENT_PART, "input"),
    ),
)
def test_field_specific_codes_echo_param(code: str, param: str) -> None:
    """The field-specific refusals name the field the SDK reports as ``error.param``."""
    body = errors.render_http(code, param=param).json()
    assert isinstance(body, dict)
    assert body["error"]["param"] == param


def test_chat_malformed_body_stays_unmapped() -> None:
    """Chat's ``malformed_request_body`` keeps its generic 500. This card does not change it."""
    assert "malformed_request_body" not in errors._ENVELOPE
    assert errors.render_http("malformed_request_body").status == errors.GENERIC_HTTP_STATUS
