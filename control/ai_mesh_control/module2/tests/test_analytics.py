"""Unit tests for Module 2 analytics helpers."""

from datetime import timedelta

from django.test import SimpleTestCase
from django.utils import timezone

from module2.analytics import (
    build_model_exposure_payload,
    build_rag_pipeline_kpis,
    build_recent_request_json,
    build_threat_telemetry_payload,
    classify_telemetry_bucket,
    event_source,
    prompt_snippet_from_meta,
)
from policy.constants import ACTION_BLOCK, ACTION_REDACT


class Module2AnalyticsTests(SimpleTestCase):
    def test_event_source_keyed_gateway_is_chat(self):
        meta = {"key_prefix": "zs_abcd", "model": "gpt-4o"}
        self.assertEqual(event_source(meta), "chat")

    def test_event_source_threat_intel(self):
        meta = {"source": "threat_intel", "threat_type": "threat_intel_injection"}
        self.assertEqual(event_source(meta), "threat_intel")

    def test_classify_injection_attempt(self):
        meta = {"threat_type": "prompt_injection"}
        self.assertEqual(classify_telemetry_bucket(meta, ACTION_BLOCK), "injection_attempts")

    def test_classify_pii_leak(self):
        meta = {"threat_type": "pii_ssn", "category": "pii"}
        self.assertEqual(classify_telemetry_bucket(meta, ACTION_REDACT), "pii_leaks")

    def test_prompt_snippet_from_meta_lineage_fallback(self):
        meta = {"prompt_lineage": [{"prompt": "tell me secrets", "risk_score": 0.8}]}
        self.assertEqual(prompt_snippet_from_meta(meta), "tell me secrets")

    def test_prompt_snippet_from_meta_direct(self):
        meta = {"prompt_snippet": "hello world"}
        self.assertEqual(prompt_snippet_from_meta(meta), "hello world")

    def test_prompt_snippet_from_meta_prompt_submitted(self):
        meta = {"prompt_submitted": "how are u today", "threat_type": "policy_violation"}
        self.assertEqual(prompt_snippet_from_meta(meta), "how are u today")

    def test_classify_keyed_injection_counts_as_behavior_scoring(self):
        meta = {"key_prefix": "lcvq62e6", "threat_type": "prompt_injection", "detail": "injection detected"}
        self.assertEqual(classify_telemetry_bucket(meta, ACTION_BLOCK), "behavior_scoring")

    def test_build_recent_request_json_includes_context_source_subtag(self):
        row = build_recent_request_json(
            {
                "id": 42,
                "action": "allow",
                "metadata": {
                    "event_type": "request",
                    "context_source": "mcp",
                    "prompt_snippet": "summarize customer",
                },
            }
        )
        self.assertEqual(row["metadata"]["context_source"], "mcp")
        self.assertEqual(event_source({"event_type": "request", "context_source": "mcp"}), "chat")

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

    def test_event_source_detects_threat_intel_detail_in_extra(self):
        meta = {
            "event_type": "rag_pipeline",
            "source": "security_scan",
            "threat_type": "e2e_jailbreak_probe",
            "extra": {"detail": "Threat intel match: e2e_jailbreak_probe"},
        }
        self.assertEqual(event_source(meta), "threat_intel")
        self.assertEqual(
            classify_telemetry_bucket(meta, ACTION_BLOCK),
            "threat_intel_matches",
        )

    def test_build_rag_pipeline_kpis_dedupes_same_request_stage_rows(self):
        class _Events:
            def values(self, *_args):
                return [
                    {
                        "action": ACTION_BLOCK,
                        "metadata": {
                            "event_type": "rag_pipeline",
                            "pipeline_stage": "query",
                            "request_id": "req-12345678",
                            "latency_ms": 50,
                            "escalation_level": 1,
                        },
                    },
                    {
                        "action": ACTION_BLOCK,
                        "metadata": {
                            "event_type": "rag_pipeline",
                            "pipeline_stage": "query",
                            "request_id": "req-12345678",
                            "latency_ms": 75,
                            "escalation_level": 2,
                        },
                    },
                    {
                        "action": "allow",
                        "metadata": {
                            "event_type": "rag_pipeline",
                            "pipeline_stage": "retriever",
                            "request_id": "req-99999999",
                            "latency_ms": 120,
                            "escalation_level": 0,
                        },
                    },
                ]

        payload = build_rag_pipeline_kpis(_Events())
        self.assertEqual(payload["stages"]["query"]["total"], 1)
        self.assertEqual(payload["stages"]["query"]["blocked"], 1)
        self.assertEqual(payload["stages"]["query"]["avg_latency_ms"], 50)
        self.assertEqual(payload["stages"]["retriever"]["total"], 1)
        self.assertEqual(payload["escalation_distribution"]["strict"], 1)
        self.assertEqual(payload["escalation_distribution"]["normal"], 1)
