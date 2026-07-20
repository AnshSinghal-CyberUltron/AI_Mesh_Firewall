"""Concurrency isolation: the shared singleton RankerStage must apply each
request's OWN org-scoped compiled policies, never another concurrent request's.

Bug (fixed): the pipeline mutated ``ranker._compiled_policies`` via
update_policies() BEFORE its await points, then ran ranker.execute() AFTER them —
so under async concurrency org A's request could apply org B's policies
(cross-tenant contamination). Fix threads compiled_policies per-request through
RankerStageInput. These tests force the interleaving with an asyncio.Barrier so
both requests reach the policy stage together, and assert each keeps its own doc.
"""
from __future__ import annotations

import asyncio

import pytest

from rag_pipeline.ranker_stage import RankerStage
from rag_pipeline.contracts import RankerStageInput


class _V:
    def __init__(self, action="allow", threat_type="", confidence=0.0, detail="",
                 matched_patterns=None, flagged_documents=None):
        self.action = action
        self.threat_type = threat_type
        self.confidence = confidence
        self.detail = detail
        self.matched_patterns = matched_patterns or []
        self.flagged_documents = flagged_documents or []


class _BenignGuard:
    def detect_embedding_anomaly(self, distances, threshold):
        return []

    async def scan_documents(self, documents, query_text):
        return _V("allow")

    async def scan_single_document(self, content):
        return _V("allow")


class _BarrierGuard(_BenignGuard):
    """Rendezvous both concurrent requests at the pre-policy scan step, so both
    have entered execute() (and any shared-state mutation would have happened)
    before either evaluates policies."""

    def __init__(self, barrier):
        self._b = barrier

    async def scan_documents(self, documents, query_text):
        await self._b.wait()
        return _V("allow")


def _pol(keyword):
    return [{
        "policy": {"id": 1, "code": keyword, "priority": 0},
        "rules": [{
            "id": 1, "rule_type": "keywords",
            "condition": {"keywords": [keyword], "field": "prompt"},
            "action": "block", "pipeline_stage": "ranker",
        }],
    }]


def _inp(doc_content, compiled):
    return RankerStageInput(
        documents=[{"content": doc_content, "_doc_id": "d1"}],
        query_text="q", policy={}, escalation_level=0, compiled_policies=compiled,
    )


async def test_concurrent_multi_org_requests_use_own_policies():
    barrier = asyncio.Barrier(2)
    ranker = RankerStage(_BarrierGuard(barrier), config={"rag_ranker_enabled": True})

    async def req(block_kw, doc_content):
        return await ranker.execute(_inp(doc_content, _pol(block_kw)))

    # orgA blocks SECRETA; its doc contains SECRETB -> must SURVIVE under orgA policy.
    # orgB blocks SECRETB; its doc contains SECRETA -> must SURVIVE under orgB policy.
    out_a, out_b = await asyncio.gather(
        req("SECRETA", "harmless text with SECRETB"),
        req("SECRETB", "harmless text with SECRETA"),
    )
    assert len(out_a.ranked_documents) == 1, "reqA doc wrongly dropped (used another org's policy)"
    assert len(out_b.ranked_documents) == 1, "reqB doc wrongly dropped (used another org's policy)"


async def test_per_request_policies_override_stale_instance_state():
    # Even if the instance carries stale/other-org policies (legacy update_policies
    # path), the per-request compiled_policies must win.
    ranker = RankerStage(_BenignGuard(), config={"rag_ranker_enabled": True})
    ranker.update_policies(_pol("SECRETB"))  # stale instance state blocks SECRETB
    out = await ranker.execute(_inp("harmless text with SECRETB", _pol("SECRETA")))
    assert len(out.ranked_documents) == 1, "per-request policy did not override instance state"


async def test_request_own_policy_still_blocks_its_match():
    # Sanity: a request's own policy DOES block its own matching doc.
    ranker = RankerStage(_BenignGuard(), config={"rag_ranker_enabled": True})
    out = await ranker.execute(_inp("text with SECRETA inside", _pol("SECRETA")))
    assert len(out.ranked_documents) == 0
