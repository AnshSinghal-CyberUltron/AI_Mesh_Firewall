"""Control BedrockScanner uses the Gemini client when TIER2_PROVIDER=gemini."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from security_engines.bedrock_scanner import BedrockScanner


def test_control_scanner_uses_gemini_client_when_provider_gemini(monkeypatch):
    monkeypatch.setenv("TIER2_PROVIDER", "gemini")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-not-a-real-key")
    fake = MagicMock(name="gemini_client")
    with patch(
        "security_engines.bedrock_scanner.default_tier2_client",
        return_value=fake,
    ):
        scanner = BedrockScanner()
    assert scanner.client is fake
