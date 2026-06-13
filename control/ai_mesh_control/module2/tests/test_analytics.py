"""Unit tests for Module 2 analytics helpers."""

from datetime import timedelta

from django.test import SimpleTestCase
from django.utils import timezone

from module2.analytics import (
    build_model_exposure_payload,
    build_threat_telemetry_payload,
    classify_telemetry_bucket,
    event_source,
)
from policy.constants import ACTION_BLOCK, ACTION_REDACT


class Module2AnalyticsTests(SimpleTestCase):
    def test_event_source_ueba(self):
        meta = {"key_prefix": "zs_abcd", "model": "gpt-4o"}
        self.assertEqual(event_source(meta), "ueba")

    def test_event_source_threat_intel(self):
        meta = {"source": "threat_intel", "threat_type": "threat_intel_injection"}
        self.assertEqual(event_source(meta), "threat_intel")

    def test_classify_injection_attempt(self):
        meta = {"threat_type": "prompt_injection"}
        self.assertEqual(classify_telemetry_bucket(meta, ACTION_BLOCK), "injection_attempts")

    def test_classify_pii_leak(self):
        meta = {"threat_type": "pii_ssn", "category": "pii"}
        self.assertEqual(classify_telemetry_bucket(meta, ACTION_REDACT), "pii_leaks")

    def test_build_model_exposure_payload(self):
        events = [
            {"action": ACTION_BLOCK, "metadata": {"model": "gpt-4o", "key_prefix": "zs_1", "latency_ms": 100}},
            {"action": ACTION_REDACT, "metadata": {"model": "gpt-4o", "key_prefix": "zs_1"}},
            {"action": ACTION_BLOCK, "metadata": {"model": "claude-3", "latency_ms": 300}},
        ]
        payload = build_model_exposure_payload(events, {"gpt-4o": "openai"}, "24h")
        self.assertEqual(payload["summary"]["active_models"], 2)
        self.assertEqual(len(payload["models"]), 2)
        self.assertEqual(payload["models"][0]["model"], "claude-3")
        self.assertIn("exposure_by_model", payload)
        self.assertIn(payload["exposure_by_model"][0]["model"], {"gpt-4o", "claude-3"})

    def test_build_threat_telemetry_payload(self):
        now = timezone.now()
        events = [
            {
                "created_at": now,
                "action": ACTION_BLOCK,
                "metadata": {"threat_type": "prompt_injection", "owasp_code": "LLM01"},
            },
            {
                "created_at": now - timedelta(hours=2),
                "action": ACTION_REDACT,
                "metadata": {"threat_type": "pii_ssn", "category": "pii", "owasp_code": "LLM06"},
            },
            {
                "created_at": now - timedelta(hours=1),
                "action": ACTION_BLOCK,
                "metadata": {"key_prefix": "zs_abcd", "threat_type": "velocity_spike"},
            },
        ]
        since = now - timedelta(hours=24)
        payload = build_threat_telemetry_payload(events, "24h", since)
        self.assertEqual(payload["summary"]["total_events"], 3)
        self.assertGreaterEqual(payload["summary"]["injection_attempts"], 1)
        self.assertGreaterEqual(payload["summary"]["pii_leaks"], 1)
        self.assertGreaterEqual(payload["summary"]["behavior_scoring_events"], 1)
        self.assertTrue(payload["timeline"])
        self.assertTrue(payload["top_attack_vectors"])
