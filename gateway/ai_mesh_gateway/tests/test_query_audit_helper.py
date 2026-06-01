"""
RED-then-GREEN tests for ``telemetry_ops.emit_query_audit_event``.

These tests cover the D_G5 query-audit helper:
    1. decision="allow" is a no-op (no sink call)
    2. decision="rewrite" emits with event_class=query_rewritten + rule_code
    3. decision="block" emits with event_class=query_blocked + severity=warning
    4. unknown decision logs at INFO and is dropped (no sink call)

Implementation in this commit already exists, so these tests should pass
immediately and act as the regression contract.
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from ai_mesh_gateway.telemetry_ops import (
    EVENT_CLASS_QUERY_BLOCKED,
    EVENT_CLASS_QUERY_DOWNGRADED,
    EVENT_CLASS_QUERY_REWRITTEN,
    emit_query_audit_event,
)


# --------------------------------------------------------------------------- #
# Capture sink                                                                #
# --------------------------------------------------------------------------- #
class _RecordingSink:
    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    async def __call__(self, event: Dict[str, Any]) -> None:
        self.events.append(event)


@pytest.fixture()
def sink():
    return _RecordingSink()


@pytest.fixture()
def patched_sink(sink):
    # Patch the symbol used inside telemetry_ops.emit_operational_event
    with patch("ai_mesh_gateway.telemetry_ops.record_enforcement_event", sink):
        yield sink


# --------------------------------------------------------------------------- #
# Tests                                                                       #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_allow_decision_is_noop(patched_sink):
    await emit_query_audit_event(
        org_slug="acme",
        decision="allow",
        rule_code="pii_email",
        metadata={"input_bytes": 42},
    )
    assert patched_sink.events == []


@pytest.mark.asyncio
async def test_rewrite_emits_query_rewritten_with_rule_code(patched_sink):
    await emit_query_audit_event(
        org_slug="acme",
        decision="rewrite",
        rule_code="pii_email",
        metadata={"input_bytes": 128},
    )
    assert len(patched_sink.events) == 1
    evt = patched_sink.events[0]
    assert evt["event_class"] == EVENT_CLASS_QUERY_REWRITTEN
    assert evt["org_slug"] == "acme"
    assert evt["metadata"]["rule_code"] == "pii_email"
    assert evt["metadata"]["input_bytes"] == 128


@pytest.mark.asyncio
async def test_block_emits_warning_severity(patched_sink):
    await emit_query_audit_event(
        org_slug="acme",
        decision="block",
        rule_code="secret_aws_key",
    )
    assert len(patched_sink.events) == 1
    evt = patched_sink.events[0]
    assert evt["event_class"] == EVENT_CLASS_QUERY_BLOCKED
    assert evt["severity"] == "warning"


@pytest.mark.asyncio
async def test_downgrade_emits_info_severity(patched_sink):
    await emit_query_audit_event(
        org_slug="acme",
        decision="downgrade",
        rule_code="toxicity_high",
    )
    assert len(patched_sink.events) == 1
    evt = patched_sink.events[0]
    assert evt["event_class"] == EVENT_CLASS_QUERY_DOWNGRADED
    assert evt["severity"] == "info"


@pytest.mark.asyncio
async def test_unknown_decision_is_dropped(patched_sink):
    await emit_query_audit_event(
        org_slug="acme",
        decision="nuke",
        rule_code="x",
    )
    assert patched_sink.events == []
