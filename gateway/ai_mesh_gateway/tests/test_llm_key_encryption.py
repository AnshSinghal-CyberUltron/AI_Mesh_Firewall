"""Tests for encrypted Redis LLM config payloads (gateway decrypt path)."""

from __future__ import annotations

import unittest
from unittest.mock import patch


class GatewayLitellmReloadTests(unittest.TestCase):
    def test_gateway_prepare_reload_entry_decrypts_encrypted_key(self):
        from ai_mesh_gateway.llm_router import LLMRouter

        with patch("ai_mesh_gateway.llm_router.decrypt_api_key", return_value="sk-test") as mock_decrypt:
            entry = {
                "model_name": "gpt-4o",
                "litellm_params": {
                    "model": "openai/gpt-4o",
                    "api_key_encrypted": "enc-blob",
                },
            }
            prepared = LLMRouter._prepare_reload_entry(entry)
            mock_decrypt.assert_called_once()
            self.assertEqual(prepared["litellm_params"]["api_key"], "sk-test")
            self.assertNotIn("api_key_encrypted", prepared["litellm_params"])

    def test_gateway_prepare_reload_entry_openrouter_openai_compat(self):
        from ai_mesh_gateway.llm_router import LLMRouter

        entry = {
            "model_name": "anthropic/claude-haiku-4.5",
            "provider": "custom",
            "litellm_params": {
                "model": "anthropic/claude-haiku-4.5",
                "api_base": "https://openrouter.ai/api/v1",
                "api_key": "sk-or-test",
            },
        }
        prepared = LLMRouter._prepare_reload_entry(entry)
        self.assertEqual(prepared["litellm_params"]["custom_llm_provider"], "openai")

    def test_gateway_prepare_reload_entry_maps_bedrock_iam_credentials(self):
        from ai_mesh_gateway.llm_router import LLMRouter

        with patch("ai_mesh_gateway.llm_router.decrypt_api_key", return_value="AKIAEXAMPLE1234567"):
            with patch.dict(
                "os.environ",
                {
                    "AWS_SECRET_ACCESS_KEY": "secret-from-env",
                    "BEDROCK_REGION": "ap-south-1",
                },
                clear=False,
            ):
                entry = {
                    "model_name": "bedrock-llama-3",
                    "provider": "aws_bedrock",
                    "litellm_params": {
                        "model": "bedrock/meta.llama3-1-70b-instruct-v1:0",
                        "api_key_encrypted": "enc-blob",
                    },
                }
                prepared = LLMRouter._prepare_reload_entry(entry)
                params = prepared["litellm_params"]
                self.assertEqual(params["aws_access_key_id"], "AKIAEXAMPLE1234567")
                self.assertEqual(params["aws_secret_access_key"], "secret-from-env")
                self.assertEqual(params["aws_region_name"], "ap-south-1")
                self.assertNotIn("api_key", params)

    def test_gateway_prepare_reload_entry_bedrock_env_credentials_without_byok(self):
        from ai_mesh_gateway.llm_router import LLMRouter

        with patch.dict(
            "os.environ",
            {
                "AWS_ACCESS_KEY_ID": "AKIAFROMENV123456",
                "AWS_SECRET_ACCESS_KEY": "secret-from-env",
                "BEDROCK_REGION": "ap-south-1",
            },
            clear=False,
        ):
                entry = {
                    "model_name": "bedrock-llama-3",
                    "provider": "aws_bedrock",
                    "litellm_params": {
                        "model": "bedrock/meta.llama3-1-70b-instruct-v1:0",
                        "aws_region_name": "ap-south-1",
                    },
                }
                prepared = LLMRouter._prepare_reload_entry(entry)
                params = prepared["litellm_params"]
                self.assertEqual(params["aws_access_key_id"], "AKIAFROMENV123456")
                self.assertEqual(params["aws_secret_access_key"], "secret-from-env")
                self.assertEqual(params["aws_region_name"], "ap-south-1")
                self.assertEqual(params["model"], "bedrock/meta.llama3-70b-instruct-v1:0")
                self.assertNotIn("api_key", params)


if __name__ == "__main__":
    unittest.main()
