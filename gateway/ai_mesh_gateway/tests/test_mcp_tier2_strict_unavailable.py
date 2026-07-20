"""Regression test for FINDING #27 — MCP Tier-2 must honor org `tier2_strict` on scanner-UNAVAILABLE.

Root cause (see docs/mcp/MCP_VALIDATION_ROOTCAUSE_REPORT.md, commit 7b8a0470): the CHAT path
(`main.py:6923/6957`) fails CLOSED (HTTP 451 `tier2_unavailable_strict`) when Tier-2 is unavailable and
the org has `tier2_strict=True` — but the MCP path (`mcp_scan_orchestrator._scan_text_tier2`) does not.
Its scanner-None branch (`:745`) returns `blocked=False` ("scanner_unavailable") UNCONDITIONALLY, ignoring
`org_tier2_strict`. So on a Bedrock outage a strict org's MCP tool calls silently skip Tier-2 (fail open).

Model comment `core/models.py:1055-1058` documents `tier2_strict` as controlling behavior "when Tier-2 is
unavailable ... `tier2_unavailable_strict` rather than silently passing through" — the MCP path violates it.

Live-corroborated: zeroshield's Redis config has `mcp_tier2_enabled=True` + `tier2_strict=True` (a real
production org that would fail open on a Bedrock outage).

This encodes the DESIRED invariant: scanner unavailable + `org_tier2_strict=True` ⇒ fail CLOSED
(blocked=True). Marked `expectedFailure` because the fix is not yet applied (co-mingled with a concurrent
gateway refactor in `mcp_scan_orchestrator.py`). When the fix lands (honor `org_tier2_strict` on the
scanner-None branch, e.g. return `blocked=True, "tier2_unavailable_strict"`), REMOVE the decorator.

`_scan_text_tier2` is importable standalone (no Django); its scanner-None branch is pure.
"""

import asyncio
import unittest


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


class Tier2StrictUnavailableTests(unittest.TestCase):
    def _call(self, *, scanner, strict):
        from mcp_scan_orchestrator import _scan_text_tier2

        return _run(
            _scan_text_tier2(
                "some text",
                scan_direction="input",
                enforcement="monitor",
                scanner=scanner,
                org_slug="zeroshield",
                org_tier2_override=True,
                org_tier2_strict=strict,
                strict_mode="strict",
            )
        )

    @unittest.expectedFailure  # FINDING #27 unfixed — remove when scanner-None honors org_tier2_strict
    def test_scanner_unavailable_strict_fails_closed(self):
        # scanner is None (Tier-2 unavailable) + org tier2_strict=True ⇒ must FAIL CLOSED.
        _text, _findings, blocked, reason = self._call(scanner=None, strict=True)
        self.assertTrue(
            blocked,
            f"MCP Tier-2 fails OPEN on scanner-unavailable despite org_tier2_strict=True "
            f"(FINDING #27): blocked={blocked} reason={reason!r}",
        )

    def test_scanner_unavailable_non_strict_fails_open(self):
        # Control: with org_tier2_strict=False, fail-open on unavailable is the CORRECT behavior.
        _text, _findings, blocked, _reason = self._call(scanner=None, strict=False)
        self.assertFalse(blocked, "non-strict org should fail OPEN when Tier-2 is unavailable")


if __name__ == "__main__":
    unittest.main()
