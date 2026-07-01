"""FIX 2: ranker embedding-anomaly fail-OPEN -> fail-CLOSED.

When the embedding-anomaly detector flagged ALL retrieved documents as anomalous
(``anomaly_removed_all``), RankerStage used to RETURN them as-is with action="flag"
(fail-open) — poisoned / anomalous documents were served to the client.

It now fails CLOSED: when every document is anomalous, the documents are DROPPED
(empty document set) and the verdict is action="block", so anomalous content is
never served. Telemetry/verdict shape (anomalous_indices, documents_removed,
threat_type="anomaly") is preserved.

These tests drive RankerStage.execute directly with a real ContextGuard.
"""
import asyncio
import os
import sys

_PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PKG_DIR not in sys.path:
    sys.path.insert(0, _PKG_DIR)

from context_guard import ContextGuard
from rag_pipeline.contracts import RankerStageInput
from rag_pipeline.ranker_stage import RankerStage


def _run(coro):
    return asyncio.run(coro)


def _make_stage():
    # rag_anomaly_detection_enabled on; ranker content-scan can stay on (benign docs).
    return RankerStage(ContextGuard(thread_pool_size=2), {"rag_anomaly_detection_enabled": True})


def test_all_anomalous_documents_are_blocked_not_served():
    """Every doc above the policy anomaly threshold -> action=block, no docs served."""
    stage = _make_stage()
    # All distances (0.95, 0.97, 0.99) exceed the policy threshold 0.5 -> all flagged.
    documents = [
        {"content": "benign doc A", "distance": 0.95, "_doc_id": "a"},
        {"content": "benign doc B", "distance": 0.97, "_doc_id": "b"},
        {"content": "benign doc C", "distance": 0.99, "_doc_id": "c"},
    ]
    inp = RankerStageInput(
        documents=documents,
        query_text="q",
        policy={"anomaly_distance_threshold": 0.5},
        escalation_level=0,
    )
    out = _run(stage.execute(inp))

    # FAIL-CLOSED: blocked, and NOTHING is returned.
    assert out.verdict.action == "block", f"expected block, got {out.verdict.action!r}"
    assert out.ranked_documents == [], "anomalous docs must NOT be served"
    assert out.verdict.threat_type == "anomaly"
    # All three were flagged anomalous and removed.
    assert sorted(out.anomalous_indices) == [0, 1, 2]
    assert out.documents_removed == 3


def test_partial_anomaly_still_serves_clean_documents():
    """When only SOME docs are anomalous, the clean ones are still returned (no
    over-block / regression of the partial path)."""
    stage = _make_stage()
    documents = [
        {"content": "clean and useful", "distance": 0.10, "_doc_id": "clean"},
        {"content": "outlier doc", "distance": 0.99, "_doc_id": "outlier"},
    ]
    inp = RankerStageInput(
        documents=documents,
        query_text="q",
        policy={"anomaly_distance_threshold": 0.5},
        escalation_level=0,
    )
    out = _run(stage.execute(inp))

    assert out.verdict.action != "block"
    served_ids = [d.get("_doc_id") for d in out.ranked_documents]
    assert "clean" in served_ids
    assert "outlier" not in served_ids


def test_no_anomaly_allows_documents():
    """No anomalies -> allow, all docs served (no false-positive block)."""
    stage = _make_stage()
    documents = [
        {"content": "doc one", "distance": 0.10, "_doc_id": "1"},
        {"content": "doc two", "distance": 0.12, "_doc_id": "2"},
    ]
    inp = RankerStageInput(
        documents=documents,
        query_text="q",
        policy={"anomaly_distance_threshold": 0.85},
        escalation_level=0,
    )
    out = _run(stage.execute(inp))
    assert out.verdict.action == "allow"
    assert len(out.ranked_documents) == 2


if __name__ == "__main__":  # pragma: no cover
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
