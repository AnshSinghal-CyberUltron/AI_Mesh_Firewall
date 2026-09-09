"""
Policy-driven detection — Task 4.2: property test for Tier-2 opt-in gating.

Feature: policy-driven-detection (no default rules).

Task 4.1 made Tier-2 (the semantic MODEL scan) opt-in, model-only, and
effective-default OFF via ``resolve_tier2_enabled`` (defined in ``config_sync``,
re-exported from ``config``): ``None`` / absent -> ``False`` (OFF), explicit
``True`` -> ``True``, explicit ``False`` (or any stale non-bool) -> ``False``.
``main.proxy_chat`` gates the ``INPUT_SCANNER.scan_prompt_with_tier2`` call on
that resolved effective value: Tier-2 runs ONLY when it resolves ``True``; when
OFF the chat path runs Tier-1-only (``scan_prompt``) and Tier-2 contributes NO
verdict (and no async Tier-2 post-scan is enqueued).

Task 4.1 (``test_policy_driven_tier2_gate.py``) proved this by EXAMPLE across the
tri-state and a fixed list of stale values. Task 4.2 (THIS module) proves the
same invariant as a UNIVERSAL PROPERTY with Hypothesis: for ANY resolved-tier2
tri-state / stale input, the Tier-2 model scan executes IFF
``resolve_tier2_enabled(value) is True``; when it does not execute it contributes
no verdict; an unresolved or absent value resolves to not-executing.

This module does NOT edit ``test_policy_driven_detection.py`` (another agent owns
it) NOR ``test_policy_driven_tier2_gate.py`` (Task 4.1). It imports the
``_SpyScanner`` / ``_run_input_scan_gate`` gate-reproduction READ-ONLY from the
4.1 module so the property exercises the SAME ``proxy_chat`` dispatch structure
(main.py ~L8438) the example tests do.

Validates: Requirements 3.2, 3.7 (Property 6).
"""

from __future__ import annotations

import asyncio
import unittest

from hypothesis import given, settings, strategies as st

from ai_mesh_gateway.config_sync import resolve_tier2_enabled
from ai_mesh_gateway.scanner import ScanVerdict

# Read-only reuse of Task 4.1's spy scanner + gate reproduction (do NOT modify
# that module — we only import its helpers so the property drives the identical
# proxy_chat gate dispatch structure).
from ai_mesh_gateway.tests.test_policy_driven_tier2_gate import (
    _SpyScanner,
    _run_input_scan_gate,
)


# --------------------------------------------------------------------------- #
# Strategy: the full tri-state plus representative STALE / non-bool values that
# an old control plane could put in the synced ``tier2_enabled`` key. Only the
# literal ``True`` should ever resolve to executing; everything else is OFF.
# --------------------------------------------------------------------------- #
_STALE_VALUES = st.sampled_from(
    [
        None,          # absent / no per-org opinion  -> OFF
        True,          # the ONLY on value            -> ON
        False,         # explicit off                 -> OFF
        "true",        # stale string (truthy!)       -> OFF
        "True",
        "false",
        "1",
        "0",
        "yes",
        "no",
        "",
        1,             # truthy int                   -> OFF
        0,
        2,
        -1,
        1.0,           # truthy float                 -> OFF
        0.0,
        [],            # empty / non-empty containers  -> OFF
        [1],
        {},
        {"enabled": True},
        object(),      # arbitrary object              -> OFF
    ]
)

# A pure tri-state strategy (None / True / False) — the canonical stored shape.
_TRISTATE = st.one_of(st.none(), st.booleans())

# Both sync-pre-llm (force_sync_tier2 => on-path takes scan_prompt_with_tier2)
# and any other execution mode (async post-scan path). The gate's ON branch
# differs, but the "runs iff resolved True" invariant must hold for both.
_EXEC_MODE = st.sampled_from(["sync_pre_llm", "async_post_llm", "", "SYNC_PRE_LLM"])


def _make_spy() -> _SpyScanner:
    """A spy whose Tier-1 and Tier-2 verdicts are distinguishable by tier so we
    can tell WHICH scan path produced the returned verdict."""
    tier1 = ScanVerdict(action="allow", tier="tier_1")
    tier2 = ScanVerdict(
        action="block", threat_type="jailbreak", confidence=0.95, tier="tier_2"
    )
    return _SpyScanner(tier1, tier2)


class Tier2OptInGatingProperty(unittest.TestCase):
    """Property 6 — Tier-2 executes iff resolved ``tier2_enabled`` is True.

    **Feature: policy-driven-detection, Property 6: For any request, the Tier-2
    model scan executes if and only if the resolved ``tier2_enabled`` is true;
    when it does not execute it contributes no verdict; an unresolved or absent
    value resolves to not-executing.**

    **Validates: Requirements 3.2, 3.7**
    """

    @settings(deadline=None)
    @given(value=_STALE_VALUES, exec_mode=_EXEC_MODE)
    def test_tier2_does_not_execute_unless_resolved_true(self, value, exec_mode):
        """For ANY stored ``tier2_enabled`` value and ANY execution mode: if the
        resolved value is NOT True the Tier-2 model scan is NEVER invoked and it
        contributes no verdict (the returned verdict is the Tier-1 one). The
        effective-off resolution is exactly ``value is True``.

        The "does-not-execute" direction is mode-independent (an OFF org never
        runs Tier-2 on any path), so this half of the iff holds for EVERY
        execution mode."""
        resolved = resolve_tier2_enabled(value)
        # The resolver's contract: only the literal True is ON; all else OFF.
        self.assertIs(resolved, value is True)

        spy = _make_spy()
        org_config = {"tier2_enabled": value, "tier2_execution_mode": exec_mode}
        verdict = asyncio.run(_run_input_scan_gate(spy, org_config))

        if resolved is not True:
            # Tier-2 does NOT execute on ANY mode: the model scan is never
            # called and contributes NO verdict — the gate returns the Tier-1
            # verdict, produced via the Tier-1-only entry point.
            self.assertEqual(spy.scan_prompt_with_tier2_calls, 0)
            self.assertEqual(spy.scan_prompt_calls, 1)
            self.assertEqual(verdict.tier, "tier_1")

    @settings(deadline=None)
    @given(value=_STALE_VALUES)
    def test_tier2_executes_when_resolved_true_sync_path(self, value):
        """The "executes" direction of the iff, on the synchronous on-path
        (``sync_pre_llm``, the default execution mode where the gate takes the
        observable ``scan_prompt_with_tier2`` branch): the Tier-2 MODEL scan is
        invoked EXACTLY when ``resolve_tier2_enabled(value) is True`` — one model
        scan, no Tier-1-only substitution, and its verdict is returned; when the
        value resolves OFF the model scan is never called."""
        resolved = resolve_tier2_enabled(value)
        spy = _make_spy()
        org_config = {"tier2_enabled": value, "tier2_execution_mode": "sync_pre_llm"}
        verdict = asyncio.run(_run_input_scan_gate(spy, org_config))

        # iff on the observable synchronous path.
        self.assertEqual(spy.scan_prompt_with_tier2_calls == 1, resolved is True)
        if resolved is True:
            self.assertEqual(spy.scan_prompt_with_tier2_calls, 1)
            self.assertEqual(spy.scan_prompt_calls, 0)
            self.assertEqual(verdict.tier, "tier_2")
        else:
            self.assertEqual(spy.scan_prompt_with_tier2_calls, 0)
            self.assertEqual(spy.scan_prompt_calls, 1)
            self.assertEqual(verdict.tier, "tier_1")

    @settings(deadline=None)
    @given(value=_TRISTATE)
    def test_tristate_gate_matches_resolution(self, value):
        """Restricted to the canonical tri-state (None/True/False): the gate runs
        Tier-2 iff the value IS True; None (unresolved/absent) and False both
        resolve to not-executing (fail toward no Tier-2 detection — R3.7)."""
        resolved = resolve_tier2_enabled(value)
        spy = _make_spy()
        org_config = {"tier2_enabled": value, "tier2_execution_mode": "sync_pre_llm"}
        verdict = asyncio.run(_run_input_scan_gate(spy, org_config))

        executed = spy.scan_prompt_with_tier2_calls == 1
        self.assertEqual(executed, resolved is True)
        self.assertEqual(executed, value is True)
        if not executed:
            self.assertEqual(verdict.tier, "tier_1")

    @settings(deadline=None)
    @given(
        # A config that OMITS the key entirely (absent) alongside random stale
        # values under a decoy key — absent must resolve to not-executing.
        decoy=st.dictionaries(
            keys=st.sampled_from(["tier2", "tier_2_enabled", "TIER2_ENABLED"]),
            values=st.booleans(),
            max_size=2,
        ),
        exec_mode=_EXEC_MODE,
    )
    def test_absent_key_never_executes_tier2(self, decoy, exec_mode):
        """When ``tier2_enabled`` is ABSENT from the org config (``dict.get`` ->
        None), Tier-2 never executes regardless of unrelated decoy keys — absent
        resolves to not-executing (Property 6 / R3.7)."""
        org_config = dict(decoy)
        org_config["tier2_execution_mode"] = exec_mode
        # No "tier2_enabled" key at all.
        self.assertNotIn("tier2_enabled", org_config)
        self.assertIs(resolve_tier2_enabled(org_config.get("tier2_enabled")), False)

        spy = _make_spy()
        verdict = asyncio.run(_run_input_scan_gate(spy, org_config))
        self.assertEqual(spy.scan_prompt_with_tier2_calls, 0)
        self.assertEqual(spy.scan_prompt_calls, 1)
        self.assertEqual(verdict.tier, "tier_1")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
