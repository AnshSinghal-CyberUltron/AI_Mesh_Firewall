"""Circuit-breaker tenant isolation in the retriever stage.

Bug (fixed): the breaker was keyed globally ``vectordb:{type}``, so in BYOK
(each org has its OWN provider + credentials) one tenant's provider outage /
bad-credentials failures opened the breaker for EVERY tenant on that provider
type. Fix keys BYOK requests per-org (project_id); the shared gateway-env client
keeps a global key.
"""
from __future__ import annotations

import pytest

from rag_pipeline.retriever_stage import RetrieverStage
from rag_pipeline.contracts import RetrieverStageInput


class _Status:
    def __init__(self, blocked):
        class _S:
            value = "open" if blocked else "closed"
        self.state = _S()
        self.should_block = blocked


class _StatefulCB:
    """Opens a key after `open_after` recorded errors (per key)."""

    def __init__(self, open_after=2):
        self.errors: dict[str, int] = {}
        self.open_after = open_after

    async def check(self, key):
        return _Status(self.errors.get(key, 0) >= self.open_after)

    async def record_success(self, key):
        pass

    async def record_error(self, key, name):
        self.errors[key] = self.errors.get(key, 0) + 1


class _OkClient:
    async def query(self, **kw):
        return [{"id": "d", "content": "x", "score": 0.9}]


class _FailClient:
    async def query(self, **kw):
        raise RuntimeError("provider down / bad credentials")


async def _run(cb, project_id, *, override=None, shared_clients=None):
    r = RetrieverStage(
        vector_clients=shared_clients or {},
        circuit_breaker=cb, rate_limiter=None, config={},
    )
    return await r.execute(RetrieverStageInput(
        query_text="q", collection_name="docs", project_id=project_id,
        vector_db_type="pinecone", n_results=5, where_filter=None, namespace="",
        policy={}, escalation_level=0, key_hash="", vector_client=override,
    ))


async def test_byok_breaker_isolated_per_org():
    cb = _StatefulCB(open_after=2)
    fail = _FailClient()
    # org A (BYOK) hammers its failing provider until its breaker opens.
    for _ in range(3):
        await _run(cb, "orgA-p", override=fail)
    out_a = await _run(cb, "orgA-p", override=fail)
    assert out_a.verdict.threat_type == "circuit_breaker", "org A breaker should be open"
    # org B (BYOK, healthy own provider) must be UNAFFECTED by org A's failures.
    out_b = await _run(cb, "orgB-p", override=_OkClient())
    assert out_b.verdict.action == "allow", "org B blocked by org A's provider failures (coupling)"
    # Keys are org-scoped and distinct.
    assert "vectordb:pinecone:orgA-p" in cb.errors
    assert "vectordb:pinecone:orgB-p" not in cb.errors


async def test_shared_env_client_breaker_stays_global():
    # No per-request override → the gateway-env client is genuinely shared, so a
    # single global key is correct (one failing shared dep protects all callers).
    cb = _StatefulCB(open_after=2)
    shared = {"pinecone": _FailClient()}
    for _ in range(3):
        await _run(cb, "orgA-p", shared_clients=shared)
    # A different org hitting the SAME shared client sees the same (open) breaker.
    out = await _run(cb, "orgB-p", shared_clients=shared)
    assert out.verdict.threat_type == "circuit_breaker"
    assert list(cb.errors.keys()) == ["vectordb:pinecone"]
