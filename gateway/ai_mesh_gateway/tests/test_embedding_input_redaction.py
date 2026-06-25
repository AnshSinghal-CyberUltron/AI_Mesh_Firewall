"""G1/G3/G4: embedding inputs must be PII/secret-scanned + redacted (or fail closed)
before they leave the gateway to the upstream embedding provider — mirroring the
chat path and gated by the SAME ``input_scan_enabled`` config.

These tests exercise ``main._scan_redact_embedding_inputs`` directly, in-process,
with the REAL ``InputScanner`` (no network, no gateway key, no Pinecone). The
fail-closed case uses a tiny scanner stub so the "redaction is impossible" branch
is exercised deterministically without depending on redactor internals.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402
from scanner import InputScanner, ScanVerdict  # noqa: E402


@pytest.fixture
def real_scanner(monkeypatch):
    """Install the real tier-1 InputScanner as the module singleton."""
    scanner = InputScanner()
    monkeypatch.setattr(main, "INPUT_SCANNER", scanner)
    return scanner


# ── Redaction: the returned text must carry NO raw PII value ──


@pytest.mark.asyncio
async def test_redacts_email(real_scanner):
    texts = ["please embed: contact me at alice.smith@example.com for details"]
    out, block = await main._scan_redact_embedding_inputs(texts, {"input_scan_enabled": True})
    assert block is None
    assert "alice.smith@example.com" not in out[0]


@pytest.mark.asyncio
async def test_redacts_ssn(real_scanner):
    texts = ["the customer ssn is 123-45-6789 on file"]
    out, block = await main._scan_redact_embedding_inputs(texts, {"input_scan_enabled": True})
    assert block is None
    assert "123-45-6789" not in out[0]


@pytest.mark.asyncio
async def test_redacts_bare_phone_via_digit_backstop(real_scanner):
    # "8929554991" with contextual lead-in is caught by the contextual phone
    # pattern; the digit backstop is the fail-closed net for any 7+ digit run the
    # verdict-aware redactor would otherwise leave raw.
    texts = ["my phone number is 8929554991 call anytime"]
    out, block = await main._scan_redact_embedding_inputs(texts, {"input_scan_enabled": True})
    assert block is None
    assert "8929554991" not in out[0]
    assert "***-***-4991" in out[0]


@pytest.mark.asyncio
async def test_clean_text_passes_through(real_scanner):
    texts = ["the quick brown fox jumps over the lazy dog"]
    out, block = await main._scan_redact_embedding_inputs(texts, {"input_scan_enabled": True})
    assert block is None
    assert out == texts


@pytest.mark.asyncio
async def test_batch_redacts_each_item(real_scanner):
    texts = [
        "clean document one",
        "reach me at bob@corp.example for the report",
        "another clean document",
    ]
    out, block = await main._scan_redact_embedding_inputs(texts, {"input_scan_enabled": True})
    assert block is None
    assert len(out) == 3
    assert out[0] == texts[0]
    assert "bob@corp.example" not in out[1]
    assert out[2] == texts[2]


# ── No-regression: scanning disabled → texts returned unchanged ──


@pytest.mark.asyncio
async def test_input_scan_disabled_returns_unchanged(real_scanner):
    texts = ["contact me at alice.smith@example.com — should NOT be redacted"]
    out, block = await main._scan_redact_embedding_inputs(texts, {"input_scan_enabled": False})
    assert block is None
    assert out == texts
    assert "alice.smith@example.com" in out[0]


@pytest.mark.asyncio
async def test_scanner_none_returns_unchanged(monkeypatch):
    monkeypatch.setattr(main, "INPUT_SCANNER", None)
    texts = ["ssn 123-45-6789 must pass through when scanner is unavailable"]
    out, block = await main._scan_redact_embedding_inputs(texts, {"input_scan_enabled": True})
    assert block is None
    assert out == texts


# ── Fail-closed (G4): detected PII that genuinely cannot be masked → blocked ──


class _UnmaskableScanner:
    """Detects PII but redaction is a no-op (masking impossible) — drives the
    byte-verified fail-closed branch."""

    async def scan_prompt(self, text, *a, **k):
        return ScanVerdict(
            action="redact",
            threat_type="pii",
            confidence=0.85,
            detail="PII detected",
            matched_patterns=["full_name"],
            tier="tier_1",
        )

    def redact_pii(self, text, verdict=None):
        return text  # cannot mask — returns input unchanged


@pytest.mark.asyncio
async def test_fail_closed_when_redaction_impossible(monkeypatch):
    monkeypatch.setattr(main, "INPUT_SCANNER", _UnmaskableScanner())
    texts = ["John Q Public is the account holder"]
    out, block = await main._scan_redact_embedding_inputs(texts, {"input_scan_enabled": True})
    assert block is not None
    assert block["blocked"] is True
    assert block["index"] == 0
    assert "could not be redacted" in block["reason"]


@pytest.mark.asyncio
async def test_fail_closed_returns_partial_before_block(monkeypatch):
    """Items before the unmaskable one are returned; the block stops processing."""
    monkeypatch.setattr(main, "INPUT_SCANNER", _UnmaskableScanner())
    texts = ["", "John Q Public holds the account"]
    out, block = await main._scan_redact_embedding_inputs(texts, {"input_scan_enabled": True})
    # First item is whitespace-only → passes through untouched; second blocks.
    assert block is not None
    assert block["index"] == 1
    assert out == [""]


@pytest.mark.asyncio
async def test_fail_closed_respects_disabled_config(monkeypatch):
    """Even an unmaskable detector must NOT block when input_scan_enabled is off."""
    monkeypatch.setattr(main, "INPUT_SCANNER", _UnmaskableScanner())
    texts = ["John Q Public holds the account"]
    out, block = await main._scan_redact_embedding_inputs(texts, {"input_scan_enabled": False})
    assert block is None
    assert out == texts
