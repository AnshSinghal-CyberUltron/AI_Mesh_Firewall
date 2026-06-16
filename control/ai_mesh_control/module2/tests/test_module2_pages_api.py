"""API tests for Module 2 model exposure, threat telemetry, and incidents endpoints."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from policy.constants import ACTION_BLOCK, ACTION_REDACT
from policy.models import EnforcementEvent, SecurityIncident

User = get_user_model()


class Module2PagesApiTests(TestCase):
    def setUp(self):
        from auth.models import Organization, UserProfile

        self.org = Organization.objects.create(name="Test Org", slug="test-org")
        self.other_org = Organization.objects.create(name="Other Org", slug="other-org")
        self.user = User.objects.create_user(username="m2user", password="pass-m2")
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.organization = self.org
        profile.save(update_fields=["organization"])

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _event(self, org, action, **meta):
        return EnforcementEvent.objects.create(
            organization=org,
            action=action,
            metadata=meta,
        )

    def _incident(self, org, title, severity="high", status="open", **meta):
        ev = self._event(org, ACTION_BLOCK, **meta)
        return SecurityIncident.objects.create(
            organization=org,
            enforcement_event=ev,
            title=title,
            severity=severity,
            status=status,
        )

    def test_model_exposure_returns_summary_and_chart_data(self):
        since = timezone.now() - timedelta(hours=1)
        ev = self._event(
            self.org,
            ACTION_BLOCK,
            model="gpt-4o",
            key_prefix="zs_test",
            latency_ms=150,
            threat_type="prompt_injection",
        )
        EnforcementEvent.objects.filter(pk=ev.pk).update(created_at=since)
        self._event(self.org, ACTION_REDACT, model="gpt-4o", key_prefix="zs_test", threat_type="pii_ssn")

        resp = self.client.get("/api/module2/models/exposure/?period=24h")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("summary", data)
        self.assertIn("exposure_by_model", data)
        self.assertIn("models", data)
        self.assertGreaterEqual(data["summary"]["active_models"], 1)
        self.assertGreaterEqual(data["summary"]["total_requests"], 2)
        self.assertEqual(data["models"][0]["model"], "gpt-4o")
        self.assertIn("exposure_score", data["models"][0])

    def test_threat_intel_telemetry_returns_timeline_and_vectors(self):
        since = timezone.now() - timedelta(hours=1)
        ev1 = self._event(
            self.org,
            ACTION_BLOCK,
            threat_type="prompt_injection",
            owasp_code="LLM01",
        )
        ev2 = self._event(
            self.org,
            ACTION_REDACT,
            threat_type="pii_ssn",
            category="pii",
            owasp_code="LLM06",
        )
        EnforcementEvent.objects.filter(pk__in=[ev1.pk, ev2.pk]).update(created_at=since)

        resp = self.client.get("/api/module2/threat-intel/telemetry/?period=24h")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("summary", data)
        self.assertIn("timeline", data)
        self.assertIn("top_attack_vectors", data)
        self.assertGreaterEqual(data["summary"]["total_events"], 2)
        self.assertTrue(data["timeline"])
        vectors = {row["vector"] for row in data["top_attack_vectors"]}
        self.assertTrue("LLM01" in vectors or "LLM06" in vectors)

    def test_incidents_list_is_paginated_and_filterable(self):
        self._incident(self.org, "UEBA key abuse", severity="high", status="open", key_prefix="zs_ab", model="gpt-4o")
        self._incident(
            self.org,
            "Threat intel hit",
            severity="critical",
            status="investigating",
            source="threat_intel",
            threat_type="threat_intel_injection",
        )
        self._incident(self.other_org, "Hidden incident", severity="low", status="open")

        resp = self.client.get("/api/module2/incidents/?page=1&page_size=10&severity=high")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["page"], 1)
        self.assertEqual(data["page_size"], 10)
        self.assertIn("total_pages", data)
        self.assertIn("summary", data)
        self.assertIn("by_source", data["summary"])
        self.assertGreaterEqual(data["count"], 1)
        self.assertTrue(all(row["severity"] == "high" for row in data["results"]))

        status_resp = self.client.get("/api/module2/incidents/?status=investigating")
        self.assertEqual(status_resp.status_code, 200)
        results = status_resp.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source"], "threat_intel")

        extra_ev = self._event(
            self.org,
            ACTION_BLOCK,
            extra={"detail": "Threat intel IOC match on prompt"},
        )
        extra_inc = SecurityIncident.objects.create(
            organization=self.org,
            enforcement_event=extra_ev,
            title="IOC extra detail",
            severity="high",
            status="open",
        )
        ti_resp = self.client.get("/api/module2/incidents/?source=threat_intel")
        self.assertEqual(ti_resp.status_code, 200)
        ti_ids = [row["id"] for row in ti_resp.json()["results"]]
        self.assertIn(extra_inc.id, ti_ids)

        search_resp = self.client.get("/api/module2/incidents/?search=UEBA")
        self.assertEqual(search_resp.status_code, 200)
        self.assertEqual(len(search_resp.json()["results"]), 1)
        self.assertIn("key_prefix", search_resp.json()["results"][0])

    def test_incident_summary_counts_statuses_with_enforcement_join(self):
        """select_related(enforcement_event) must not break status KPI aggregation."""
        for i in range(4):
            self._incident(self.org, f"Open case {i}", status="open", severity="medium")
        self._incident(self.org, "Escalated case", status="escalated", severity="high")
        self._incident(self.org, "Resolved case", status="resolved", severity="low")

        resp = self.client.get("/api/module2/incidents/")
        self.assertEqual(resp.status_code, 200)
        summary = resp.json()["summary"]
        self.assertEqual(summary["open"], 4)
        self.assertEqual(summary["escalated"], 1)
        self.assertEqual(summary["resolved"], 1)
        self.assertEqual(summary["active"], 5)
        self.assertEqual(summary["total"], 6)

    def test_org_scoping_hides_other_org_incidents(self):
        self._incident(self.other_org, "Foreign incident", severity="critical", status="open")
        resp = self.client.get("/api/module2/incidents/")
        self.assertEqual(resp.status_code, 200)
        titles = [row["title"] for row in resp.json()["results"]]
        self.assertNotIn("Foreign incident", titles)

    def test_resolve_incident_endpoint_updates_security_incident(self):
        incident = self._incident(self.org, "Resolvable case", severity="medium", status="open")
        resp = self.client.post(f"/api/security/incidents/{incident.id}/resolve-incident/", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertEqual(data["status"], "resolved")
        self.assertIsNotNone(data.get("resolved_at"))
        incident.refresh_from_db()
        self.assertEqual(incident.status, "resolved")
