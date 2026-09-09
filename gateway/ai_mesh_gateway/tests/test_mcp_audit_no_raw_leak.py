"""CHG-0139 regression-lock: the MCP scan's AUDIT-serializable output — the McpFinding
list (``to_finding_dict``) + compliance_tags + scan metadata that ``_record_gateway_event``
records into the MCPEvent — must NEVER contain the RAW matched PII/secret value.

The MCPEvent audit trail is stored at rest; a finding/metadata that embedded the raw
value (e.g. a future ``McpFinding.detail`` that quoted the matched span, or a caller that
threaded raw content into ``metadata``) would leak the exact secret/PII into the audit DB —
a leak-at-rest that defeats the redaction. Findings must carry only the detector TYPE
(``matched_kinds``), offsets, and tag CODES. Verified sound today; this locks it against the
many parallel sessions editing the scanner / orchestrator.

(Distinct from the redaction tests, which assert the EGRESS RESULT bytes drop the value;
this asserts the AUDIT RECORD bytes drop it.)
"""
import json

import pytest
from unittest.mock import patch

from policy_engine import EvaluationResult
from mcp_scan_orchestrator import scan_mcp_payload


# (label, raw value that must never appear in the audit record)
_SECRETS = [
    ("aws_key", "AKIAIOSFODNN7EXAMPLE"),
    ("email", "victim.person@corp.example"),
    ("us_ssn", "123-45-6789"),
    ("github_pat", "ghp_" + "0123456789abcdefghijklmnopqrstuvwxyzAB"[:36]),
    ("stripe", "sk_live_abcdefghij0123456789ABCD"),
    ("internal_ip", "10.11.12.13"),
]


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


def _eff():
    return {
        "scan_controls_configured": True,
        "tier1_output": {"enabled": True, "target_mode": "entire", "key_path": "",
                         "strict_mode": "fail_open", "control_id": "t1", "action": "redact"},
        "tier2_output": {"enabled": False, "target_mode": "entire", "key_path": "",
                         "strict_mode": "strict", "control_id": None},
    }


async def _scan(text):
    payload = {"content": [{"type": "text", "text": text}]}
    with (
        patch("mcp_scan_orchestrator._get_policy_sync", return_value=None),
        patch("mcp_scan_orchestrator.evaluate_mcp_policies", return_value=EvaluationResult(action="allow")),
    ):
        return await scan_mcp_payload(
            payload, scan_direction="output", enforcement="redact",
            effective_controls=_eff(), org_slug="demo", server_slug="srv", tool_name="fetch",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("label,raw", _SECRETS)
async def test_audit_record_never_contains_raw_value(label, raw):
    scanned, result = await _scan(f"context {raw} more context")

    # The exact surface _record_gateway_event serializes into the MCPEvent:
    audit_blob = json.dumps({
        "findings": [f.to_finding_dict() for f in (result.findings or [])],
        "compliance_tags": list(result.compliance_tags or []),
        "metadata": getattr(result, "metadata", None) or {},
    })
    assert raw not in audit_blob, f"{label}: raw value leaked into the AUDIT record"

    # Sanity: the value WAS detected (a finding exists) and the egress result is redacted,
    # so this is not a vacuous pass (the scanner actually saw the secret).
    assert result.findings, f"{label}: expected the scanner to detect the value"
    assert raw not in json.dumps(scanned), f"{label}: raw value survived in the redacted result"
