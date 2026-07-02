"""Regression tests for the transport-aware OAuth guard (MCP OAuth bugs #1/#2).

OAuth 2.1 (auth_type="oauth") is HTTP-only: the control-plane flow runs RFC
9728/8414 discovery + token injection against an HTTP MCP endpoint URL. A stdio
(or websocket) server has no URL, so persisting auth_type="oauth" on it produced
a second, broken "Authorize" button (dup OAuth UI) that 400s with
"Server has no URL; OAuth is only for HTTP transports" when clicked.

MCPServerCreateSerializer.validate must reject auth_type="oauth" for any
non-HTTP transport, while still accepting it for streamable-http / sse.
"""

from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase

from mcp_connector.serializers import MCPServerCreateSerializer
from mcp_connector.views import oauth_http_transport_error


class OAuthTransportGuardTests(TestCase):
    def _errors(self, data):
        ser = MCPServerCreateSerializer(data=data)
        ok = ser.is_valid()
        return ok, ser.errors

    def test_stdio_plus_oauth_is_rejected(self):
        ok, errors = self._errors(
            {
                "name": "guard-stdio-oauth",
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "mcp-remote", "https://mcp.linear.app/mcp"],
                "auth_type": "oauth",
            }
        )
        self.assertFalse(ok)
        self.assertIn("auth_type", errors)
        self.assertRegex(str(errors["auth_type"][0]), r"HTTP MCP transport")

    def test_websocket_plus_oauth_is_rejected(self):
        # websocket is a non-HTTP transport -> oauth must never be accepted.
        # (Rejected either by the URLField/scheme check or the oauth guard;
        # either way registration must fail closed.)
        ok, _errors = self._errors(
            {
                "name": "guard-ws-oauth",
                "transport": "websocket",
                "url": "wss://mcp.linear.app/mcp",
                "auth_type": "oauth",
            }
        )
        self.assertFalse(ok)

    def test_streamable_http_plus_oauth_is_allowed(self):
        ok, errors = self._errors(
            {
                "name": "guard-http-oauth",
                "transport": "streamable-http",
                "url": "https://mcp.linear.app/mcp",
                "auth_type": "oauth",
            }
        )
        self.assertTrue(ok, msg=f"expected valid, got {errors}")

    def test_sse_plus_oauth_is_allowed(self):
        ok, errors = self._errors(
            {
                "name": "guard-sse-oauth",
                "transport": "sse",
                "url": "https://mcp.linear.app/sse",
                "auth_type": "oauth",
            }
        )
        self.assertTrue(ok, msg=f"expected valid, got {errors}")

    def test_stdio_plus_none_is_allowed(self):
        # stdio mcp-remote (Linear) is legitimate — upstream OAuth is handled
        # gateway-side; auth_type stays "none".
        ok, errors = self._errors(
            {
                "name": "guard-stdio-none",
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "mcp-remote", "https://mcp.linear.app/mcp"],
                "auth_type": "none",
            }
        )
        self.assertTrue(ok, msg=f"expected valid, got {errors}")


class OAuthStartViewTransportGuardTests(SimpleTestCase):
    """B1: the *authorize* endpoint (MCPServerOAuthStartView) must enforce the
    same HTTP-only invariant as registration — server-side, BEFORE discovery or
    any auth_type mutation. This is what makes the dup/broken control authorize
    path unreachable ('Server has no URL' + the oauth+stdio guard-bypass)."""

    def test_stdio_row_is_rejected_with_transport_error_not_no_url(self):
        # A stdio row (even one carrying an mcp-remote HTTPS URL in args and a
        # populated url column) must be rejected with the TRANSPORT error, never
        # allowed to proceed to discovery or flip auth_type.
        srv = SimpleNamespace(transport="stdio", url="https://mcp.linear.app/mcp")
        err = oauth_http_transport_error(srv)
        self.assertIsNotNone(err)
        self.assertRegex(err, r"HTTP MCP transport")
        # It must NOT be the misleading "Server has no URL" message.
        self.assertNotRegex(err, r"has no URL")

    def test_websocket_row_is_rejected(self):
        srv = SimpleNamespace(transport="websocket", url="wss://mcp.linear.app/mcp")
        err = oauth_http_transport_error(srv)
        self.assertIsNotNone(err)
        self.assertRegex(err, r"HTTP MCP transport")

    def test_stdio_row_without_url_still_gets_transport_error(self):
        # Order matters: transport is checked first, so a URL-less stdio row gets
        # the clear transport error, not "Server has no URL".
        srv = SimpleNamespace(transport="stdio", url="")
        err = oauth_http_transport_error(srv)
        self.assertRegex(err, r"HTTP MCP transport")

    def test_streamable_http_with_url_is_eligible(self):
        srv = SimpleNamespace(transport="streamable-http", url="https://mcp.example.com/mcp")
        self.assertIsNone(oauth_http_transport_error(srv))

    def test_sse_with_url_is_eligible(self):
        srv = SimpleNamespace(transport="sse", url="https://mcp.example.com/sse")
        self.assertIsNone(oauth_http_transport_error(srv))

    def test_http_transport_missing_url_is_the_only_no_url_path(self):
        # The corrupted-data edge case (HTTP transport but empty url) is the ONLY
        # remaining path to the "Server has no URL" message — unreachable via
        # normal registration (serializer requires url for HTTP transports).
        srv = SimpleNamespace(transport="streamable-http", url="")
        err = oauth_http_transport_error(srv)
        self.assertIsNotNone(err)
        self.assertRegex(err, r"has no URL")
