"""TIER2_PROVIDER factory + Gemini payload shape + scanner model selection."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_default_tier2_scanner_model_gemini(monkeypatch):
    monkeypatch.setenv("TIER2_PROVIDER", "gemini")
    monkeypatch.setenv("VERTEX_TIER2_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setenv("BEDROCK_MODEL", "global.anthropic.claude-haiku-4-5-20251001-v1:0")
    from platform_models import default_tier2_scanner_model

    assert default_tier2_scanner_model() == "gemini-3.5-flash-lite"


def test_default_tier2_scanner_model_gemini_unset_vertex(monkeypatch):
    monkeypatch.setenv("TIER2_PROVIDER", "gemini")
    monkeypatch.delenv("VERTEX_TIER2_MODEL", raising=False)
    monkeypatch.setenv("BEDROCK_MODEL", "global.anthropic.claude-haiku-4-5-20251001-v1:0")
    from platform_models import default_tier2_scanner_model

    assert default_tier2_scanner_model() == "gemini-3.5-flash-lite"


def test_default_tier2_scanner_model_bedrock_when_unset(monkeypatch):
    monkeypatch.delenv("TIER2_PROVIDER", raising=False)
    monkeypatch.setenv("BEDROCK_MODEL", "global.anthropic.claude-haiku-4-5-20251001-v1:0")
    monkeypatch.delenv("BEDROCK_TIER2_SCANNER_MODEL", raising=False)
    monkeypatch.delenv("VERTEX_TIER2_MODEL", raising=False)
    from platform_models import default_tier2_scanner_model

    assert default_tier2_scanner_model() == "global.anthropic.claude-haiku-4-5-20251001-v1:0"


def test_factory_selects_gemini_client(monkeypatch):
    monkeypatch.setenv("TIER2_PROVIDER", "gemini")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-not-a-real-key")
    fake = MagicMock()
    with patch("ai_mesh_shared.tier2_gemini_client._new_genai_client", return_value=fake):
        from bedrock_scanner import default_tier2_client

        client = default_tier2_client()
    assert type(client).__name__ == "GeminiTier2Client"


@pytest.mark.parametrize("provider", ["google", "google_genai", "vertex", "vertex_ai"])
def test_factory_aliases_select_gemini(monkeypatch, provider):
    monkeypatch.setenv("TIER2_PROVIDER", provider)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-not-a-real-key")
    fake = MagicMock()
    with patch("ai_mesh_shared.tier2_gemini_client._new_genai_client", return_value=fake):
        from bedrock_scanner import default_tier2_client

        assert type(default_tier2_client()).__name__ == "GeminiTier2Client"


def test_factory_defaults_to_bedrock(monkeypatch):
    monkeypatch.delenv("TIER2_PROVIDER", raising=False)
    sentinel = object()
    with patch("bedrock_scanner.default_bedrock_client", return_value=sentinel):
        from bedrock_scanner import default_tier2_client

        assert default_tier2_client() is sentinel


def test_factory_bedrock_warns_once(monkeypatch, caplog):
    monkeypatch.setenv("TIER2_PROVIDER", "bedrock")
    import bedrock_scanner as bs

    bs.reset_tier2_factory_for_tests()
    sentinel = object()
    with patch("bedrock_scanner.default_bedrock_client", return_value=sentinel):
        with caplog.at_level("WARNING"):
            bs.default_tier2_client()
            bs.default_tier2_client()
    warnings = [r for r in caplog.records if "TIER2_PROVIDER=bedrock" in r.getMessage()]
    assert len(warnings) == 1


def test_build_payload_gemini_is_openai_shaped(monkeypatch):
    monkeypatch.setenv("TIER2_PROVIDER", "gemini")
    from bedrock_scanner import BedrockScanner

    scanner = BedrockScanner(client=MagicMock(), model="gemini-3.5-flash-lite")
    payload = scanner._build_payload("TEXT TO ANALYZE", 256)
    assert "anthropic_version" not in payload
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1]["role"] == "user"
    assert payload["max_tokens"] == 256
    assert payload["temperature"] == 0.0
