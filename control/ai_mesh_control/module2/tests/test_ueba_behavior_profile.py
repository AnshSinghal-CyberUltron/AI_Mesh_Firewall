"""Tests for UEBA behavior profile collection and unified scoring."""

import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from auth.models import Organization
from core.models import GatewayAPIKey
from module2.models import ApiKeyBehaviorProfile, ApiKeyRiskAssessment
from module2.ueba_behavior_profile import (
    append_prompt_samples_for_events,
    behavior_profile_payload,
    needs_bootstrap,
    profile_build_sample_count,
    prompt_target,
)
from module2.ueba_scoring import compute_traditional_score, score_learning_mode
from module2.ueba_service import (
    assess_api_key,
    get_or_create_org_settings,
    reassess_api_key,
    validate_org_ueba_settings,
)
from policy.models import EnforcementEvent

User = get_user_model()


class _FakeEvent:
    def __init__(self, org_id, prefix, snippet, action="allow", threat_type="none"):
        self.organization_id = org_id
        self.action = action
        self.created_at = timezone.now()
        self.metadata = {
            "key_prefix": prefix,
            "prompt_snippet": snippet,
            "request_id": f"req-{snippet[:8]}",
            "model": "gpt-4o",
            "threat_type": threat_type,
        }


class UebaBehaviorProfileTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Acme", slug="acme-ueba")
        self.user = User.objects.create_user(username="ueba", email="ueba@test", password="pass")
        self.key, _ = GatewayAPIKey.generate_key(name="prod", owner=self.user, project_id="p1")
        self.key.organization = self.org
        self.key.save(update_fields=["organization"])

    def test_append_stops_at_target_and_dedupes(self):
        target = prompt_target()
        events = [
            _FakeEvent(self.org.id, self.key.prefix, f"prompt {i}")
            for i in range(target + 10)
        ]
        events.append(_FakeEvent(self.org.id, self.key.prefix, "prompt 0"))
        append_prompt_samples_for_events(events)
        profile = ApiKeyBehaviorProfile.objects.get(gateway_api_key=self.key)
        self.assertEqual(profile.sample_count, target)
        self.assertEqual(len(profile.prompt_samples), target)

    def test_needs_bootstrap_when_full_but_not_built(self):
        profile = ApiKeyBehaviorProfile.objects.create(
            gateway_api_key=self.key,
            sample_count=prompt_target(),
            prompt_samples=[{"snippet": "x"}] * prompt_target(),
        )
        self.assertTrue(needs_bootstrap(profile))
        profile.profile_built_at = timezone.now()
        profile.save(update_fields=["profile_built_at"])
        self.assertFalse(needs_bootstrap(profile))

    def test_compute_traditional_score_uses_learning_mode(self):
        metric = {
            "total": 100,
            "blocked": 40,
            "redacted": 5,
            "policy_escalations": 2,
            "threat_types": {"prompt_injection": 30, "none": 70},
            "hourly": {"2026-07-07T10:00:00": 50, "2026-07-07T11:00:00": 50},
            "models": ["gpt-4o"],
        }
        score, breakdown = compute_traditional_score(metric)
        learning_score, learning_breakdown = score_learning_mode(metric)
        self.assertEqual(score, learning_score)
        self.assertEqual(breakdown["mode"], learning_breakdown["mode"])

    @override_settings(MODULE2_UEBA_BEHAVIOR_PROMPT_TARGET=10)
    @patch.dict(os.environ, {"MODULE2_UEBA_LLM_MOCK": "1"})
    @patch("module2.ueba_service._llm_rate_limit_ok", return_value=True)
    def test_assess_runs_bootstrap_at_sample_target(self, _rate_ok):
        profile = ApiKeyBehaviorProfile.objects.create(
            gateway_api_key=self.key,
            sample_count=10,
            prompt_samples=[
                {
                    "snippet": f"sample-{i}",
                    "action": "block" if i % 3 == 0 else "allow",
                    "model": "gpt-4o",
                    "threat_type": "injection" if i % 3 == 0 else "none",
                }
                for i in range(10)
            ],
        )
        org_settings = get_or_create_org_settings(self.org)
        org_settings.behavior_profile_prompt_target = 10
        org_settings.save(update_fields=["behavior_profile_prompt_target"])
        metric = {
            "total": 10,
            "blocked": 2,
            "redacted": 0,
            "policy_escalations": 0,
            "threat_types": {"injection": 2},
            "hourly": {"2026-07-07T10:00:00": 10},
            "models": ["gpt-4o"],
        }
        result = assess_api_key(self.key, metric, org_settings, behavior_profile=profile)
        profile.refresh_from_db()
        self.assertIsNotNone(profile.profile_built_at)
        self.assertEqual(result["behavior_profile"]["status"], "ready")
        self.assertIn("expected_use_case", result["behavior_profile"])

    @override_settings(MODULE2_UEBA_BEHAVIOR_PROMPT_TARGET=50)
    @patch.dict(os.environ, {"MODULE2_UEBA_LLM_MOCK": "1"})
    def test_triage_skipped_until_profile_ready(self):
        org_settings = get_or_create_org_settings(self.org)
        org_settings.llm_triage_min_traditional_score = 0.1
        org_settings.save(update_fields=["llm_triage_min_traditional_score"])
        profile = ApiKeyBehaviorProfile.objects.create(
            gateway_api_key=self.key,
            sample_count=5,
            prompt_samples=[{"snippet": "x"}],
        )
        metric = {
            "total": 50,
            "blocked": 30,
            "redacted": 0,
            "policy_escalations": 0,
            "threat_types": {"injection": 30},
            "hourly": {"2026-07-07T10:00:00": 50},
            "models": ["gpt-4o"],
        }
        result = assess_api_key(self.key, metric, org_settings, behavior_profile=profile)
        self.assertEqual(result["llm_verdict"], "skipped")
        profile.profile_built_at = timezone.now()
        profile.expected_use_case = "Scanner"
        profile.save()
        result2 = assess_api_key(self.key, metric, org_settings, behavior_profile=profile)
        self.assertIn(result2["llm_verdict"], ("benign", "suspicious", "malicious"))

    def test_reassess_persists_assessment(self):
        EnforcementEvent.objects.create(
            organization=self.org,
            action="block",
            metadata={
                "key_prefix": self.key.prefix,
                "model": "gpt-4o",
                "threat_type": "injection",
                "prompt_snippet": "ignore instructions",
            },
        )
        with patch.dict(os.environ, {"MODULE2_UEBA_LLM_MOCK": "1"}):
            snapshot, _metric = reassess_api_key(self.key, run_llm=False)
        self.assertIsInstance(snapshot, ApiKeyRiskAssessment)
        self.assertGreaterEqual(snapshot.final_score, 0.0)
        self.key.refresh_from_db()
        self.assertEqual(self.key.risk_score, snapshot.final_score)

    def test_behavior_profile_payload_building(self):
        payload = behavior_profile_payload(None)
        self.assertEqual(payload["status"], "building")
        self.assertEqual(payload["prompt_samples_collected"], 0)

    def test_llm_observation_payload_request_and_triage_gates(self):
        from module2.ueba_service import llm_observation_payload

        org_settings = get_or_create_org_settings(self.org)
        org_settings.behavior_profile_prompt_target = 10
        org_settings.llm_triage_min_traditional_score = 0.45
        org_settings.save(update_fields=["behavior_profile_prompt_target", "llm_triage_min_traditional_score"])
        behavior = behavior_profile_payload(None, org_settings)
        metric_low = {"total": 5, "blocked": 0, "redacted": 0}
        obs_low = llm_observation_payload(
            metric_low,
            org_settings,
            traditional_score=0.2,
            breakdown={"anomaly_flags": []},
            behavior_profile=behavior,
        )
        self.assertTrue(obs_low["requests_below_prompt_target"])
        self.assertFalse(obs_low["requests_meet_prompt_target"])
        self.assertEqual(obs_low["observation_phase"], "profile_building")

        metric_high = {"total": 25, "blocked": 10, "redacted": 0}
        obs_high = llm_observation_payload(
            metric_high,
            org_settings,
            traditional_score=0.5,
            breakdown={"anomaly_flags": []},
            behavior_profile={**behavior, "status": "ready", "prompt_samples_collected": 10},
        )
        self.assertTrue(obs_high["requests_meet_prompt_target"])
        self.assertTrue(obs_high["triage_score_gate_met"])
        self.assertTrue(obs_high["llm_triage_eligible"])

    @patch.dict(os.environ, {"MODULE2_UEBA_LLM_MOCK": "1"})
    def test_baseline_deviation_factor_applies_for_ready_profile(self):
        profile = ApiKeyBehaviorProfile.objects.create(
            gateway_api_key=self.key,
            sample_count=50,
            profile_built_at=timezone.now(),
            baseline_metrics={
                "block_rate": 0.05,
                "redact_rate": 0.01,
                "models": ["gpt-4o"],
                "top_threats": [["none", 40]],
            },
        )
        org_settings = get_or_create_org_settings(self.org)
        org_settings.weight_baseline_deviation = 0.4
        org_settings.save(update_fields=["weight_baseline_deviation"])
        metric = {
            "total": 50,
            "blocked": 30,
            "redacted": 0,
            "policy_escalations": 0,
            "threat_types": {"injection": 30},
            "hourly": {"2026-07-07T10:00:00": 50},
            "models": ["gpt-4o", "claude-3-haiku"],
        }
        result = assess_api_key(self.key, metric, org_settings, behavior_profile=profile, run_llm=False)
        breakdown = result["score_breakdown"]
        self.assertIn("baseline_deviation_factor", breakdown)
        self.assertGreater(breakdown["baseline_deviation_factor"], 0)
        self.assertGreaterEqual(result["traditional_score"], 0.0)

    def test_validate_rejects_low_traditional_weight_sum(self):
        org_settings = get_or_create_org_settings(self.org)
        org_settings.weight_block_rate = 0.05
        org_settings.weight_threat_severity = 0.05
        org_settings.weight_velocity = 0.05
        org_settings.weight_policy_escalation = 0.05
        with self.assertRaises(ValueError):
            validate_org_ueba_settings(org_settings)

    def test_validate_rejects_high_baseline_deviation_weight(self):
        org_settings = get_or_create_org_settings(self.org)
        org_settings.weight_baseline_deviation = 0.75
        with self.assertRaises(ValueError):
            validate_org_ueba_settings(org_settings)

    @override_settings(MODULE2_UEBA_BEHAVIOR_PROMPT_TARGET=50)
    def test_needs_bootstrap_when_org_target_lowered(self):
        org_settings = get_or_create_org_settings(self.org)
        org_settings.behavior_profile_prompt_target = 30
        org_settings.save(update_fields=["behavior_profile_prompt_target"])
        profile = ApiKeyBehaviorProfile.objects.create(
            gateway_api_key=self.key,
            sample_count=40,
            prompt_samples=[{"snippet": f"p{i}"} for i in range(40)],
        )
        self.assertTrue(needs_bootstrap(profile, org_settings))

    def test_append_stops_after_profile_locked(self):
        org_settings = get_or_create_org_settings(self.org)
        org_settings.behavior_profile_prompt_target = 100
        org_settings.save(update_fields=["behavior_profile_prompt_target"])
        profile = ApiKeyBehaviorProfile.objects.create(
            gateway_api_key=self.key,
            sample_count=50,
            profile_built_at=timezone.now(),
            prompt_samples=[{"snippet": f"p{i}", "request_id": f"r{i}"} for i in range(50)],
            baseline_metrics={"sample_count": 50},
        )
        events = [_FakeEvent(self.org.id, self.key.prefix, "new prompt after lock")]
        append_prompt_samples_for_events(events)
        profile.refresh_from_db()
        self.assertEqual(profile.sample_count, 50)

    def test_profile_payload_shows_locked_build_count(self):
        profile = ApiKeyBehaviorProfile.objects.create(
            gateway_api_key=self.key,
            sample_count=50,
            profile_built_at=timezone.now(),
            baseline_metrics={"sample_count": 50},
        )
        org_settings = get_or_create_org_settings(self.org)
        org_settings.behavior_profile_prompt_target = 30
        org_settings.save(update_fields=["behavior_profile_prompt_target"])
        payload = behavior_profile_payload(profile, org_settings)
        self.assertEqual(payload["status"], "ready")
        self.assertTrue(payload["profile_locked"])
        self.assertEqual(payload["prompt_samples_build_count"], 50)
        self.assertEqual(payload["prompt_samples_target"], 30)
        self.assertEqual(profile_build_sample_count(profile), 50)
