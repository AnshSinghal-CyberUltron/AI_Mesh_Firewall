# Validates: Requirements 8.1, 8.2, 8.3
"""Envelope rows for the GW17 MCP value-codes."""

from __future__ import annotations

import pytest

from gateway_v2.edge import errors
from gateway_v2.edge.wire.sse import ERROR_SHAPES

_EXPECTED: tuple[tuple[str, int, str], ...] = (
    ("mcp_server_not_found", 404, "not_found_error"),
    ("mcp_tool_disabled", 403, "permission_error"),
    ("mcp_upstream_auth_unsupported", 403, "permission_error"),
    ("mcp_destination_refused", 403, "permission_error"),
    ("mcp_org_unmapped", 403, "permission_error"),
    ("mcp_registry_unavailable", 503, "service_unavailable"),
    ("mcp_broker_unavailable", 503, "service_unavailable"),
    ("mcp_upstream_error", 502, "gateway_error"),
    ("mcp_request_too_large", 413, "invalid_request_error"),
    ("mcp_result_too_large", 502, "gateway_error"),
    ("mcp_parse_error", 400, "invalid_request_error"),
    ("mcp_invalid_request", 400, "invalid_request_error"),
    ("mcp_method_not_found", 404, "not_found_error"),
    ("mcp_invalid_params", 400, "invalid_request_error"),
)


@pytest.mark.parametrize(("code", "status", "error_type"), _EXPECTED)
def test_mcp_code_renders_declared_status(code: str, status: int, error_type: str) -> None:
    response = errors.render_http(code)
    body = response.json()
    assert isinstance(body, dict)
    assert response.status == status
    assert body["error"]["type"] == error_type
    assert body["error"]["code"] == code
    assert body["error"]["message"] == ERROR_SHAPES[code][1]
    assert ERROR_SHAPES[code][0] == error_type
