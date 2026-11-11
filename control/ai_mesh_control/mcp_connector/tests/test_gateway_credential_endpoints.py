"""GW17b: the gateway-internal credential endpoints (internal key only, org-scoped, CAS write-back).

Credentials are returned ONLY by these endpoints; the console list/detail serializers never carry
a secret. Every credential write bumps ``mcp:scan_ver`` so the gateway's cache drops the entry.
"""
from datetime import timedelta
from unittest.mock import patch

import fakeredis
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from mcp_connector.models import MCPServerRegistration
from mcp_connector.signals import scan_version_key

INTERNAL = {"HTTP_X_GATEWAY_AUTH": "true", "HTTP_X_GATEWAY_INTERNAL_KEY": "internal-test-key"}
URL = "/api/mcp-connector/internal/credential/"
WRITE = "/api/mcp-connector/internal/credential/refresh-result/"
SECRET = "tok-LIVE-9f8e7d6c5b4a"


@override_settings(GATEWAY_INTERNAL_API_KEY="internal-test-key")
class CredentialEndpointTests(TestCase):
    def setUp(self):
        from auth.models import Organization

        self.org = Organization.objects.create(name="Org A", slug="org-a")
        self.other = Organization.objects.create(name="Org B", slug="org-b")
        self.redis = fakeredis.FakeRedis(decode_responses=True)
        patcher = patch("mcp_connector.signals._get_redis_client", return_value=self.redis)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = APIClient()

    def _server(self, **fields):
        base = {"name": "S", "server_slug": "s", "url": "https://mcp.example.com/mcp",
                "organization": self.org, "auth_type": "bearer", "auth_token": SECRET}
        base.update(fields)
        with self.captureOnCommitCallbacks(execute=True):
            return MCPServerRegistration.objects.create(**base)

    def _get(self, org="org-a", slug="s", **query):
        return self.client.get(URL, {"server_slug": slug, **query}, HTTP_X_ORG_SLUG=org, **INTERNAL)

    def test_bearer_credential_and_no_store(self):
        self._server()
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Cache-Control"], "no-store")
        self.assertEqual(resp.json()["token"], SECRET)
        self.assertEqual(resp.json()["auth_type"], "bearer")

    def test_authheaders_maps_to_header_and_basic_shape(self):
        self._server(server_slug="h", name="H", auth_type="authheaders",
                     auth_header_key="X-Api-Key", auth_header_value=SECRET)
        self._server(server_slug="b", name="B", auth_type="basic",
                     auth_username="user", auth_password=SECRET)
        header = self._get(slug="h").json()
        self.assertEqual((header["auth_type"], header["header_name"]), ("header", "X-Api-Key"))
        basic = self._get(slug="b").json()
        self.assertEqual((basic["username"], basic["token"]), ("user", SECRET))

    def test_refresh_material_only_when_asked(self):
        self._server(
            auth_type="oauth", oauth_refresh_token="refresh-123456",
            oauth_token_endpoint="https://idp.example.com/token",
            oauth_token_expires_at=timezone.now() + timedelta(hours=1),
        )
        plain = self._get().json()
        self.assertNotIn("refresh_token", plain)
        self.assertTrue(plain["refreshable"])
        asked = self._get(need_refresh="1").json()
        self.assertEqual(asked["refresh_token"], "refresh-123456")

    def test_requires_internal_key_and_is_not_open_to_console_users(self):
        self._server()
        # No credentials at all: DRF answers 401 (unauthenticated); either way it is refused.
        self.assertIn(self.client.get(URL, {"server_slug": "s"}).status_code, (401, 403))
        bad = dict(INTERNAL, HTTP_X_GATEWAY_INTERNAL_KEY="wrong")
        resp = self.client.get(URL, {"server_slug": "s"}, HTTP_X_ORG_SLUG="org-a", **bad)
        self.assertIn(resp.status_code, (401, 403))
        self.assertNotIn(SECRET, resp.content.decode())

    def test_404_is_uniform_for_missing_and_foreign_servers(self):
        self._server()
        missing = self._get(slug="nope")
        foreign = self._get(org="org-b")
        self.assertEqual((missing.status_code, foreign.status_code), (404, 404))
        self.assertEqual(missing.content, foreign.content)
        self.assertNotIn(SECRET, foreign.content.decode())

    def test_auth_none_and_query_param_have_no_credential(self):
        self._server(auth_type="none", auth_token="")
        self.assertEqual(self._get().status_code, 404)

    def test_write_back_is_compare_and_set_and_bumps_scan_version(self):
        server = self._server(
            auth_type="oauth", oauth_refresh_token="refresh-123456",
            oauth_token_endpoint="https://idp.example.com/token", needs_reauth=True,
        )
        key = scan_version_key("org-a", "s")
        before = int(self.redis.get(key) or 0)
        body = {"server_slug": "s", "version": server.credential_version,
                "access_token": "new-access-token", "expires_in": 3600, "refresh_token": "rot-9"}
        with self.captureOnCommitCallbacks(execute=True):
            ok = self.client.post(WRITE, body, format="json", HTTP_X_ORG_SLUG="org-a", **INTERNAL)
        self.assertEqual(ok.status_code, 200)
        server.refresh_from_db()
        self.assertEqual(server.auth_token, "new-access-token")
        self.assertEqual(server.oauth_refresh_token, "rot-9")
        self.assertFalse(server.needs_reauth)
        self.assertEqual(server.credential_version, 2)
        self.assertGreater(int(self.redis.get(key)), before)
        stale = self.client.post(WRITE, body, format="json", HTTP_X_ORG_SLUG="org-a", **INTERNAL)
        self.assertEqual(stale.status_code, 409)  # the same (now stale) version loses
        server.refresh_from_db()
        self.assertEqual(server.auth_token, "new-access-token")

    def test_write_back_rejects_malformed_and_foreign(self):
        server = self._server(auth_type="oauth")
        bad = {"server_slug": "s", "version": True, "access_token": "x", "expires_in": 5}
        self.assertEqual(
            self.client.post(WRITE, bad, format="json", HTTP_X_ORG_SLUG="org-a", **INTERNAL).status_code, 404
        )
        good = {"server_slug": "s", "version": server.credential_version,
                "access_token": "tok", "expires_in": 5}
        self.assertEqual(
            self.client.post(WRITE, good, format="json", HTTP_X_ORG_SLUG="org-b", **INTERNAL).status_code, 404
        )

    def test_credential_field_writes_bump_the_scan_version(self):
        server = self._server()
        key = scan_version_key("org-a", "s")
        for field, value in (("auth_token", "another-token-1"), ("needs_reauth", True)):
            before = int(self.redis.get(key) or 0)
            setattr(server, field, value)
            with self.captureOnCommitCallbacks(execute=True):
                server.save(update_fields=[field])
            self.assertGreater(int(self.redis.get(key) or 0), before, field)

    def test_console_serializers_never_carry_a_credential(self):
        from mcp_connector import serializers

        self._server()
        names = [n for n in dir(serializers) if n.endswith("Serializer")]
        rendered = ""
        for name in names:
            cls = getattr(serializers, name)
            meta = getattr(cls, "Meta", None)
            if getattr(meta, "model", None) is MCPServerRegistration:
                rendered += repr(cls(MCPServerRegistration.objects.first()).data)
        self.assertIn("server_slug", rendered)  # the check really rendered the console shapes
        self.assertNotIn(SECRET, rendered)
