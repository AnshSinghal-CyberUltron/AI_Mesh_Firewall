"""Tests for agent API key auth (core.agent_auth).

Regression: secrets.compare_digest raises TypeError on str operands containing
non-ASCII. The agent key comes from an attacker-controlled HTTP header (Django
decodes headers latin-1, so bytes >0x7F arrive as U+0080-U+00FF), so a crafted
key must yield a 401 (AuthenticationFailed), never an unhandled TypeError (500).
"""

from __future__ import annotations

from django.test import RequestFactory, TestCase, override_settings
from rest_framework.exceptions import AuthenticationFailed

CONFIGURED_KEY = "ascii-configured-key"
# é = U+00E9, ÿ = U+00FF: both reachable via Django's latin-1 header decoding.
NON_ASCII_KEY = "kéyÿ"


class AgentAuthNonAsciiKeyTests(TestCase):
    """A non-ASCII key against an ASCII configured key must 401, not TypeError."""

    def setUp(self):
        self.factory = RequestFactory()

    def _request_with_key(self, key: str):
        return self.factory.get("/api/agents/", HTTP_X_AGENT_KEY=key)

    @override_settings(AGENT_API_KEY=CONFIGURED_KEY)
    def test_resolve_agent_auth_non_ascii_key_raises_authentication_failed(self):
        from core.agent_auth import _resolve_agent_auth

        request = self._request_with_key(NON_ASCII_KEY)
        with self.assertRaises(AuthenticationFailed):
            _resolve_agent_auth(request)

    @override_settings(AGENT_API_KEY=CONFIGURED_KEY)
    def test_authentication_class_non_ascii_key_raises_authentication_failed(self):
        from core.agent_auth import AgentKeyAuthentication

        request = self._request_with_key(NON_ASCII_KEY)
        with self.assertRaises(AuthenticationFailed):
            AgentKeyAuthentication().authenticate(request)

    @override_settings(AGENT_API_KEY=CONFIGURED_KEY)
    def test_permission_class_non_ascii_key_raises_authentication_failed(self):
        from core.agent_auth import AgentAPIKeyPermission

        request = self._request_with_key(NON_ASCII_KEY)
        with self.assertRaises(AuthenticationFailed):
            AgentAPIKeyPermission().has_permission(request, view=None)

    @override_settings(AGENT_API_KEY=CONFIGURED_KEY)
    def test_non_ascii_bearer_key_raises_authentication_failed(self):
        from core.agent_auth import AgentKeyAuthentication

        request = self.factory.get(
            "/api/agents/", HTTP_AUTHORIZATION=f"Bearer {NON_ASCII_KEY}"
        )
        with self.assertRaises(AuthenticationFailed):
            AgentKeyAuthentication().authenticate(request)

    @override_settings(AGENT_API_KEY=CONFIGURED_KEY)
    def test_matching_ascii_key_still_authenticates(self):
        from core.agent_auth import AgentKeyAuthentication, _resolve_agent_auth

        request = self._request_with_key(CONFIGURED_KEY)
        self.assertTrue(_resolve_agent_auth(request))

        request = self._request_with_key(CONFIGURED_KEY)
        result = AgentKeyAuthentication().authenticate(request)
        self.assertIsNotNone(result)
        self.assertEqual(result[1], CONFIGURED_KEY)

    @override_settings(AGENT_API_KEY=NON_ASCII_KEY)
    def test_matching_non_ascii_key_authenticates(self):
        """Byte comparison must still match equal non-ASCII keys on both sides."""
        from core.agent_auth import _resolve_agent_auth

        request = self._request_with_key(NON_ASCII_KEY)
        self.assertTrue(_resolve_agent_auth(request))

    # Lone surrogates are reachable on the *configured* side: os.environ decodes
    # env bytes with surrogateescape, so invalid UTF-8 in AGENT_API_KEY arrives
    # as e.g. U+DCFF. The compare must 401 a non-matching key, not raise.
    @override_settings(AGENT_API_KEY="k\udcffy")
    def test_lone_surrogate_in_configured_key_raises_authentication_failed(self):
        from core.agent_auth import _resolve_agent_auth

        request = self._request_with_key("wrong-key")
        with self.assertRaises(AuthenticationFailed):
            _resolve_agent_auth(request)
