"""Tests for ModelState bootstrap and Redis kill-switch validation."""

from __future__ import annotations

from django.test import SimpleTestCase, TestCase

from core.redis_kill_switch_views import _scan_org_kill_switch_keys


class RedisScanTests(SimpleTestCase):
    def test_scan_detects_malformed_json(self):
        class FakeRedis:
            def scan_iter(self, pattern):
                return [b"kill_switch:acme:global"]

            def get(self, key):
                return b"not-json{{"

        findings = _scan_org_kill_switch_keys(FakeRedis(), "acme")
        self.assertEqual(len(findings), 1)
        self.assertTrue(findings[0]["malformed"])


class ModelStateBootstrapTests(TestCase):
    def test_ensure_model_states_creates_from_llm_config(self):
        from auth.models import Organization
        from core.model_state_bootstrap import ensure_model_states_for_org
        from core.models import LLMModelConfig, ModelState

        org = Organization.objects.create(name="Triage Org", slug="triage-org")
        LLMModelConfig.objects.create(
            organization=org,
            provider="openai",
            model_name="live-triage-openai",
            model_id="openai/gpt-4o-mini",
            is_active=True,
        )

        created, active = ensure_model_states_for_org(org)
        self.assertEqual(active, 1)
        self.assertEqual(created, 1)
        self.assertTrue(
            ModelState.objects.filter(organization=org, model_name="live-triage-openai").exists()
        )

        created_again, _ = ensure_model_states_for_org(org)
        self.assertEqual(created_again, 0)

    def test_merge_includes_virtual_config_rows(self):
        from auth.models import Organization
        from core.model_state_bootstrap import merge_model_states_with_configs
        from core.models import LLMModelConfig, ModelState

        org = Organization.objects.create(name="Merge Org", slug="merge-org")
        LLMModelConfig.objects.create(
            organization=org,
            provider="openai",
            model_name="only-config",
            model_id="openai/gpt-4o-mini",
            is_active=True,
        )
        ModelState.objects.create(organization=org, model_name="has-state")

        merged = merge_model_states_with_configs(org, ModelState.objects.filter(organization=org))
        names = {row["model_name"] for row in merged}
        self.assertEqual(names, {"has-state", "only-config"})
        virtual = next(row for row in merged if row["model_name"] == "only-config")
        self.assertIsNone(virtual["id"])
        self.assertFalse(virtual["is_bootstrapped"])
