"""Routing pool hardening: selector pool must match router-serviceable pool."""

from __future__ import annotations

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

from ai_mesh_gateway import main as gateway_main
from ai_mesh_gateway.llm_router import LLMRouter
from ai_mesh_gateway.platform_models import is_platform_model_name


class RoutingPoolHardeningTests(unittest.TestCase):
    def test_partition_drops_reserved_bedrock_foundation_id(self):
        routing_models = [
            {
                "model_name": "bedrock-llama-3",
                "model_id": "meta.llama3-70b-instruct-v1:0",
                "provider": "aws_bedrock",
                "is_active": True,
                "api_key_set": True,
            },
            {
                "model_name": "gpt-5.2",
                "model_id": "openai/gpt-5.2",
                "provider": "openai",
                "is_active": True,
                "api_key_set": True,
            },
        ]
        eligible, reserved = gateway_main._partition_inference_eligible_models(routing_models)
        self.assertEqual([m["model_name"] for m in eligible], ["gpt-5.2"])
        self.assertEqual(reserved, ["bedrock-llama-3"])
        self.assertTrue(is_platform_model_name("meta.llama3-70b-instruct-v1:0"))

    def test_filter_router_serviceable_models_intersects_active_names(self):
        models = [
            {"model_name": "gpt-5.2", "model_id": "openai/gpt-5.2"},
            {"model_name": "local-codellama", "model_id": "ollama/codellama"},
        ]
        serviceable, excluded = gateway_main._filter_router_serviceable_models(
            models,
            ["gpt-5.2"],
        )
        self.assertEqual([m["model_name"] for m in serviceable], ["gpt-5.2"])
        self.assertEqual(excluded, ["local-codellama"])

    def test_select_model_never_returns_model_outside_active_router_pool(self):
        router = LLMRouter({"org_only_inference": True})
        router._active_model_names = ["gpt-5.2"]
        routing_models = [
            {
                "model_name": "bedrock-llama-3",
                "model_id": "meta.llama3-70b-instruct-v1:0",
                "provider": "aws_bedrock",
                "is_active": True,
                "api_key_set": True,
                "routing_priority": 90,
            },
            {
                "model_name": "gpt-5.2",
                "model_id": "openai/gpt-5.2",
                "provider": "openai",
                "is_active": True,
                "api_key_set": True,
                "routing_priority": 10,
            },
        ]
        eligible, _ = gateway_main._partition_inference_eligible_models(routing_models)
        serviceable, _ = gateway_main._filter_router_serviceable_models(
            eligible,
            router.get_active_model_names(),
        )
        selection = router.select_model(routing_models=serviceable)
        self.assertIsNotNone(selection)
        self.assertEqual(selection.model_name, "gpt-5.2")
        self.assertIn(selection.model_name, router.get_active_model_names())

    def test_remap_telemetry_hook_invoked_on_runtime_remap(self):
        router = LLMRouter({"org_only_inference": True, "litellm_default_model": "gpt-5.2"})
        router._active_model_names = ["gpt-5.2"]
        seen: dict = {}

        def _hook(**kwargs):
            seen.update(kwargs)

        router.set_remap_telemetry_hook(_hook)
        kwargs = router._build_kwargs(
            {"model": "bedrock-llama-3", "messages": [{"role": "user", "content": "hi"}]},
            stream=False,
        )
        self.assertEqual(kwargs["model"], "gpt-5.2")
        self.assertEqual(seen.get("requested_model"), "bedrock-llama-3")
        self.assertEqual(seen.get("resolved_model"), "gpt-5.2")

    def test_acompletion_retries_compliant_chain_on_api_connection_error(self):
        router = LLMRouter({"org_only_inference": True})
        router._active_model_names = ["dead-ollama", "gpt-5.2"]
        router._qualified_model_names = {"gpt-5.2"}

        primary_exc = sys.modules["litellm.exceptions"].APIConnectionError("connection refused")
        success = MagicMock()
        success.model_dump.return_value = {"choices": [{"message": {"content": "ok"}}]}

        import asyncio

        async def _run():
            return await router.acompletion(
                {
                    "model": "dead-ollama",
                    "messages": [{"role": "user", "content": "hi"}],
                    "_inference_allowlist": ["dead-ollama", "gpt-5.2"],
                    "_compliant_fallback_chain": ["gpt-5.2"],
                }
            )

        with patch.object(router, "_execute_completion", side_effect=[primary_exc, success]) as mock_exec:
            status, payload = asyncio.run(_run())
        self.assertEqual(status, 200)
        self.assertEqual(mock_exec.call_count, 2)


if __name__ == "__main__":
    unittest.main()
