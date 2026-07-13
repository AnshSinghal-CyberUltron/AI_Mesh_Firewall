"""Regression: HTTP OAuth servers pending first authorize must not sync-fail.

Bug #3 / B2: background tool discovery on a freshly registered HTTP OAuth server
called _ensure_oauth_token_fresh without a token, which marked needs_reauth and
surfaced "token expired" instead of the pending-authorization UI.
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from mcp_connector.views import (
    _discover_tools_via_gateway,
    _ensure_oauth_token_fresh,
    _oauth_pending_initial_authorization,
)


class OAuthPendingAuthorizationTests(SimpleTestCase):
    def test_pending_helper_true_without_token(self):
        srv = SimpleNamespace(auth_type="oauth", auth_token="")
        self.assertTrue(_oauth_pending_initial_authorization(srv))

    def test_pending_helper_false_with_token(self):
        srv = SimpleNamespace(auth_type="oauth", auth_token="tok")
        self.assertFalse(_oauth_pending_initial_authorization(srv))

    def test_ensure_fresh_does_not_mark_needs_reauth_when_never_authorized(self):
        srv = SimpleNamespace(
            auth_type="oauth",
            auth_token="",
            oauth_refresh_token="",
            oauth_token_endpoint="",
            server_slug="pending-oauth",
        )
        with patch("mcp_connector.views._mark_needs_reauth") as mark:
            self.assertFalse(_ensure_oauth_token_fresh(srv))
            mark.assert_not_called()

    @patch("mcp_connector.views.requests.post")
    def test_discover_skips_gateway_when_pending(self, post):
        srv = SimpleNamespace(
            auth_type="oauth",
            auth_token="",
            transport="streamable-http",
            url="https://mcp.example.com/mcp",
            server_slug="pending-oauth",
        )
        org = SimpleNamespace(slug="demo")
        with patch("mcp_connector.views._gateway_internal_secret", return_value="secret"):
            tools, err = _discover_tools_via_gateway(srv, org)
        self.assertEqual(tools, [])
        self.assertIsNone(err)
        post.assert_not_called()
