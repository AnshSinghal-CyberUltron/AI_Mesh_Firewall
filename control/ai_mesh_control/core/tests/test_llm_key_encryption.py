"""Tests for build_litellm_entry encrypted Redis payloads."""

from __future__ import annotations

from django.test import TestCase

from core.models import LLMModelConfig


class BuildLitellmEntryTests(TestCase):
    def test_build_litellm_entry_uses_encrypted_key_not_plaintext(self):
        cfg = LLMModelConfig(
            provider="openai",
            model_name="gpt-4o",
            model_id="openai/gpt-4o",
            encrypted_api_key="enc-blob-xyz",
        )
        entry = cfg.build_litellm_entry()
        params = entry["litellm_params"]
        self.assertIn("api_key_encrypted", params)
        self.assertEqual(params["api_key_encrypted"], "enc-blob-xyz")
        self.assertTrue(
            "api_key" not in params or params.get("api_key", "").startswith("os.environ/")
        )

    def test_build_litellm_entry_openrouter_sets_openai_compat(self):
        cfg = LLMModelConfig(
            provider="custom",
            model_name="anthropic/claude-haiku-4.5",
            model_id="anthropic/claude-haiku-4.5",
            api_base="https://openrouter.ai/api/v1",
            encrypted_api_key="enc-blob-xyz",
        )
        entry = cfg.build_litellm_entry()
        params = entry["litellm_params"]
        self.assertEqual(params["custom_llm_provider"], "openai")
        self.assertEqual(entry["provider"], "custom")
