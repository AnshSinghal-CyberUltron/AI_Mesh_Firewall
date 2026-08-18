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


class PlatformModelGuardTests(unittest.TestCase):
    """Platform guard/scanner models must never be routable through LiteLLM BYOK.

    The rest of this module previously covered the Bedrock routing ADJUDICATOR, which
    has been deleted — routing is now fully deterministic (no LLM). Its behavioural
    coverage moved to test_deterministic_routing_matrix.py.
    """

    def test_platform_model_rejected_from_litellm_resolve(self):
        router = LLMRouter({"org_only_inference": True})
        router._active_model_names = ["org-haiku"]
        self.assertTrue(is_platform_model_name("zeroshield-guard-120b"))
        with self.assertRaises(ValueError):
            router._resolve_runtime_model("zeroshield-guard-120b")


if __name__ == "__main__":
    unittest.main()
