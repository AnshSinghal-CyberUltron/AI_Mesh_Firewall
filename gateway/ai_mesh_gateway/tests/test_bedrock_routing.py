import asyncio
import json
import sys
import types
import unittest


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

try:
    from gateway.llm_router import LLMRouter
except ModuleNotFoundError:
    from llm_router import LLMRouter


class BedrockRoutingAdjudicationTests(unittest.TestCase):
    def test_adjudicator_can_override_preferred_model_with_valid_candidate(self):
        router = LLMRouter({"litellm_config_path": ""})

        async def fake_acompletion(body, redacted_content=None):
            return 200, {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "selected_model": "bedrock-gpt-oss-120b",
                                    "reason": "Sensitive request should stay on the highest-governance Bedrock target.",
                                    "policy_summary": "Compliance and risk outweighed the client preference.",
                                    "decision_factors": ["compliance", "risk"],
                                }
                            )
                        }
                    }
                ]
            }

        router.acompletion = fake_acompletion
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
                adjudicator_model="bedrock-gpt-oss-120b",
            )
        )

        self.assertIsNotNone(selection)
        self.assertEqual(selection.model_name, "bedrock-gpt-oss-120b")
        self.assertEqual(selection.decision_source, "bedrock_adjudicator")
        self.assertEqual(selection.evaluator_model, "bedrock-gpt-oss-120b")

    def test_adjudicator_falls_back_to_weighted_selection_on_invalid_output(self):
        router = LLMRouter({"litellm_config_path": ""})

        async def fake_acompletion(body, redacted_content=None):
            return 200, {
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
            }

        router.acompletion = fake_acompletion
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
                    }
                ],
                request_messages=[{"role": "user", "content": "Hello"}],
                preferred_model="auto",
                data_sensitivity="internal",
            )
        )

        self.assertIsNotNone(selection)
        self.assertEqual(selection.model_name, "bedrock-gpt-oss-120b")
        self.assertEqual(selection.decision_source, "weighted_fallback")


if __name__ == "__main__":
    unittest.main()