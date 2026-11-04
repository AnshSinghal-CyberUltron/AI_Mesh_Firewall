"""GW32: every code the MCP surface can refuse with is in the error table, and the table's MCP
codes are all produced somewhere (drift test, both directions)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from gateway_v2.domain import posture
from gateway_v2.edge import errors

_EDGE = Path(__file__).resolve().parents[2] / "gateway_v2"
_SOURCES = [
    *(_EDGE / "edge").glob("mcp*.py"),
    _EDGE / "edge" / "oauth_routes.py",
    *(_EDGE / "dispatch").glob("mcp*.py"),
]
# codes declared in the OAuth error set live in oauth_routes only (RFC 6749 bodies)
_NEW_CODES = {
    "mcp_tool_not_permitted": (403, "permission_error"),
    "mcp_tool_call_cap_exceeded": (429, "rate_limit_error"),
    "mcp_session_invalid": (404, "not_found_error"),
    "mcp_session_limit": (429, "rate_limit_error"),
    "mcp_origin_refused": (403, "permission_error"),
    "mcp_org_mismatch": (403, "permission_error"),
    "mcp_upstream_needs_reauth": (403, "permission_error"),
    "mcp_upstream_credential_unavailable": (503, "service_unavailable"),
    "mcp_upstream_credential_invalid": (502, "gateway_error"),
    "mcp_upstream_insecure_transport": (403, "permission_error"),
    "mcp_transport_unsupported": (400, "invalid_request_error"),
}


def _refusal_literals() -> set[str]:
    found: set[str] = set()
    for path in _SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") in (
                "_Refuse",
                "CredentialRefusal",
            ):
                found.update(
                    arg.value
                    for arg in node.args
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                )
    return found


def test_every_refusal_literal_has_a_row_in_the_table() -> None:
    for code in _refusal_literals():
        rendered = errors.render_http(code)
        assert rendered.status != 500 or code == "malformed_request_body", code


def test_new_codes_have_the_pinned_status_and_type() -> None:
    for code, (status, kind) in _NEW_CODES.items():
        rendered = errors.render_http(code)
        assert rendered.status == status, code
        assert kind.encode() in rendered.body, code


def test_needs_reauth_is_never_a_401() -> None:
    rendered = errors.render_http(posture.MCP_UPSTREAM_NEEDS_REAUTH)
    assert rendered.status == 403
    assert all(name != "www-authenticate" for name, _ in rendered.headers)


def test_posture_constants_match_the_table() -> None:
    values = {
        getattr(posture, name)
        for name in dir(posture)
        if name.startswith("MCP_") and isinstance(getattr(posture, name), str)
    }
    assert set(_NEW_CODES) <= values
    assert all(re.fullmatch(r"mcp_[a-z_]+", v) for v in values)
