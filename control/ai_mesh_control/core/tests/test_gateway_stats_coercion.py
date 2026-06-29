"""M-24: GatewayStatsListView must not 500 on garbage Agent.metadata values.

Agent.metadata is agent-supplied JSON; counters can arrive as strings or
arbitrary junk. The view previously did unguarded int() casts on
total_requests/blocked/allowed/avg_latency_ms, so a single bad telemetry
payload poisoned the whole stats dashboard with a 500.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.gateway_views import _safe_int

User = get_user_model()


class SafeIntHelperTests(TestCase):
    """Unit tests for the _safe_int coercion helper."""

    def test_numeric_passthrough(self):
        self.assertEqual(_safe_int(1542), 1542)
        self.assertEqual(_safe_int(12.9), 12)
        self.assertEqual(_safe_int(True), 1)

    def test_numeric_strings(self):
        self.assertEqual(_safe_int("1542"), 1542)
        self.assertEqual(_safe_int("12.5"), 12)

    def test_garbage_returns_default(self):
        self.assertEqual(_safe_int("abc"), 0)
        self.assertEqual(_safe_int(None), 0)
        self.assertEqual(_safe_int({"a": 1}), 0)
        self.assertEqual(_safe_int([1, 2]), 0)
        self.assertEqual(_safe_int(""), 0)

    def test_non_finite_returns_default(self):
        self.assertEqual(_safe_int("inf"), 0)
        self.assertEqual(_safe_int("-inf"), 0)
        self.assertEqual(_safe_int("nan"), 0)
        self.assertEqual(_safe_int(float("inf")), 0)

    def test_custom_default(self):
        self.assertIsNone(_safe_int("abc", None))
        self.assertIsNone(_safe_int(None, None))
        self.assertEqual(_safe_int("junk", -1), -1)


class GatewayStatsCoercionTests(TestCase):
    """End-to-end: garbage metadata degrades to defaults instead of 500."""

    def setUp(self):
        from auth.models import Organization, UserProfile
        from core.models import Agent, Endpoint

        self.org = Organization.objects.create(name="Stats Org", slug="stats-org")
        self.user = User.objects.create_user(username="stats_user", password="pw")
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])

        self.endpoint = Endpoint.objects.create(
            name="gw-endpoint",
            identifier="gw-endpoint-1",
            organization=self.org,
        )
        self.agent = Agent.objects.create(
            agent_type="gateway",
            name="Gateway Proxy",
            endpoint=self.endpoint,
            metadata={
                "total_requests": "1542",  # numeric string -> 1542
                "blocked": "abc",  # garbage -> 0
                "allowed": None,  # -> 0
                "avg_latency_ms": "12.5",  # numeric string -> "12ms"
                "rules_applied": {"oops": True},  # dict -> 0
                "active_connections": "7",  # numeric string -> 7
                "location": "us-east-1",
            },
        )

    def _get_stats(self):
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(user=self.user)
        return client.get("/api/gateways/stats/")

    def test_garbage_metadata_does_not_500(self):
        resp = self._get_stats()
        self.assertEqual(resp.status_code, 200)
        rows = resp.json()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["totalRequests"], 1542)
        self.assertEqual(row["blocked"], 0)
        self.assertEqual(row["allowed"], 0)
        self.assertEqual(row["blockRate"], 0.0)
        self.assertEqual(row["avgLatency"], "12ms")
        self.assertEqual(row["rulesApplied"], 0)
        self.assertEqual(row["activeConn"], 7)

    def test_missing_latency_renders_dash(self):
        self.agent.metadata = {"total_requests": 10, "blocked": 2, "allowed": 8}
        self.agent.save(update_fields=["metadata"])
        resp = self._get_stats()
        self.assertEqual(resp.status_code, 200)
        row = resp.json()[0]
        self.assertEqual(row["avgLatency"], "—")
        self.assertEqual(row["blockRate"], 20.0)

    def test_garbage_latency_renders_dash_not_500(self):
        self.agent.metadata = {"avg_latency_ms": "fast"}
        self.agent.save(update_fields=["metadata"])
        resp = self._get_stats()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()[0]["avgLatency"], "—")
