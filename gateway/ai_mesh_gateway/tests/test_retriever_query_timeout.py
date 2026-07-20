"""#25: the retriever ran client.query with NO timeout (the SDK calls execute in a
ThreadPoolExecutor and Chroma's HttpClient / Pinecone index configure none), so a
hanging/slow provider (network failure, unresponsive server) blocked the request
indefinitely. Fixed: asyncio.wait_for(rag_query_timeout_s, default 30) → a timeout
raises, the circuit breaker records the error (repeated timeouts OPEN it), and the
caller gets a clean 'retrieval_error' block instead of a hung connection.
(VECTOR PROVIDER: timeouts / network failures)
"""
import time

import pytest

from rag_pipeline.retriever_stage import RetrieverStage
from rag_pipeline.contracts import RetrieverStageInput


class _HangClient:
    async def query(self, **kw):
        import asyncio
        await asyncio.sleep(5)  # far longer than the test timeout
        return []


class _FastClient:
    async def query(self, **kw):
        return [{"id": "d1", "content": "x", "score": 0.9}]


def _inp(client):
    return RetrieverStageInput(
        query_text="q", collection_name="docs", project_id="org1-default",
        vector_db_type="pinecone", n_results=5, where_filter=None, namespace="",
        policy={}, escalation_level=0, key_hash="", vector_client=client,
    )


async def test_retriever_query_timeout_blocks_not_hangs():
    stage = RetrieverStage(vector_clients={}, circuit_breaker=None, rate_limiter=None,
                           config={"rag_query_timeout_s": 0.1})
    t0 = time.perf_counter()
    out = await stage.execute(_inp(_HangClient()))
    elapsed = time.perf_counter() - t0
    assert out.verdict.action == "block"
    assert out.verdict.threat_type == "retrieval_error"
    assert elapsed < 2.0, f"retriever hung {elapsed:.2f}s despite a 0.1s query timeout"
    # E3: no provider internals leaked in the timeout block detail
    assert "sleep" not in (out.verdict.detail or "").lower()


async def test_retriever_fast_query_succeeds_within_timeout():
    stage = RetrieverStage(vector_clients={}, circuit_breaker=None, rate_limiter=None,
                           config={"rag_query_timeout_s": 5.0})
    out = await stage.execute(_inp(_FastClient()))
    assert out.verdict.action == "allow"
    assert out.total_retrieved == 1


class _HangRerankClient:
    _reranker_model = "bge-reranker-v2-m3"

    async def query(self, **kw):
        return [{"id": "d1", "content": "a", "score": 0.9},
                {"id": "d2", "content": "b", "score": 0.8}]

    async def rerank(self, query, docs, top_n=10):
        import asyncio
        await asyncio.sleep(5)  # hangs
        return list(reversed(docs))


async def test_reranker_timeout_keeps_retrieval_order_fail_open():
    # #26: a hanging provider-hosted reranker must not block the request after
    # retrieval succeeded — timeout -> fail-open -> keep retrieval order.
    stage = RetrieverStage(vector_clients={}, circuit_breaker=None, rate_limiter=None,
                           config={"rag_query_timeout_s": 5.0, "rag_rerank_timeout_s": 0.1})
    t0 = time.perf_counter()
    out = await stage.execute(_inp(_HangRerankClient()))
    elapsed = time.perf_counter() - t0
    assert out.verdict.action == "allow"          # query still succeeded
    assert out.total_retrieved == 2
    assert out.documents[0]["id"] == "d1"          # rerank's reversal never applied
    assert elapsed < 2.0, f"rerank hung {elapsed:.2f}s despite a 0.1s rerank timeout"
