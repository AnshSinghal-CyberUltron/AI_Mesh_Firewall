"""Detection-layer failures in the RAG query stage must FAIL OPEN (not crash the
query, not block it) but be OBSERVABLE (WARNING logged) — a silent debug-swallow
is a hidden fallback: an enabled layer that silently no-ops with zero signal.
"""
import logging
import pytest

from rag_pipeline.query_stage import QueryStage
from rag_pipeline.contracts import QueryStageInput


class _Raises:
    def classify(self, text):
        raise RuntimeError("intent boom")

    async def check(self, text):
        raise RuntimeError("vault boom")

    @property
    def enabled(self):
        return True

    async def judge(self, text):
        raise RuntimeError("judge boom")


def _inp():
    return QueryStageInput(
        query_text="what is the refund policy", collection_name="docs",
        project_id="org1", vector_db_type="pinecone", n_results=5,
        where_filter=None, namespace="", policy={}, key_hash="",
    )


async def test_all_layers_fail_open_and_warn(caplog):
    r = _Raises()
    stage = QueryStage(scanner=None, config={}, llm_judge=r,
                       embedding_vault=r, intent_classifier=r)
    with caplog.at_level(logging.WARNING, logger="gateway.rag_pipeline.query_stage"):
        out = await stage.execute(_inp())
    # Fail-open: the query still resolves (not blocked, not crashed).
    assert out.verdict.action in ("allow", "flag")
    # Observable: each failing layer emitted a WARNING (no longer a silent debug).
    msgs = " ".join(rec.getMessage() for rec in caplog.records)
    assert "Intent classifier failed" in msgs
    assert "Embedding vault check failed" in msgs
    assert "LLM Judge failed" in msgs
