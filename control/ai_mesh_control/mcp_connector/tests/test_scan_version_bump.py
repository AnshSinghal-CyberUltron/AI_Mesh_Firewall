"""M-15: Redis scan-version bump on MCP tool/scan config changes.

The gateway invalidates its per-(org, server) enabled-tools cache by watching
``mcp:scan_ver:{org_slug}`` and ``mcp:scan_ver:{org_slug}:{server_slug}``
version keys in Redis. These tests assert that control bumps the right key
whenever an operator changes gateway-visible tool/scan configuration, and
that pure bookkeeping saves do NOT bump (so health pings don't churn caches).

Redis is faked via ``fakeredis`` by patching ``mcp_connector.signals
._get_redis_client``; ``captureOnCommitCallbacks(execute=True)`` flushes the
``transaction.on_commit`` deferral that the signal handlers use.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import fakeredis
import redis as redis_lib
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from mcp_connector.models import (
    MCPScanControl,
    MCPServerRegistration,
    MCPToolRegistration,
)
from mcp_connector.signals import scan_version_key

User = get_user_model()


class ScanVersionBumpTestCase(TestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org = Organization.objects.create(name="Mesh Org", slug="mesh-org")
        self.user = User.objects.create_user(username="mesh_op", password="pass")
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])

        self.fake_redis = fakeredis.FakeRedis(decode_responses=True)
        patcher = patch(
            "mcp_connector.signals._get_redis_client",
            return_value=self.fake_redis,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        # Server + tool creation in setUp also fires the signals; that churn
        # is flushed/ignored — each test reads the version BEFORE acting.
        with self.captureOnCommitCallbacks(execute=True):
            self.server = MCPServerRegistration.objects.create(
                name="Files Server",
                server_slug="files-server",
                url="https://mcp.example.com/mcp",
                organization=self.org,
            )
            self.tool = MCPToolRegistration.objects.create(
                server=self.server,
                tool_name="delete_file",
                organization=self.org,
                enabled=True,
            )

        self.server_key = scan_version_key("mesh-org", "files-server")
        self.org_key = scan_version_key("mesh-org")

    def _ver(self, key: str) -> int:
        return int(self.fake_redis.get(key) or 0)

    # ── Tool enable/disable ──────────────────────────────────────────

    def test_tool_disable_via_api_bumps_server_version(self):
        before = self._ver(self.server_key)
        client = APIClient()
        client.force_authenticate(user=self.user)
        with self.captureOnCommitCallbacks(execute=True):
            resp = client.patch(
                f"/api/mcp-connector/servers/{self.server.pk}/tools/delete_file/",
                {"enabled": False},
                format="json",
            )
        self.assertEqual(resp.status_code, 200)
        self.tool.refresh_from_db()
        self.assertFalse(self.tool.enabled)
        self.assertGreater(self._ver(self.server_key), before)

    def test_tool_scan_action_change_bumps_server_version(self):
        before = self._ver(self.server_key)
        client = APIClient()
        client.force_authenticate(user=self.user)
        with self.captureOnCommitCallbacks(execute=True):
            resp = client.patch(
                f"/api/mcp-connector/servers/{self.server.pk}/tools/delete_file/",
                {"scan_action": "block"},
                format="json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(self._ver(self.server_key), before)

    def test_tool_model_save_bumps_server_version(self):
        before = self._ver(self.server_key)
        with self.captureOnCommitCallbacks(execute=True):
            self.tool.enabled = False
            self.tool.save()
        self.assertEqual(self._ver(self.server_key), before + 1)

    def test_tool_delete_bumps_server_version(self):
        before = self._ver(self.server_key)
        with self.captureOnCommitCallbacks(execute=True):
            self.tool.delete()
        self.assertGreater(self._ver(self.server_key), before)

    # ── Server-level scan config ─────────────────────────────────────

    def test_server_default_scan_action_bumps_server_version(self):
        before = self._ver(self.server_key)
        with self.captureOnCommitCallbacks(execute=True):
            self.server.default_scan_action = "redact"
            self.server.save(update_fields=["default_scan_action", "updated_at"])
        self.assertEqual(self._ver(self.server_key), before + 1)

    def test_server_bookkeeping_save_does_not_bump(self):
        """Health/sync bookkeeping saves must not churn gateway caches."""
        before = self._ver(self.server_key)
        with self.captureOnCommitCallbacks(execute=True):
            self.server.connection_status = "connected"
            self.server.tools_count = 1
            self.server.save(
                update_fields=["connection_status", "tools_count", "updated_at"]
            )
        self.assertEqual(self._ver(self.server_key), before)

    def test_server_delete_bumps_server_version(self):
        before = self._ver(self.server_key)
        with self.captureOnCommitCallbacks(execute=True):
            self.server.delete()
        self.assertGreater(self._ver(self.server_key), before)

    # ── Scan controls (org- and server-scoped) ───────────────────────

    def test_org_scoped_scan_control_bumps_org_version_only(self):
        org_before = self._ver(self.org_key)
        srv_before = self._ver(self.server_key)
        with self.captureOnCommitCallbacks(execute=True):
            MCPScanControl.objects.create(
                organization=self.org,
                tier="tier1",
                direction="input",
                scope_type="org",
            )
        self.assertEqual(self._ver(self.org_key), org_before + 1)
        self.assertEqual(self._ver(self.server_key), srv_before)

    def test_server_scoped_scan_control_bumps_server_version(self):
        before = self._ver(self.server_key)
        with self.captureOnCommitCallbacks(execute=True):
            MCPScanControl.objects.create(
                organization=self.org,
                server=self.server,
                tier="tier2",
                direction="output",
                scope_type="server",
            )
        self.assertEqual(self._ver(self.server_key), before + 1)

    def test_scan_control_delete_bumps(self):
        with self.captureOnCommitCallbacks(execute=True):
            control = MCPScanControl.objects.create(
                organization=self.org,
                server=self.server,
                tier="tier1",
                direction="both",
                scope_type="server",
            )
        before = self._ver(self.server_key)
        with self.captureOnCommitCallbacks(execute=True):
            control.delete()
        self.assertEqual(self._ver(self.server_key), before + 1)

    def test_scan_control_patch_via_api_bumps(self):
        with self.captureOnCommitCallbacks(execute=True):
            control = MCPScanControl.objects.create(
                organization=self.org,
                server=self.server,
                tier="tier1",
                direction="input",
                scope_type="server",
            )
        before = self._ver(self.server_key)
        client = APIClient()
        client.force_authenticate(user=self.user)
        with self.captureOnCommitCallbacks(execute=True):
            resp = client.patch(
                f"/api/mcp-connector/scan-controls/{control.pk}/",
                {"enabled": False},
                format="json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(self._ver(self.server_key), before)

    # ── Failure handling ─────────────────────────────────────────────

    def test_redis_failure_never_blocks_save(self):
        """A Redis outage degrades to TTL-only gateway caching, not a 500."""
        broken = MagicMock()
        broken.incr.side_effect = redis_lib.RedisError("redis down")
        with patch(
            "mcp_connector.signals._get_redis_client", return_value=broken
        ):
            with self.captureOnCommitCallbacks(execute=True):
                self.tool.enabled = False
                self.tool.save()
        self.tool.refresh_from_db()
        self.assertFalse(self.tool.enabled)
        broken.incr.assert_called_once()

    def test_no_org_means_no_bump(self):
        """Servers without an organization cannot map to a gateway key."""
        with self.captureOnCommitCallbacks(execute=True):
            orphan = MCPServerRegistration.objects.create(
                name="Orphan", server_slug="orphan", url="https://x.example/mcp"
            )
            MCPToolRegistration.objects.create(server=orphan, tool_name="t1")
        self.assertEqual(self._ver(scan_version_key("", "orphan")), 0)
        self.assertFalse(
            [k for k in self.fake_redis.keys("mcp:scan_ver:*") if ":orphan" in k]
        )
