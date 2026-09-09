"""
Policy-driven detection — Task 7.2: RAG (ingest + query) is policy-driven.

Feature: policy-driven-detection (no default rules).

Task 7.2 extends the policy-driven model to the RAG Detection_Surface
(``/v1/rag`` ingest + query, served by ``gateway/ai_mesh_gateway/main.py`` +
``rag_pipeline/query_stage.py``):

  * **Tier-1 = enabled policies ONLY.** The RAG query stage's operator-policy
    Tier-1 detection comes exclusively from the org's enabled compiled policies
    (``policy_engine.evaluate_for_stage(..., stage="query")``); with NO enabled
    policy the query-stage policy gate contributes nothing and the query is
    passthrough. There is no built-in default Tier-1 scan driving a block.
  * **Tier-2 = opt-in, model-only, effective default OFF.** The RAG Tier-2
    (Bedrock guard-model) scan on ingest AND query runs ONLY when the resolved
    ``rag_tier2_enabled`` is True, mirroring how ``resolve_tier2_enabled`` gates
    the chat path. Absent / ``None`` / any stale non-True value resolves to OFF
    ("fail toward no Tier-2 detection", R6.5/R3.7). This module locks that in via
    the new single-source ``resolve_rag_tier2_enabled`` resolver (config_sync,
    re-exported from config) and the QueryStage's Tier-2 gate.
  * **Zero enabled policies + RAG Tier-2 off ⇒ passthrough** on both ingest and
    query: no block, redact, or flag verdict is produced.

This module is ISOLATED (task 7.2 constraint): it does NOT edit
``test_policy_driven_detection.py`` — it imports that module's shared prompt sets
(ATTACK/PII/SECRET/BENIGN) read-only so the RAG surface is exercised over the
same representative content the chat-path property tests use.

Validates: Requirements 6.1, 6.3 (and, for the resolver, 3.7/6.5 fail-toward-off).
"""

from __future__ import annotations

import asyncio
import unittest

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ai_mesh_gateway.config import resolve_rag_tier2_enabled as resolve_from_config
from ai_mesh_gateway.config_sync import (
    resolve_rag_tier2_enabled,
    resolve_tier2_enabled,
)
from ai_mesh_gateway.rag_pipeline.contracts import QueryStageInput
from ai_mesh_gateway.rag_pipeline.query_stage import QueryStage
from ai_mesh_gateway.scanner import InputScanner

# Read-only reuse of the feature's shared representative prompt sets (task 7.2
# constraint: import the scaffold/prompt sets from test_policy_driven_detection,
# never edit that module).
from ai_mesh_gateway.tests.test_policy_driven_detection import (
    ATTACK_PROMPTS,
    BENIGN_PROMPTS,
    PII_PROMPTS,
    SECRET_PROMPTS,
)

_ALL_PROMPTS = {**ATTACK_PROMPTS, **PII_PROMPTS, **SECRET_PROMPTS, **BENIGN_PROMPTS}


# --------------------------------------------------------------------------- #
# A Tier-2-recording scanner double. It records every scan call kind so a test
# can assert whether the (opt-in, model-only) Tier-2 scan actually ran, and
# returns a NEUTRAL Tier-1 verdict so the built-in scanner contributes no
# non-allow decision (post-cutover shape — task 3.1 already neutralised the
# built-in ATTACK_PATTERNS auto-scan; here we only need the RAG surface to make
# no INDEPENDENT built-in verdict).
# --------------------------------------------------------------------------- #
class _RecordingScanner:
    """Minimal scanner double for the RAG QueryStage Tier-2 gate.

    ``scan_prompt`` (Tier-1-only) returns a neutral allow verdict; when it is
    called we record ``("tier1", text)``. ``scan_prompt_with_tier2`` records
    ``("tier2", text)`` — its presence in the recorded calls proves the model
    scan ran. Both return a real ``ScanVerdict`` so the QueryStage's
    verdict-aware code paths behave normally.
    """

    def __init__(self, *, tier2_action: str = "allow", tier2_threat: str = ""):
        self.calls: list[tuple[str, str]] = []
        self._tier2_action = tier2_action
        self._tier2_threat = tier2_threat
        # A real InputScanner backs redact_pii (used by the QueryStage's
        # PII-redaction defense-in-depth) so we exercise the actual redactor.
        self._real = InputScanner()
        self._real.tier2_enabled = False
        self._real._bedrock_scanner = None

    async def scan_prompt(self, text, is_rag=False):
        self.calls.append(("tier1", text))
        from scanner import ScanVerdict

        return ScanVerdict()  # neutral allow — no built-in Tier-1 contribution

    async def scan_prompt_with_tier2(self, text, **kwargs):
        self.calls.append(("tier2", text))
        from scanner import ScanVerdict

        return ScanVerdict(
            action=self._tier2_action,
            threat_type=self._tier2_threat,
            confidence=1.0 if self._tier2_action != "allow" else 0.0,
            tier="tier_2",
        )

    def redact_pii(self, text, verdict=None):
        return self._real.redact_pii(text, verdict=verdict)


def _run_query_stage(
    *,
    query_text: str,
    policy: dict | None = None,
    compiled_policies: list[dict] | None = None,
    tier2_action: str = "allow",
    tier2_threat: str = "",
    config: dict | None = None,
) -> tuple[_RecordingScanner, object]:
    """Drive the RAG QueryStage exactly as ``rag_query`` builds it.

    Returns ``(scanner, output)`` so a test can inspect BOTH the recorded scan
    calls (did Tier-2 run?) and the resolved stage verdict.
    """
    scanner = _RecordingScanner(tier2_action=tier2_action, tier2_threat=tier2_threat)
    stage = QueryStage(scanner=scanner, config=config or {})
    inp = QueryStageInput(
        query_text=query_text,
        collection_name="c1",
        project_id="p1",
        vector_db_type="chroma",
        n_results=5,
        where_filter=None,
        namespace="p1__c1",
        policy=policy if policy is not None else {},
        key_hash="kh",
        compiled_policies=compiled_policies or [],
    )
    out = asyncio.run(stage.execute(inp))
    return scanner, out


# A single-rule enabled compiled-policy bundle scoped to the QUERY stage. Shape
# matches ``policy_engine.evaluate_for_stage``'s expected compiled entry.
def _query_stage_policies(*, action: str = "block", keyword: str = "sekrit-rag") -> list[dict]:
    return [
        {
            "policy": {
                "id": 1,
                "code": "RAG_TEST_PKG",
                "name": "RAG Query Test Package",
                "priority": 100,
                "category": "test_family",
                "severity": "high",
                "domains": ["pipeline", "rag", ""],
            },
            "rules": [
                {
                    "id": 11,
                    "name": "rag-query-keyword-rule",
                    "rule_type": "keywords",
                    "condition": {"keywords": [keyword], "field": "both"},
                    "action": action,
                    "pipeline_stage": "query",
                }
            ],
        }
    ]


# --------------------------------------------------------------------------- #
# Group 1 — the RAG Tier-2 resolver (effective default OFF, single source).
# --------------------------------------------------------------------------- #
class ResolveRagTier2EnabledTests(unittest.TestCase):
    """``resolve_rag_tier2_enabled`` tri-state → effective bool, default OFF.

    **Validates: Requirements 6.1, 6.3, 3.7** (RAG Tier-2 opt-in; an
    unresolved/absent value ⇒ Tier-2 does not run on the RAG surface).
    """

    def test_none_resolves_off(self):
        self.assertIs(resolve_rag_tier2_enabled(None), False)

    def test_absent_key_resolves_off(self):
        org_config: dict = {}
        self.assertIs(
            resolve_rag_tier2_enabled(org_config.get("rag_tier2_enabled")), False
        )

    def test_false_resolves_off(self):
        self.assertIs(resolve_rag_tier2_enabled(False), False)

    def test_true_resolves_on(self):
        self.assertIs(resolve_rag_tier2_enabled(True), True)

    def test_stale_nonbool_values_resolve_off(self):
        for stale in ("true", "True", 1, "1", "yes", [], {}, 0, "", "false"):
            with self.subTest(stale=stale):
                self.assertIs(resolve_rag_tier2_enabled(stale), False)

    def test_config_reexport_is_same_function(self):
        """``config.resolve_rag_tier2_enabled`` is the SAME single-source resolver
        as ``config_sync.resolve_rag_tier2_enabled`` (parity with the chat-path
        re-export)."""
        self.assertIs(resolve_from_config, resolve_rag_tier2_enabled)

    def test_matches_chat_resolver_contract(self):
        """The RAG resolver applies the identical rule as the chat resolver."""
        for value in (None, False, True, "true", 1, 0, [], {}, "yes"):
            with self.subTest(value=value):
                self.assertEqual(
                    resolve_rag_tier2_enabled(value), resolve_tier2_enabled(value)
                )


# --------------------------------------------------------------------------- #
# Group 2 — RAG QUERY Tier-2 opt-in gating (model-only, default OFF).
# --------------------------------------------------------------------------- #
class RagQueryTier2GatingTests(unittest.TestCase):
    """The RAG query-stage Tier-2 model scan runs iff resolved rag_tier2_enabled.

    **Validates: Requirements 6.1, 6.3** (RAG honors the opt-in, model-only Tier-2
    model; default OFF).
    """

    def test_tier2_off_absent_stays_tier1_only(self):
        """No ``rag_tier2_enabled`` key ⇒ Tier-2 does not run on the RAG query."""
        scanner, _out = _run_query_stage(query_text="please summarise the report")
        kinds = [c[0] for c in scanner.calls]
        self.assertNotIn("tier2", kinds)
        self.assertIn("tier1", kinds)

    def test_tier2_explicit_false_stays_tier1_only(self):
        scanner, _out = _run_query_stage(
            query_text="please summarise the report",
            policy={"rag_tier2_enabled": False},
        )
        self.assertNotIn("tier2", [c[0] for c in scanner.calls])

    def test_tier2_none_stays_tier1_only(self):
        scanner, _out = _run_query_stage(
            query_text="please summarise the report",
            policy={"rag_tier2_enabled": None},
        )
        self.assertNotIn("tier2", [c[0] for c in scanner.calls])

    def test_stale_truthy_values_do_not_enable_tier2(self):
        """A stale/old control-plane non-True value must NOT run Tier-2
        (fail toward no detection)."""
        for truthy in ("true", "True", 1, "yes", [1], {"x": 1}):
            with self.subTest(truthy=truthy):
                scanner, _out = _run_query_stage(
                    query_text="please summarise the report",
                    policy={"rag_tier2_enabled": truthy},
                )
                self.assertNotIn(
                    "tier2",
                    [c[0] for c in scanner.calls],
                    msg=f"Tier-2 must stay OFF for stale value {truthy!r}",
                )

    def test_tier2_true_routes_through_model_scan(self):
        """Explicit True ⇒ the model-only Tier-2 scan runs on the RAG query."""
        scanner, _out = _run_query_stage(
            query_text="please summarise the report",
            policy={"rag_tier2_enabled": True, "_org_slug": "acme", "tier2_strict": True},
        )
        self.assertIn("tier2", [c[0] for c in scanner.calls])


# --------------------------------------------------------------------------- #
# Group 3 — RAG QUERY zero-policy + Tier-2 off ⇒ passthrough (no detection).
# --------------------------------------------------------------------------- #
class RagQueryZeroPolicyPassthroughTests(unittest.TestCase):
    """Zero enabled policies + RAG Tier-2 off ⇒ the query passes through.

    **Feature: policy-driven-detection, Property 1 (RAG query surface): For any
    input content, if the org's enabled policy set is empty AND Tier-2 is off,
    the RAG query resolves to allow with no block/redact/flag verdict.**

    **Validates: Requirements 6.1, 6.2, 6.3**
    """

    def _assert_passthrough(self, prompt: str) -> None:
        scanner, out = _run_query_stage(
            query_text=prompt,
            policy={},              # zero-policy: no rag_tier2_enabled, no thresholds
            compiled_policies=[],   # empty enabled-policy set
        )
        # No Tier-2 model scan (opt-in, off).
        self.assertNotIn(
            "tier2",
            [c[0] for c in scanner.calls],
            msg=f"Tier-2 must not run for {prompt!r} under zero-policy",
        )
        # The stage verdict is a passthrough — allow, no block/redact/flag.
        self.assertEqual(
            out.verdict.action,
            "allow",
            msg=f"zero-policy + Tier-2 off must pass through, got "
            f"{out.verdict.action!r} for {prompt!r}",
        )
        self.assertEqual(out.injection_flags, [])

    def test_representative_prompts_pass_through(self):
        for label, prompt in _ALL_PROMPTS.items():
            with self.subTest(prompt=label):
                self._assert_passthrough(prompt)

    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        text=st.one_of(
            st.text(min_size=1, max_size=256).filter(lambda s: s.strip() != ""),
            st.sampled_from(list(_ALL_PROMPTS.values())),
        )
    )
    def test_property_zero_policy_passthrough(self, text: str):
        """Any non-empty input, empty enabled policies, Tier-2 off ⇒ allow (RAG query)."""
        self._assert_passthrough(text)


# --------------------------------------------------------------------------- #
# Group 4 — RAG QUERY Tier-1 is policy-only (enabled rule fires; none ⇒ allow).
# --------------------------------------------------------------------------- #
class RagQueryTier1PolicyOnlyTests(unittest.TestCase):
    """RAG query-stage Tier-1 detection comes ONLY from enabled policies.

    **Validates: Requirements 6.1, 6.3** (a RAG surface previously running a
    built-in default now runs detection only from the Enabled_Policy_Set).
    """

    def test_enabled_query_policy_blocks_its_keyword(self):
        """An enabled query-stage BLOCK rule fires on its keyword."""
        _scanner, out = _run_query_stage(
            query_text="please use the sekrit-rag handshake",
            compiled_policies=_query_stage_policies(action="block", keyword="sekrit-rag"),
        )
        self.assertEqual(out.verdict.action, "block")
        self.assertEqual(out.verdict.threat_type, "policy_violation")

    def test_enabled_query_policy_ignores_non_matching_text(self):
        """The SAME enabled policy passes through text its rule does not match."""
        _scanner, out = _run_query_stage(
            query_text="nothing sensitive here at all",
            compiled_policies=_query_stage_policies(action="block", keyword="sekrit-rag"),
        )
        self.assertEqual(out.verdict.action, "allow")

    def test_no_enabled_policy_does_not_block_matching_text(self):
        """With NO enabled policy, the same 'matching' text is passthrough — the
        block came only from the enabled rule, never a built-in default."""
        _scanner, out = _run_query_stage(
            query_text="please use the sekrit-rag handshake",
            compiled_policies=[],
        )
        self.assertEqual(out.verdict.action, "allow")


# --------------------------------------------------------------------------- #
# Group 5 — RAG INGEST Tier-2 gate resolves effective-OFF-by-default.
#
# The ingest handler (main.py) computes ``rag_tier2_enabled`` via
# ``resolve_rag_tier2_enabled(org_config.get("rag_tier2_enabled"))`` and only then
# calls ``INPUT_SCANNER.scan_prompt_with_tier2``. We assert the resolver — the
# single gate the ingest path now uses — yields the effective-OFF-by-default
# decision for every config shape the ingest handler can receive.
# --------------------------------------------------------------------------- #
class RagIngestTier2ResolutionTests(unittest.TestCase):
    """RAG ingest resolves Tier-2 to effective-OFF unless explicitly True.

    **Validates: Requirements 6.1, 6.3** (RAG ingest Tier-2 opt-in, default OFF).
    """

    def test_ingest_default_off_shapes(self):
        # These are the org_config shapes an ingest request can carry.
        for org_config in ({}, {"rag_tier2_enabled": None}, {"rag_tier2_enabled": False}):
            with self.subTest(org_config=org_config):
                self.assertIs(
                    resolve_rag_tier2_enabled(org_config.get("rag_tier2_enabled")),
                    False,
                )

    def test_ingest_on_only_when_explicit_true(self):
        self.assertIs(
            resolve_rag_tier2_enabled({"rag_tier2_enabled": True}.get("rag_tier2_enabled")),
            True,
        )

    def test_ingest_source_wires_resolver(self):
        """main.rag ingest gates Tier-2 via the shared resolver, not a bare bool()."""
        import inspect

        from ai_mesh_gateway import main

        src = inspect.getsource(main)
        self.assertIn(
            "resolve_rag_tier2_enabled(org_config.get(\"rag_tier2_enabled\"))",
            src,
            msg="RAG ingest must gate Tier-2 through resolve_rag_tier2_enabled",
        )


if __name__ == "__main__":
    unittest.main()
