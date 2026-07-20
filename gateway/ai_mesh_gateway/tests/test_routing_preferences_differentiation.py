"""Routing preference differentiation + sensitivity soft-fallback + remap honesty."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

if "litellm" not in sys.modules:
    fake_litellm = types.ModuleType("litellm")

    class _FakeRouter:
        def __init__(self, *args, **kwargs):
            self.model_list = kwargs.get("model_list", [])

    async def _unused_async_completion(*args, **kwargs):
        raise RuntimeError("litellm completion should be stubbed in tests")

    fake_litellm.Router = _FakeRouter
    fake_litellm.acompletion = _unused_async_completion
    fake_litellm.aembedding = _unused_async_completion
    fake_litellm.drop_params = True
    fake_litellm.request_timeout = 120
    fake_litellm.num_retries = 2
    fake_litellm.ssl_verify = True

    fake_exceptions = types.ModuleType("litellm.exceptions")
    for exc_name in (
        "APIConnectionError",
        "APIError",
        "AuthenticationError",
        "BadRequestError",
        "BudgetExceededError",
        "ContentPolicyViolationError",
        "ContextWindowExceededError",
        "NotFoundError",
        "RateLimitError",
        "ServiceUnavailableError",
        "Timeout",
    ):
        setattr(fake_exceptions, exc_name, type(exc_name, (Exception,), {}))

    sys.modules["litellm"] = fake_litellm
    sys.modules["litellm.exceptions"] = fake_exceptions

from ai_mesh_gateway.llm_router import LLMRouter
from ai_mesh_gateway.platform_models import DEFAULT_HAIKU_45


def _catalog() -> list[dict]:
    return [
        {
            "model_name": "cheap-free",
            "model_id": "prov/cheap-free",
            "is_active": True,
            "compliance_tags": [],
            "data_sensitivity_level": "public",
            "routing_priority": 40,
            "latency_sla_ms": 800,
            "cost_per_1k_input_tokens": 0.00001,
            "risk_score": 0.40,
        },
        {
            "model_name": "safe-internal",
            "model_id": "prov/safe-internal",
            "is_active": True,
            "compliance_tags": ["SOC2"],
            "data_sensitivity_level": "confidential",
            "routing_priority": 90,
            "latency_sla_ms": 4000,
            "cost_per_1k_input_tokens": 0.003,
            "risk_score": 0.05,
        },
        {
            "model_name": "fast-balanced",
            "model_id": "prov/fast-balanced",
            "is_active": True,
            "compliance_tags": [],
            "data_sensitivity_level": "internal",
            "routing_priority": 70,
            "latency_sla_ms": 900,
            "cost_per_1k_input_tokens": 0.0005,
            "risk_score": 0.18,
        },
        {
            "model_name": "paid-strong",
            "model_id": "prov/paid-strong",
            "is_active": True,
            "compliance_tags": [],
            "data_sensitivity_level": "internal",
            "routing_priority": 80,
            "latency_sla_ms": 6000,
            "cost_per_1k_input_tokens": 0.01,
            "risk_score": 0.12,
        },
    ]


class RoutingPreferencesDifferentiationTests(unittest.TestCase):
    def test_cost_biased_picks_cheapest(self):
        router = LLMRouter({"org_only_inference": True})
        sel = router.select_model(
            routing_models=_catalog(),
            weights={"risk": 0, "cost": 1, "latency": 0, "priority": 0},
        )
        self.assertIsNotNone(sel)
        self.assertEqual(sel.model_name, "cheap-free")

    def test_risk_biased_picks_safest(self):
        router = LLMRouter({"org_only_inference": True})
        sel = router.select_model(
            routing_models=_catalog(),
            weights={"risk": 1, "cost": 0, "latency": 0, "priority": 0},
            request_risk_score=0.5,
        )
        self.assertIsNotNone(sel)
        self.assertEqual(sel.model_name, "safe-internal")

    def test_latency_biased_picks_lowest_sla(self):
        router = LLMRouter({"org_only_inference": True})
        sel = router.select_model(
            routing_models=_catalog(),
            weights={"risk": 0, "cost": 0, "latency": 1, "priority": 0},
            latency_budget_ms=1000,
        )
        self.assertIsNotNone(sel)
        self.assertEqual(sel.model_name, "cheap-free")

    def test_sensitivity_confidential_soft_fallback_when_only_public(self):
        router = LLMRouter({"org_only_inference": True})
        public_only = [
            {
                "model_name": "public-a",
                "model_id": "a",
                "is_active": True,
                "data_sensitivity_level": "public",
                "routing_priority": 10,
                "latency_sla_ms": 1000,
                "cost_per_1k_input_tokens": 0.0,
                "risk_score": 0.2,
            },
            {
                "model_name": "public-b",
                "model_id": "b",
                "is_active": True,
                "data_sensitivity_level": "public",
                "routing_priority": 50,
                "latency_sla_ms": 2000,
                "cost_per_1k_input_tokens": 0.001,
                "risk_score": 0.1,
            },
        ]
        sel = router.select_model(
            routing_models=public_only,
            data_sensitivity="confidential",
            weights={"risk": 0, "cost": 0, "latency": 0, "priority": 1},
        )
        self.assertIsNotNone(sel)
        self.assertTrue(sel.sensitivity_fallback)
        self.assertIn("sensitivity_unsatisfiable_fallback", sel.decision_factors)
        self.assertEqual(sel.model_name, "public-b")

    def test_sensitivity_prefers_matching_model(self):
        router = LLMRouter({"org_only_inference": True})
        sel = router.select_model(
            routing_models=_catalog(),
            data_sensitivity="confidential",
            weights={"risk": 0.25, "cost": 0.25, "latency": 0.25, "priority": 0.25},
        )
        self.assertIsNotNone(sel)
        self.assertFalse(sel.sensitivity_fallback)
        self.assertEqual(sel.model_name, "safe-internal")

    def test_compliance_unsatisfiable_still_none(self):
        router = LLMRouter({"org_only_inference": True})
        sel = router.select_model(
            routing_models=_catalog(),
            required_compliance=["HIPAA"],
            data_sensitivity="public",
        )
        self.assertIsNone(sel)

    def test_weight_extreme_skips_adjudicator_and_honors_cost(self):
        router = LLMRouter({"org_only_inference": True})
        mock_client = MagicMock()
        with patch.dict(os.environ, {"ROUTING_ADJUDICATOR_ALWAYS": "true"}, clear=False):
            with patch(
                "ai_mesh_gateway.bedrock_client.default_bedrock_client",
                return_value=mock_client,
            ):
                selection = asyncio.run(
                    router.adjudicate_model_selection(
                        routing_models=_catalog(),
                        request_messages=[{"role": "user", "content": "hi"}],
                        request_risk_score=0.0,
                        weights={"risk": 0, "cost": 1, "latency": 0, "priority": 0},
                    )
                )
        self.assertIsNotNone(selection)
        self.assertEqual(selection.model_name, "cheap-free")
        self.assertEqual(selection.decision_source, "weighted_fastpath")
        self.assertTrue(
            any("weight_extreme" in str(f) for f in (selection.decision_factors or []))
        )
        mock_client.converse.assert_not_called()

    def test_adjudicator_always_invokes_on_balanced_weights(self):
        router = LLMRouter({"org_only_inference": True})
        router._active_model_names = ["cheap-free", "safe-internal"]
        mock_client = MagicMock()
        mock_client.converse.return_value = {
            "raw": {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "selected_model": "safe-internal",
                                    "reason": "Prefer safer model.",
                                    "policy_summary": "Balanced preference.",
                                    "decision_factors": ["risk"],
                                }
                            )
                        }
                    }
                ]
            },
            "tokens_in": 10,
            "tokens_out": 5,
            "elapsed_s": 0.1,
            "model_id": DEFAULT_HAIKU_45,
            "call_site": "adjudicator",
        }
        with patch.dict(
            os.environ,
            {"ROUTING_ADJUDICATOR_ALWAYS": "true", "BEDROCK_ADJUDICATOR_MODEL": DEFAULT_HAIKU_45},
            clear=False,
        ):
            with patch(
                "ai_mesh_gateway.bedrock_client.default_bedrock_client",
                return_value=mock_client,
            ):
                selection = asyncio.run(
                    router.adjudicate_model_selection(
                        routing_models=_catalog()[:2],
                        request_messages=[{"role": "user", "content": "hi"}],
                        request_risk_score=0.0,
                        weights={"risk": 0.4, "cost": 0.3, "latency": 0.2, "priority": 0.1},
                    )
                )
        self.assertIsNotNone(selection)
        self.assertEqual(selection.decision_source, "policy_adjudicator")
        mock_client.converse.assert_called_once()

    def test_adjudicator_skipped_single_candidate_even_when_always(self):
        router = LLMRouter({"org_only_inference": True})
        mock_client = MagicMock()
        with patch.dict(os.environ, {"ROUTING_ADJUDICATOR_ALWAYS": "true"}, clear=False):
            with patch(
                "ai_mesh_gateway.bedrock_client.default_bedrock_client",
                return_value=mock_client,
            ):
                selection = asyncio.run(
                    router.adjudicate_model_selection(
                        routing_models=_catalog()[:1],
                        request_messages=[{"role": "user", "content": "hi"}],
                        request_risk_score=0.0,
                    )
                )
        self.assertIsNotNone(selection)
        self.assertEqual(selection.decision_source, "weighted_fastpath")
        mock_client.converse.assert_not_called()

    def test_resolve_runtime_prefers_highest_scored_active(self):
        router = LLMRouter({"org_only_inference": True})
        router._active_model_names = ["safe-internal", "cheap-free"]
        from ai_mesh_gateway.llm_router import ModelSelection

        sel = ModelSelection(
            model_name="inactive-winner",
            model_id="inactive-winner",
            fallback_chain=["safe-internal", "cheap-free"],
            reason="picked inactive",
            decision_factors=[],
        )
        out = router.resolve_runtime_selection(sel)
        self.assertEqual(out.model_name, "safe-internal")
        self.assertEqual(out.remapped_from, "inactive-winner")
        self.assertIn("inactive_model_remapped", out.decision_factors)


if __name__ == "__main__":
    unittest.main()
