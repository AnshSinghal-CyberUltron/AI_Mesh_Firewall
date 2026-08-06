"""RAG-33: the per-org Tier-2 (Bedrock guard model) switch must actually gate
the RAG query path — and must gate it in BOTH directions.

`FirewallConfig.rag_tier2_enabled` existed (default False), `config_sync`
mirrored it per-org, and the frontend already rendered the on/off switch
(RagSecurityCard.jsx). But `rag_query` never copied the key into the policy dict
and QueryStage called the Tier-1-only ``scan_prompt`` — so an operator could
switch Tier-2 ON for the org and RAG queries were still never judged by the
model. RAG *ingest* already ran Tier-2, so the asymmetry was query-vs-ingest.

The override is passed BY IDENTITY: the scanner distinguishes
``None`` ("no org opinion, use the gateway default") from ``False`` ("the
operator explicitly turned it OFF"), so collapsing them would silently re-enable
a stage the operator disabled. Default stays OFF — nothing by default.
"""
import asyncio

import pytest

from rag_pipeline.contracts import QueryStageInput
from rag_pipeline.query_stage import QueryStage


class _Verdict:
    def __init__(self, tier="tier_1"):
        self.action = "allow"
        self.threat_type = ""
        self.confidence = 0.0
        self.tier = tier
        self.matched_patterns = []


class _RecScanner:
    """Records which scan entrypoint the stage chose."""

    def __init__(self):
        self.calls = []

    async def scan_prompt(self, text, is_rag=False, **kw):
        self.calls.append(("tier1", kw))
        return _Verdict("tier_1")

    async def scan_prompt_with_tier2(self, text, **kw):
        self.calls.append(("tier2", kw))
        return _Verdict("tier_2")

    def redact_pii(self, text, *a, **kw):
        return text


def _run(policy):
    scanner = _RecScanner()
    stage = QueryStage(scanner, {})
    inp = QueryStageInput(
        query_text="what is the refund policy",
        collection_name="cdocs",
        project_id="org1-proj",
        vector_db_type="chroma",
        n_results=5,
        where_filter=None,
        namespace="",
        policy=policy,
        key_hash="k",
    )
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        stage.execute(inp)
    ) if False else asyncio.run(stage.execute(inp))
    return scanner


def test_toggle_on_routes_through_tier2():
    s = _run({"rag_tier2_enabled": True, "_org_slug": "acme", "tier2_strict": True})
    kinds = [c[0] for c in s.calls]
    assert "tier2" in kinds, f"Tier-2 not invoked with the toggle ON: {kinds}"
    kw = dict(s.calls[[c[0] for c in s.calls].index("tier2")][1])
    # must be forwarded explicitly, and as True (not a truthy accident)
    assert kw.get("org_tier2_override") is True
    assert kw.get("is_rag") is True
    assert kw.get("org_slug") == "acme"


def test_toggle_off_stays_tier1_only():
    s = _run({"rag_tier2_enabled": False, "_org_slug": "acme"})
    kinds = [c[0] for c in s.calls]
    assert "tier2" not in kinds, "operator turned Tier-2 OFF but it ran anyway"
    assert "tier1" in kinds


def test_absent_key_stays_tier1_only():
    """No org opinion => the default, which is OFF. Nothing by default."""
    s = _run({"_org_slug": "acme"})
    kinds = [c[0] for c in s.calls]
    assert "tier2" not in kinds
    assert "tier1" in kinds


@pytest.mark.parametrize("truthy", ["true", 1, "yes"])
def test_only_real_true_enables_tier2(truthy):
    """Identity, not truthiness.

    A string "true" arriving from a mis-typed config must NOT switch on a
    Bedrock-billed stage the operator did not select.
    """
    s = _run({"rag_tier2_enabled": truthy, "_org_slug": "acme"})
    assert "tier2" not in [c[0] for c in s.calls]
