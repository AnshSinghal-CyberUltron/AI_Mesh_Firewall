"""Regression tests for the transport-aware OAuth guard (MCP OAuth bugs #1/#2).

OAuth 2.1 (auth_type="oauth") is HTTP-only: the control-plane flow runs RFC
9728/8414 discovery + token injection against an HTTP MCP endpoint URL. A stdio
(or websocket) server has no URL, so persisting auth_type="oauth" on it produced
a second, broken "Authorize" button (dup OAuth UI) that 400s with
"Server has no URL; OAuth is only for HTTP transports" when clicked.

MCPServerCreateSerializer.validate must reject auth_type="oauth" for any
non-HTTP transport, while still accepting it for streamable-http / sse.
"""

from django.test import TestCase

from mcp_connector.serializers import MCPServerCreateSerializer


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
