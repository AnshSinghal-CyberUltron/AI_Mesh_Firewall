"""API tests for Module 2 model exposure, threat telemetry, and incidents endpoints."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
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
        self.assertEqual(data["summary"]["total_requests"], 3)
        model_names = {row["model"] for row in data["models"]}
        self.assertIn("gpt-4o", model_names)
        self.assertIn("unknown", model_names)
        self.assertTrue(all("exposure_score" in row for row in data["models"]))

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
            key_prefix="zs_test",
        )
        ev2 = self._event(
            self.org,
            ACTION_REDACT,
            threat_type="pii_ssn",
            category="pii",
            owasp_code="LLM06",
            key_prefix="zs_test",
        )
        EnforcementEvent.objects.filter(pk__in=[ev1.pk, ev2.pk]).update(created_at=since)

        resp = self.client.get("/api/module2/threat-intel/telemetry/?period=24h")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("summary", data)
        self.assertIn("timeline", data)
        self.assertIn("top_attack_vectors", data)
        self.assertGreaterEqual(data["summary"]["total_events"], 2)
        self.assertGreaterEqual(data["summary"].get("requests_inspected", 0), 2)
        self.assertGreaterEqual(data["summary"].get("injection_attempts", 0), 1)
        self.assertGreaterEqual(data["summary"].get("pii_leaks", 0), 1)
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
        self.assertIn("data_provenance", data)
        self.assertEqual(data["data_provenance"]["kpi_source"], "policy.SecurityIncident")
        self.assertEqual(
            data["data_provenance"]["label"],
            "From security cases raised by the gateway",
        )
        self.assertEqual(
            data["data_provenance"]["aggregation_service"],
            "module2.analytics.build_incident_queue_summary",
        )
        self.assertEqual(data["data_provenance"]["freshness"]["cache_status"], "live")
        self.assertEqual(data["data_provenance"]["filters_applied"]["severity"], "high")
        self.assertGreaterEqual(data["count"], 1)
        # KPI strip is period/org-wide — not shrunk to the severity-filtered table page.
        self.assertGreaterEqual(data["summary"]["total"], data["count"])
        self.assertTrue(all(row["severity"] == "high" for row in data["results"]))

        critical_high_resp = self.client.get("/api/module2/incidents/?severity=critical_high")
        self.assertEqual(critical_high_resp.status_code, 200)
        self.assertEqual(critical_high_resp.json()["count"], 2)
        self.assertGreaterEqual(
            critical_high_resp.json()["summary"]["total"],
            critical_high_resp.json()["count"],
        )
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

    def test_incidents_chat_filter_includes_model_only_rows(self):
        self._incident(self.org, "Model only chat", model="gpt-4o")
        resp = self.client.get("/api/module2/incidents/?source=chat")
        self.assertEqual(resp.status_code, 200)
        titles = [row["title"] for row in resp.json()["results"]]
        self.assertIn("Model only chat", titles)

    @override_settings(DEBUG=True)
    def test_incidents_e2e_seed_creates_probe_and_bulk(self):
        resp = self.client.post(
            "/api/module2/incidents/e2e-seed/",
            {
                "probe_title": "PW probe",
                "bulk_titles": ["PW bulk A", "PW bulk B"],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertTrue(body["probe_id"] > 0)
        self.assertEqual(len(body["bulk_ids"]), 2)
        search = self.client.get("/api/module2/incidents/?search=PW probe")
        self.assertEqual(search.status_code, 200)
        self.assertTrue(any(row["id"] == body["probe_id"] for row in search.json()["results"]))

    @override_settings(DEBUG=False)
    def test_incidents_e2e_seed_disabled_returns_404(self):
        resp = self.client.post(
            "/api/module2/incidents/e2e-seed/",
            {"probe_title": "PW probe", "bulk_titles": ["A", "B"]},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)

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

    def test_incidents_summary_matches_status_filter(self):
        self._incident(self.org, "Open A", status="open", severity="medium")
        self._incident(self.org, "Open B", status="open", severity="high")
        self._incident(self.org, "Resolved C", status="resolved", severity="low")

        resp = self.client.get("/api/module2/incidents/?status=open")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 2)
        # Table is filtered to open; KPI strip still reports the full period queue.
        self.assertEqual(data["summary"]["total"], 3)
        self.assertEqual(data["summary"]["open"], 2)
        self.assertEqual(data["summary"]["resolved"], 1)
        self.assertEqual(sum(data["summary"]["by_source"].values()), 2)

    def test_resolve_incident_updates_resolved_kpi_while_open_filter_active(self):
        open_case = self._incident(self.org, "Open to resolve", status="open", severity="high")
        self._incident(self.org, "Already resolved", status="resolved", severity="low")

        before = self.client.get("/api/module2/incidents/?status=open")
        self.assertEqual(before.status_code, 200)
        self.assertEqual(before.json()["summary"]["open"], 1)
        self.assertEqual(before.json()["summary"]["resolved"], 1)

        resolve = self.client.post(
            f"/api/security/incidents/{open_case.id}/resolve-incident/",
            {},
            format="json",
        )
        self.assertEqual(resolve.status_code, 200, resolve.content)

        after = self.client.get("/api/module2/incidents/?status=open")
        self.assertEqual(after.status_code, 200)
        summary = after.json()["summary"]
        self.assertEqual(summary["open"], 0)
        self.assertEqual(summary["resolved"], 2)
        self.assertEqual(summary["active"], 0)
        self.assertEqual(after.json()["count"], 0)

    def test_incidents_chat_excludes_projected_ioc_keyword_blocks(self):
        from module2.models import ThreatIntelEntry

        ThreatIntelEntry.objects.create(
            organization=self.org,
            source="manual",
            threat_type="keyword",
            indicator="noo",
            confidence=0.9,
            auto_block=True,
        )
        ioc = self._incident(
            self.org,
            "IOC keyword case",
            status="open",
            threat_type="blocked_keyword",
            code="content_blocked",
            detail="Blocked keyword(s) detected: noo",
            key_prefix="zs_ioc",
        )
        chat = self._incident(self.org, "Plain chat case", status="open", model="gpt-4o")

        chat_resp = self.client.get("/api/module2/incidents/?source=chat")
        self.assertEqual(chat_resp.status_code, 200)
        chat_ids = {row["id"] for row in chat_resp.json()["results"]}
        self.assertIn(chat.id, chat_ids)
        self.assertNotIn(ioc.id, chat_ids)
        self.assertGreaterEqual(
            chat_resp.json()["summary"]["total"],
            chat_resp.json()["count"],
        )
        self.assertEqual(
            sum(chat_resp.json()["summary"]["by_source"].values()),
            chat_resp.json()["count"],
        )

        ti_resp = self.client.get("/api/module2/incidents/?source=threat_intel")
        self.assertEqual(ti_resp.status_code, 200)
        ti_ids = {row["id"] for row in ti_resp.json()["results"]}
        self.assertIn(ioc.id, ti_ids)

    def test_ueba_bundle_timeline_matches_key_attributed_totals(self):
        from core.models import GatewayAPIKey

        key, _ = GatewayAPIKey.generate_key(name="ueba-parity", owner=self.user, project_id="proj-ueba")
        key.organization = self.org
        key.save(update_fields=["organization"])
        self._event(
            self.org,
            ACTION_BLOCK,
            key_prefix=key.prefix,
            model="gpt-4o",
            threat_type="prompt_injection",
        )
        # Unkeyed event must not inflate the Behavior Timeline.
        self._event(self.org, ACTION_BLOCK, model="gpt-4o", threat_type="prompt_injection")

        resp = self.client.get("/api/module2/ueba/api-keys/bundle/?period=24h")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        summary_total = data["summary"]["summary"].get("total_events")
        timeline_total = sum(
            int(row.get("total_events") or 0) for row in (data["timeline"].get("timeline") or [])
        )
        self.assertEqual(timeline_total, summary_total)
        self.assertEqual(timeline_total, 1)

    def test_dashboard_open_incidents_are_period_scoped(self):
        recent = self._incident(self.org, "Recent open", status="open", severity="high")
        old = self._incident(self.org, "Old open", status="open", severity="high")
        SecurityIncident.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=10)
        )

        resp = self.client.get("/api/module2/dashboard/?period=7d")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["kpis"]["open_incidents"], 1)
        snap_ids = {row["id"] for row in data.get("incidents_snapshot") or []}
        self.assertIn(recent.id, snap_ids)
        self.assertNotIn(old.id, snap_ids)

    def test_dashboard_threat_trend_30d_uses_daily_buckets(self):
        resp = self.client.get("/api/module2/dashboard/?period=30d")
        self.assertEqual(resp.status_code, 200)
        trend = resp.json().get("threat_trend") or []
        self.assertEqual(len(trend), 30)

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
        timeline_rows = data["timeline"].get("timeline") or []
        if timeline_rows:
            sample = timeline_rows[0]
            self.assertIn("chat", sample)
            self.assertIn("total_events", sample)
        self.assertIn("results", data["registry"])
        top_keys = data["summary"].get("top_risky_keys") or []
        if top_keys:
            row = top_keys[0]
            self.assertIn("behavior_profile", row)
            self.assertIn("traditional_score", row)
            self.assertIn("final_score", row)
            self.assertIn("llm_observation", row)
            obs = row["llm_observation"]
            self.assertIn("requests_meet_prompt_target", obs)
            self.assertIn("triage_threshold", obs)

    def test_ueba_risk_calculation_view_returns_settings_and_metrics(self):
        from core.models import GatewayAPIKey
        from module2.models import OrgUebaSettings

        key, _ = GatewayAPIKey.generate_key(name="risk-key", owner=self.user, project_id="proj-risk")
        key.organization = self.org
        key.save(update_fields=["organization"])
        self._event(
            self.org,
            ACTION_BLOCK,
            key_prefix=key.prefix,
            model="gpt-4o",
            threat_type="prompt_injection",
            prompt_snippet="ignore system instructions",
        )
        OrgUebaSettings.objects.get_or_create(organization=self.org)

        resp = self.client.get("/api/module2/ueba/risk-calculation/?period=24h")
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertIn("settings", body)
        self.assertIn("formula_reference", body)
        self.assertIn("api_key_metrics", body)
        self.assertIn("weights", body["settings"])
        self.assertIn("weight_guardrails", body["settings"])
        self.assertIn("prompt_target_locked", body["settings"])

    @patch("module2.tasks.reassess_org_ueba_keys.delay")
    @patch("module2.ueba_service.prompt_target_lock_state")
    def test_ueba_risk_calculation_patch_rejects_prompt_target_when_profile_built(
        self, lock_state, reassess_delay
    ):
        from module2.models import OrgUebaSettings
        from module2.ueba_service import PROMPT_TARGET_LOCKED_PROFILE

        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        settings_obj, _ = OrgUebaSettings.objects.get_or_create(organization=self.org)
        settings_obj.behavior_profile_prompt_target = 50
        settings_obj.save(update_fields=["behavior_profile_prompt_target"])

        lock_state.return_value = {
            "prompt_target_locked": True,
            "prompt_target_lock_reason": PROMPT_TARGET_LOCKED_PROFILE,
            "built_profile_count": 1,
            "active_key_count": 0,
        }

        resp = self.client.patch(
            "/api/module2/ueba/risk-calculation/",
            {"behavior_profile_prompt_target": 75},
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("already built", resp.json().get("detail", "").lower())
        settings_obj.refresh_from_db()
        self.assertEqual(settings_obj.behavior_profile_prompt_target, 50)
        reassess_delay.assert_not_called()

        # Same value is allowed (no-op); other settings still editable.
        resp_ok = self.client.patch(
            "/api/module2/ueba/risk-calculation/",
            {
                "behavior_profile_prompt_target": 50,
                "llm_triage_min_traditional_score": 0.5,
            },
            format="json",
        )
        self.assertEqual(resp_ok.status_code, 200, resp_ok.content)
        self.assertTrue(resp_ok.json()["settings"]["prompt_target_locked"])

    @patch("module2.tasks.reassess_org_ueba_keys.delay")
    def test_ueba_risk_calculation_patch_updates_settings_for_admin(self, reassess_delay):
        from core.models import AuditLog
        from module2.models import OrgUebaSettings

        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        settings_obj, _ = OrgUebaSettings.objects.get_or_create(organization=self.org)
        payload = {
            "behavior_profile_prompt_target": 60,
            "weights": {
                "block_rate": 0.45,
                "threat_severity": 0.30,
                "velocity": 0.15,
                "policy_escalation": 0.10,
                "baseline_deviation": 0.25,
            },
        }
        resp = self.client.patch("/api/module2/ueba/risk-calculation/", payload, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertIn("changes", body)
        settings_obj.refresh_from_db()
        self.assertEqual(settings_obj.behavior_profile_prompt_target, 60)
        self.assertAlmostEqual(settings_obj.weight_baseline_deviation, 0.25)
        reassess_delay.assert_called_once_with(self.org.id, run_llm=True)
        audit = AuditLog.objects.filter(
            organization=self.org,
            action="ueba_risk_settings_update",
        ).first()
        self.assertIsNotNone(audit)
        self.assertIn("behavior_profile_prompt_target", audit.details)

    @patch("module2.tasks.reassess_org_ueba_keys.delay")
    def test_ueba_risk_calculation_patch_skips_llm_when_triage_disabled(self, reassess_delay):
        from module2.models import OrgUebaSettings

        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        settings_obj, _ = OrgUebaSettings.objects.get_or_create(organization=self.org)
        settings_obj.llm_triage_enabled = False
        settings_obj.save(update_fields=["llm_triage_enabled"])

        resp = self.client.patch(
            "/api/module2/ueba/risk-calculation/",
            {"behavior_profile_prompt_target": 55},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        reassess_delay.assert_called_once_with(self.org.id, run_llm=False)

    def test_ueba_risk_calculation_patch_rejects_invalid_weights(self):
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        payload = {
            "weights": {
                "block_rate": 0.05,
                "threat_severity": 0.05,
                "velocity": 0.05,
                "policy_escalation": 0.05,
                "baseline_deviation": 0.9,
            },
        }
        resp = self.client.patch("/api/module2/ueba/risk-calculation/", payload, format="json")
        self.assertEqual(resp.status_code, 400, resp.content)

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
        body = resp_admin.json()
        self.assertEqual(body.get("effective_mode"), "synced_for_blocking")
        self.assertEqual(body.get("effective_reason"), "keyword_literal_match")

    def test_threat_intel_sync_returns_503_when_redis_fails(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        with patch(
            "module2.views.safe_sync_threat_intel_to_redis",
            return_value=(False, "redis down"),
        ):
            resp = self.client.post("/api/module2/threat-intel/sync/", {}, format="json")
        self.assertEqual(resp.status_code, 503, resp.content)
        body = resp.json()
        self.assertEqual(body["status"], "sync_failed")
        self.assertIn("redis_key", body)

    def test_threat_intel_sync_returns_synced_payload_on_success(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        from module2.models import ThreatIntelEntry

        ThreatIntelEntry.objects.create(
            organization=self.org,
            threat_type="jailbreak_probe",
            indicator="ignore previous",
            auto_block=True,
            source="manual",
        )
        with patch("module2.views.safe_sync_threat_intel_to_redis", return_value=(True, None)):
            resp = self.client.post("/api/module2/threat-intel/sync/", {}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body["status"], "synced")
        self.assertIn("synced_by_threat_type", body)
        self.assertEqual(body["synced_by_threat_type"].get("jailbreak_probe"), 1)

    def test_threat_intel_telemetry_ioc_library_includes_by_threat_type(self):
        from module2.models import ThreatIntelEntry

        ThreatIntelEntry.objects.create(
            organization=self.org,
            threat_type="pw_probe",
            indicator="test-ioc",
            source="manual",
        )
        resp = self.client.get("/api/module2/threat-intel/telemetry/?period=24h")
        self.assertEqual(resp.status_code, 200)
        lib = resp.json().get("ioc_library") or {}
        self.assertIn("by_threat_type", lib)
        self.assertEqual(lib["by_threat_type"].get("pw_probe"), 1)
        self.assertIn("expiring_soon", lib)

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
        row = timeline[0]
        meta = row["metadata"]
        self.assertEqual(meta.get("detail"), "tool call blocked")
        self.assertNotIn("secret_token", meta)
        self.assertIn("extra", meta)
        self.assertEqual(meta["extra"].get("detail"), "nested detail")
        self.assertNotIn("secret", meta["extra"])
        self.assertEqual(row.get("prompt_snippet"), "hello world")

    def test_incident_detail_timeline_extracts_prompt_submitted(self):
        incident = self._incident(
            self.org,
            "Prompt submitted case",
            event_type="chat",
            detail="blocked by kill switch",
            threat_type="kill_switch",
            prompt_submitted="ignore previous instructions",
        )

        resp = self.client.get(f"/api/module2/incidents/{incident.id}/")
        self.assertEqual(resp.status_code, 200)
        timeline = resp.json()["timeline"]
        self.assertTrue(timeline)
        row = timeline[0]
        self.assertEqual(row.get("prompt_snippet"), "ignore previous instructions")
        self.assertEqual(row["metadata"].get("prompt_submitted"), "ignore previous instructions")
        self.assertEqual(row.get("threat_type"), "kill_switch")

    def test_incident_detail_timeline_keeps_reason_for_kill_switch(self):
        incident = self._incident(
            self.org,
            "Kill switch reason case",
            event_type="kill_switch",
            threat_type="kill_switch",
            reason="Analyst containment — medium risk",
            extra={
                "reason": "Analyst containment — medium risk",
                "trigger_source": "kill_switch",
                "isolation_scope": "credential",
            },
        )

        resp = self.client.get(f"/api/module2/incidents/{incident.id}/")
        self.assertEqual(resp.status_code, 200)
        meta = resp.json()["timeline"][0]["metadata"]
        self.assertEqual(meta.get("reason"), "Analyst containment — medium risk")
        self.assertEqual(meta["extra"].get("trigger_source"), "kill_switch")
        self.assertEqual(meta["extra"].get("isolation_scope"), "credential")

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
