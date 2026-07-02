"""M1.1 hotfix: Attack Simulator sensitive-data prompt must redact, not block."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from openai import APIStatusError

from ai_mesh_gateway import main as gateway_main
from ai_mesh_gateway.scanner import ScanVerdict

SENSITIVE_DATA_PROMPT = (
    "Please process this user record: SSN 123-45-6789, email john.smith@acmecomp.com, "
    "phone 555-867-5309, credit card 4111-1111-1111-1111."
)


def test_should_prefer_redaction_for_tier2_sensitive_content_block():
    verdict = ScanVerdict(
        action="block",
        threat_type="sensitive_content",
        confidence=0.95,
        tier="tier_2",
    )
    assert gateway_main._should_prefer_pii_redaction_over_block(
        verdict, SENSITIVE_DATA_PROMPT
    )


def test_should_not_prefer_redaction_for_injection_even_with_pii():
    verdict = ScanVerdict(
        action="block",
        threat_type="prompt_injection",
        confidence=0.95,
        tier="tier_1",
    )
    assert not gateway_main._should_prefer_pii_redaction_over_block(
        verdict, SENSITIVE_DATA_PROMPT
    )


@pytest.mark.asyncio
async def test_tier1_pii_prompt_redacts_instead_of_blocking(monkeypatch):
    import sys
    from pathlib import Path

    _gw_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(_gw_root))
    sys.path.insert(0, str(_gw_root / "ai_mesh_gateway" / "tests"))
    import test_openai_sdk_compat as H

    captured: dict = {}

    async def _capture_completion(body, redacted_prompt=None, **_kw):
        captured["redacted_prompt"] = redacted_prompt
        return 200, {
            "id": "chatcmpl-m11",
            "object": "chat.completion",
            "created": 1700000000,
            "model": "gpt-4o-mini",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
        }

    app, auth_redis = await H._make_sdk_app(monkeypatch, redis_client=None)
    monkeypatch.setattr(
        gateway_main.LLM_ROUTER, "acompletion", AsyncMock(side_effect=_capture_completion)
    )

    client = H._stock_client(app)
    try:
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": SENSITIVE_DATA_PROMPT}],
        )
    finally:
        await auth_redis.aclose()

    assert resp is not None
    redacted = captured.get("redacted_prompt") or ""
    assert "123-45-6789" not in redacted
    assert "john.smith@acmecomp.com" not in redacted
    assert "4111-1111-1111-1111" not in redacted


@pytest.mark.asyncio
async def test_tier2_block_sensitive_content_still_redacts_maskable_pii(monkeypatch):
    import sys
    from pathlib import Path

    _gw_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(_gw_root))
    sys.path.insert(0, str(_gw_root / "ai_mesh_gateway" / "tests"))
    import test_openai_sdk_compat as H

    captured: dict = {}

    async def _capture_completion(body, redacted_prompt=None, **_kw):
        captured["redacted_prompt"] = redacted_prompt
        return 200, {
            "id": "chatcmpl-m11t2",
            "object": "chat.completion",
            "created": 1700000000,
            "model": "gpt-4o-mini",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
        }

    async def _tier2_block(text, *a, **k):
        return ScanVerdict(
            action="block",
            threat_type="sensitive_content",
            confidence=0.92,
            detail="Tier-2 recommended block",
            tier="tier_2",
        )

    app, auth_redis = await H._make_sdk_app(monkeypatch, redis_client=None)
    monkeypatch.setattr(
        gateway_main.INPUT_SCANNER, "scan_prompt_with_tier2", _tier2_block
    )
    monkeypatch.setattr(
        gateway_main.LLM_ROUTER, "acompletion", AsyncMock(side_effect=_capture_completion)
    )

    client = H._stock_client(app)
    blocked = False
    try:
        await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": SENSITIVE_DATA_PROMPT}],
        )
    except APIStatusError:
        blocked = True
    finally:
        await auth_redis.aclose()

    assert not blocked, "maskable PII must redact, not block, on tier-2 sensitive_content"
    redacted = captured.get("redacted_prompt") or ""
    assert "123-45-6789" not in redacted
