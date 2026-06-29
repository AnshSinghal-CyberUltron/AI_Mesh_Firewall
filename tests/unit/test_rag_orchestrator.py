"""Unit tests for RAG Orchestrator (GAP 3)."""
import sys
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

# The gateway data plane uses flat intra-package imports (e.g.
# ``from rag_pipeline.ranker_stage import ...`` inside rag_orchestrator),
# so the package directory itself must be on sys.path.
sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "..", "gateway", "ai_mesh_gateway"),
)

from rag_orchestrator import RAGOrchestrator, RAGVerdict, compute_trust_score


class TestComputeTrustScore:
    def test_default_score(self):
        doc = {"distance": 0.3}
        score = compute_trust_score(doc, {"anomaly_distance_threshold": 0.85})
        assert 0.0 <= score <= 1.0

    def test_score_decreases_near_threshold(self):
        policy = {"anomaly_distance_threshold": 0.85}
        far_doc = {"distance": 0.80}
        close_doc = {"distance": 0.30}
        far_score = compute_trust_score(far_doc, policy)
        close_score = compute_trust_score(close_doc, policy)
        assert close_score > far_score

    def test_verified_source_bonus(self):
        policy = {"anomaly_distance_threshold": 0.85}
        unverified = {"distance": 0.7, "metadata": {}}
        verified = {"distance": 0.7, "metadata": {"verified_source": True}}
        assert compute_trust_score(verified, policy) > compute_trust_score(
            unverified, policy
        )

    def test_system_created_bonus(self):
        policy = {"anomaly_distance_threshold": 0.85}
        user_doc = {"distance": 0.7, "metadata": {"created_by": "user"}}
        sys_doc = {"distance": 0.7, "metadata": {"created_by": "system"}}
        assert compute_trust_score(sys_doc, policy) > compute_trust_score(
            user_doc, policy
        )

    def test_score_clamped(self):
        doc = {"distance": 0.0, "metadata": {"verified_source": True, "created_by": "system"}}
        score = compute_trust_score(doc, {"anomaly_distance_threshold": 0.85})
        assert score <= 1.0


@pytest.fixture
def mock_context_guard():
    guard = MagicMock()
    guard.detect_embedding_anomaly = MagicMock(return_value=[])
    guard.scan_documents = AsyncMock(
        return_value=MagicMock(action="allow", flagged_documents=[])
    )
    return guard


@pytest.fixture
def mock_vector_client():
    client = MagicMock()
    client.query = AsyncMock(
        return_value=[
            {"content": "doc1", "distance": 0.3, "metadata": {}},
            {"content": "doc2", "distance": 0.5, "metadata": {}},
        ]
    )
    return client


class TestRAGOrchestrator:
    @pytest.mark.asyncio
    async def test_basic_query(self, mock_context_guard, mock_vector_client):
        orch = RAGOrchestrator(
            context_guard=mock_context_guard,
            vector_clients={"chroma": mock_vector_client},
            config={},
        )
        result = await orch.execute_query(
            collection_name="test",
            query_text="search query",
            project_id="proj1",
            vector_db_type="chroma",
        )
        assert result.action == "allow"
        assert len(result.documents) == 2

    @pytest.mark.asyncio
    async def test_no_client_blocks(self, mock_context_guard):
        orch = RAGOrchestrator(
            context_guard=mock_context_guard,
            vector_clients={},
            config={},
        )
        result = await orch.execute_query(
            collection_name="test",
            query_text="query",
            project_id="proj1",
            vector_db_type="pinecone",
        )
        assert result.action == "block"

    @pytest.mark.asyncio
    async def test_anomaly_filtering(self, mock_context_guard, mock_vector_client):
        mock_context_guard.detect_embedding_anomaly.return_value = [1]
        orch = RAGOrchestrator(
            context_guard=mock_context_guard,
            vector_clients={"chroma": mock_vector_client},
            config={},
        )
        result = await orch.execute_query(
            collection_name="test",
            query_text="query",
            project_id="proj1",
            vector_db_type="chroma",
        )
        assert result.action == "allow"
        assert len(result.documents) == 1
        assert 1 in result.anomalous_indices

    @pytest.mark.asyncio
    async def test_all_anomalous_flags_without_drop(
        self, mock_context_guard, mock_vector_client
    ):
        """When EVERY document is anomalous, the pipeline intentionally does
        not hard-block on anomaly heuristics alone (ranker_stage.py): it
        preserves retrieval continuity, keeps the documents, and surfaces a
        'flag' verdict with the anomalous indices for downstream handling.
        """
        mock_context_guard.detect_embedding_anomaly.return_value = [0, 1]
        orch = RAGOrchestrator(
            context_guard=mock_context_guard,
            vector_clients={"chroma": mock_vector_client},
            config={},
        )
        result = await orch.execute_query(
            collection_name="test",
            query_text="query",
            project_id="proj1",
            vector_db_type="chroma",
        )
        assert result.action == "allow"
        assert len(result.documents) == 2
        assert result.anomalous_indices == [0, 1]
        assert result.scan_verdict["action"] == "flag"
        assert "anomaly heuristics" in result.detail

    @pytest.mark.asyncio
    async def test_context_scan_block(self, mock_context_guard, mock_vector_client):
        mock_context_guard.scan_documents = AsyncMock(
            return_value=MagicMock(action="block", detail="Injection detected", flagged_documents=[])
        )
        orch = RAGOrchestrator(
            context_guard=mock_context_guard,
            vector_clients={"chroma": mock_vector_client},
            config={},
        )
        result = await orch.execute_query(
            collection_name="test",
            query_text="query",
            project_id="proj1",
            vector_db_type="chroma",
            policy={"require_context_scan": True},
        )
        assert result.action == "block"

    @pytest.mark.asyncio
    async def test_empty_results(self, mock_context_guard):
        empty_client = MagicMock()
        empty_client.query = AsyncMock(return_value=[])
        orch = RAGOrchestrator(
            context_guard=mock_context_guard,
            vector_clients={"chroma": empty_client},
            config={},
        )
        result = await orch.execute_query(
            collection_name="test",
            query_text="query",
            project_id="proj1",
            vector_db_type="chroma",
        )
        assert result.action == "allow"
        assert result.documents == []

    def test_rag_verdict_dataclass(self):
        v = RAGVerdict(action="block", detail="test")
        assert v.action == "block"
        assert v.documents == []
