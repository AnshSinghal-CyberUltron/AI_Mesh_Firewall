"""Unit tests for differentiated routing catalog profiles (no Redis)."""

from __future__ import annotations

from decimal import Decimal

from django.test import SimpleTestCase

from core.routing_catalog import (
    differentiate_model_defaults,
    resolve_routing_profile,
)


class RoutingCatalogTests(SimpleTestCase):
    def test_north_mini_is_cheap_fast(self):
        self.assertEqual(
            resolve_routing_profile("cohere/north-mini-code:free"),
            "cheap_fast",
        )

    def test_claude_is_safe_sensitive(self):
        self.assertEqual(resolve_routing_profile("anthropic/claude-sonnet-4"), "safe_sensitive")

    def test_gpt5_is_paid_strong(self):
        self.assertEqual(resolve_routing_profile("openai/gpt-5.2"), "paid_strong")

    def test_lanes_differ_on_cost_and_sensitivity(self):
        cheap = differentiate_model_defaults("cohere/north-mini-code:free")
        safe = differentiate_model_defaults("anthropic/claude-sonnet-4")
        paid = differentiate_model_defaults("openai/gpt-5.2")
        self.assertLess(cheap["cost_per_1k_input_tokens"], paid["cost_per_1k_input_tokens"])
        self.assertLess(cheap["latency_sla_ms"], safe["latency_sla_ms"])
        self.assertGreater(float(safe["risk_score"]), -0.01)
        self.assertLess(float(safe["risk_score"]), float(cheap["risk_score"]))
        self.assertEqual(cheap["data_sensitivity_level"], "public")
        self.assertEqual(safe["data_sensitivity_level"], "confidential")
        self.assertIsInstance(cheap["cost_per_1k_input_tokens"], Decimal)

    def test_idempotent_same_name(self):
        a = differentiate_model_defaults("cohere/north-mini-code:free")
        b = differentiate_model_defaults("cohere/north-mini-code:free")
        a.pop("_profile", None)
        b.pop("_profile", None)
        self.assertEqual(a, b)

    def test_same_lane_models_not_identical(self):
        a = differentiate_model_defaults("provider/alpha-free:free")
        b = differentiate_model_defaults("provider/beta-free:free")
        self.assertNotEqual(
            a["cost_per_1k_input_tokens"],
            b["cost_per_1k_input_tokens"],
        )
