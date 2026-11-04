"""GW32 (DD-4): internal upstream OAuth flow endpoints: permission, org scoping, uniform 404,
single-use state, TTL, no secret in status, token write bumps the version and scan version."""
from datetime import timedelta
from unittest.mock import patch

import fakeredis
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from mcp_connector.models import MCPServerRegistration
from mcp_connector.signals import scan_version_key

INTERNAL = {"HTTP_X_GATEWAY_AUTH": "true", "HTTP_X_GATEWAY_INTERNAL_KEY": "internal-test-key"}
BASE = "/api/mcp-connector/internal/"
STATE = "s" * 43
VERIFIER = "v" * 64
SECRET = "client-secret-9f8e7d"


@override_settings(GATEWAY_INTERNAL_API_KEY="internal-test-key")
class OAuthFlowEndpointTests(TestCase):
    def setUp(self):
        from auth.models import Organization

        self.org = Organization.objects.create(name="Org A", slug="org-a")
        Organization.objects.create(name="Org B", slug="org-b")
        self.redis = fakeredis.FakeRedis(decode_responses=True)
        patcher = patch("mcp_connector.signals._get_redis_client", return_value=self.redis)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = APIClient()
        with self.captureOnCommitCallbacks(execute=True):
            self.server = MCPServerRegistration.objects.create(
                name="S", server_slug="s", url="https://mcp.example.com/mcp",
                organization=self.org, transport="stdio", command="npx",
            )

    def _post(self, path, body, org="org-a", **extra):
        return self.client.post(BASE + path, body, format="json", HTTP_X_ORG_SLUG=org,
                                **{**INTERNAL, **extra})

    def _flow(self, **over):
        body = {
            "server_slug": "s", "state": STATE, "code_verifier": VERIFIER, "key_id": "kid",
            "ttl_s": 600, "authorization_endpoint": "https://idp.example.com/authorize",
            "token_endpoint": "https://idp.example.com/token", "registration_endpoint": "",
            "client_id": "cid", "client_secret": SECRET, "resource": "https://mcp.example.com",
            "scope": "",
        }
        body.update(over)
        return body

    def test_flow_requires_internal_key(self):
        resp = self.client.post(BASE + "oauth-flow/", self._flow(), format="json",
                                HTTP_X_ORG_SLUG="org-a")
        self.assertIn(resp.status_code, (401, 403))

    def test_flow_store_then_consume_once(self):
        self.assertEqual(self._post("oauth-flow/", self._flow()).status_code, 200)
        first = self._post("oauth-flow/consume/", {"state": STATE}, org="")
        self.assertEqual(first.status_code, 200)
        body = first.json()
        self.assertEqual((body["org_slug"], body["server_slug"], body["key_id"]),
                         ("org-a", "s", "kid"))
        self.assertEqual(body["code_verifier"], VERIFIER)
        replay = self._post("oauth-flow/consume/", {"state": STATE}, org="")
        self.assertEqual(replay.status_code, 404)

    def test_expired_state_is_refused_and_cleared(self):
        self._post("oauth-flow/", self._flow())
        MCPServerRegistration.objects.filter(pk=self.server.pk).update(
            oauth_flow_expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertEqual(self._post("oauth-flow/consume/", {"state": STATE}).status_code, 404)
        self.server.refresh_from_db()
        self.assertEqual((self.server.oauth_state, self.server.oauth_code_verifier), ("", ""))

    def test_unknown_or_malformed_state_is_404(self):
        for state in ("x" * 43, "short", 7, None):
            self.assertEqual(self._post("oauth-flow/consume/", {"state": state}).status_code, 404)

    def test_foreign_org_and_bad_bodies_are_uniform_404(self):
        foreign = self._post("oauth-flow/", self._flow(), org="org-b")
        missing = self._post("oauth-flow/", self._flow(server_slug="nope"))
        self.assertEqual((foreign.status_code, missing.status_code), (404, 404))
        self.assertEqual(foreign.content, missing.content)
        for over in ({"ttl_s": True}, {"state": "short"}, {"code_verifier": "v"}, {"key_id": ""}):
            self.assertEqual(self._post("oauth-flow/", self._flow(**over)).status_code, 404)

    def test_status_has_no_secret_and_tokens_write_bumps_versions(self):
        self._post("oauth-flow/", self._flow())
        status_before = self.client.get(BASE + "oauth-status/", {"server_slug": "s"},
                                        HTTP_X_ORG_SLUG="org-a", **INTERNAL).json()
        self.assertFalse(status_before["authorized"])
        key = scan_version_key("org-a", "s")
        before = int(self.redis.get(key) or 0)
        with self.captureOnCommitCallbacks(execute=True):
            resp = self._post("credential/oauth-tokens/", {
                "server_slug": "s", "access_token": "access-tok-1234",
                "refresh_token": "ref-1234", "expires_in": 3600,
            })
        self.assertEqual(resp.status_code, 200)
        self.server.refresh_from_db()
        self.assertEqual((self.server.auth_type, self.server.auth_token), ("oauth", "access-tok-1234"))
        self.assertEqual(self.server.credential_version, 2)
        self.assertGreater(int(self.redis.get(key) or 0), before)
        after = self.client.get(BASE + "oauth-status/", {"server_slug": "s"},
                                HTTP_X_ORG_SLUG="org-a", **INTERNAL)
        payload = after.content.decode()
        for secret in ("access-tok-1234", "ref-1234", SECRET, VERIFIER):
            self.assertNotIn(secret, payload)
        body = after.json()
        self.assertTrue(body["authorized"] and body["refreshable"])
        self.assertIsNotNone(body["obtained_at"])
        self.assertEqual(after["Cache-Control"], "no-store")

    def test_tokens_write_is_org_scoped(self):
        resp = self._post("credential/oauth-tokens/", {"server_slug": "s", "access_token": "a"},
                          org="org-b")
        self.assertEqual(resp.status_code, 404)
