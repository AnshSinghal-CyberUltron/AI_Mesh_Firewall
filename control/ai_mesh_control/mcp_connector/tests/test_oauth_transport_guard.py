"""Regression tests for the transport-aware OAuth guard (MCP OAuth bugs #1/#2).

OAuth 2.1 (auth_type="oauth") is HTTP-only: the control-plane flow runs RFC
9728/8414 discovery + token injection against an HTTP MCP endpoint URL. A stdio
(or websocket) server has no URL, so persisting auth_type="oauth" on it produced
a second, broken "Authorize" button (dup OAuth UI) that 400s with
"Server has no URL; OAuth is only for HTTP transports" when clicked.

MCPServerCreateSerializer.validate must reject auth_type="oauth" for any
non-HTTP transport, while still accepting it for streamable-http / sse.
"""

import os
from types import SimpleNamespace
from unittest import mock

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
        # (Rejected by the oauth guard; registration must fail closed.)
        ok, _errors = self._errors(
            {
                "name": "guard-ws-oauth",
                "transport": "websocket",
                "url": "wss://mcp.linear.app/mcp",
                "auth_type": "oauth",
            }
        )
        self.assertFalse(ok)

    # The ws:// cases below pin DNS and the SSRF allowlist instead of reading them
    # from the host. `ws-everything.stub` only exists on the docker-compose network,
    # where scripts/mcp_enable_http_via_sandbox.sh puts it in MCP_ALLOW_INTERNAL_HOSTS
    # (8d3e744b: "serializer SSRF guard unchanged; ws stub allowlisted"). Elsewhere the
    # name does not resolve and the guard correctly fails closed, which is why the
    # original test failed in CI (#106).
    _WS_STUB = "ws://ws-everything.stub:3003/mcp"

    def _ws_errors(self, url, *, resolves_to=None, allow_internal=""):
        import socket

        def _getaddrinfo(host, port, *args, **kwargs):
            if resolves_to is None:
                raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (resolves_to, port or 0))]

        with mock.patch.dict(os.environ, {"MCP_ALLOW_INTERNAL_HOSTS": allow_internal}), mock.patch(
            "mcp_connector._url_guard.socket.getaddrinfo", side_effect=_getaddrinfo
        ) as getaddrinfo:
            ok, errors = self._errors(
                {"name": "guard-ws-stub", "transport": "websocket", "url": url, "auth_type": "none"}
            )
        return ok, errors, getaddrinfo

    def test_websocket_ws_scheme_url_is_allowed(self):
        # P4.13: CharField + serializer SSRF guard must accept ws:// (URLField rejected it).
        # The host resolves to a public address, so the full guard runs and passes.
        ok, errors, getaddrinfo = self._ws_errors(self._WS_STUB, resolves_to="93.184.215.14")
        self.assertTrue(ok, msg=f"expected valid, got {errors}")
        getaddrinfo.assert_called_once()

    def test_websocket_ws_url_to_private_address_is_rejected(self):
        # On the compose network the stub resolves to a private address: without the
        # allowlist entry the SSRF guard must refuse it.
        ok, errors, _ = self._ws_errors(self._WS_STUB, resolves_to="172.20.0.7")
        self.assertFalse(ok)
        self.assertRegex(str(errors["url"][0]), r"SSRF guard.*private address")

    def test_websocket_ws_url_unresolvable_fails_closed(self):
        ok, errors, _ = self._ws_errors(self._WS_STUB, resolves_to=None)
        self.assertFalse(ok)
        self.assertRegex(str(errors["url"][0]), r"SSRF guard.*DNS resolution failed")

    def test_websocket_ws_loopback_literal_is_rejected(self):
        ok, errors, getaddrinfo = self._ws_errors("ws://127.0.0.1:3003/mcp")
        self.assertFalse(ok)
        # ipaddress classifies 127.0.0.1 as private before loopback; either way blocked.
        self.assertRegex(str(errors["url"][0]), r"SSRF guard: blocked host: .*\(127\.0\.0\.1\)")
        getaddrinfo.assert_not_called()

    def test_websocket_ws_stub_allowed_only_via_explicit_allowlist(self):
        # Guard semantics for a host:port allowlist entry, not a copy of the deployed
        # list: scripts/mcp_enable_http_via_sandbox.sh:8 allowlists both
        # ws-everything.stub:3003 and the bare ws-everything.stub, and a bare host
        # entry admits every port (_url_guard._host_is_allowlisted).
        ok, errors, _ = self._ws_errors(
            self._WS_STUB, resolves_to="172.20.0.7", allow_internal="ws-everything.stub:3003"
        )
        self.assertTrue(ok, msg=f"expected valid, got {errors}")
        # The entry is port-specific: another port on the same host is not allowlisted.
        ok, _errors, _ = self._ws_errors(
            "ws://ws-everything.stub:3999/mcp",
            resolves_to="172.20.0.7",
            allow_internal="ws-everything.stub:3003",
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
