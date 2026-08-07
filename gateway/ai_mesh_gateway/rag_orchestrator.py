"""
RAG Security Orchestrator -- backward-compatibility wrapper.

The real pipeline lives in gateway.rag_pipeline.pipeline.RAGFirewallPipeline.
This module re-exports compute_trust_score and RAGVerdict for existing test
compatibility, and wraps the pipeline in the old RAGOrchestrator interface.

DEAD CODE — RAGOrchestrator has ZERO production call sites (tests only). It is
kept for the re-exports above, not because anything routes through it. Read
before reviving: it builds RAGFirewallPipeline with no circuit breaker, no rate
limiter, no telemetry and no redis client, so a revived call path silently loses
the breaker, request throttling and the audit trail. The scanner that drives
QueryStage input scanning is now a required constructor argument for exactly
this reason — disabling it must be a visible decision at the call site, never a
default. Prefer RAGFirewallPipeline directly. (RAG-24)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

# Re-export compute_trust_score from canonical location
from rag_pipeline.ranker_stage import compute_trust_score  # noqa: F401

LOG = logging.getLogger("gateway.rag_orchestrator")


@dataclass
class RAGVerdict:
    """Result of the full RAG security pipeline (backward-compat)."""

    action: str = "allow"
    documents: list[dict[str, Any]] = field(default_factory=list)
    anomalous_indices: list[int] = field(default_factory=list)
    scan_verdict: Any = None
    detail: str = ""


class RAGOrchestrator:
    """Backward-compat wrapper. Delegates to RAGFirewallPipeline. DEAD — see module docstring."""

    def __init__(self, context_guard, vector_clients: dict, config: dict, *, input_scanner):
        """``input_scanner`` is keyword-only and has NO default.

        It used to be hardcoded to None here, so reviving this wrapper would
        have silently disabled QueryStage input scanning — every query reaching
        the retriever unscanned, with nothing at the call site to show for it.
        Passing None is still allowed (it is what the surviving tests want) but
        it now has to be written down, and it logs. (RAG-24)
        """
        from rag_pipeline import RAGFirewallPipeline

        if input_scanner is None:
            LOG.warning(
                "RAGOrchestrator constructed with input_scanner=None: QueryStage "
                "input scanning is DISABLED for this pipeline (RAG-24)"
            )

        self._pipeline = RAGFirewallPipeline(
            input_scanner=input_scanner,
            context_guard=context_guard,
            leakage_detector=None,
            vector_clients=vector_clients,
            circuit_breaker=None,
            rate_limiter=None,
            telemetry=None,
            redis_client=None,
            config=config,
        )

    async def execute_query(
        self,
        collection_name: str,
        query_text: str,
        project_id: str,
        vector_db_type: str = "pinecone",
        policy: dict | None = None,
        n_results: int = 10,
    ) -> RAGVerdict:
        """Full RAG pipeline with anomaly detection and context scanning."""
        result = await self._pipeline.execute(
            query_text=query_text,
            collection_name=collection_name,
            project_id=project_id,
            vector_db_type=vector_db_type,
            n_results=n_results,
            policy=policy,
        )
        return RAGVerdict(
            action=result.action,
            documents=result.documents,
            anomalous_indices=result.scan_verdict.get("anomalous_documents", []),
            scan_verdict=result.scan_verdict,
            detail=result.scan_verdict.get("detail", ""),
        )
