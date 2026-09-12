"""Tier-2 Gemini Developer API client — mocked generate_content, no live key.

PIPELINE-0033: ascan_prompt returns Bedrock/OpenAI-shaped ``raw.choices[0].message.content``
so BedrockScanner._extract_content stays unchanged. thoughtSignature is ignored.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _fake_part(text: str, *, thought: bool = False, thought_signature: str | None = None):
    return SimpleNamespace(text=text, thought=thought, thought_signature=thought_signature)


def _fake_response(text: str, *, prompt_tokens: int = 11, candidate_tokens: int = 7):
    part = _fake_part(text, thought_signature="SHOULD_IGNORE")
    thought_part = _fake_part("", thought=True, thought_signature="THOUGHT_BLOB")
    candidate = SimpleNamespace(content=SimpleNamespace(parts=[thought_part, part]))
    usage = SimpleNamespace(
        prompt_token_count=prompt_tokens,
        candidates_token_count=candidate_tokens,
        thoughts_token_count=96,
    )
    return SimpleNamespace(candidates=[candidate], usage_metadata=usage)


@pytest.mark.asyncio
async def test_ascan_prompt_returns_openai_shaped_raw(monkeypatch):
    monkeypatch.setenv("TIER2_PROVIDER", "gemini")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-not-a-real-key")
    monkeypatch.setenv("VERTEX_TIER2_MODEL", "gemini-3.5-flash-lite")

    payload_json = '{"risk_score":0,"findings":[],"recommended_action":"allow"}'
    fake_sdk = MagicMock()
    fake_sdk.aio.models.generate_content = AsyncMock(return_value=_fake_response(payload_json))

    with patch("ai_mesh_shared.tier2_gemini_client._new_genai_client", return_value=fake_sdk):
        from ai_mesh_shared.tier2_gemini_client import GeminiTier2Client

        client = GeminiTier2Client()
        result = await client.ascan_prompt(
            model="gemini-3.5-flash-lite",
            prompt_payload={
                "messages": [
                    {"role": "system", "content": "You are a scanner."},
                    {"role": "user", "content": "hello"},
                ],
                "max_tokens": 256,
                "temperature": 0.0,
            },
            request_id="zs-test-1",
            call_site="tier2_scan",
        )

    assert result["raw"]["choices"][0]["message"]["content"] == payload_json
    assert "SHOULD_IGNORE" not in result["raw"]["choices"][0]["message"]["content"]
    assert "THOUGHT_BLOB" not in result["raw"]["choices"][0]["message"]["content"]
    assert result["api_method"] == "generateContent"
    assert result["ran_inference"] is True
    assert result["tokens_in"] == 11
    assert result["tokens_out"] == 7
    assert result["model_id"] == "gemini-3.5-flash-lite"
    assert result["gateway_request_id"] == "zs-test-1"
    fake_sdk.aio.models.generate_content.assert_awaited_once()
    call_kw = fake_sdk.aio.models.generate_content.await_args.kwargs
    assert call_kw["model"] == "gemini-3.5-flash-lite"
    config = call_kw.get("config")
    assert config is None or getattr(config, "thinking_config", None) is None
    assert call_kw["contents"] == "hello"


def test_extract_parts_text_skips_thought_signature():
    from ai_mesh_shared.tier2_gemini_client import extract_gemini_text

    resp = _fake_response('{"ok":true}')
    assert extract_gemini_text(resp) == '{"ok":true}'


def test_extract_parts_text_empty_candidates():
    from ai_mesh_shared.tier2_gemini_client import extract_gemini_text

    assert extract_gemini_text(SimpleNamespace(candidates=[])) == ""
    assert extract_gemini_text(None) == ""


@pytest.mark.asyncio
async def test_ascan_retries_generic_400_without_thinking(monkeypatch):
    monkeypatch.setenv("TIER2_PROVIDER", "gemini")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-not-a-real-key")
    payload_json = '{"risk_score":0,"findings":[],"recommended_action":"allow"}'
    fake_sdk = MagicMock()
    fake_sdk.aio.models.generate_content = AsyncMock(
        side_effect=[
            Exception("400 INVALID_ARGUMENT. Request contains an invalid argument."),
            _fake_response(payload_json),
        ]
    )
    with patch("ai_mesh_shared.tier2_gemini_client._new_genai_client", return_value=fake_sdk):
        from ai_mesh_shared.tier2_gemini_client import GeminiTier2Client

        client = GeminiTier2Client()
        result = await client.ascan_prompt(
            model="gemini-3.5-flash-lite",
            prompt_payload={"messages": [{"role": "user", "content": "hello"}]},
        )
    assert result["raw"]["choices"][0]["message"]["content"] == payload_json
    assert fake_sdk.aio.models.generate_content.await_count == 2
