"""Regression: legacy ``tag`` posture is observe-only for static floors (E12/credential).

``tag`` must behave like ``monitor`` for hardcoded detection floors while explicit
policy block rules remain honored under ``tag`` (not under ``monitor``).
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from mcp_scan_orchestrator import _is_observe_only_posture, _scan_text_tier1_sync


def test_is_observe_only_posture_tag_and_monitor():
    assert _is_observe_only_posture("tag") is True
    assert _is_observe_only_posture("monitor") is True
    assert _is_observe_only_posture("TAG") is True
    assert _is_observe_only_posture("redact") is False
    assert _is_observe_only_posture("block") is False


@pytest.mark.asyncio
async def test_policy_block_honored_under_tag_not_monitor():
    """Explicit policy block under tag blocks; under monitor it does not."""
    policies = [
        {
            "policy": {"id": 1, "name": "P1", "code": "P1", "redaction_fields": []},
            "rules": [
                {
                    "id": 10,
                    "name": "block-ssn",
                    "action": "block",
                    "enabled": True,
                    "priority": 1,
                    "condition": {"regex": r"\d{3}-\d{2}-\d{4}"},
                }
            ],
        }
    ]
    with patch("mcp_scan_orchestrator._get_policy_sync") as ps:
        ps.return_value.get_policies_for_server.return_value = policies
        _, _, blocked_tag, _ = _scan_text_tier1_sync(
            "SSN 123-45-6789",
            scan_direction="input",
            enforcement="tag",
            full_payload={},
            org_slug="zeroshield",
            server_slug="everything-1",
            tool_name="echo",
        )
        _, _, blocked_mon, _ = _scan_text_tier1_sync(
            "SSN 123-45-6789",
            scan_direction="input",
            enforcement="monitor",
            full_payload={},
            org_slug="zeroshield",
            server_slug="everything-1",
            tool_name="echo",
        )
    assert blocked_tag is True
    assert blocked_mon is False


def test_e12_floor_gate_tag_is_observe_only():
    from mcp_proxy import _is_observe_only_posture as proxy_observe

    # E12 / credential floors use ``not _is_observe_only_posture(scan_action)``.
    assert proxy_observe("tag") is True
    assert proxy_observe("monitor") is True
    assert proxy_observe("redact") is False
    assert proxy_observe("block") is False


@pytest.mark.asyncio
async def test_policy_redact_applies_to_payload_under_tag():
    """Policy redact rules mutate args under tag (observe-only for static floors only)."""
    from mcp_scan_orchestrator import scan_mcp_payload

    policies = [
        {
            "policy": {"id": 2, "name": "PII", "code": "PII_MCP_2", "redaction_fields": []},
            "rules": [
                {
                    "id": 20,
                    "name": "US Social Security Number",
                    "action": "redact",
                    "enabled": True,
                    "priority": 1,
                    "condition": {"regex": r"\d{3}-\d{2}-\d{4}"},
                    "redaction": {"preset": "ssn"},
                }
            ],
        }
    ]
    effective = {
        "scan_controls_configured": False,
        "tier1_input": {
            "enabled": True,
            "target_mode": "entire",
            "key_path": "",
            "strict_mode": "fail_open",
            "action": "inherit",
        },
        "tier1_output": {"enabled": True, "target_mode": "entire", "key_path": "", "strict_mode": "fail_open"},
        "tier2_input": {"enabled": False},
        "tier2_output": {"enabled": False},
    }
    with patch("mcp_scan_orchestrator._get_policy_sync") as ps:
        ps.return_value.get_policies_for_server.return_value = policies
        out, res = await scan_mcp_payload(
            {"message": "SSN 123-45-6789"},
            scan_direction="input",
            enforcement="tag",
            effective_controls=effective,
            org_slug="zeroshield",
            server_slug="everything-1",
            tool_name="echo",
        )
    assert res.blocked is False
    assert out["message"] != "SSN 123-45-6789"
    assert "123-45-6789" not in out["message"]
