"""Regression test for FINDING #24 — scan-control conflict-resolution determinism.

Root cause (see docs/mcp/MCP_VALIDATION_ROOTCAUSE_REPORT.md, commit 3f6fc3c1):
``_pick_control`` scores candidates by ``sort_key = (SCOPE_RANK[scope], priority)`` and
picks ``max(candidates, key=sort_key)``. Two rows that are equal on
(scope_type, server_id, tool_name, tier, direction, priority) but conflict on
``action``/``enabled`` tie in the sort key, so ``max()`` returns whichever appears FIRST
in ``rows``. The DB query (MCPScanControl.objects.filter(...), no ORDER BY beyond
Meta.ordering=["-priority","tier","direction"], which does not discriminate rows already
equal on those three) therefore resolves the conflict by unspecified physical order —
a security-relevant nondeterminism (a ``block`` can be silently overridden by a tied
``monitor``). There is no UniqueConstraint on the model and no duplicate-guard in
MCPScanControlSerializer.validate, so such duplicate rows are creatable via the API.

This test encodes the DESIRED invariant: conflict resolution must NOT depend on row order.
It is marked ``expectedFailure`` because the fix is not yet applied (the three fix sites —
_pick_control sort_key, serializer duplicate-guard, model UniqueConstraint — are currently
being concurrently refactored). When the fix lands (e.g. a stable ``control_id`` tiebreak
and/or most-restrictive-wins), REMOVE the decorator: the test will then pass and guard
against regression.

scan_controls.py is pure Python (no Django models), so SimpleTestCase (no DB) suffices.
"""

import unittest

from django.test import SimpleTestCase

from mcp_connector.scan_controls import resolve_effective_controls


def _tool_row(rid: str, action: str, *, enabled: bool = True) -> dict:
    """A tool-scope tier1/input row — identical scope/server/tool/priority to its sibling."""
    return {
        "id": rid,
        "tier": "tier1",
        "enabled": enabled,
        "direction": "input",
        "scope_type": "tool",
        "server_id": "srv-1",
        "tool_name": "send_mail",
        "target_mode": "entire",
        "key_path": "",
        "strict_mode": "fail_open",
        "action": action,
        "priority": 5,
    }


class ScanControlConflictDeterminismTests(SimpleTestCase):
    def _resolve(self, rows):
        return resolve_effective_controls(
            rows, server_id="srv-1", tool_name="send_mail"
        )["tier1_input"]

    @unittest.expectedFailure  # FINDING #24 unfixed — remove when conflict resolution is deterministic
    def test_action_conflict_is_order_independent(self):
        block_row = _tool_row("BLOCK", "block")
        monitor_row = _tool_row("MONITOR", "monitor")

        forward = self._resolve([block_row, monitor_row])["action"]
        reverse = self._resolve([monitor_row, block_row])["action"]

        # Two identical-scope/priority conflicting rows must resolve the SAME way
        # regardless of input order. Today they do not (max() is first-of-ties).
        self.assertEqual(
            forward,
            reverse,
            "scan-control conflict resolution is order-dependent (FINDING #24): "
            f"forward={forward!r} reverse={reverse!r}",
        )

    @unittest.expectedFailure  # FINDING #24 unfixed — remove when most-restrictive-wins (or deterministic) lands
    def test_enabled_conflict_is_order_independent(self):
        on_row = _tool_row("ON", "block", enabled=True)
        off_row = _tool_row("OFF", "block", enabled=False)

        forward = self._resolve([on_row, off_row])["enabled"]
        reverse = self._resolve([off_row, on_row])["enabled"]

        self.assertEqual(
            forward,
            reverse,
            "scan-control enabled/disabled conflict is order-dependent (FINDING #24): "
            f"forward={forward!r} reverse={reverse!r}",
        )
