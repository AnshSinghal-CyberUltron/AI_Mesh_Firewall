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
from ai_mesh_gateway.platform_models import DEFAULT_HAIKU_45, is_platform_model_name


class BedrockRoutingAdjudicationTests(unittest.TestCase):
    def setUp(self):
        # These tests exercise the Bedrock ADJUDICATOR path specifically. The H5
        # perf fastpath (llm_router.py:1807) short-circuits the adjudicator for a
        # single-candidate / low-risk request, so without forcing adjudication
        # these would silently take the deterministic ``weighted_fastpath`` and the
        # adjudicator assertions (converse called, decision_source) would never be
        # exercised. Force the documented operator override so the adjudicator runs.
        self._prev_adj_always = os.environ.get("ROUTING_ADJUDICATOR_ALWAYS")
        os.environ["ROUTING_ADJUDICATOR_ALWAYS"] = "true"

    def tearDown(self):
        if self._prev_adj_always is None:
            os.environ.pop("ROUTING_ADJUDICATOR_ALWAYS", None)
        else:
            os.environ["ROUTING_ADJUDICATOR_ALWAYS"] = self._prev_adj_always

    def _mock_bedrock_converse(self, selected_model: str):
        mock_client = MagicMock()
        mock_client.converse.return_value = {
            "raw": {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "selected_model": selected_model,
                                    "reason": "Sensitive request should stay on the highest-governance target.",
                                    "policy_summary": "Compliance and risk outweighed the client preference.",
                                    "decision_factors": ["compliance", "risk"],
                                }
                            )
                        }
                    }
                ]
            },
            "tokens_in": 100,
            "tokens_out": 50,
            "elapsed_s": 0.2,
            "model_id": DEFAULT_HAIKU_45,
            "call_site": "adjudicator",
        }
        return mock_client

    def test_adjudicator_uses_bedrock_not_litellm(self):
        router = LLMRouter({"org_only_inference": True})
        router._active_model_names = ["org-haiku", "gpt-5.2"]

        async def forbidden_acompletion(*args, **kwargs):
            raise AssertionError("adjudicator must not call LiteLLM acompletion")

        router.acompletion = forbidden_acompletion
        mock_client = self._mock_bedrock_converse("org-haiku")

        with patch.dict(os.environ, {"BEDROCK_ADJUDICATOR_MODEL": DEFAULT_HAIKU_45, "ROUTING_ADJUDICATOR_ALWAYS": "true"}, clear=False):
            with patch("ai_mesh_gateway.bedrock_client.default_bedrock_client", return_value=mock_client):
                selection = asyncio.run(
                    router.adjudicate_model_selection(
                        routing_models=[
                            {
                                "model_name": "org-haiku",
                                "model_id": "anthropic/claude-haiku",
                                "is_active": True,
                                "compliance_tags": ["HIPAA", "GDPR"],
                                "data_sensitivity_level": "restricted",
                                "routing_priority": 90,
                                "latency_sla_ms": 900,
                                "cost_per_1k_input_tokens": 0.0008,
                                "risk_score": 0.05,
                            },
                            {
                                "model_name": "org-haiku-alt",
                                "model_id": "anthropic/claude-haiku-alt",
                                "is_active": True,
                                "compliance_tags": ["HIPAA", "GDPR"],
                                "data_sensitivity_level": "restricted",
                                "routing_priority": 70,
                                "latency_sla_ms": 1100,
                                "cost_per_1k_input_tokens": 0.0010,
                                "risk_score": 0.08,
                            },
                            {
                                "model_name": "gpt-5.2",
                                "model_id": "openai/gpt-5.2",
                                "is_active": True,
                                "compliance_tags": ["GDPR"],
                                "data_sensitivity_level": "internal",
                                "routing_priority": 40,
                                "latency_sla_ms": 500,
                                "cost_per_1k_input_tokens": 0.0003,
                                "risk_score": 0.2,
                            },
                        ],
                        request_messages=[{"role": "user", "content": "Summarize patient intake."}],
                        preferred_model="gpt-5.2",
                        required_compliance=["HIPAA"],
                        data_sensitivity="restricted",
                        request_risk_score=0.8,
                        adjudicator_model="zeroshield-guard-120b",
                    )
                )

        self.assertIsNotNone(selection)
        self.assertEqual(selection.model_name, "org-haiku")
        self.assertEqual(selection.decision_source, "policy_adjudicator")
        self.assertEqual(selection.evaluator_model, DEFAULT_HAIKU_45)
        mock_client.converse.assert_called_once()
        call_kwargs = mock_client.converse.call_args.kwargs
        self.assertEqual(call_kwargs["model"], DEFAULT_HAIKU_45)
        self.assertEqual(call_kwargs["call_site"], "adjudicator")

    def test_adjudicator_can_override_preferred_model_with_valid_candidate(self):
        router = LLMRouter({"org_only_inference": True})
        mock_client = self._mock_bedrock_converse("bedrock-gpt-oss-120b")

        with patch.dict(os.environ, {"ROUTING_ADJUDICATOR_ALWAYS": "true"}, clear=False):
            with patch("ai_mesh_gateway.bedrock_client.default_bedrock_client", return_value=mock_client):
                selection = asyncio.run(
                    router.adjudicate_model_selection(
                    routing_models=[
                        {
                            "model_name": "bedrock-gpt-oss-120b",
                            "model_id": "bedrock/openai.gpt-oss-120b-1:0",
                            "is_active": True,
                            "compliance_tags": ["HIPAA", "GDPR"],
                            "data_sensitivity_level": "restricted",
                            "routing_priority": 90,
                            "latency_sla_ms": 900,
                            "cost_per_1k_input_tokens": 0.0008,
                            "risk_score": 0.05,
                        },
                        {
                            "model_name": "bedrock-haiku-hipaa",
                            "model_id": "bedrock/anthropic.claude-haiku",
                            "is_active": True,
                            "compliance_tags": ["HIPAA", "GDPR"],
                            "data_sensitivity_level": "restricted",
                            "routing_priority": 60,
                            "latency_sla_ms": 700,
                            "cost_per_1k_input_tokens": 0.0005,
                            "risk_score": 0.1,
                        },
                        {
                            "model_name": "bedrock-llama-3.1-70b",
                            "model_id": "bedrock/meta.llama3-1-70b-instruct-v1:0",
                            "is_active": True,
                            "compliance_tags": ["GDPR"],
                            "data_sensitivity_level": "internal",
                            "routing_priority": 40,
                            "latency_sla_ms": 500,
                            "cost_per_1k_input_tokens": 0.0003,
                            "risk_score": 0.2,
                        },
                    ],
                    request_messages=[{"role": "user", "content": "Summarize this patient intake document."}],
                    preferred_model="bedrock-llama-3.1-70b",
                    required_compliance=["HIPAA"],
                    data_sensitivity="restricted",
                    request_risk_score=0.8,
                    estimated_tokens=1200,
                    latency_budget_ms=1200,
                    weights={"risk": 0.5, "cost": 0.1, "latency": 0.1, "priority": 0.3},
                )
            )

        self.assertIsNotNone(selection)
        self.assertEqual(selection.model_name, "bedrock-gpt-oss-120b")
        self.assertEqual(selection.decision_source, "policy_adjudicator")

    def test_adjudicator_falls_back_to_weighted_selection_on_invalid_output(self):
        router = LLMRouter({"org_only_inference": True})
        mock_client = MagicMock()
        mock_client.converse.return_value = {
            "raw": {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "selected_model": "non-existent-model",
                                    "reason": "invalid",
                                    "policy_summary": "invalid",
                                    "decision_factors": ["invalid"],
                                }
                            )
                        }
                    }
                ]
            },
        }

        with patch.dict(os.environ, {"ROUTING_ADJUDICATOR_ALWAYS": "true"}, clear=False):
            with patch("ai_mesh_gateway.bedrock_client.default_bedrock_client", return_value=mock_client):
                selection = asyncio.run(
                    router.adjudicate_model_selection(
                    routing_models=[
                        {
                            "model_name": "bedrock-gpt-oss-120b",
                            "model_id": "bedrock/openai.gpt-oss-120b-1:0",
                            "is_active": True,
                            "compliance_tags": ["SOC2"],
                            "data_sensitivity_level": "internal",
                            "routing_priority": 80,
                            "latency_sla_ms": 850,
                            "cost_per_1k_input_tokens": 0.0008,
                            "risk_score": 0.1,
                        },
                        {
                            "model_name": "bedrock-llama-alt",
                            "model_id": "bedrock/meta.llama-alt",
                            "is_active": True,
                            "compliance_tags": ["SOC2"],
                            "data_sensitivity_level": "internal",
                            "routing_priority": 40,
                            "latency_sla_ms": 1200,
                            "cost_per_1k_input_tokens": 0.0004,
                            "risk_score": 0.2,
                        },
                    ],
                    request_messages=[{"role": "user", "content": "Hello"}],
                    preferred_model="auto",
                    data_sensitivity="internal",
                )
            )

        self.assertIsNotNone(selection)
        self.assertEqual(selection.model_name, "bedrock-gpt-oss-120b")
        self.assertEqual(selection.decision_source, "weighted_fallback")

    def test_platform_model_rejected_from_litellm_resolve(self):
        router = LLMRouter({"org_only_inference": True})
        router._active_model_names = ["org-haiku"]
        self.assertTrue(is_platform_model_name("zeroshield-guard-120b"))
        with self.assertRaises(ValueError):
            router._resolve_runtime_model("zeroshield-guard-120b")


if __name__ == "__main__":
    unittest.main()
