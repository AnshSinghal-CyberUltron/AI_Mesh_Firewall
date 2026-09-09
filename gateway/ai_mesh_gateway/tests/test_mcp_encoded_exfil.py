"""CHG-0076: the MCP orchestrator's tier-1 scan had NO text-encoding obfuscation check
(the chat output scanner does, via scanner._decode_text_encoding_variants — G33/G35).
detect_secrets folds base64/hex transport, but a SECRET / CREDENTIAL / INTERNAL NETWORK
IP hidden by a TEXT-encoding (HTML char refs &#..;, percent-encoding, \\u / \\x escapes)
dodges the raw regexes — yet a markdown/HTML MCP client decodes it back to the value, so
a malicious upstream MCP server can exfil a stolen credential / internal IP past the
firewall (or a tenant can smuggle one in tool ARGS to an untrusted upstream).

redact_all cannot mask an ENCODED run, so these now BLOCK (fail-closed). SCOPED to
secret/credential/internal-network-IP; generic PII stays UNBLOCKED so a scraped HTML
page's entity-encoded contact email does not false-block a legitimate web/HTML tool.
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

_SECRET = "sk-ant-AAAABBBBCCCCDDDDEEEEFFFFGGGG1234"
_IP = "10.0.0.5"
_EMAIL = "alice@corp.example"


# __PDD_PRESET_FIXTURE__

# policy-driven-detection cutover (task 9): the MCP built-in default detectors (the Tier-1
# PRESET pass) are now EFFECTIVE-DEFAULT OFF (mcp_scan_orchestrator._mcp_default_detection_enabled)
# so a zero-enabled-policy org is passthrough. This module exercises the RETAINED preset
# DETECTION MACHINERY (redaction / fail-closed byte-truth / exfil-defang / authz / audit), which
# stays reachable via the explicit opt-in env. Enable it for this module so those invariants are
# still tested. The default-OFF (Zero_Policy_State passthrough) contract is asserted by the
# dedicated test_policy_driven_* modules, not weakened here.
import os as _os_pdd


@pytest.fixture(autouse=True)
def _enable_builtin_mcp_presets(monkeypatch):
    monkeypatch.setenv("GATEWAY_MCP_DEFAULT_DETECTION", "true")
    monkeypatch.setenv("GATEWAY_MCP_REDACT_RESULT_ON_DETECT", "true")
    yield


def _htmlent(s: str) -> str:
    return "".join(f"&#{ord(c)};" for c in s)


# STRICT OPERATOR CONTROL (2026-07-21): the encoded-exfil fail-closed block and the
# E12 result-redaction floor are static hardening floors — they fire only under an
# operator-selected ENFORCING posture. These helpers used to pass ``enabled_info=None``,
# which resolves to observe-only ``tag`` (detect + tag, never mutate, never block).
_ENFORCING = {"default_scan_action": "redact"}


async def _floor(text):
    return await mcp_proxy._scan_tool_result_floor(
        text, tool_name="fetch", enabled_info=_ENFORCING, org_slug="o", server_slug="s", actor=None)


async def _argblock(args):
    return await mcp_proxy._scan_tool_args_block(
        args, tool_name="fetch", enabled_info=_ENFORCING, org_slug="o", server_slug="s", actor=None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        f"token {_htmlent(_SECRET)}",                       # HTML-entity encoded secret
        "token " + _SECRET.replace("-", "%2D"),            # percent-encoded secret
        f"host {_htmlent(_IP)} up",                        # HTML-entity encoded internal IP
    ],
)
async def test_encoded_secret_or_ip_in_result_blocks(text):
    scanned, blocked, _t, findings, _m = await _floor(text)
    # STRICT OPERATOR CONTROL (2026-07-22): redact masks the encoded secret/IP in place
    # and forwards — never the old redact->block escalation.
    assert not blocked
    assert any(f.get("threat_type") == "secret" for f in findings)


@pytest.mark.asyncio
async def test_encoded_secret_in_args_blocks():
    scanned, blocked, _t, _f, _m = await _argblock({"note": f"key {_htmlent(_SECRET)}"})
    assert not blocked
    assert _SECRET not in str(scanned)   # masked, not exfiltrated raw


@pytest.mark.asyncio
async def test_encoded_generic_pii_not_blocked():
    # A scraped HTML page's entity-encoded contact email is usually benign public
    # content — must NOT false-block a legitimate web/HTML tool result (PII excluded).
    _s, blocked, _t, _f, _m = await _floor(f"contact us at {_htmlent(_EMAIL)}")
    assert not blocked


@pytest.mark.asyncio
async def test_raw_secret_still_masked_not_blocked():
    # regression: a RAW (unencoded) secret is still redacted by the floor, not blocked.
    scanned, blocked, _t, _f, meta = await _floor(f"token {_SECRET}")
    assert not blocked
    assert _SECRET not in str(scanned)
    # ``meta["result_redaction_floor"]`` is NOT asserted: it marks the E12 RE-SCAN
    # floor, which only fires when the first pass left the result unmutated. Under the
    # explicit ``redact`` posture the first pass masks inline, so the flag is
    # legitimately absent — the contract is the egress bytes above.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "benign",
    [
        "page &amp; title &#8212; em-dash and &lt;b&gt;bold&lt;/b&gt; markup",
        "the weather in Paris is sunny today",
        "download at https://example.com/report.pdf now",
    ],
)
async def test_benign_encoded_and_plain_not_blocked(benign):
    _s, blocked, _t, _f, _m = await _floor(benign)
    assert not blocked


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
