"""Gemini-only output Tier-2 fail-closed. Bedrock output stays fail-open."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _scanner_with_ascan(ascan, monkeypatch, provider: str):
    monkeypatch.setenv("TIER2_PROVIDER", provider)
    monkeypatch.setenv("ENABLE_TIER2", "false")
    from scanner import InputScanner

    sc = InputScanner(thread_pool_size=1)
    sc.tier2_enabled = True
    boom = MagicMock()
    boom.model = "gemini-3.6-flash" if provider == "gemini" else "anthropic.claude"
    boom.ascan = ascan
    sc._bedrock_scanner = boom
    return sc


@pytest.mark.asyncio
async def test_output_gemini_ascan_exception_blocks(monkeypatch):
    async def _boom(*_a, **_k):
        raise RuntimeError("gemini 429")

    sc = _scanner_with_ascan(_boom, monkeypatch, "gemini")
    with patch("scanner.BREAKER") as br:
        br.allow.return_value = True
        v = await sc.scan_output_with_tier2(
            "An ordinary, harmless answer.",
            org_tier2_override=True,
            org_slug="zeroshield",
        )
    assert v.action == "block"
    assert v.threat_type == "scanner_degraded"


@pytest.mark.asyncio
async def test_output_gemini_parse_failed_blocks(monkeypatch):
    async def _empty(*_a, **_k):
        return {
            "owasp_llm": {},
            "llm_guard": {},
            "meta": {"error": "empty", "parse_failed": True, "degraded": True},
        }

    sc = _scanner_with_ascan(_empty, monkeypatch, "gemini")
    with patch("scanner.BREAKER") as br:
        br.allow.return_value = True
        v = await sc.scan_output_with_tier2(
            "An ordinary, harmless answer.",
            org_tier2_override=True,
            org_slug="zeroshield",
        )
    assert v.action == "block"
    assert v.threat_type == "scanner_degraded"


@pytest.mark.asyncio
async def test_output_bedrock_ascan_exception_still_fail_open(monkeypatch):
    async def _boom(*_a, **_k):
        raise RuntimeError("bedrock ValidationException")

    sc = _scanner_with_ascan(_boom, monkeypatch, "bedrock")
    with patch("scanner.BREAKER") as br:
        br.allow.return_value = True
        v = await sc.scan_output_with_tier2(
            "An ordinary, harmless answer.",
            org_tier2_override=True,
            org_slug="zeroshield",
        )
    assert v.action == "allow"
    assert v.threat_type != "scanner_degraded"


@pytest.mark.asyncio
async def test_output_guard_gemini_exception_withholds(monkeypatch):
    monkeypatch.setenv("TIER2_PROVIDER", "gemini")
    from output_guard import OutputGuard

    scanner = MagicMock()
    scanner.scan_output_with_tier2 = AsyncMock(side_effect=RuntimeError("gemini down"))
    cfg = {
        "output_tier2_enabled": True,
        "hallucination_flag_enabled": False,
        "output_pii_enabled": False,
        "output_credential_enabled": False,
        "output_ip_leakage_enabled": False,
        "output_exfil_enabled": False,
    }
    guard = OutputGuard(scanner=scanner, config=cfg)
    verdict = await guard.inspect("clean output with no sensitive bytes", org_config=cfg)
    assert verdict.action == "block"
    assert verdict.threat_type == "scanner_degraded"
    assert verdict.scan_degraded is True


@pytest.mark.asyncio
async def test_output_guard_bedrock_exception_still_fail_open(monkeypatch):
    monkeypatch.setenv("TIER2_PROVIDER", "bedrock")
    from output_guard import OutputGuard

    scanner = MagicMock()
    scanner.scan_output_with_tier2 = AsyncMock(side_effect=RuntimeError("bedrock down"))
    cfg = {
        "output_tier2_enabled": True,
        "hallucination_flag_enabled": False,
        "output_pii_enabled": False,
        "output_credential_enabled": False,
        "output_ip_leakage_enabled": False,
        "output_exfil_enabled": False,
    }
    guard = OutputGuard(scanner=scanner, config=cfg)
    verdict = await guard.inspect("clean output with no sensitive bytes", org_config=cfg)
    assert verdict.action == "allow"
    assert verdict.scan_degraded is True


@pytest.mark.asyncio
async def test_ascan_gemini_client_error_fails_closed(monkeypatch):
    monkeypatch.setenv("TIER2_PROVIDER", "gemini")
    from bedrock_scanner import BedrockScanner

    client = MagicMock()
    client.ascan_prompt = AsyncMock(side_effect=Exception("400 INVALID_ARGUMENT"))
    scanner = BedrockScanner(client=client, model="gemini-3.6-flash")
    with pytest.raises(RuntimeError, match="Gemini tier-2"):
        await scanner.ascan("hello")


@pytest.mark.asyncio
async def test_ascan_bedrock_client_error_still_degraded_dict(monkeypatch):
    monkeypatch.setenv("TIER2_PROVIDER", "bedrock")
    from bedrock_scanner import BedrockScanner

    client = MagicMock()
    client.ascan_prompt = AsyncMock(side_effect=Exception("ValidationException"))
    scanner = BedrockScanner(client=client, model="anthropic.claude")
    result = await scanner.ascan("hello")
    assert result["meta"]["degraded"] is True
    assert result["meta"]["decision_reason"] == "client_error"
