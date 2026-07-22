"""CHG-0083: several CREDENTIALS live in PII_PATTERNS (detected by detect_pii), not
SECRET_PATTERNS — aws_access_key (AKIA/ASIA), aws_secret_access_key, api_key_openai,
github_token, private_key_header. The CHG-0076 (text-encoding) + CHG-0079 (invisible/
confusable-unicode) encoded-exfil BLOCK only ran detect_secrets / detect_credential_
exposure / detect_ip_leakage — so an OBFUSCATED AWS key (HTML-entity / zero-width /
homoglyph) slipped past the block while its RAW form masks.

Fix: the encoded-exfil probe now also includes decoded detect_pii matches whose compliance
tag is SECRET (credentials misfiled as PII) — NOT generic PII (email/phone/ssn/cc), which
stays excluded to avoid FP on entity-encoded scraped-HTML PII.

Also a durable adversarial matrix: raw sensitive → masked/blocked; benign → unchanged.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_GW = Path(__file__).resolve().parents[1]
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))
_SHARED = _GW.parents[1] / "shared"
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.append(str(_SHARED))

import mcp_proxy  # noqa: E402

_ZW = "​"


def _zw(s: str) -> str:
    return _ZW.join(s[i:i + 2] for i in range(0, len(s), 2))


def _ent(s: str) -> str:
    return "".join(f"&#{ord(c)};" for c in s)


# STRICT OPERATOR CONTROL (2026-07-21): the encoded-exfil fail-closed block and the
# E12 result-redaction floor are static hardening floors — they fire only under an
# operator-selected ENFORCING posture. This helper used to pass ``enabled_info=None``,
# which resolves to observe-only ``tag`` (detect + tag, never mutate, never block).
_ENFORCING = {"default_scan_action": "redact"}


async def _floor(text):
    return await mcp_proxy._scan_tool_result_floor(
        {"content": [{"type": "text", "text": text}]},
        tool_name="fetch", enabled_info=_ENFORCING, org_slug="o", server_slug="s", actor=None)


# credentials that live in PII_PATTERNS (SECRET-tagged) — obfuscated forms MUST block
_CRED_IN_PII = {
    "aws_akia": "AKIAIOSFODNN7EXAMPLE",
    "aws_asia": "ASIAABCDEFGHIJKLMNOP",
    "github": "ghp_" + "a" * 36,
    "openai": "sk-proj-" + "a" * 20,
}


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "val"), list(_CRED_IN_PII.items()))
@pytest.mark.parametrize("obf", ["htmlent", "zerowidth"])
async def test_obfuscated_cred_in_pii_blocks(name, val, obf):
    enc = _ent(val) if obf == "htmlent" else _zw(val)
    _s, blocked, _t, _f, _m = await _floor(f"note {enc} end")
    assert blocked, f"obfuscated {name} ({obf}) must block (credential misfiled as PII)"


@pytest.mark.asyncio
@pytest.mark.parametrize("val", ["alice@corp.example", "123-45-6789", "(415) 555-0142"])
async def test_obfuscated_generic_pii_not_blocked(val):
    # entity-encoded generic PII (scraped HTML) must NOT false-block (FP guard)
    _s, blocked, _t, _f, _m = await _floor(f"contact {_ent(val)} today")
    assert not blocked


@pytest.mark.asyncio
async def test_raw_aws_key_still_masked():
    scanned, blocked, _t, _f, _m = await _floor("aws key AKIAIOSFODNN7EXAMPLE configured")
    assert "AKIAIOSFODNN7EXAMPLE" not in json.dumps(scanned)


# ── durable adversarial matrix: raw sensitive → masked/blocked; benign → unchanged ──
_SENSITIVE = {
    "email": "alice.smith@corp.example", "ssn": "123-45-6789", "cc": "4111 1111 1111 1111",
    "phi_mrn": "MRN 4567890", "pci_iban": "GB82WEST12345698765432",
    "aws_akia": "AKIAIOSFODNN7EXAMPLE", "anthropic": "sk-ant-" + "A" * 24,
    "stripe": "sk_live_abcdefghij0123456789ABCD", "rfc1918": "10.10.5.7",
    "ipv6": "fc00::1234:5678", "metadata": "169.254.169.254", "hostname": "db1.internal",
}


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "val"), list(_SENSITIVE.items()))
async def test_raw_sensitive_never_egresses(name, val):
    scanned, blocked, _t, _f, _m = await _floor(f"data: {val} :end")
    if not blocked:
        assert val not in json.dumps(scanned), f"{name} egressed raw"


@pytest.mark.asyncio
@pytest.mark.parametrize("benign", [
    "The weather in Paris is sunny today.",
    "Version 1.2.3 shipped on schedule.",
])
async def test_benign_unchanged(benign):
    scanned, blocked, _t, _f, meta = await _floor(benign)
    assert not blocked
    assert benign in json.dumps(scanned)


@pytest.mark.asyncio
async def test_unix_file_path_passes_under_tag_and_fails_closed_under_redact():
    """A private file path is flag-tier under ``tag`` and fail-closed under ``redact``.

    "Reads a file from /home/user/project and returns its size." used to be a
    ``test_benign_unchanged`` case run with ``enabled_info=None``. That premise was
    wrong on two counts: with nothing selected the posture is observe-only ``tag``,
    where NOTHING is ever mutated or blocked (so it proved nothing about false
    positives), and the string is not detector-benign — it matches
    ``file_path_unix`` (ip_leakage). Under an operator-selected ``redact`` posture
    ``redact_all`` deliberately does not mask private file paths, and
    ``_scan_text_tier1``'s egress-byte verify blocks rather than forward a
    "redacted" result that still carries the detected value. Both halves are
    pinned here so neither can regress silently.
    """
    text = "Reads a file from /home/user/project and returns its size."
    payload = {"content": [{"type": "text", "text": text}]}

    scanned, blocked, _t, _f, meta = await mcp_proxy._scan_tool_result_floor(
        payload, tool_name="fetch", enabled_info={"default_scan_action": "tag"},
        org_slug="o", server_slug="s", actor=None)
    assert not blocked, "observe-only tag must never block"
    assert text in json.dumps(scanned), "observe-only tag must never mutate"

    _s, blocked, _t, _f, _m = await _floor(text)
    assert blocked, "an unmaskable file path must fail CLOSED under redact"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
