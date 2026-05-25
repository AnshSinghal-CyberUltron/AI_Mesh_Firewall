"""Retriever stage: execute vector DB query with circuit breaker and rate limiting."""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, TYPE_CHECKING

from .contracts import DocumentManifest, RetrieverStageInput, RetrieverStageOutput, StageVerdict

if TYPE_CHECKING:
    from circuit_breaker import CircuitBreaker
    from rate_limiter import RateLimiter

LOG = logging.getLogger("gateway.rag_pipeline.retriever_stage")


class RetrieverStage:
    """Wraps VectorDBClient with circuit breaker and rate limiting.

    Responsibilities:
    - RPM rate limiting for RAG queries
    - Circuit breaker for vector DB availability
    - Namespace-isolated vector query
    - Relevance threshold filtering
    """

    def __init__(
        self,
        vector_clients: dict[str, Any],
        circuit_breaker: "CircuitBreaker | None",
        rate_limiter: "RateLimiter | None",
        config: dict,
    ) -> None:
        self._clients = vector_clients
        self._cb = circuit_breaker
        self._rl = rate_limiter
        self._config = config

    async def execute(self, inp: RetrieverStageInput) -> RetrieverStageOutput:
        start = time.perf_counter()
        cb_state = "closed"

        # ── 1. Rate limiting for RAG queries ──
        if self._rl is not None:
            rag_rpm = self._config.get("rag_rate_limit_rpm", 0)
            if rag_rpm > 0:
                allowed, count = await self._rl.check_model_rate_limit(
                    model_name=f"rag:{inp.project_id}",
                    max_rpm=rag_rpm,
                )
                if not allowed:
                    return RetrieverStageOutput(
                        verdict=StageVerdict(
                            action="block",
                            threat_type="rate_limit",
                            confidence=1.0,
                            detail=f"RAG rate limit exceeded ({count}/{rag_rpm} rpm)",
                        ),
                        circuit_breaker_state=cb_state,
                        rate_limit_remaining=0,
                    )

        # ── 2. Circuit breaker check for vector DB ──
        cb_key = f"vectordb:{inp.vector_db_type}"
        if self._cb is not None:
            status = await self._cb.check(cb_key)
            cb_state = status.state.value
            if status.should_block:
                return RetrieverStageOutput(
                    verdict=StageVerdict(
                        action="block",
                        threat_type="circuit_breaker",
                        confidence=1.0,
                        detail=f"Vector DB circuit breaker OPEN for {inp.vector_db_type}",
                    ),
                    circuit_breaker_state=cb_state,
                )

        # ── 3. Get vector client ──
        client = self._clients.get(inp.vector_db_type)
        if client is None:
            return RetrieverStageOutput(
                verdict=StageVerdict(
                    action="block",
                    threat_type="client_unavailable",
                    confidence=1.0,
                    detail=f"No vector client for type: {inp.vector_db_type}",
                ),
                circuit_breaker_state=cb_state,
            )

        # ── 4. Execute query ──
        try:
            project_id = inp.project_id if self._config.get("vector_db_isolation", True) else None
            documents = await client.query(
                collection_name=inp.collection_name,
                query_text=inp.query_text,
                n_results=inp.n_results,
                where=inp.where_filter,
                namespace=inp.namespace,
                project_id=project_id,
            )

            if self._cb is not None:
                await self._cb.record_success(cb_key)

        except Exception as exc:
            LOG.error("Vector query failed: %s", exc)
            if self._cb is not None:
                await self._cb.record_error(cb_key, type(exc).__name__)
            return RetrieverStageOutput(
                verdict=StageVerdict(
                    action="block",
                    threat_type="retrieval_error",
                    confidence=1.0,
                    detail=f"Vector query failed: {exc}",
                ),
                retrieval_latency_ms=(time.perf_counter() - start) * 1000,
                circuit_breaker_state=cb_state,
            )

        # ── 5. Relevance threshold filtering ──
        relevance_threshold = self._config.get("rag_relevance_threshold", 0.75)
        if documents and relevance_threshold > 0:
            documents = [
                doc for doc in documents
                if doc.get("score", doc.get("similarity", 1.0)) >= relevance_threshold
            ]

        # ── 6. Build document manifest for chain-of-custody tracking ──
        manifest: list[DocumentManifest] = []
        for doc in (documents or []):
            content = doc.get("content", "")
            doc_id = doc.get("id", hashlib.sha256(content.encode()).hexdigest()[:16])
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            doc["_doc_id"] = str(doc_id)
            doc["_content_hash"] = content_hash
            manifest.append(DocumentManifest(
                doc_id=str(doc_id),
                content_hash=content_hash,
                source_stage="retriever",
                approved_by=["retriever"],
            ))

        latency = (time.perf_counter() - start) * 1000
        return RetrieverStageOutput(
            verdict=StageVerdict(action="allow"),
            documents=documents or [],
            total_retrieved=len(documents or []),
            retrieval_latency_ms=latency,
            circuit_breaker_state=cb_state,
            rate_limit_remaining=-1,
            document_manifest=manifest,
        )
