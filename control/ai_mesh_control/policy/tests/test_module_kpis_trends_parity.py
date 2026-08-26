"""Parity tests: module-kpis totals and pressure align with module-trends buckets."""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from policy.constants import ACTION_BLOCK, ACTION_REDACT
from policy.firewall_module_classifier import MODULE_PRESSURE_METRIC
from policy.models import EnforcementEvent
from policy.tests.analytics_api_testcase import AnalyticsAPITestCase

User = get_user_model()


FIXTURE_EVENTS = [
    {"action": ACTION_BLOCK, "metadata": {"event_type": "rag_pipeline", "source": "rag"}},
    {"action": ACTION_BLOCK, "metadata": {"event_type": "vector_query", "source": "vector"}},
    {"action": ACTION_REDACT, "metadata": {"source": "mcp_scan", "event_type": "tool_call"}},
    {
        "action": ACTION_BLOCK,
        "metadata": {
            "event_type": "kill_switch",
            "source": "policy",
            "module_id": "1.6",
            "security_risk_score": 85,
        },
    },
    {"action": ACTION_BLOCK, "metadata": {"event_type": "output_guard", "source": "output"}},
]


class ModuleKpisTrendsParityTests(AnalyticsAPITestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org = Organization.objects.create(name="Parity Org", slug="parity-org")
        self.user = User.objects.create_user(username="parity_user", password="pass")
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])

        since = timezone.now() - timedelta(minutes=30)
        for payload in FIXTURE_EVENTS:
            ev = EnforcementEvent.objects.create(
                organization=self.org,
                action=payload["action"],
                metadata=payload["metadata"],
            )
            EnforcementEvent.objects.filter(pk=ev.pk).update(created_at=since)

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_kpi_totals_equal_sum_of_trend_bucket_totals(self):
        kpi_resp = self.client.get("/api/security/module-kpis/?period=24h")
        self.assertEqual(kpi_resp.status_code, 200)
        kpis = kpi_resp.json().get("modules", {})

        trend_resp = self.client.get("/api/security/module-trends/?period=24h")
        self.assertEqual(trend_resp.status_code, 200)
        trends = trend_resp.json()

        for mid in ("1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7"):
            series = trends.get(mid, [])
            trend_total = sum(pt.get("total", 0) for pt in series)
            self.assertEqual(
                kpis.get(mid, {}).get("total", 0),
                trend_total,
                msg=f"module {mid} KPI total != trend bucket sum",
            )

    def test_pressure_sums_match_kpi_numerator(self):
        kpi_resp = self.client.get("/api/security/module-kpis/?period=24h")
        kpis = kpi_resp.json().get("modules", {})
        trend_resp = self.client.get("/api/security/module-trends/?period=24h")
        trends = trend_resp.json()

        for mid, metric in MODULE_PRESSURE_METRIC.items():
            series = trends.get(mid, [])
            pressure_sum = sum(pt.get("pressure", pt.get("value", 0)) for pt in series)
            kpi_value = kpis.get(mid, {}).get(metric, 0)
            self.assertEqual(
                kpi_value,
                pressure_sum,
                msg=f"module {mid} KPI {metric} != sum of trend pressure",
            )

    def test_rag_pipeline_increments_1_3_kpi(self):
        kpi_resp = self.client.get("/api/security/module-kpis/?period=24h")
        modules = kpi_resp.json().get("modules", {})
        self.assertGreaterEqual(modules.get("1.3", {}).get("total", 0), 2)
