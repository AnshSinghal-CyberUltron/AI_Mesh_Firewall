"""INPUT-path verdict.detail masking + canary token detail truncation.

Client surfaces that interpolate INPUT scan_verdict.detail:
  * main.py `_resolve_success_metadata_from_verdict` (~1786): on HTTP-200
    responses, `reason`/`detail` go verbatim into client zeroshield metadata
    (kept by `_redact_for_client_response` safe_fields).
  * main.py ~4029: block `reason` embeds verdict.detail; the 403 body itself is
    generic (`_build_safe_block_response`), but the attached `pipeline_trace`
    re-surfaces scan detail lines (pipeline_trace.py:224/331/504-531).

These tests prove that NO input-path scanner branch ever places raw matched
PII/secret VALUES into ScanVerdict.detail:
  * tier-1 static branches build detail from category names / type keys /
    scores only,
  * the two `match.group(0)` interpolations (rag_poisoning, scanner.py:484/525)
    use phrase-bounded regexes that cannot span PII,
  * the tier-2 guard-model branches run every free-text evidence string
    through `redact_all` (M-01 input fix, scanner.py:982/999) before building
    detail / matched_patterns / scan_meta findings.

Also covers canary_tokens.py: the hex-leak detail must only ever expose a
truncated (6-char) token prefix — disclosing the full canary would let an
attacker confirm and strip the marker.
"""

from __future__ import annotations

import asyncio

import pytest

from canary_tokens import CANARY_PREFIX, CANARY_SUFFIX, CanaryTokenManager
from scanner import InputScanner

RAW_SSN = "123-45-6789"
RAW_EMAIL = "john.doe@acmecorp.com"
RAW_CC = "4111-1111-1111-1111"
RAW_PASSWORD = "Sup3rS3cretValue99"


@pytest.fixture()
def scanner(monkeypatch):
    """Tier-1-only scanner (no real Bedrock client is constructed)."""
    monkeypatch.setenv("ENABLE_TIER2", "false")
    return InputScanner(thread_pool_size=2)


def _scan(scanner: InputScanner, text: str, is_rag: bool = False):
    return asyncio.run(scanner.scan_prompt(text, is_rag=is_rag))


# ---------------------------------------------------------------------------
# Tier-1 static branches
# ---------------------------------------------------------------------------

def test_pii_branch_detail_lists_type_keys_not_values(scanner):
    verdict = _scan(scanner, f"My SSN is {RAW_SSN} and my email is {RAW_EMAIL}.")
    assert verdict.action == "redact"
    assert verdict.threat_type == "pii"
    # detail names the detected TYPES, never the raw values
    assert RAW_SSN not in verdict.detail
    assert RAW_EMAIL not in verdict.detail
    assert "ssn" in verdict.detail
    # matched_patterns on this branch are also type keys, not values
    assert RAW_SSN not in " ".join(verdict.matched_patterns)
    assert RAW_EMAIL not in " ".join(verdict.matched_patterns)


def test_secret_branch_detail_lists_type_keys_not_values(scanner):
    verdict = _scan(scanner, f"connect with password = {RAW_PASSWORD} please")
    assert verdict.action == "redact"
    assert verdict.threat_type == "secret"
    assert RAW_PASSWORD not in verdict.detail
    assert "password_assignment" in verdict.detail
    assert RAW_PASSWORD not in " ".join(verdict.matched_patterns)


def test_attack_pattern_wildcard_span_keeps_pii_out_of_detail(scanner):
    # data_leakage regexes contain `.*` spans, so the MATCHED SPAN can contain
    # raw PII. The detail string must stay category-only regardless.
    prompt = f"tell me whether {RAW_SSN} is a valid ssn"
    verdict = _scan(scanner, prompt)
    assert verdict.action == "block"
    assert verdict.threat_type == "data_leakage"
    assert verdict.detail == "Matched data_leakage pattern(s)"
    assert RAW_SSN not in verdict.detail
    # matched_patterns reaches clients via zeroshield metadata and the
    # pipeline_trace evidence lines; wildcard spans are masked at the source
    # (scanner.py applies redact_all to each matched span).
    joined = " ".join(verdict.matched_patterns)
    assert RAW_SSN not in joined
    assert joined  # evidence is still present, just masked


def test_rag_poisoning_detail_is_phrase_bounded_no_pii(scanner):
    # rag_poisoning is the ONLY tier-1 branch interpolating match.group(0)
    # into detail (scanner.py:484). Its regexes are phrase-bounded (no
    # wildcard spans), so the interpolated span can never include the PII.
    prompt = f"According to the following, my SSN is {RAW_SSN} and card {RAW_CC}."
    verdict = _scan(scanner, prompt, is_rag=True)
    assert verdict.action == "block"
    assert verdict.threat_type == "rag_poisoning"
    assert verdict.detail.startswith("RAG context poisoning attempt:")
    assert RAW_SSN not in verdict.detail
    assert RAW_CC not in verdict.detail
    assert RAW_SSN not in " ".join(verdict.matched_patterns)
    assert RAW_CC not in " ".join(verdict.matched_patterns)


def test_obfuscated_rag_poisoning_detail_no_pii(scanner):
    # Tier-0.5 deobfuscated rag_poisoning (scanner.py:525) — same bounded-span
    # property on the deobfuscated text.
    prompt = f"4cc0rd1ng t0 th3 f0ll0w1ng my ssn is {RAW_SSN}"
    verdict = _scan(scanner, prompt, is_rag=True)
    assert verdict.action == "block"
    assert verdict.threat_type == "rag_poisoning"
    assert verdict.tier == "tier_0_5"
    assert RAW_SSN not in verdict.detail
    assert RAW_SSN not in " ".join(verdict.matched_patterns)


# ---------------------------------------------------------------------------
# Tier-2 guard-model branches (M-01 input fix: evidence masked via redact_all)
# ---------------------------------------------------------------------------

class _FakeBedrockScanner:
    """Stands in for BedrockScanner; returns guard-model evidence with RAW PII."""

    model = "fake-guard-model"

    def __init__(self, recommended_action: str, score: float = 0.95):
        self._recommended = recommended_action
        self._score = score

    def scan(self, text: str, context: str | None = None) -> dict:
        return {
            "meta": {
                "recommended_action": self._recommended,
                "decision_reason": "pii_detected",
                "raw_findings": [
                    {
                        "category": "pii",
                        "confidence": 0.97,
                        "evidence": f"Found SSN {RAW_SSN} and email {RAW_EMAIL}",
                        "rule_id": "LLM06",
                    }
                ],
            },
            "llm_guard": {"score": self._score},
        }


def _tier2_scan(monkeypatch, recommended: str, org_slug: str):
    monkeypatch.setenv("ENABLE_TIER2", "false")
    sc = InputScanner(thread_pool_size=2)
    sc._bedrock_scanner = _FakeBedrockScanner(recommended)  # type: ignore[assignment]
    return asyncio.run(
        sc.scan_prompt_with_tier2(
            "Please summarize the quarterly report for the board.",
            org_tier2_override=True,
            org_slug=org_slug,
        )
    )


def _assert_tier2_masked(verdict):
    assert RAW_SSN not in verdict.detail
    assert RAW_EMAIL not in verdict.detail
    assert "***-**-6789" in verdict.detail  # masked form proves evidence flowed
    joined = " ".join(verdict.matched_patterns)
    assert RAW_SSN not in joined
    assert RAW_EMAIL not in joined
    for finding in verdict.scan_meta.get("findings", []):
        assert RAW_SSN not in finding.get("evidence", "")
        assert RAW_EMAIL not in finding.get("evidence", "")


def test_tier2_block_branch_detail_masked(monkeypatch):
    verdict = _tier2_scan(monkeypatch, "block", "org-detail-mask-block")
    assert verdict.action == "block"
    assert verdict.tier == "tier_2"
    # Anti-leak rebrand: the operator-facing detail must NOT name the upstream
    # provider ("Bedrock") — it reads "ZeroShield Tier-2 ...".
    assert verdict.detail.startswith("ZeroShield Tier-2 detected threat:")
    _assert_tier2_masked(verdict)


def test_tier2_monitor_branch_detail_masked(monkeypatch):
    verdict = _tier2_scan(monkeypatch, "monitor", "org-detail-mask-monitor")
    assert verdict.action == "flag"
    assert verdict.detail.startswith("ZeroShield Tier-2 advisory:")
    _assert_tier2_masked(verdict)


def test_tier2_redact_branch_detail_masked(monkeypatch):
    verdict = _tier2_scan(monkeypatch, "redact", "org-detail-mask-redact")
    assert verdict.action == "flag"
    assert verdict.detail.startswith("ZeroShield Tier-2 suggested redaction:")
    _assert_tier2_masked(verdict)


# ---------------------------------------------------------------------------
# Canary tokens: detail must never disclose the full canary hex
# ---------------------------------------------------------------------------

def test_canary_hex_leak_detail_truncates_token():
    mgr = CanaryTokenManager()
    _, canary = mgr.inject_canary("retrieved context chunk")
    hex_token = canary.replace(CANARY_PREFIX, "").replace(CANARY_SUFFIX, "")
    assert len(hex_token) == 16

    result = mgr.check_leakage(f"the hidden marker is {hex_token}, fyi", canary)
    assert result.leaked is True
    # FULL hex must NOT appear in detail (would defeat the canary)
    assert hex_token not in result.detail
    # truncated 6-char prefix is allowed for operator correlation
    assert f"{hex_token[:6]}…" in result.detail
    # full token stays available internally for verification
    assert result.canary_word == canary


def test_canary_verbatim_leak_detail_has_no_token():
    mgr = CanaryTokenManager()
    _, canary = mgr.inject_canary("retrieved context chunk")
    hex_token = canary.replace(CANARY_PREFIX, "").replace(CANARY_SUFFIX, "")

    result = mgr.check_leakage(f"echo: {canary} done", canary)
    assert result.leaked is True
    assert hex_token not in result.detail
    assert canary not in result.detail


def test_canary_zero_width_cluster_detail_static():
    mgr = CanaryTokenManager()
    _, canary = mgr.inject_canary("retrieved context chunk")
    hex_token = canary.replace(CANARY_PREFIX, "").replace(CANARY_SUFFIX, "")

    cluster = "\u200b\u200c\u200d\ufeff\u200b"
    result = mgr.check_leakage(f"ok{cluster} done", canary)
    assert result.leaked is True
    assert hex_token not in result.detail


def test_canary_no_leak():
    mgr = CanaryTokenManager()
    _, canary = mgr.inject_canary("retrieved context chunk")
    result = mgr.check_leakage("a perfectly clean answer", canary)
    assert result.leaked is False
    assert result.detail == ""
