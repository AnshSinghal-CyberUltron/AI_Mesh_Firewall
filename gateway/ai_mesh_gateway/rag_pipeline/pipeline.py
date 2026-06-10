"""RAGFirewallPipeline: the single source of truth for RAG request processing.

Orchestrates 4 stages (Query → Retriever → Ranker → Generator) with
independent policy decisions, inter-stage escalation, per-stage telemetry,
document chain-of-custody, query rewrite, and a full audit trail.
"""
from __future__ import annotations

import logging
import time
from typing import Any, TYPE_CHECKING

from .context import PipelineContext, StageRecord
from .contracts import (
    GeneratorStageInput,
    PipelineResult,
    QueryStageInput,
    RankerStageInput,
    RetrieverStageInput,
    StageVerdict,
)
from .query_stage import QueryStage
from .retriever_stage import RetrieverStage
from .ranker_stage import RankerStage
from .generator_stage import GeneratorStage

if TYPE_CHECKING:
    from circuit_breaker import CircuitBreaker
    from context_guard import ContextGuard
    from embedding_vault import EmbeddingVault
    from intent_classifier import IntentClassifier
    from leakage_detector import SemanticLeakageDetector
    from llm_judge import LLMJudge
    from canary_tokens import CanaryTokenManager
    from rate_limiter import RateLimiter
    from scanner import InputScanner
    from telemetry import TelemetryProducer

LOG = logging.getLogger("gateway.rag_pipeline")


class RAGFirewallPipeline:
    """Orchestrates the 4-stage RAG firewall pipeline.

    Each stage:
    - Wraps an existing gateway component (no rewrites)
    - Makes an independent policy decision (StageVerdict)
    - Contributes to a PipelineContext audit trail
    - Supports inter-stage escalation
    - Emits stage-specific telemetry
    - Tracks document chain-of-custody via DocumentManifest
    """

    def __init__(
        self,
        input_scanner: "InputScanner | None",
        context_guard: "ContextGuard | None",
        leakage_detector: "SemanticLeakageDetector | None",
        vector_clients: dict[str, Any],
        circuit_breaker: "CircuitBreaker | None",
        rate_limiter: "RateLimiter | None",
        telemetry: "TelemetryProducer | None",
        redis_client: Any,
        config: dict,
        policy_sync: Any = None,
        llm_judge: "LLMJudge | None" = None,
        embedding_vault: "EmbeddingVault | None" = None,
        canary_token_manager: "CanaryTokenManager | None" = None,
        intent_classifier: "IntentClassifier | None" = None,
    ) -> None:
        self._query = QueryStage(
            input_scanner, config,
            llm_judge=llm_judge,
            embedding_vault=embedding_vault,
            intent_classifier=intent_classifier,
        )
        self._retriever = RetrieverStage(vector_clients, circuit_breaker, rate_limiter, config)
        self._ranker = RankerStage(context_guard, config)
        self._generator = GeneratorStage(
            leakage_detector, redis_client, config,
            canary_token_manager=canary_token_manager,
        )
        self._telemetry = telemetry
        self._config = config
        self._policy_sync = policy_sync

    async def execute(
        self,
        query_text: str,
        collection_name: str,
        project_id: str,
        vector_db_type: str = "pinecone",
        n_results: int = 10,
        where_filter: dict[str, Any] | None = None,
        namespace: str = "",
        policy: dict[str, Any] | None = None,
        key_hash: str = "",
        organization_id: int | None = None,
        user_id: int | str | None = None,
        vector_client_override: Any = None,
    ) -> PipelineResult:
        effective_policy = policy or {}
        ctx = PipelineContext(
            project_id=project_id,
            collection_name=collection_name,
            query_text=query_text,
        )

        # Hot-update compiled policies from sync cache (org-scoped)
        org_slug = (effective_policy or {}).get("_org_slug") or ""
        compiled_policies = self._get_compiled_policies(org_slug)
        if compiled_policies:
            self._ranker.update_policies(compiled_policies)

        # ──────── Stage 1: Query ────────
        t0 = time.perf_counter()
        q_out = await self._query.execute(QueryStageInput(
            query_text=query_text,
            collection_name=collection_name,
            project_id=project_id,
            vector_db_type=vector_db_type,
            n_results=n_results,
            where_filter=where_filter,
            namespace=namespace,
            policy=effective_policy,
            key_hash=key_hash,
        ))
        ctx.add_stage(StageRecord(
            stage_name="query",
            started_at=t0,
            completed_at=time.perf_counter(),
            verdict=q_out.verdict,
            document_count_in=0,
            document_count_out=0,
            metadata={
                "injection_flags": q_out.injection_flags,
                "scan_tier": q_out.scan_tier,
                "rewritten_query": q_out.rewritten_query,
                "original_query": q_out.original_query,
            },
        ))
        self._emit_stage_telemetry(ctx, "query", q_out.verdict, project_id, key_hash, collection_name, q_out.latency_ms, organization_id=organization_id, user_id=user_id, namespace=namespace)
        if q_out.verdict.action == "block":
            ctx.final_action = "block"
            return self._build_result(ctx, 0, blocked=True)

        # Determine effective query: use rewritten if available
        if q_out.verdict.action == "rewrite" and q_out.rewritten_query:
            effective_query = q_out.rewritten_query
        else:
            effective_query = q_out.sanitized_query

        # Track model downgrade from query stage
        model_downgrade = ""
        if q_out.verdict.action == "model_downgrade" and q_out.verdict.downgrade_model:
            model_downgrade = q_out.verdict.downgrade_model

        # ──────── Stage 2: Retriever ────────
        t1 = time.perf_counter()
        r_out = await self._retriever.execute(RetrieverStageInput(
            query_text=effective_query,
            collection_name=collection_name,
            project_id=project_id,
            vector_db_type=vector_db_type,
            n_results=n_results,
            where_filter=where_filter,
            namespace=namespace,
            policy=effective_policy,
            escalation_level=ctx.escalation_level,
            key_hash=key_hash,
            vector_client=vector_client_override,
        ))
        ctx.add_stage(StageRecord(
            stage_name="retriever",
            started_at=t1,
            completed_at=time.perf_counter(),
            verdict=r_out.verdict,
            document_count_in=0,
            document_count_out=r_out.total_retrieved,
            metadata={
                "circuit_breaker_state": r_out.circuit_breaker_state,
                "retrieval_latency_ms": round(r_out.retrieval_latency_ms, 2),
            },
            approved_doc_ids=[m.doc_id for m in r_out.document_manifest],
        ))
        self._emit_stage_telemetry(ctx, "retriever", r_out.verdict, project_id, key_hash, collection_name, r_out.retrieval_latency_ms, organization_id=organization_id, user_id=user_id, namespace=namespace)
        if r_out.verdict.action == "block":
            ctx.final_action = "block"
            return self._build_result(ctx, r_out.total_retrieved, blocked=True)

        # ──────── Stage 3: Ranker ────────
        t2 = time.perf_counter()
        rank_out = await self._ranker.execute(RankerStageInput(
            documents=r_out.documents,
            query_text=effective_query,
            policy=effective_policy,
            escalation_level=ctx.escalation_level,
        ))
        t2_end = time.perf_counter()
        ctx.add_stage(StageRecord(
            stage_name="ranker",
            started_at=t2,
            completed_at=t2_end,
            verdict=rank_out.verdict,
            document_count_in=len(r_out.documents),
            document_count_out=len(rank_out.ranked_documents),
            metadata={
                "anomalous_indices": rank_out.anomalous_indices,
                "flagged_indices": rank_out.flagged_indices,
                "documents_removed": rank_out.documents_removed,
            },
            approved_doc_ids=[m.doc_id for m in rank_out.approved_manifest],
        ))
        self._emit_stage_telemetry(ctx, "ranker", rank_out.verdict, project_id, key_hash, collection_name, (t2_end - t2) * 1000, organization_id=organization_id, user_id=user_id, namespace=namespace)
        if rank_out.verdict.action == "block":
            ctx.final_action = "block"
            return self._build_result(ctx, r_out.total_retrieved, blocked=True)

        # ──────── Stage 4: Generator ────────
        t3 = time.perf_counter()
        gen_out = await self._generator.execute(GeneratorStageInput(
            documents=rank_out.ranked_documents,
            query_text=effective_query,
            project_id=project_id,
            policy=effective_policy,
            escalation_level=ctx.escalation_level,
            key_hash=key_hash,
            approved_manifest=rank_out.approved_manifest,
        ))
        t3_end = time.perf_counter()
        ctx.add_stage(StageRecord(
            stage_name="generator",
            started_at=t3,
            completed_at=t3_end,
            verdict=gen_out.verdict,
            document_count_in=len(rank_out.ranked_documents),
            document_count_out=len(gen_out.safe_documents),
            metadata={
                "context_binding_id": gen_out.context_binding_id,
                "leakage_registrations": gen_out.leakage_registrations,
                "context_integrity_verified": gen_out.context_integrity_verified,
            },
            approved_doc_ids=[m.doc_id for m in gen_out.verified_manifest],
        ))
        self._emit_stage_telemetry(ctx, "generator", gen_out.verdict, project_id, key_hash, collection_name, (t3_end - t3) * 1000, organization_id=organization_id, user_id=user_id, namespace=namespace)

        if gen_out.verdict.action == "block":
            ctx.final_action = "block"
            return self._build_result(ctx, r_out.total_retrieved, blocked=True)

        ctx.final_action = gen_out.verdict.action
        audit = ctx.to_audit_dict()
        return PipelineResult(
            action=gen_out.verdict.action,
            documents=gen_out.safe_documents,
            total_retrieved=r_out.total_retrieved,
            filtered_count=r_out.total_retrieved - len(gen_out.safe_documents),
            scan_verdict={
                "action": rank_out.verdict.action,
                "flagged_documents": rank_out.flagged_indices,
                "anomalous_documents": rank_out.anomalous_indices,
                "detail": rank_out.verdict.detail,
            },
            context_binding_id=gen_out.context_binding_id,
            pipeline_context=ctx,
            context_chunks=gen_out.context_chunks,
            pipeline_audit=audit,
            canary_word=gen_out.canary_word,
            model_downgrade=model_downgrade or gen_out.model_downgrade,
        )

    def _get_compiled_policies(self, org_slug: str = "") -> list[dict]:
        """Retrieve compiled policy entries from the sync cache for an org."""
        if self._policy_sync is None:
            return []
        slug = (org_slug or "").strip() or "default"
        try:
            try:
                from ..policy_sync import filter_policies_by_domain
            except ImportError:
                from policy_sync import filter_policies_by_domain
            return filter_policies_by_domain(
                self._policy_sync.get_policies(slug) or [], "rag"
            )
        except Exception:
            LOG.debug("Failed to retrieve compiled policies from sync", exc_info=True)
        return []

    def _emit_stage_telemetry(
        self,
        ctx: PipelineContext,
        stage: str,
        verdict: StageVerdict,
        project_id: str,
        key_hash: str,
        collection_name: str,
        latency_ms: float,
        organization_id: int | None = None,
        user_id: int | str | None = None,
        namespace: str = "",
    ) -> None:
        if self._telemetry is None:
            return
        try:
            from telemetry import build_telemetry_event
        except ImportError:
            from gateway.telemetry import build_telemetry_event

        self._telemetry.emit(build_telemetry_event(
            event_type="rag_pipeline",
            project_id=project_id,
            key_prefix=key_hash[:8] if key_hash else "",
            action=verdict.action,
            threat_type=verdict.threat_type,
            risk_score=verdict.confidence,
            pipeline_stage=stage,
            latency_ms=latency_ms,
            organization_id=organization_id,
            user_id=user_id,
            metadata={
                "request_id": ctx.request_id,
                "collection": collection_name,
                "namespace": namespace,
                "escalation_level": ctx.escalation_level,
                "detail": verdict.detail,
                "module": "1.3",
                "module_id": "1.3",
            },
        ))

    @staticmethod
    def _build_result(ctx: PipelineContext, total_retrieved: int, blocked: bool = False) -> PipelineResult:
        last_stage = ctx.stages[-1] if ctx.stages else None
        audit = ctx.to_audit_dict()
        return PipelineResult(
            action="block" if blocked else (last_stage.verdict.action if last_stage else "allow"),
            total_retrieved=total_retrieved,
            filtered_count=total_retrieved,
            scan_verdict={
                "action": last_stage.verdict.action if last_stage else "block",
                "detail": last_stage.verdict.detail if last_stage else "",
            },
            pipeline_context=ctx,
            pipeline_audit=audit,
        )
