"""PIPELINE-0011: plain-text PII must NOT be classified as 'Markdown/HTML-obfuscated'.

Obfuscation detection (G33/G53/G76) must only fire when the PII/secret is genuinely
REVEALED by decoding or emphasis-stripping — not when the value was already detectable
in the un-transformed source text. A plain SSN/email/secret alongside unrelated emphasis
or HTML entities must follow the normal redact/monitor path, not the obfuscation-block.

Genuinely obfuscated PII (emphasis AROUND the digits, entity-encoded chars) must STILL be
caught. These tests verify both sides and lock the fix against regression.
"""
from __future__ import annotations

import os

os.environ.setdefault("ENABLE_TIER2", "false")

import pytest

import patterns
from patterns import detect_pii, detect_secrets, detect_credential_exposure
from scanner import InputScanner

_SCANNER = InputScanner()


# policy-driven-detection cutover (task 9): the automatic built-in default scan was REMOVED
# from the LIVE ``_scan_prompt_sync`` (task 3.1) — it now returns a neutral ``ScanVerdict()``
# (a zero-policy org is passthrough). The obfuscation-FP CLASSIFICATION mechanics these tests
# lock (PIPELINE-0011/0012: plain PII near unrelated emphasis/entities redacts and is NOT
# mislabelled obfuscated; genuinely-obfuscated PII/secret still blocks) are RETAINED in the
# reference body ``_scan_prompt_sync_disabled_builtin_default``. Drive the tests through that
# retained executor so they keep exercising the FP logic without asserting a built-in default
# fires on the live policy-only path (Zero_Policy_State).
def _verdict(text: str) -> str:
    return _SCANNER._scan_prompt_sync_disabled_builtin_default(text, False, None).action


def _threat(text: str) -> str:
    return _SCANNER._scan_prompt_sync_disabled_builtin_default(text, False, None).threat_type


# ── A: PLAIN PII + unrelated emphasis → redact, NOT block/obfuscated ──

_PLAIN_PII_WITH_EMPHASIS = [
    ("ssn_with_bold",
     "The **important** note: SSN is 123-45-6789 for the record."),
    ("ssn_with_italic",
     "Please see *attachment*: SSN 987-65-4321 is the patient ID."),
    ("email_with_bold",
     "Contact **us** at john.doe@example.com for help."),
    ("email_with_strikethrough",
     "The ~~old~~ address is admin@corp.example for support."),
    ("ssn_with_comment",
     "Report <!-- draft --> contains SSN 111-22-3333 in it."),
]


@pytest.mark.parametrize("label,payload", _PLAIN_PII_WITH_EMPHASIS)
def test_plain_pii_with_unrelated_emphasis_is_redacted(label, payload):
    """Plain PII near unrelated markdown/HTML emphasis must redact (not block)."""
    action = _verdict(payload)
    assert action == "redact", (
        f"{label}: plain PII wrongly classified, expected 'redact' got '{action}'"
    )


@pytest.mark.parametrize("label,payload", _PLAIN_PII_WITH_EMPHASIS)
def test_plain_pii_with_emphasis_not_obfuscated_threat(label, payload):
    """The threat_type must be 'pii', never 'obfuscated_pii'."""
    tt = _threat(payload)
    assert tt == "pii", (
        f"{label}: expected threat_type='pii', got '{tt}'"
    )


# ── B: PLAIN PII + unrelated HTML entities → redact, NOT obfuscated-block ──

_PLAIN_PII_WITH_ENTITIES = [
    ("ssn_with_euro",
     "Price: &euro;50 and SSN 222-33-4444 for ID."),
    ("ssn_with_copyright",
     "&#169; 2026 Corp. SSN: 555-66-7777 on file."),
    ("email_with_amp",
     "Tom &amp; Jerry, email: tom@example.com for support."),
]


@pytest.mark.parametrize("label,payload", _PLAIN_PII_WITH_ENTITIES)
def test_plain_pii_with_unrelated_entities_is_redacted(label, payload):
    """Plain PII near unrelated HTML entities must redact (not block)."""
    action = _verdict(payload)
    assert action == "redact", (
        f"{label}: plain PII with unrelated entities wrongly classified, "
        f"expected 'redact' got '{action}'"
    )


@pytest.mark.parametrize("label,payload", _PLAIN_PII_WITH_ENTITIES)
def test_plain_pii_with_entities_not_obfuscated(label, payload):
    tt = _threat(payload)
    assert tt == "pii", (
        f"{label}: expected threat_type='pii', got '{tt}'"
    )


# ── C: GENUINELY OBFUSCATED PII must still be BLOCKED ──

_GENUINE_OBFUSCATED = [
    ("ssn_emphasis_split",
     "my ssn is 1**2**3-45-6789 please store it"),
    ("ssn_html_comment_split",
     "the ssn is 12<!-- x -->3-45-6789 in the record"),
    ("ssn_empty_span_split",
     "id 1<span></span>23-45-6789 today"),
    ("secret_emphasis_split",
     "api key sk_live_**ab**cdef0123456789ghij here"),
]


@pytest.mark.parametrize("label,payload", _GENUINE_OBFUSCATED)
def test_genuinely_obfuscated_pii_still_blocked(label, payload):
    """PII/secret with emphasis/HTML WITHIN the value must still be caught."""
    action = _verdict(payload)
    assert action == "block", (
        f"{label}: genuinely obfuscated PII slipped, expected 'block' got '{action}'"
    )


@pytest.mark.parametrize("label,payload", _GENUINE_OBFUSCATED)
def test_genuinely_obfuscated_has_obfuscated_threat(label, payload):
    """Genuinely obfuscated PII must have threat_type 'obfuscated_pii' or 'obfuscated_secret'."""
    tt = _threat(payload)
    assert tt.startswith("obfuscated"), (
        f"{label}: expected obfuscated threat_type, got '{tt}'"
    )


# ── D: MCP orchestrator — plain secret + unrelated encoding must NOT block ──

def _mcp_scan(text: str, enforcement: str = "tag"):
    """Thin wrapper around _scan_text_tier1_sync with stub org/server/tool."""
    from mcp_scan_orchestrator import _scan_text_tier1_sync
    return _scan_text_tier1_sync(
        text,
        scan_direction="output",
        enforcement=enforcement,
        full_payload=None,
        org_slug="test-org",
        server_slug="test-server",
        tool_name="test-tool",
    )


def test_mcp_plain_secret_with_unrelated_entity_not_obfuscation_blocked():
    """A plain Stripe key alongside an unrelated HTML entity must NOT be escalated
    to block by the G76 encoded-exfil check."""
    text = "API key: sk_live_1234567890abcdefghijklmn and price &#8364;50"
    _mutated, findings, blocked, _tags = _mcp_scan(text)
    encoded_findings = [
        f for f in findings if "Encoded exfil" in (f.detail or "")
    ]
    assert not encoded_findings, (
        "Plain secret re-detected as encoded-exfil (false positive)"
    )


def test_mcp_genuinely_encoded_secret_still_blocked():
    """A secret encoded via HTML entities must still be caught as encoded exfil."""
    key_encoded = "sk_live_" + "".join(f"&#{ord(c)};" for c in "1234567890abcdefghijklmn")
    text = f"here is the key: {key_encoded}"
    _mutated, findings, blocked, _tags = _mcp_scan(text)
    assert blocked or any("Encoded exfil" in (f.detail or "") for f in findings), (
        "Genuinely encoded secret NOT caught by G76"
    )


# ── E: benign markdown (no PII) stays allowed ──

_BENIGN_NO_PII = [
    "please make this **important** and bold",
    "the variable snake_case_name is used here",
    "compute 2*3*4 and 5*6 results now",
    "the **budget** is 50 dollars this quarter",
    "see &#169; 2026 for details",
]


@pytest.mark.parametrize("payload", _BENIGN_NO_PII)
def test_benign_markdown_still_allowed(payload):
    assert _verdict(payload) == "allow", f"benign markdown wrongly actioned: {payload[:40]!r}"


# ── C: ALREADY smart-masked PII → redact, NOT obfuscated-block (PIPELINE-0012) ──

_PRE_MASKED_USER_RECORD = (
    "Please process this user record: SSN ***-**-6789, email j***@a***.com, "
    "phone ***-***-5309, credit card ****-****-****-1111."
)


def test_pre_masked_user_record_is_redacted_not_blocked():
    """Smart-mask shapes must redact (already masked), never obfuscated_pii block."""
    action = _verdict(_PRE_MASKED_USER_RECORD)
    assert action == "redact", f"expected redact for pre-masked record, got {action!r}"


def test_pre_masked_user_record_not_obfuscated_threat():
    tt = _threat(_PRE_MASKED_USER_RECORD)
    assert tt == "pii", f"expected threat_type='pii', got {tt!r}"


def test_strip_emphasis_does_not_reconstruct_smart_mask_email():
    raw = "email j***@a***.com here"
    stripped = patterns.strip_interleaved_emphasis(raw)
    assert stripped == raw, "smart-mask email must survive emphasis strip unchanged"
    assert detect_pii(raw), "smart-mask email must be detectable as PII"
