"""
Policy-driven detection — Task 4.1: Tier-2 opt-in gating (chat + OpenAI-SDK).

Feature: policy-driven-detection (no default rules).

Task 4.1 makes Tier-2 (the semantic MODEL scan) OPT-IN, model-only, and
effective-default OFF:

  * ``config_sync.resolve_tier2_enabled`` (re-exported from ``config``) resolves
    the nullable tri-state ``tier2_enabled`` config value to its EFFECTIVE value:
    ``None`` / absent -> ``False`` (OFF), explicit ``True`` -> ``True``, explicit
    ``False`` (or any stale non-bool) -> ``False``. The raw tri-state stored value
    is NOT collapsed — only the effective resolution defaults OFF.

  * ``main.proxy_chat`` gates the ``INPUT_SCANNER.scan_prompt_with_tier2`` call on
    that resolved effective value: Tier-2 runs ONLY when it is ``True``. When OFF,
    the chat path runs Tier-1 only (``scan_prompt``) and Tier-2 contributes NO
    verdict (and the async Tier-2 post-scan job is NOT enqueued). This single gate
    governs BOTH the native surface and the OpenAI-SDK surface, because
    ``/v1/chat/completions`` (what the OpenAI SDK calls) is served by the same
    ``proxy_chat`` handler.

This module does NOT edit ``test_policy_driven_detection.py`` (another agent owns
it). It (1) unit-tests the resolver directly, and (2) reproduces the exact
``proxy_chat`` dispatch structure against a SPY scanner so we can observe — via
recorded invocation — that the Tier-2 model scan is skipped when OFF and run
(feeding enforcement) when ON, without standing up the full app stack.

Validates: Requirements 3.1, 3.2, 3.4, 3.5, 3.7 (Properties 6 + 8).
"""

from __future__ import annotations

import asyncio
import unittest

from ai_mesh_gateway.config import resolve_tier2_enabled as resolve_from_config
from ai_mesh_gateway.config_sync import resolve_tier2_enabled
from ai_mesh_gateway.enforcement import PipelineDecision, resolve_and_enforce
from ai_mesh_gateway.scanner import ScanVerdict


# --------------------------------------------------------------------------- #
# (1) Resolver unit tests — None/absent -> OFF, False -> OFF, True -> ON.
# --------------------------------------------------------------------------- #
class ResolveTier2EnabledTests(unittest.TestCase):
    """``resolve_tier2_enabled`` tri-state -> effective bool.

    **Validates: Requirements 3.1, 3.2, 3.7** (Property 6: an unresolved/absent
    value resolves to not-executing).
    """

    def test_none_resolves_off(self):
        """None (no per-org opinion) => effective OFF."""
        self.assertIs(resolve_tier2_enabled(None), False)

    def test_false_resolves_off(self):
        """Explicit False => OFF."""
        self.assertIs(resolve_tier2_enabled(False), False)

    def test_true_resolves_on(self):
        """Explicit True => ON (the ONLY value that turns Tier-2 on)."""
        self.assertIs(resolve_tier2_enabled(True), True)

    def test_absent_key_resolves_off(self):
        """A missing key (``dict.get`` -> None) => OFF (default OFF)."""
        org_config: dict = {}
        self.assertIs(resolve_tier2_enabled(org_config.get("tier2_enabled")), False)

    def test_stale_nonbool_values_resolve_off(self):
        """Any stale/legacy non-``True`` value from an old control plane => OFF.

        Only ``value is True`` turns Tier-2 on, so a truthy string / 1 / "true"
        from a mis-typed stale payload never accidentally enables Tier-2.
        """
        for stale in ("true", "True", 1, "1", "yes", [], {}, 0, "", "false"):
            with self.subTest(stale=stale):
                self.assertIs(resolve_tier2_enabled(stale), False)

    def test_config_reexport_is_same_function(self):
        """``config.resolve_tier2_enabled`` is the SAME single-source resolver
        the gateway proxy_chat gate imports from ``config_sync`` (parity)."""
        self.assertIs(resolve_from_config, resolve_tier2_enabled)

    def test_raw_tristate_is_not_collapsed(self):
        """The resolver does NOT mutate the stored config; it only READS the
        tri-state. Absent/None/False are all preserved distinctly in storage."""
        cfg_none = {"tier2_enabled": None}
        cfg_false = {"tier2_enabled": False}
        cfg_true = {"tier2_enabled": True}
        # Effective resolution:
        self.assertFalse(resolve_tier2_enabled(cfg_none["tier2_enabled"]))
        self.assertFalse(resolve_tier2_enabled(cfg_false["tier2_enabled"]))
        self.assertTrue(resolve_tier2_enabled(cfg_true["tier2_enabled"]))
        # Stored raw values are untouched (tri-state preserved).
        self.assertIsNone(cfg_none["tier2_enabled"])
        self.assertIs(cfg_false["tier2_enabled"], False)
        self.assertIs(cfg_true["tier2_enabled"], True)


# --------------------------------------------------------------------------- #
# (2) Chat-path gate: a spy scanner records which scan method the gate calls,
#     reproducing the exact ``proxy_chat`` dispatch structure (main.py ~L8438).
# --------------------------------------------------------------------------- #
class _SpyScanner:
    """Records invocation of the two scan entry points the gate chooses between.

    ``scan_prompt`` = Tier-1 only. ``scan_prompt_with_tier2`` = the Tier-1 +
    Tier-2 MODEL path. The spy lets us assert the gate NEVER calls the Tier-2
    entry point when Tier-2 is OFF, and DOES when it is ON.
    """

    def __init__(self, tier1_verdict: ScanVerdict, tier2_verdict: ScanVerdict):
        self._tier1_verdict = tier1_verdict
        self._tier2_verdict = tier2_verdict
        self.scan_prompt_calls = 0
        self.scan_prompt_with_tier2_calls = 0

    async def scan_prompt(self, text, is_rag=False, toxicity_threshold=None):
        self.scan_prompt_calls += 1
        return self._tier1_verdict

    async def scan_prompt_with_tier2(
        self,
        text,
        is_rag=False,
        org_tier2_override=None,
        org_slug="",
        org_tier2_strict=True,
        toxicity_threshold=None,
        request_id="",
    ):
        self.scan_prompt_with_tier2_calls += 1
        return self._tier2_verdict


async def _run_input_scan_gate(scanner: _SpyScanner, org_config: dict) -> ScanVerdict:
    """Reproduce the exact ``proxy_chat`` Tier-2 gate dispatch (main.py ~L8438).

    Mirrors:
        resolved_tier2_enabled = resolve_tier2_enabled(org_config.get("tier2_enabled"))
        if not resolved_tier2_enabled:      -> scan_prompt (Tier-1 only)
        elif force_sync_tier2:              -> scan_prompt_with_tier2 (Tier-2 model)
        else:                               -> scan_prompt + async Tier-2 post-scan

    Uses ``sync_pre_llm`` (the default execution mode) so ``force_sync_tier2`` is
    True and the ON path takes the ``scan_prompt_with_tier2`` branch.
    """
    tier2_execution_mode = str(
        org_config.get("tier2_execution_mode", "sync_pre_llm")
    ).strip().lower()
    force_sync_tier2 = tier2_execution_mode == "sync_pre_llm"
    resolved_tier2_enabled = resolve_tier2_enabled(org_config.get("tier2_enabled"))

    if not resolved_tier2_enabled:
        return await scanner.scan_prompt("scan-text", is_rag=False)
    if force_sync_tier2:
        return await scanner.scan_prompt_with_tier2(
            "scan-text",
            is_rag=False,
            org_tier2_override=org_config.get("tier2_enabled"),
            org_slug="",
            org_tier2_strict=bool(org_config.get("tier2_strict", True)),
        )
    return await scanner.scan_prompt("scan-text", is_rag=False)


class Tier2GateChatPathTests(unittest.TestCase):
    """The chat-path gate runs Tier-2 iff resolved ``tier2_enabled`` is True.

    **Feature: policy-driven-detection, Property 6: For any request, the Tier-2
    model scan executes if and only if the resolved ``tier2_enabled`` is true;
    when it does not execute it contributes no verdict; an unresolved or absent
    value resolves to not-executing.**

    **Feature: policy-driven-detection, Property 8: no Tier-2 verdict is produced
    when ``tier2_enabled`` is off.**

    **Validates: Requirements 3.2, 3.4, 3.5, 3.7**
    """

    def _make_spy(self):
        # Distinct verdicts so we can tell WHICH path produced the returned
        # verdict: Tier-1 allow vs a Tier-2 model "block".
        tier1 = ScanVerdict(action="allow", tier="tier_1")
        tier2 = ScanVerdict(action="block", threat_type="jailbreak",
                            confidence=0.95, tier="tier_2")
        return _SpyScanner(tier1, tier2)

    def test_tier2_off_none_skips_model_scan_no_verdict(self):
        """tier2_enabled absent/None => Tier-2 model scan NOT invoked; the returned
        verdict is the Tier-1 verdict (Tier-2 contributes nothing)."""
        spy = self._make_spy()
        org_config = {"tier2_enabled": None, "tier2_execution_mode": "sync_pre_llm"}
        verdict = asyncio.run(_run_input_scan_gate(spy, org_config))
        self.assertEqual(spy.scan_prompt_with_tier2_calls, 0)
        self.assertEqual(spy.scan_prompt_calls, 1)
        # No Tier-2 verdict contributed — returned verdict is the Tier-1 one.
        self.assertEqual(verdict.action, "allow")
        self.assertEqual(verdict.tier, "tier_1")

    def test_tier2_off_explicit_false_skips_model_scan(self):
        """tier2_enabled=False => Tier-2 model scan NOT invoked."""
        spy = self._make_spy()
        org_config = {"tier2_enabled": False, "tier2_execution_mode": "sync_pre_llm"}
        verdict = asyncio.run(_run_input_scan_gate(spy, org_config))
        self.assertEqual(spy.scan_prompt_with_tier2_calls, 0)
        self.assertEqual(spy.scan_prompt_calls, 1)
        self.assertEqual(verdict.tier, "tier_1")

    def test_tier2_off_stale_value_skips_model_scan(self):
        """A stale/legacy truthy-string value => still OFF => model scan NOT run."""
        spy = self._make_spy()
        org_config = {"tier2_enabled": "true", "tier2_execution_mode": "sync_pre_llm"}
        asyncio.run(_run_input_scan_gate(spy, org_config))
        self.assertEqual(spy.scan_prompt_with_tier2_calls, 0)
        self.assertEqual(spy.scan_prompt_calls, 1)

    def test_tier2_on_invokes_model_scan(self):
        """tier2_enabled=True => Tier-2 model scan IS invoked and its verdict is
        the one returned (Tier-1-only path NOT taken)."""
        spy = self._make_spy()
        org_config = {"tier2_enabled": True, "tier2_execution_mode": "sync_pre_llm"}
        verdict = asyncio.run(_run_input_scan_gate(spy, org_config))
        self.assertEqual(spy.scan_prompt_with_tier2_calls, 1)
        self.assertEqual(spy.scan_prompt_calls, 0)
        self.assertEqual(verdict.tier, "tier_2")
        self.assertEqual(verdict.action, "block")


class Tier2VerdictFeedsEnforcementTests(unittest.TestCase):
    """When Tier-2 is ON, its model action feeds ``resolve_and_enforce``; when OFF
    the enforcement authority sees no Tier-2 recommendation.

    **Validates: Requirements 3.4, 3.5** (Tier-2 is model-only; its action feeds
    enforcement; no policy feeds Tier-2).
    """

    def _feed_enforcement(self, verdict: ScanVerdict) -> PipelineDecision:
        """Thread a scan verdict into the enforcement authority the way the chat
        path's secondary guard does — the Tier-2 action becomes the scanner
        recommendation; org policy action is None (Tier-2 takes no policy)."""
        return resolve_and_enforce(
            scanner_recommendation=verdict.action,
            scanner_action=verdict.action,
            scanner_threat_type=(verdict.threat_type or None),
            scanner_confidence=(verdict.confidence or None),
            scanner_tier=(verdict.tier or None),
            org_policy_action=None,
            enforcement_mode="block",
        )

    def test_tier2_on_block_verdict_feeds_enforcement_block(self):
        """A Tier-2 model 'block' verdict (high-confidence jailbreak) resolves to a
        terminal block through the enforcement authority."""
        spy = _SpyScanner(
            ScanVerdict(action="allow", tier="tier_1"),
            ScanVerdict(action="block", threat_type="jailbreak",
                        confidence=0.95, tier="tier_2"),
        )
        org_config = {"tier2_enabled": True, "tier2_execution_mode": "sync_pre_llm"}
        verdict = asyncio.run(_run_input_scan_gate(spy, org_config))
        self.assertEqual(verdict.tier, "tier_2")
        decision = self._feed_enforcement(verdict)
        self.assertEqual(decision.action, "block")
        self.assertTrue(decision.is_terminal_block)

    def test_tier2_off_no_verdict_enforcement_allows(self):
        """Tier-2 OFF => Tier-1 allow verdict => enforcement resolves to allow (no
        Tier-2 recommendation contributed)."""
        spy = _SpyScanner(
            ScanVerdict(action="allow", tier="tier_1"),
            ScanVerdict(action="block", threat_type="jailbreak",
                        confidence=0.95, tier="tier_2"),
        )
        org_config = {"tier2_enabled": None, "tier2_execution_mode": "sync_pre_llm"}
        verdict = asyncio.run(_run_input_scan_gate(spy, org_config))
        # Tier-1 verdict only; the Tier-2 'block' was never produced.
        self.assertEqual(verdict.tier, "tier_1")
        self.assertEqual(spy.scan_prompt_with_tier2_calls, 0)
        decision = self._feed_enforcement(verdict)
        self.assertEqual(decision.action, "allow")
        self.assertFalse(decision.is_terminal_block)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
