"""Unit tests for firewall_module_classifier — aligned with frontend MODULE_FILTERS."""

from __future__ import annotations

from django.test import SimpleTestCase

from policy.firewall_module_classifier import (
    MODULE_PRESSURE_METRIC,
    empty_bucket,
    event_matches_module,
    increment_bucket,
    module_enforcement_q,
    specialty_modules_for_event,
)


class FirewallModuleClassifierTests(SimpleTestCase):
    def test_rag_pipeline_matches_1_2_and_1_3(self):
        meta = {"event_type": "rag_pipeline", "source": "rag"}
        self.assertTrue(event_matches_module("1.2", meta))
        self.assertTrue(event_matches_module("1.3", meta))
        modules = specialty_modules_for_event(meta)
        self.assertIn("1.2", modules)
        self.assertIn("1.3", modules)

    def test_vector_query_matches_1_3_not_by_threat_type_only(self):
        meta = {"event_type": "vector_query", "source": "vector"}
        self.assertTrue(event_matches_module("1.3", meta))
        self.assertFalse(event_matches_module("1.2", meta))

    def test_embedding_request_matches_1_3(self):
        meta = {"event_type": "embedding_request", "source": "vector"}
        self.assertTrue(event_matches_module("1.3", meta))

    def test_rag_poisoning_owasp_matches_1_2_and_1_3(self):
        meta = {"threat_type": "rag_poisoning", "owasp_code": "LLM08"}
        self.assertTrue(event_matches_module("1.2", meta))
        self.assertTrue(event_matches_module("1.3", meta))

    def test_mcp_scan_matches_1_4(self):
        meta = {"source": "mcp_scan", "event_type": "tool_call"}
        self.assertTrue(event_matches_module("1.4", meta))
        self.assertIn("1.4", specialty_modules_for_event(meta, source="mcp_scan"))

    def test_routing_matches_1_5(self):
        meta = {"source": "routing", "event_type": "model_routed"}
        self.assertTrue(event_matches_module("1.5", meta))

    def test_kill_switch_delegates_to_module_16(self):
        meta = {
            "event_type": "kill_switch",
            "source": "policy",
            "module_id": "1.6",
            "security_risk_score": 60,
        }
        self.assertTrue(event_matches_module("1.6", meta))

    def test_output_guard_matches_1_7(self):
        meta = {"event_type": "output_guard", "source": "output"}
        self.assertTrue(event_matches_module("1.7", meta))

    def test_module_1_1_matches_all_events(self):
        meta = {"event_type": "request"}
        self.assertTrue(event_matches_module("1.1", meta))

    def test_pressure_metric_mapping(self):
        self.assertEqual(MODULE_PRESSURE_METRIC["1.4"], "redacted")
        self.assertEqual(MODULE_PRESSURE_METRIC["1.6"], "critical")
        self.assertEqual(MODULE_PRESSURE_METRIC["1.3"], "blocked")

    def test_module_enforcement_q_1_3_includes_event_types(self):
        q = module_enforcement_q("1.3")
        self.assertIn("metadata__event_type", str(q))

    def test_increment_bucket_accepts_group_count(self):
        bucket = empty_bucket()
        increment_bucket(
            bucket,
            is_blocked=True,
            is_redacted=False,
            is_critical=True,
            is_flagged=True,
            n=4,
        )
        self.assertEqual(bucket["total"], 4)
        self.assertEqual(bucket["blocked"], 4)
        self.assertEqual(bucket["critical"], 4)
        self.assertEqual(bucket["flagged"], 4)
