"""Tests for Module-2 MCPEvent → EnforcementEvent repair projection."""

from django.test import TestCase
from django.utils import timezone

from auth.models import Organization
from mcp_connector.models import MCPEvent
from module2.mcp_enforcement_projection import repair_mcp_enforcement_projection
from policy.models import EnforcementEvent


class McpEnforcementProjectionTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="M2 MCP Proj", slug="m2-mcp-proj")

    def test_creates_enforcement_event_for_unmirrored_mcp_event(self):
        ev = MCPEvent.objects.create(
            organization=self.org,
            username="alice",
            server_slug="echo",
            server_name="Echo",
            tool_name="echo",
            decision="block",
            policy_reason="tool_disabled",
            request_id="req-m2-mcp-1",
            latency_ms=12,
            metadata={},
        )
        stats = repair_mcp_enforcement_projection(lookback_hours=24, batch_size=50, org_id=self.org.id)
        self.assertEqual(stats["created"], 1)
        self.assertEqual(stats["skipped"], 0)

        mirrored = EnforcementEvent.objects.filter(
            organization=self.org,
            metadata__source="mcp_scan",
            metadata__request_id="req-m2-mcp-1",
        ).get()
        self.assertEqual(mirrored.action, "block")
        self.assertEqual(mirrored.metadata.get("tool_name"), "echo")
        self.assertTrue(mirrored.metadata.get("module2_mcp_projection"))
        self.assertEqual(mirrored.metadata.get("mcp_event_id"), str(ev.id))

    def test_idempotent_skips_existing_request_id(self):
        MCPEvent.objects.create(
            organization=self.org,
            server_slug="echo",
            tool_name="echo",
            decision="redact",
            request_id="req-m2-mcp-2",
            metadata={},
        )
        EnforcementEvent.objects.create(
            organization=self.org,
            action="redact",
            metadata={
                "source": "mcp_scan",
                "request_id": "req-m2-mcp-2",
                "tool_name": "echo",
            },
            created_at=timezone.now(),
        )
        stats = repair_mcp_enforcement_projection(lookback_hours=24, batch_size=50, org_id=self.org.id)
        self.assertEqual(stats["created"], 0)
        self.assertGreaterEqual(stats["skipped"], 1)
        self.assertEqual(
            EnforcementEvent.objects.filter(
                organization=self.org,
                metadata__request_id="req-m2-mcp-2",
            ).count(),
            1,
        )

    def test_second_run_is_idempotent_via_projection_marker(self):
        MCPEvent.objects.create(
            organization=self.org,
            server_slug="echo",
            tool_name="sum",
            decision="monitor",
            request_id="req-m2-mcp-3",
            metadata={},
        )
        first = repair_mcp_enforcement_projection(lookback_hours=24, batch_size=50, org_id=self.org.id)
        second = repair_mcp_enforcement_projection(lookback_hours=24, batch_size=50, org_id=self.org.id)
        self.assertEqual(first["created"], 1)
        self.assertEqual(second["created"], 0)
        self.assertEqual(
            EnforcementEvent.objects.filter(
                organization=self.org,
                metadata__source="mcp_scan",
                metadata__request_id="req-m2-mcp-3",
            ).count(),
            1,
        )
