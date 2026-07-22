"""CHG-0075: the MCP tier-1 scan (`mcp_scan_orchestrator._scan_text_tier1`) ran
detect_pii / detect_secrets / detect_ip_leakage but NOT detect_credential_exposure.
CREDENTIAL_EXPOSURE_PATTERNS is a SEPARATE dict (Stripe / Twilio / Azure-storage /
GCP-service-account / DB connection-string / bearer / jwt / slack / github-fine-
grained-PAT). redact_all masks it, but detect_secrets does NOT read it — so a
credential whose ONLY match was a CREDENTIAL_EXPOSURE kind was never DETECTED, drove
no enforcement, and egressed RAW on a tool RESULT (and passed unblocked in tool ARGS
to a possibly-untrusted upstream). Same wrong-dict class as CHG-0071.

Also (CHG-0075 part B): 7 CREDENTIAL_EXPOSURE keys had NO COMPLIANCE_TAG_MAP entry
(get_compliance_tags -> []), so a detected Stripe/Twilio/Azure/GCP/Slack/JWT/fine-
grained-PAT credential was never tagged SECRET.

These tests drive the REAL orchestrator + proxy paths (nothing mocked) and assert on
the EGRESS BYTES.
"""
from __future__ import annotations

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
import patterns as P  # noqa: E402

# (name, text carrying the credential, raw substring that must not survive)
_CRED_CASES = [
    ("stripe_key", "charge with sk_live_abcdefghij0123456789ABCD now", "sk_live_abcdefghij0123456789ABCD"),
    ("twilio_api_key", "twilio key SK0123456789abcdef0123456789abcdef", "SK0123456789abcdef0123456789abcdef"),
    ("azure_storage_key",
     "cfg AccountKey=abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOP0123456789ab==",
     "AccountKey=abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOP0123456789ab=="),
    ("connection_string", "db postgres://admin:S3cr3tPass@dbhost.example:5432/prod", "admin:S3cr3tPass"),
]


def test_all_credential_exposure_keys_tag_secret():
    # CHG-0075 part B: every CREDENTIAL_EXPOSURE kind maps to SECRET.
    missing = [k for k in P.CREDENTIAL_EXPOSURE_PATTERNS if "SECRET" not in P.get_compliance_tags([k])]
    assert not missing, f"CREDENTIAL_EXPOSURE keys missing SECRET tag: {missing}"


# STRICT OPERATOR CONTROL (2026-07-21): the E12 result-redaction floor and the
# credential force-block are static hardening floors — they fire only under an
# operator-selected ENFORCING posture. These helpers used to pass
# ``enabled_info=None``, which resolves to observe-only ``tag`` (detect + tag, never
# mutate, never block), so they now select ``redact`` explicitly.
_ENFORCING = {"default_scan_action": "redact"}


async def _floor(text):
    return await mcp_proxy._scan_tool_result_floor(
        text, tool_name="fetch", enabled_info=_ENFORCING, org_slug="o", server_slug="s", actor=None)


async def _argblock(args, posture="redact"):
    return await mcp_proxy._scan_tool_args_block(
        args, tool_name="fetch", enabled_info={"default_scan_action": posture},
        org_slug="o", server_slug="s", actor=None)


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "text", "raw"), _CRED_CASES)
async def test_credential_in_result_is_masked_not_raw(name, text, raw):
    scanned, blocked, tags, findings, meta = await _floor(text)
    # The contract is the EGRESS BYTES. ``meta["result_redaction_floor"]`` marks the
    # E12 RE-SCAN floor specifically, which fires only when the first pass left the
    # result unmutated; under an explicit ``redact`` posture the first pass masks
    # inline, so that flag is legitimately absent.
    assert blocked or raw not in str(scanned), f"{name}: {raw!r} egressed RAW in a tool result"
    assert "SECRET" in tags


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "text", "raw"), _CRED_CASES)
async def test_credential_in_args_redacted_not_blocked(name, text, raw):
    """STRICT OPERATOR CONTROL (2026-07-22): redact means redact. A credential in tool
    ARGS is MASKED in place and forwarded under ``redact`` — NOT force-blocked. The
    old credential force-block escalated redact -> block regardless of the selected
    posture (a real incident: an operator selected redact and got HTTP 400). It is now
    off by default; an operator who wants a credential to hard-block the call selects
    the ``block`` posture (below) or opts the floor back in."""
    scanned, blocked, _t, _f, meta = await _argblock({"value": text})
    assert not blocked, f"{name}: redact must mask, not block"
    assert not meta.get("credential_force_block")
    assert raw not in str(scanned), f"{name}: {raw!r} must be masked under redact"


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "text", "raw"), _CRED_CASES)
async def test_credential_in_args_blocked_under_block_posture(name, text, raw):
    """The operator's ``block`` selection blocks a credential-bearing tool call."""
    _s, blocked, _t, _f, _meta = await _argblock({"value": text}, posture="block")
    assert blocked, f"{name}: block posture must block"


@pytest.mark.asyncio
async def test_credential_force_block_opt_in_still_available(monkeypatch):
    """The force-block remains available as an explicit opt-in for operators who want
    a credential to hard-block even under redact."""
    monkeypatch.setenv("GATEWAY_MCP_BLOCK_ON_CREDENTIAL", "true")
    name, text, raw = _CRED_CASES[0]
    _s, blocked, _t, _f, meta = await _argblock({"value": text})
    assert blocked and meta.get("credential_force_block")


@pytest.mark.asyncio
@pytest.mark.parametrize("benign", ["what is the weather in Paris", "the meeting is at 3pm on Tuesday"])
async def test_no_false_positive(benign):
    scanned, blocked, tags, findings, meta = await _floor(benign)
    assert not blocked
    assert not meta.get("result_redaction_floor")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
