"""Hybrid MCPEvent + EnforcementEvent reads and on-demand projection for Module 2."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from auth.models import Organization, UserProfile
from mcp_connector.models import MCPEvent
from module2.analytics import (
    build_hybrid_mcp_activity_payload,
    build_lane_summary,
)
from module2.ondemand_refresh import maybe_ondemand_mcp_projection
from module2.views import McpRiskView
from policy.models import EnforcementEvent

User = get_user_model()


class HybridMcpRiskTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name="Hybrid MCP Org", slug="hybrid-mcp")
        self.user = User.objects.create_user("hybrid-mcp-user", password="x")
        UserProfile.objects.create(user=self.user, organization=self.org)
        self.factory = APIRequestFactory()

    def test_mcp_event_only_appears_on_risk_before_projection(self):
        MCPEvent.objects.create(
            organization=self.org,
            server_slug="echo",
            tool_name="echo",
            decision="block",
            request_id="req-hybrid-mcp-1",
            metadata={},
        )
        self.assertEqual(
            EnforcementEvent.objects.filter(organization=self.org, metadata__source="mcp_scan").count(),
            0,
        )

        payload = build_hybrid_mcp_activity_payload(
            EnforcementEvent.objects.filter(organization=self.org),
            MCPEvent.objects.filter(organization=self.org),
        )
        # Primary KPIs match Module 1 (EF only) — MCPEvent-only is labeled extra.
        self.assertEqual(payload["summary"]["total_events"], 0)
        self.assertEqual(payload["module1_aligned"]["summary"]["total_events"], 0)
        self.assertEqual(payload["module2_extra"]["summary"]["total_events"], 1)
        self.assertEqual(payload["module2_extra"]["summary"]["blocked"], 1)
        self.assertEqual(payload["hybrid"]["summary"]["total_events"], 1)
        self.assertEqual(payload["hybrid"]["tool_ledger"][0]["tool"], "echo")

    def test_no_double_count_when_ef_and_mcp_event_share_request_id(self):
        MCPEvent.objects.create(
            organization=self.org,
            server_slug="echo",
            tool_name="echo",
            decision="block",
            request_id="req-hybrid-mcp-2",
            metadata={},
        )
        EnforcementEvent.objects.create(
            organization=self.org,
            action="block",
            metadata={
                "source": "mcp_scan",
                "event_type": "mcp_tool_call",
                "request_id": "req-hybrid-mcp-2",
                "tools_invoked": ["echo"],
                "server_slug": "echo",
            },
            created_at=timezone.now(),
        )
        payload = build_hybrid_mcp_activity_payload(
            EnforcementEvent.objects.filter(organization=self.org),
            MCPEvent.objects.filter(organization=self.org),
        )
        self.assertEqual(payload["summary"]["total_events"], 1)
        self.assertEqual(payload["summary"]["blocked_tool_calls"], 1)
        self.assertEqual(payload["module2_extra"]["summary"]["total_events"], 0)
        self.assertEqual(payload["module1_aligned"]["summary"]["total_events"], 1)

    def test_dashboard_lane_includes_mcp_event_only(self):
        MCPEvent.objects.create(
            organization=self.org,
            server_slug="echo",
            tool_name="sum",
            decision="allow",
            request_id="req-hybrid-mcp-3",
            metadata={},
        )
        lanes = build_lane_summary(
            EnforcementEvent.objects.filter(organization=self.org),
            mcp_events=MCPEvent.objects.filter(organization=self.org),
        )
        self.assertEqual(lanes["mcp"]["total"], 1)
        self.assertEqual(lanes["mcp"]["blocked"], 0)

    def test_ondemand_projection_rate_limited(self):
        MCPEvent.objects.create(
            organization=self.org,
            server_slug="echo",
            tool_name="echo",
            decision="redact",
            request_id="req-hybrid-mcp-4",
            metadata={},
        )
        first = maybe_ondemand_mcp_projection(self.org.id)
        self.assertFalse(first.get("skipped"))
        self.assertEqual(first.get("created"), 1)
        second = maybe_ondemand_mcp_projection(self.org.id)
        self.assertTrue(second.get("skipped"))
        self.assertEqual(second.get("reason"), "rate_limited")
        self.assertEqual(
            EnforcementEvent.objects.filter(
                organization=self.org,
                metadata__request_id="req-hybrid-mcp-4",
            ).count(),
            1,
        )

    def test_mcp_risk_api_returns_mcp_event_only(self):
        MCPEvent.objects.create(
            organization=self.org,
            server_slug="echo",
            tool_name="echo",
            decision="block",
            request_id="req-hybrid-mcp-api",
            metadata={},
        )
        req = self.factory.get("/api/module2/mcp/risk/?period=24h")
        force_authenticate(req, user=self.user)
        resp = McpRiskView.as_view()(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["summary"]["total_events"], 1)
        self.assertEqual(resp.data["summary"]["blocked_tool_calls"], 1)
