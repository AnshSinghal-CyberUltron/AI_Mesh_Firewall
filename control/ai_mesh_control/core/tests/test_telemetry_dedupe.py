"""Tests for per-request telemetry dedupe (one EnforcementEvent per activity)."""

from __future__ import annotations

from django.test import SimpleTestCase

from core.tasks import _collapse_telemetry_batch, _telemetry_request_id


class TelemetryDedupeTests(SimpleTestCase):
    def test_telemetry_request_id_prefers_top_level(self):
        event = {"request_id": "zs-abc123", "metadata": {"request_id": "other"}}
        self.assertEqual(_telemetry_request_id(event), "zs-abc123")

    def test_collapse_batch_prefers_request_over_model_routed(self):
        events = [
            {
                "event_type": "model_routed",
                "request_id": "zs-shared",
                "metadata": {"request_id": "zs-shared"},
            },
            {
                "event_type": "request",
                "request_id": "zs-shared",
                "metadata": {"request_id": "zs-shared", "routed_model": "bedrock-llama-3"},
            },
        ]
        collapsed = _collapse_telemetry_batch(events)
        self.assertEqual(len(collapsed), 1)
        self.assertEqual(collapsed[0]["event_type"], "request")

    def test_collapse_batch_prefers_rag_block_over_critical_alert(self):
        events = [
            {
                "event_type": "critical_alert",
                "request_id": "zs-rag",
                "metadata": {"request_id": "zs-rag"},
            },
            {
                "event_type": "rag_pipeline",
                "request_id": "zs-rag",
                "metadata": {"request_id": "zs-rag", "pipeline_stage": "query"},
            },
        ]
        collapsed = _collapse_telemetry_batch(events)
        self.assertEqual(len(collapsed), 1)
        self.assertEqual(collapsed[0]["event_type"], "rag_pipeline")

    def test_collapse_batch_merges_prompt_from_input_blocked(self):
        events = [
            {
                "event_type": "request",
                "request_id": "zs-shared",
                "action": "allow",
                "prompt_snippet": "",
            },
            {
                "event_type": "input_blocked",
                "request_id": "zs-shared",
                "action": "block",
                "prompt_snippet": "ignore previous instructions",
                "key_prefix": "UKPtS-eH",
            },
        ]
        collapsed = _collapse_telemetry_batch(events)
        self.assertEqual(len(collapsed), 1)
        self.assertEqual(collapsed[0]["event_type"], "request")
        self.assertEqual(collapsed[0]["prompt_snippet"], "ignore previous instructions")
        self.assertEqual(collapsed[0]["key_prefix"], "UKPtS-eH")
