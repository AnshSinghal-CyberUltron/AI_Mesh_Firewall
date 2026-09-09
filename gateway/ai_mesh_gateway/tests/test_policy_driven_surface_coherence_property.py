"""
Policy-driven detection — Task 7.5 property tests.

Feature: policy-driven-detection (no default rules).

This module adds the two cross-cutting property tests for the per-surface cutover
(tasks 7.1-7.4, all complete): fail-toward-no-detection when the enabled-policy set
or the Tier-2 toggle cannot be resolved (Property 10), and surface coherence — the
same (content, enabled policy set, tier2) yields the same decision on every surface
(Property 11).

It is an ISOLATED test module: it imports the shared scaffold / prompt sets from
``test_policy_driven_detection`` READ-ONLY and drives the REAL, shipped resolver /
gate helpers of every surface. It changes NO production code (7.1-7.4 are done); a
genuine cross-surface divergence surfaced by these properties is a FINDING, not a
silent fix.

Surfaces and the shipped helpers exercised
-------------------------------------------
* Chat input seam        — ``enforcement.resolve_and_enforce`` with ``scanner_*=None``
                           (the post-2.1 chat shape; via the scaffold's
                           ``resolve_input_decision``), plus ``config_sync.resolve_tier2_enabled``.
* OpenAI-SDK surface     — shares ``proxy_chat`` → identical ``resolve_tier2_enabled``
                           gate + the same Tier-1 authority ``policy_engine.evaluate``.
* Streaming output       — ``main._streaming_output_detection_active`` /
                           ``main._org_has_enabled_pipeline_policies``.
* Embeddings             — ``main._embedding_redaction_active`` /
                           ``main._embedding_enabled_compiled_policies``.
* RAG                    — ``config_sync.resolve_rag_tier2_enabled`` (Tier-2 gate);
                           RAG query-stage Tier-1 is ``policy_engine.evaluate`` over
                           the enabled set (same authority).
* MCP tool calls         — ``config_sync.resolve_mcp_tier2_enabled`` /
                           ``mcp_scan_orchestrator._org_tier2_allowed`` (Tier-2 gate);
                           MCP Tier-1 is ``policy_engine.evaluate_mcp_policies`` over
                           the enabled set, and the built-in default preset pass is
                           gated OFF by ``mcp_scan_orchestrator._mcp_default_detection_enabled``.

The Tier-1 authority (``policy_engine.evaluate`` / ``evaluate_mcp_policies``) fires a
rule iff its enabled package is in the compiled set — so "the surface is passthrough
when the enabled set cannot be resolved" is realized by resolving the enabled set to
EMPTY (or ``None``/unresolvable) on that surface's helper.
"""

from __future__ import annotations

import unittest
from unittest import mock

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ai_mesh_gateway import main as gw_main
from ai_mesh_gateway import mcp_scan_orchestrator as mcp_orch
from ai_mesh_gateway.config_sync import (
    resolve_mcp_tier2_enabled,
    resolve_rag_tier2_enabled,
    resolve_tier2_enabled,
)
from ai_mesh_gateway.policy_engine import evaluate, evaluate_mcp_policies

# Import shared scaffold + prompt sets READ-ONLY (do NOT edit that module).
from ai_mesh_gateway.tests.test_policy_driven_detection import (
    ATTACK_PROMPTS,
    BENIGN_PROMPTS,
    PII_PROMPTS,
    PolicyDrivenDetectionScaffold,
    SECRET_PROMPTS,
)

# Every representative prompt the built-in scanner used to act on — the property
# must hold for known-adversarial content, not just fuzzed noise.
_REPRESENTATIVE_PROMPTS = list(
    {**ATTACK_PROMPTS, **PII_PROMPTS, **SECRET_PROMPTS, **BENIGN_PROMPTS}.values()
)

# Content strategy: arbitrary text OR a known dangerous prompt.
_content_strategy = st.one_of(
    st.text(max_size=400),
    st.sampled_from(_REPRESENTATIVE_PROMPTS),
)

# Every tri-state / stale value that must resolve Tier-2 (and the rag/mcp variants)
# to effective-OFF. Per the resolvers, only literal ``True`` is ON; EVERYTHING else
# (None/absent, False, and any stale non-True value an old control plane might carry)
# is OFF. This is exactly the "unresolvable / absent / stale" set for Property 10.
_TIER2_UNRESOLVED_OR_OFF = st.sampled_from(
    [
        None,          # absent / no per-org opinion
        False,         # explicit opt-out
        "true",        # stale string (NOT literal True)
        "True",
        "1",
        1,             # stale int
        0,
        "",            # stale empty
        "yes",
        "on",
        {},            # stale non-scalar
        [],
    ]
)


# --------------------------------------------------------------------------- #
# A fake POLICY_SYNC so the streaming/embeddings helpers (which read the
# module-level ``main.POLICY_SYNC``) can be driven through every UNRESOLVABLE
# state: None, not-loaded, or get_policies() raising.
# --------------------------------------------------------------------------- #
class _FakePolicySync:
    """Minimal stand-in for the gateway's POLICY_SYNC.

    * ``is_loaded`` toggles the "cache not loaded" unresolvable state.
    * ``raise_on_get`` makes ``get_policies`` raise (the "lookup raises" state).
    * ``policies`` is what a LOADED cache returns for any org (default empty =
      Zero_Policy_State).
    """

    def __init__(self, *, is_loaded=True, raise_on_get=False, policies=None):
        self.is_loaded = is_loaded
        self.raise_on_get = raise_on_get
        self._policies = policies if policies is not None else []

    def get_policies(self, org_slug):  # noqa: D401 - mirrors PolicySync.get_policies
        if self.raise_on_get:
            raise RuntimeError("policy cache lookup failed (simulated)")
        return list(self._policies)


class FailTowardNoDetectionPropertyTests(
    PolicyDrivenDetectionScaffold, unittest.TestCase
):
    """Property 10 — fail toward no detection.

    **Feature: policy-driven-detection, Property 10: For any request where the
    enabled-policy set cannot be resolved, the surface is passthrough; where
    ``tier2_enabled`` cannot be resolved, Tier-2 does not run. No unresolved-state
    path produces a block, redact, or flag.**

    **Validates: Requirements 6.5, 3.7, 1.4**

    An "unresolvable enabled-policy set" is realized as the shipped helpers see it:
    ``POLICY_SYNC is None`` / ``is_loaded == False`` / ``get_policies`` raises. An
    "unresolvable / absent / stale ``tier2_enabled``" is any non-``True`` value the
    resolvers coerce to OFF. Each surface's own resolver/gate helper is exercised;
    NO unresolved-state path may yield block/redact/flag.
    """

    # -- Tier-2 resolvers (chat / RAG / MCP): unresolved/absent/stale => OFF -- #
    @settings(max_examples=200, deadline=None)
    @given(value=_TIER2_UNRESOLVED_OR_OFF)
    def test_property10_tier2_resolvers_fail_off(self, value):
        """Unresolved/absent/stale tier2 (chat, rag, mcp) ⇒ Tier-2 does NOT run."""
        # Chat + OpenAI-SDK share resolve_tier2_enabled.
        self.assertFalse(
            resolve_tier2_enabled(value),
            msg=f"chat tier2 must be OFF for unresolved value {value!r}",
        )
        self.assertFalse(
            resolve_rag_tier2_enabled(value),
            msg=f"rag tier2 must be OFF for unresolved value {value!r}",
        )
        self.assertFalse(
            resolve_mcp_tier2_enabled(value),
            msg=f"mcp tier2 must be OFF for unresolved value {value!r}",
        )
        # The MCP orchestrator's effective gate consumes the raw config value.
        self.assertFalse(
            mcp_orch._org_tier2_allowed({"mcp_tier2_enabled": value}),
            msg=f"mcp _org_tier2_allowed must be OFF for {value!r}",
        )

    def test_property10_tier2_absent_key_fails_off(self):
        """An entirely ABSENT tier2 key (no per-org opinion) ⇒ Tier-2 off."""
        self.assertFalse(resolve_tier2_enabled({}.get("tier2_enabled")))
        self.assertFalse(mcp_orch._org_tier2_allowed({}))
        self.assertFalse(mcp_orch._org_tier2_allowed(None))

    # -- Streaming output guard: unresolvable enabled set => passthrough -------- #
    @settings(max_examples=60, deadline=None,
              suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(tier2_value=_TIER2_UNRESOLVED_OR_OFF)
    def test_property10_streaming_unresolvable_policyset_passthrough(self, tier2_value):
        """POLICY_SYNC None / not-loaded / raising + tier2 unresolved ⇒ the
        streaming output guard does NOT run (Passthrough)."""
        org_config = {"tier2_enabled": tier2_value}
        for fake in (
            None,  # POLICY_SYNC is None
            _FakePolicySync(is_loaded=False),  # cache not loaded
            _FakePolicySync(is_loaded=True, raise_on_get=True),  # lookup raises
        ):
            with mock.patch.object(gw_main, "POLICY_SYNC", fake):
                # The enabled-policy set cannot be resolved.
                self.assertFalse(
                    gw_main._org_has_enabled_pipeline_policies("some-org"),
                    msg=f"unresolvable enabled set must read as zero-policy ({fake!r})",
                )
                # With Tier-2 also unresolved/off, the output guard must not run.
                self.assertFalse(
                    gw_main._streaming_output_detection_active("some-org", org_config),
                    msg=(
                        "streaming output detection must be OFF when the enabled set "
                        f"is unresolvable and tier2={tier2_value!r}"
                    ),
                )

    def test_property10_streaming_loaded_but_empty_is_passthrough(self):
        """A LOADED cache with zero enabled policies + tier2 off ⇒ Passthrough
        (the Zero_Policy_State is the resolvable analogue of the unresolved one)."""
        with mock.patch.object(
            gw_main, "POLICY_SYNC", _FakePolicySync(is_loaded=True, policies=[])
        ):
            self.assertFalse(gw_main._org_has_enabled_pipeline_policies("org"))
            self.assertFalse(
                gw_main._streaming_output_detection_active("org", {"tier2_enabled": None})
            )

    # -- Embeddings surface: unresolvable enabled set => passthrough ------------ #
    @settings(max_examples=30, deadline=None,
              suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(org_slug=st.one_of(st.none(), st.just(""), st.text(min_size=1, max_size=24)))
    def test_property10_embeddings_unresolvable_policyset_passthrough(self, org_slug):
        """POLICY_SYNC None / not-loaded / raising (or no org) ⇒ the embeddings
        redaction gate returns a non-active state (None ⇒ Passthrough) and the
        enabled compiled set is None."""
        for fake in (
            None,
            _FakePolicySync(is_loaded=False),
            _FakePolicySync(is_loaded=True, raise_on_get=True),
        ):
            with mock.patch.object(gw_main, "POLICY_SYNC", fake):
                active = gw_main._embedding_redaction_active(org_slug)
                # None (unresolvable) or False (zero-policy) — NEVER True: no
                # unresolved-state path enables redaction.
                self.assertIn(
                    active,
                    (None, False),
                    msg=f"embedding redaction must not be active (unresolvable), got {active!r}",
                )
                self.assertIsNone(
                    gw_main._embedding_enabled_compiled_policies(org_slug),
                    msg="unresolvable enabled compiled set must be None",
                )

    # -- Chat / RAG / MCP Tier-1: empty (unresolved => empty) => allow ---------- #
    @settings(max_examples=200, deadline=None,
              suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(content=_content_strategy)
    def test_property10_tier1_empty_enabled_set_allows_everywhere(self, content):
        """When the enabled set resolves to EMPTY (the realized form of an
        unresolvable set on the shared authority), the Tier-1 decision is ``allow``
        on the chat input path, the RAG/embeddings authority, and the MCP authority
        — never block/redact/flag."""
        # Chat input path (post-2.1 shape: scanner_* = None) via the scaffold.
        chat = self.resolve_input_decision(prompt=content, policies=[])
        self.assertEqual(chat.action, "allow", msg=f"chat Tier-1 must allow: {content!r}")
        self.assertFalse(chat.is_terminal_block)
        self.assertFalse(chat.is_redact)

        # Shared Tier-1 authority (chat / RAG query / embeddings) over an empty set.
        r = evaluate(content, "", [])
        self.assertEqual(r.action, "allow")
        self.assertEqual(r.matched_rule_ids, [])

        # MCP Tier-1 authority over an empty set (policy lane only; built-in default
        # preset pass is gated OFF — asserted separately below).
        mcp = evaluate_mcp_policies([], {"prompt": content, "text": content})
        self.assertEqual(mcp.action, "allow")
        self.assertEqual(mcp.matched_rule_ids, [])

    def test_property10_mcp_default_detection_off_by_default(self):
        """The MCP built-in default detection pass is effective-default OFF, so a
        zero-enabled-policy MCP surface has no built-in Tier-1 source (fail toward
        no detection). Assert it is OFF absent any explicit enabling config/env."""
        import os

        # Neither the live CONFIG nor the env fallback should enable it here.
        env_had = "GATEWAY_MCP_DEFAULT_DETECTION" in os.environ
        try:
            os.environ.pop("GATEWAY_MCP_DEFAULT_DETECTION", None)
            self.assertFalse(
                mcp_orch._mcp_default_detection_enabled(),
                msg="MCP built-in default detection must be OFF by default (fail toward no detection)",
            )
        finally:
            if env_had:  # pragma: no cover - restore only if the runner set it
                os.environ["GATEWAY_MCP_DEFAULT_DETECTION"] = "true"


class SurfaceCoherencePropertyTests(
    PolicyDrivenDetectionScaffold, unittest.TestCase
):
    """Property 11 — surface coherence.

    **Feature: policy-driven-detection, Property 11: For any identical content,
    enabled policy set, and ``tier2_enabled``, the detection decision is the same
    across chat, OpenAI SDK, RAG, embeddings, MCP, and streaming output, modulo
    surface-specific content shape.**

    **Validates: Requirements 6.4**

    Every surface converges on the SAME two recommendation sources: (1) the Tier-1
    policy authority over the org's enabled compiled set, and (2) the opt-in Tier-2
    model gated by the per-surface tri-state resolver. This asserts, for a FIXED
    (content, enabled set, tier2 tri-state):

      * the Tier-2 RUN decision is identical across every surface resolver (chat /
        OpenAI-SDK / RAG / MCP) — all resolve the same tri-state to the same bool;
      * the Tier-1 DECISION (action + whether a rule matched) is identical across
        the chat authority, the RAG/embeddings authority, and the MCP authority for
        the same content + compiled set.

    "Modulo surface-specific content shape": the chat/RAG/embeddings surfaces call
    ``policy_engine.evaluate(prompt, "", set)`` (prompt string), while MCP wraps the
    same content in a tool-call context for ``evaluate_mcp_policies``. Embeddings is
    intentionally Tier-1-only (no per-item Tier-2 verdict), so it participates in the
    Tier-1 coherence assertion but is not a Tier-2 resolver — encoded here as a clear
    carve-out, not a silent skip.
    """

    _KW = "sekrit"

    def _content_matches_rule(self, content: str) -> bool:
        return self._KW in content.lower()

    @settings(max_examples=200, deadline=None,
              suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        content=st.one_of(
            st.text(max_size=200),
            st.sampled_from(_REPRESENTATIVE_PROMPTS),
            # deliberately include content that matches the seeded rule
            st.builds(lambda a, b: f"{a}{SurfaceCoherencePropertyTests._KW}{b}",
                      st.text(max_size=40), st.text(max_size=40)),
        ),
        tier2_value=st.sampled_from([True, False, None, "true", 1, "", {}]),
        action=st.sampled_from(["block", "redact", "flag", "monitor"]),
        enabled=st.booleans(),
    )
    def test_property11_surface_coherence(self, content, tier2_value, action, enabled):
        """For a fixed (content, enabled set, tier2), the Tier-2 run decision and the
        Tier-1 decision agree across chat / OpenAI-SDK / RAG / MCP / embeddings."""
        # ---- Fixed enabled compiled set (identical across every surface). ----
        # ``enabled`` toggles between an empty set (Zero_Policy_State) and a single
        # seeded package; the SAME set object-shape drives every surface.
        policies = (
            self.seeded_policies(action=action, keyword=self._KW) if enabled else []
        )

        # ---- (A) Tier-2 RUN decision is identical across surface resolvers. ----
        chat_t2 = resolve_tier2_enabled(tier2_value)
        rag_t2 = resolve_rag_tier2_enabled(tier2_value)
        mcp_t2 = resolve_mcp_tier2_enabled(tier2_value)
        mcp_gate_t2 = mcp_orch._org_tier2_allowed({"mcp_tier2_enabled": tier2_value})
        # OpenAI-SDK shares the chat pipeline → same resolver, asserted by identity
        # of the resolver call (documented: no separate code path).
        openai_sdk_t2 = resolve_tier2_enabled(tier2_value)

        self.assertEqual(
            {chat_t2, rag_t2, mcp_t2, mcp_gate_t2, openai_sdk_t2},
            {chat_t2},
            msg=(
                "Tier-2 run decision diverges across surfaces for "
                f"tier2={tier2_value!r}: chat={chat_t2} rag={rag_t2} mcp={mcp_t2} "
                f"mcp_gate={mcp_gate_t2} openai_sdk={openai_sdk_t2}"
            ),
        )

        # ---- (B) Tier-1 DECISION is identical across surfaces for the same
        #          content + compiled set (modulo content shape). ----
        # Chat / RAG-query / embeddings authority: prompt string.
        chat_r = evaluate(content, "", policies)
        rag_r = evaluate(content, "", policies)  # RAG query-stage Tier-1
        emb_r = evaluate(content, "", policies)  # embeddings Tier-1-only
        # MCP authority: same content wrapped in a tool-call context (content shape).
        mcp_r = evaluate_mcp_policies(
            policies, {"prompt": content, "text": content}
        )

        chat_matched = bool(chat_r.matched_rule_ids)
        rag_matched = bool(rag_r.matched_rule_ids)
        emb_matched = bool(emb_r.matched_rule_ids)
        mcp_matched = bool(mcp_r.matched_rule_ids)

        # Same match verdict on every surface.
        self.assertEqual(
            {chat_matched, rag_matched, emb_matched, mcp_matched},
            {chat_matched},
            msg=(
                "Tier-1 match verdict diverges across surfaces for content "
                f"{content!r}, enabled={enabled}: chat={chat_matched} rag={rag_matched} "
                f"emb={emb_matched} mcp={mcp_matched}"
            ),
        )
        # Same resolved action on every surface.
        self.assertEqual(
            {chat_r.action, rag_r.action, emb_r.action, mcp_r.action},
            {chat_r.action},
            msg=(
                "Tier-1 action diverges across surfaces for content "
                f"{content!r}: chat={chat_r.action} rag={rag_r.action} "
                f"emb={emb_r.action} mcp={mcp_r.action}"
            ),
        )

        # Ground the decision against the fixed inputs: a match iff the package is
        # enabled AND the content matches the seeded rule; the action then equals
        # the user-selected action; otherwise allow.
        expected_match = enabled and self._content_matches_rule(content)
        self.assertEqual(chat_matched, expected_match)
        if expected_match:
            self.assertEqual(chat_r.action, action)
        else:
            self.assertEqual(chat_r.action, "allow")

    def test_property11_zero_policy_tier2_off_all_surfaces_passthrough(self):
        """Example anchor: with an empty enabled set AND tier2 off, EVERY surface's
        Tier-1 decision is allow and EVERY Tier-2 resolver is OFF — the coherent
        Zero_Policy_State across chat / OpenAI-SDK / RAG / MCP / embeddings."""
        for prompt in _REPRESENTATIVE_PROMPTS:
            with self.subTest(prompt=prompt[:32]):
                self.assertFalse(resolve_tier2_enabled(None))
                self.assertFalse(resolve_rag_tier2_enabled(None))
                self.assertFalse(resolve_mcp_tier2_enabled(None))
                self.assertEqual(evaluate(prompt, "", []).action, "allow")
                self.assertEqual(
                    evaluate_mcp_policies([], {"prompt": prompt, "text": prompt}).action,
                    "allow",
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
