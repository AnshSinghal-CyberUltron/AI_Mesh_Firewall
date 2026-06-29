"""SOC KPI period window tests (including 6h support)."""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from policy.constants import ACTION_BLOCK
from policy.models import EnforcementEvent

User = get_user_model()


class SocKpisPeriodTests(TestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org = Organization.objects.create(name="Period Org", slug="period-org")
        self.user = User.objects.create_user(username="period_user", password="pass")
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _create_event(self, hours_ago: float, action: str = "block"):
        ev = EnforcementEvent.objects.create(
            organization=self.org,
            action=action,
            metadata={"source": "security_scan"},
        )
        EnforcementEvent.objects.filter(pk=ev.pk).update(
            created_at=timezone.now() - timedelta(hours=hours_ago)
        )
        return ev

    def test_soc_kpis_6h_period_returns_200(self):
        resp = self.client.get("/api/security/soc-kpis/?period=6h")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("period"), "6h")

    def test_soc_kpis_6h_excludes_events_older_than_six_hours(self):
        self._create_event(hours_ago=2)
        self._create_event(hours_ago=8)

        resp = self.client.get("/api/security/soc-kpis/?period=6h")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("total_threats"), 1)

    def test_module_kpis_6h_matches_soc_kpis_total_for_gateway_module(self):
        self._create_event(hours_ago=1, action=ACTION_BLOCK)
        self._create_event(hours_ago=2, action="allow")
        self._create_event(hours_ago=10, action=ACTION_BLOCK)

        soc = self.client.get("/api/security/soc-kpis/?period=6h").json()
        module = self.client.get("/api/security/module-kpis/?period=6h").json()

        self.assertEqual(soc["total_threats"], 2)
        self.assertEqual(module["modules"]["1.1"]["total"], 2)
        self.assertEqual(module["modules"]["1.1"]["blocked"], soc["blocked"])

    def _create_event_rid(self, hours_ago: float, action: str, event_type: str, request_id: str):
        ev = EnforcementEvent.objects.create(
            organization=self.org,
            action=action,
            metadata={"source": "gateway", "event_type": event_type, "request_id": request_id},
        )
        EnforcementEvent.objects.filter(pk=ev.pk).update(
            created_at=timezone.now() - timedelta(hours=hours_ago)
        )
        return ev

    def test_requests_inspected_is_distinct_per_request_not_per_row(self):
        # Request A: completed+routed → request(allow) + model_routed(reroute) — 2
        # rows, ONE request, allowed.
        self._create_event_rid(1, "allow", "request", "zs-aaaaaaaaaaaa")
        self._create_event_rid(1, "reroute", "model_routed", "zs-aaaaaaaaaaaa")
        # Request B: model unavailable → model_routed(confirm) + request(block)
        # marker — 2 rows, ONE request, blocked (NOT allowed).
        self._create_event_rid(1, "confirm", "model_routed", "zs-bbbbbbbbbbbb")
        self._create_event_rid(1, "block", "request", "zs-bbbbbbbbbbbb")
        # Request C: blocked at input → input_blocked(block) only — 1 row, blocked.
        self._create_event_rid(1, "block", "input_blocked", "zs-cccccccccccc")

        soc = self.client.get("/api/security/soc-kpis/?period=6h").json()
        # 5 raw rows, but 3 distinct requests.
        self.assertEqual(soc["total_threats"], 5)
        self.assertEqual(soc["requests_inspected"], 3)
        self.assertEqual(soc["requests_allowed"], 1)
        self.assertEqual(soc["requests_blocked"], 2)
        self.assertEqual(soc["requests_redacted"], 0)
        # Clean partition.
        self.assertEqual(
            soc["requests_inspected"],
            soc["requests_allowed"] + soc["requests_blocked"] + soc["requests_redacted"],
        )
