"""Tests for BYOK OpenAI-compatible LiteLLM param normalization."""

from __future__ import annotations

from django.test import SimpleTestCase

from ai_mesh_shared.litellm_byok import (
    is_openrouter_api_base,
    needs_openai_compatible_client,
    normalize_litellm_params,
    sanitize_api_base,
)


class LitellmByokTests(SimpleTestCase):
    def test_is_openrouter_api_base(self):
        self.assertTrue(is_openrouter_api_base("https://openrouter.ai/api/v1"))
        self.assertTrue(is_openrouter_api_base("https://openrouter.ai/api/v1/"))
        self.assertFalse(is_openrouter_api_base("https://api.openai.com/v1"))
        self.assertFalse(is_openrouter_api_base(""))

    def test_sanitize_api_base_strips_chat_completions_suffix(self):
        self.assertEqual(
            sanitize_api_base("https://openrouter.ai/api/v1/chat/completions"),
            "https://openrouter.ai/api/v1",
        )

    def test_openrouter_custom_model_gets_openai_compat_provider(self):
        params = normalize_litellm_params(
            {
                "model": "anthropic/claude-haiku-4.5",
                "api_base": "https://openrouter.ai/api/v1/chat/completions",
                "api_key_encrypted": "enc",
            },
            provider="custom",
        )
        self.assertEqual(params["api_base"], "https://openrouter.ai/api/v1")
        self.assertEqual(params["custom_llm_provider"], "openai")
        self.assertEqual(params["model"], "anthropic/claude-haiku-4.5")

    def test_native_anthropic_without_api_base_unchanged(self):
        params = normalize_litellm_params(
            {"model": "anthropic/claude-3-5-haiku-20241022"},
            provider="anthropic",
        )
        self.assertNotIn("custom_llm_provider", params)

    def test_custom_together_endpoint_gets_openai_compat(self):
        self.assertTrue(
            needs_openai_compatible_client(
                provider="custom",
                api_base="https://api.together.xyz/v1",
            )
        )
        params = normalize_litellm_params(
            {
                "model": "meta-llama/Llama-3-8b-chat-hf",
                "api_base": "https://api.together.xyz/v1",
            },
            provider="custom",
        )
        self.assertEqual(params["custom_llm_provider"], "openai")
