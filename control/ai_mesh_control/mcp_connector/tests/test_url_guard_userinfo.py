"""The control-side SSRF guard refuses userinfo and any '@' in the URL authority.

#113 review note N1: urlparse takes the host after the LAST '@', so
"ws://127.0.0.1\\@public.example/mcp" was checked as public.example. The gateway_v2
dispatch guard (gateway_v2/dispatch/url_guard.py) already refuses userinfo; this keeps
the control plane at parity and refuses before any DNS lookup.
"""

from __future__ import annotations

import os
import socket
from unittest import mock

from django.test import SimpleTestCase

from mcp_connector._url_guard import is_safe_outbound_url

_PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.215.14", 443))]


class UrlGuardUserinfoTests(SimpleTestCase):
    def _check(self, url, schemes=("ws", "wss", "http", "https")):
        with mock.patch.dict(os.environ, {"MCP_ALLOW_INTERNAL_HOSTS": ""}), mock.patch(
            "mcp_connector._url_guard.socket.getaddrinfo", return_value=_PUBLIC
        ) as getaddrinfo:
            ok, reason = is_safe_outbound_url(url, allowed_schemes=schemes)
        return ok, reason, getaddrinfo

    def test_userinfo_forms_refused_before_dns(self):
        for url in (
            "ws://127.0.0.1\\@public.example/mcp",
            "https://user:pass@mcp.example.com/mcp",
            "https://user@mcp.example.com/mcp",
            "https://@mcp.example.com/mcp",
            "http://169.254.169.254@mcp.example.com/",
        ):
            ok, reason, getaddrinfo = self._check(url)
            self.assertFalse(ok, url)
            self.assertIn("userinfo", reason, url)
            getaddrinfo.assert_not_called()

    def test_userinfo_refused_even_for_allowlisted_host(self):
        with mock.patch.dict(os.environ, {"MCP_ALLOW_INTERNAL_HOSTS": "mcp.example.com"}):
            ok, reason = is_safe_outbound_url("https://u:p@mcp.example.com/mcp")
        self.assertFalse(ok)
        self.assertIn("userinfo", reason)

    def test_plain_public_url_still_allowed(self):
        ok, reason, getaddrinfo = self._check("https://mcp.example.com/mcp")
        self.assertTrue(ok, reason)
        getaddrinfo.assert_called_once()
