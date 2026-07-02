"""API tests for Module 2 model exposure, threat telemetry, and incidents endpoints."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import LLMModelConfig
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
        LLMModelConfig.objects.create(
            organization=self.org,
            provider="openai",
            model_name="gpt-4o",
            model_id="openai/gpt-4o",
            is_active=True,
        )
        LLMModelConfig.objects.create(
            organization=self.org,
            provider="anthropic",
            model_name="claude-3.5-haiku",
            model_id="anthropic/claude-3.5-haiku",
            is_active=True,
        )
        LLMModelConfig.objects.create(
            organization=self.org,
            provider="openai",
            model_name="gpt-4.1-mini",
            model_id="openai/gpt-4.1-mini",
            is_active=True,
        )
        LLMModelConfig.objects.create(
            organization=self.org,
            provider="internal",
            model_name="zeroshield-guard-120b",
            model_id="bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0",
            is_active=True,
        )
        LLMModelConfig.objects.create(
            organization=self.org,
            provider="openai",
            model_name="deprecated-model",
            model_id="openai/deprecated-model",
            is_active=False,
        )

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
        self._event(self.org, ACTION_BLOCK, model="not-connected-model", key_prefix="zs_test", threat_type="prompt_injection")

        resp = self.client.get("/api/module2/models/exposure/?period=24h")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("summary", data)
        self.assertIn("exposure_by_model", data)
        self.assertIn("models", data)
        self.assertEqual(data["summary"]["active_models"], 3)
        self.assertEqual(data["summary"]["total_requests"], 2)
        self.assertEqual(data["models"][0]["model"], "gpt-4o")
        self.assertEqual(len(data["models"]), 1)
        self.assertIn("exposure_score", data["models"][0])

    def test_model_exposure_accepts_model_id_alias_from_gateway_metadata(self):
        self._event(
            self.org,
            ACTION_BLOCK,
            model="anthropic/claude-3.5-haiku",
            key_prefix="zs_test",
            threat_type="prompt_injection",
        )

        resp = self.client.get("/api/module2/models/exposure/?period=24h")
        self.assertEqual(resp.status_code, 200)
        rows = resp.json()["models"]
        self.assertTrue(any(row["model"] == "claude-3.5-haiku" for row in rows))

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

        critical_high_resp = self.client.get("/api/module2/incidents/?severity=critical_high")
        self.assertEqual(critical_high_resp.status_code, 200)
        self.assertEqual(critical_high_resp.json()["count"], 2)
        self.assertTrue(
            all(row["severity"] in {"critical", "high"} for row in critical_high_resp.json()["results"])
        )

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
        chat_resp = self.client.get("/api/module2/incidents/?source=chat")
        self.assertEqual(chat_resp.status_code, 200)
        chat_sources = {row["source"] for row in chat_resp.json()["results"]}
        self.assertNotIn("threat_intel", chat_sources)

        search_resp = self.client.get("/api/module2/incidents/?search=UEBA")
        self.assertEqual(search_resp.status_code, 200)
        self.assertEqual(len(search_resp.json()["results"]), 1)
        self.assertIn("key_prefix", search_resp.json()["results"][0])

    def test_incidents_list_rejects_invalid_filters(self):
        for query in (
            "status=bad",
            "severity=urgent",
            "source=foo",
            "queue=everything",
            "period=90d",
        ):
            resp = self.client.get(f"/api/module2/incidents/?{query}")
            self.assertEqual(resp.status_code, 400, query)

    def test_incidents_list_period_filters_summary_and_rows(self):
        old = self._incident(self.org, "Old incident", status="open", severity="medium")
        recent = self._incident(self.org, "Recent incident", status="open", severity="high")
        cutoff = timezone.now() - timedelta(days=10)
        SecurityIncident.objects.filter(pk=old.pk).update(created_at=cutoff)

        resp = self.client.get("/api/module2/incidents/?period=7d")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        titles = [row["title"] for row in data["results"]]
        self.assertIn("Recent incident", titles)
        self.assertNotIn("Old incident", titles)
        self.assertEqual(data["summary"]["total"], 1)

    def test_incidents_bulk_resolve_selected_rows(self):
        open_a = self._incident(self.org, "Bulk A", status="open", severity="medium")
        open_b = self._incident(self.org, "Bulk B", status="investigating", severity="high")
        resolved = self._incident(self.org, "Already done", status="resolved", severity="low")

        resp = self.client.post(
            "/api/module2/incidents/bulk-resolve/",
            {"incident_ids": [open_a.id, open_b.id, resolved.id]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["resolved_count"], 2)
        self.assertIn(open_a.id, body["resolved_ids"])
        self.assertIn(open_b.id, body["resolved_ids"])

        open_a.refresh_from_db()
        open_b.refresh_from_db()
        resolved.refresh_from_db()
        self.assertEqual(open_a.status, "resolved")
        self.assertEqual(open_b.status, "resolved")
        self.assertEqual(resolved.status, "resolved")

    def test_incidents_bulk_resolve_rejects_empty_ids(self):
        resp = self.client.post(
            "/api/module2/incidents/bulk-resolve/",
            {"incident_ids": []},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_incidents_bulk_resolve_rejects_non_integer_ids(self):
        resp = self.client.post(
            "/api/module2/incidents/bulk-resolve/",
            {"incident_ids": ["abc", 5]},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_incidents_bulk_resolve_skips_foreign_org_ids(self):
        local_open = self._incident(self.org, "Local open", status="open")
        foreign_open = self._incident(self.other_org, "Foreign open", status="open")
        resp = self.client.post(
            "/api/module2/incidents/bulk-resolve/",
            {"incident_ids": [local_open.id, foreign_open.id]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["resolved_count"], 1)
        self.assertEqual(body["skipped_count"], 1)
        self.assertEqual(body["resolved_ids"], [local_open.id])

    def test_incidents_bulk_resolve_applies_notes(self):
        incident = self._incident(self.org, "Needs note", status="open")
        note = "resolved via bulk operation"
        resp = self.client.post(
            "/api/module2/incidents/bulk-resolve/",
            {"incident_ids": [incident.id], "notes": note},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        incident.refresh_from_db()
        self.assertEqual(incident.status, "resolved")
        self.assertEqual(incident.notes, note)

    def test_ueba_bundle_returns_summary_timeline_registry(self):
        resp = self.client.get("/api/module2/ueba/api-keys/bundle/?period=24h")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("summary", data)
        self.assertIn("timeline", data)
        self.assertIn("registry", data)
        self.assertIn("summary", data["summary"])
        self.assertIn("timeline", data["timeline"])
        self.assertIn("results", data["registry"])

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

    def test_incident_detail_rejects_orgless_non_superuser(self):
        incident = self._incident(self.other_org, "Foreign incident detail", severity="critical", status="open")
        no_org_user = User.objects.create_user(username="no-org-user", password="pass-no-org")
        no_org_client = APIClient()
        no_org_client.force_authenticate(user=no_org_user)
        resp = no_org_client.get(f"/api/module2/incidents/{incident.id}/")
        self.assertEqual(resp.status_code, 404)

    def test_threat_intel_write_requires_admin_or_superuser(self):
        payload = {
            "source": "manual",
            "threat_type": "prompt_injection",
            "indicator": "ignore previous instructions",
            "owasp_code": "LLM01",
            "confidence": 0.95,
            "auto_block": True,
        }
        resp = self.client.post("/api/module2/threat-intel/", payload, format="json")
        self.assertEqual(resp.status_code, 403)

        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        resp_admin = self.client.post("/api/module2/threat-intel/", payload, format="json")
        self.assertEqual(resp_admin.status_code, 201, resp_admin.content)

    def test_incident_detail_timeline_redacts_unknown_metadata_fields(self):
        incident = self._incident(
            self.org,
            "Metadata redaction case",
            event_type="mcp_tool_call",
            detail="tool call blocked",
            prompt_snippet="hello world",
            secret_token="should_not_leak",
            extra={"detail": "nested detail", "secret": "hidden"},
        )

        resp = self.client.get(f"/api/module2/incidents/{incident.id}/")
        self.assertEqual(resp.status_code, 200)
        timeline = resp.json()["timeline"]
        self.assertTrue(timeline)
        meta = timeline[0]["metadata"]
        self.assertEqual(meta.get("detail"), "tool call blocked")
        self.assertNotIn("secret_token", meta)
        self.assertIn("extra", meta)
        self.assertEqual(meta["extra"].get("detail"), "nested detail")
        self.assertNotIn("secret", meta["extra"])

    def test_investigate_incident_endpoint_claims_open_case(self):
        incident = self._incident(self.org, "Investigate me", severity="medium", status="open")
        resp = self.client.post(
            f"/api/security/incidents/{incident.id}/investigate-incident/",
            {},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertEqual(data["status"], "investigating")
        self.assertEqual(data["assigned_to_username"], self.user.username)
        incident.refresh_from_db()
        self.assertEqual(incident.status, "investigating")

    def test_resolve_incident_endpoint_updates_security_incident(self):
        incident = self._incident(self.org, "Resolvable case", severity="medium", status="open")
        resp = self.client.post(f"/api/security/incidents/{incident.id}/resolve-incident/", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertEqual(data["status"], "resolved")
        self.assertIsNotNone(data.get("resolved_at"))
        incident.refresh_from_db()
        self.assertEqual(incident.status, "resolved")

    def test_resolve_incident_succeeds_when_cache_invalidation_fails(self):
        from unittest.mock import patch

        incident = self._incident(self.org, "Cache failure case", severity="medium", status="open")
        with patch("django.core.cache.cache.delete", side_effect=ConnectionError("redis down")):
            resp = self.client.post(
                f"/api/security/incidents/{incident.id}/resolve-incident/",
                {},
                format="json",
            )
        self.assertEqual(resp.status_code, 200, resp.content)
        incident.refresh_from_db()
        self.assertEqual(incident.status, "resolved")

    def test_resolve_incident_succeeds_when_cache_invalidation_fails(self):
        from unittest.mock import patch

        incident = self._incident(self.org, "Cache failure case", severity="medium", status="open")
        with patch("django.core.cache.cache.delete", side_effect=ConnectionError("redis down")):
            resp = self.client.post(
                f"/api/security/incidents/{incident.id}/resolve-incident/",
                {},
                format="json",
            )
        self.assertEqual(resp.status_code, 200, resp.content)
        incident.refresh_from_db()
        self.assertEqual(incident.status, "resolved")
