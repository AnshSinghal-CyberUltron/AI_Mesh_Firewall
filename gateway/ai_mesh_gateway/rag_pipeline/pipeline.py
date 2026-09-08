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

# Every stage the pipeline can run, in execution order. Used to record the
# stages a terminated request never reached. (RAG-19)
CANONICAL_STAGES: tuple[str, ...] = ("query", "retriever", "ranker", "generator")


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
        actor: dict[str, Any] | None = None,
        telemetry_enabled: bool = True,
        escalation_level: int = 0,
    ) -> PipelineResult:
        effective_policy = policy or {}
        ctx = PipelineContext(
            project_id=project_id,
            collection_name=collection_name,
            query_text=query_text,
            # Starting escalation level, clamped to the configured range. No
            # caller sets it today, which is why level 2 (the only config with
            # block_on_any_flag) was structurally unreachable: it needs two
            # upstream "flag" verdicts, and the only stage that can flag twice
            # is itself gated on level 2. This is the explicit opt-in that makes
            # the level reachable — it must stay caller-driven, since arming
            # strict enforcement the operator did not select is an over-block.
            # (RAG-20)
            escalation_level=min(max(int(escalation_level or 0), 0), 2),
            # Honor the caller's per-org audit gate for per-stage telemetry
            # (mirrors the chat path). The handler resolves this from the org's
            # telemetry_enabled/audit_logging_enabled config.
            telemetry_enabled=telemetry_enabled,
        )

        # Resolve org-scoped compiled policies for THIS request. Passed
        # per-request into RankerStageInput (below) instead of mutating the
        # shared singleton ranker via update_policies() — the latter races
        # across the pipeline's await points and let one org's request apply
        # another org's policies (cross-tenant contamination) under concurrency.
        org_slug = (effective_policy or {}).get("_org_slug") or ""
        compiled_policies = self._get_compiled_policies(org_slug)

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
            # RAG-27: pass the already-fetched RAG-domain bundle so QueryStage can
            # evaluate rules the operator scoped to pipeline_stage="query".
            compiled_policies=compiled_policies or [],
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
            compiled_policies=compiled_policies or [],  # RAG-30
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

        # ──────── Guardrails-only simplified query pipeline ────────
        # When both downstream stages are disabled (the default), the gateway's
        # job is done after retrieval-scanning: return the retriever-approved
        # documents directly. The client owns reranking/generation and calls the
        # generator model through the normal chat pipeline (where Tier-1/Tier-2 +
        # output guard apply). This removes the reranker/generator overhead.
        rag_ranker_on = bool(self._config.get("rag_ranker_enabled", False))
        rag_generator_on = bool(self._config.get("rag_generator_enabled", False))
        # Force the ranker to run when the active policy declares document-content
        # controls, even if the global ranker flag is off. RankerStage is the SOLE
        # consumer of block_sensitive_documents / anomaly_distance_threshold /
        # require_context_scan / sensitive_fields; skipping it would return docs
        # with SSN/secret content (or sensitive metadata) verbatim. The
        # guardrails-only fast path only applies when none of these are requested.
        if not rag_ranker_on and self._policy_requires_ranker(effective_policy):
            rag_ranker_on = True
        # RAG-31: a stage the operator wrote a rule for MUST run, otherwise their
        # selection silently does nothing. The generator was reachable ONLY via
        # the gateway-wide GATEWAY_RAG_GENERATOR_ENABLED env var — absent from
        # the per-org config and from every policy field — so an operator could
        # create a rule scoped to pipeline_stage="generator", see it Enabled in
        # the UI, and have it evaluate zero times with no way to switch the stage
        # on. That is the inverse of operator control: not "enforcement they did
        # not choose", but "a choice they cannot act on".
        # Activation stays OPERATOR-DRIVEN (their own rule turns the stage on) —
        # the stage is NOT defaulted on, so an org with no such rule keeps the
        # cheap guardrails-only path.
        if not rag_generator_on and self._bundle_targets_stage(compiled_policies, "generator"):
            rag_generator_on = True
        if not rag_ranker_on and self._bundle_targets_stage(compiled_policies, "ranker"):
            rag_ranker_on = True
        if not rag_ranker_on and not rag_generator_on:
            ctx.final_action = r_out.verdict.action
            # The ranker is the SOLE producer of flagged_documents /
            # anomalous_documents and the generator the sole producer of
            # context_binding_id / canary_word. Neither ran, so the empty values
            # below are absences, not clean results. Record the skip and label
            # the scan state explicitly — an empty flagged_documents on its own
            # reads as "scanned, nothing found", which is how a prior review
            # concluded anomaly detection was a hardcoded empty literal.
            # (RAG-19)
            ctx.mark_stage_skipped("ranker", "disabled")
            ctx.mark_stage_skipped("generator", "disabled")
            audit = ctx.to_audit_dict()
            return PipelineResult(
                action=r_out.verdict.action,
                documents=r_out.documents,
                total_retrieved=r_out.total_retrieved,
                filtered_count=0,
                scan_verdict={
                    "action": r_out.verdict.action,
                    # StageVerdict carries threat_type and confidence, but this
                    # boundary dropped both — so every downstream consumer (the
                    # vector routes and the dashboard they feed) fell back to a
                    # generic "rag_threat" at risk 0. Computed, then discarded one
                    # line before it was needed.
                    "threat_type": r_out.verdict.threat_type,
                    "confidence": r_out.verdict.confidence,
                    # Kept as lists (never None): rag_orchestrator feeds
                    # anomalous_documents straight into RAGVerdict.anomalous_indices
                    # and main.py setdefaults flagged_documents. The not-run
                    # signal rides alongside them in document_content_scan.
                    "flagged_documents": [],
                    "anomalous_documents": [],
                    "document_content_scan": self._content_scan_state(ctx),
                    "detail": r_out.verdict.detail,
                },
                context_binding_id="",
                pipeline_context=ctx,
                context_chunks=[],
                pipeline_audit=audit,
                canary_word="",
                model_downgrade=model_downgrade,
            )

        # ──────── Stage 3: Ranker ────────
        t2 = time.perf_counter()
        rank_out = await self._ranker.execute(RankerStageInput(
            documents=r_out.documents,
            query_text=effective_query,
            policy=effective_policy,
            escalation_level=ctx.escalation_level,
            actor=actor,
            compiled_policies=compiled_policies,
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

        # Generator stage disabled (default): return the ranker-approved
        # documents directly. Canary tokens / context-binding / leakage
        # registration are RAG-application plumbing the client owns.
        if not rag_generator_on:
            ctx.final_action = rank_out.verdict.action
            ctx.mark_stage_skipped("generator", "disabled")
            audit = ctx.to_audit_dict()
            return PipelineResult(
                action=rank_out.verdict.action,
                documents=rank_out.ranked_documents,
                total_retrieved=r_out.total_retrieved,
                filtered_count=r_out.total_retrieved - len(rank_out.ranked_documents),
                scan_verdict={
                    "action": rank_out.verdict.action,
                    "threat_type": rank_out.verdict.threat_type,
                    "confidence": rank_out.verdict.confidence,
                    "flagged_documents": rank_out.flagged_indices,
                    "anomalous_documents": rank_out.anomalous_indices,
                    # RAG-05a: the LOWER distance tail, kept DISTINCT from
                    # ``anomalous_documents`` on purpose — the ranker turns "every
                    # document anomalous" into a hard block, and an exact-match
                    # query legitimately produces a low-distance outlier, so
                    # folding these in would fail-closed on valid traffic. They
                    # carry a trust PENALTY only (ranker_stage.compute_trust_score
                    # ``near_duplicate=True``); surfacing them here is what makes
                    # that penalty observable instead of silent.
                    "near_duplicate_documents": getattr(
                        rank_out, "near_duplicate_indices", []
                    ),
                    # The ranker ran: these lists are real findings. (RAG-19)
                    "document_content_scan": self._content_scan_state(ctx),
                    "detail": rank_out.verdict.detail,
                },
                context_binding_id="",
                pipeline_context=ctx,
                context_chunks=[],
                pipeline_audit=audit,
                canary_word="",
                model_downgrade=model_downgrade,
            )

        # ──────── Stage 4: Generator ────────
        t3 = time.perf_counter()
        gen_out = await self._generator.execute(GeneratorStageInput(
            compiled_policies=compiled_policies or [],  # RAG-30
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
                "threat_type": rank_out.verdict.threat_type,
                "confidence": rank_out.verdict.confidence,
                "flagged_documents": rank_out.flagged_indices,
                "anomalous_documents": rank_out.anomalous_indices,
                # RAG-05a: lower-tail near-duplicates — see the guardrails path
                # above for why these stay separate from ``anomalous_documents``.
                "near_duplicate_documents": getattr(
                    rank_out, "near_duplicate_indices", []
                ),
                # The ranker ran: these lists are real findings. (RAG-19)
                "document_content_scan": self._content_scan_state(ctx),
                "detail": rank_out.verdict.detail,
            },
            context_binding_id=gen_out.context_binding_id,
            pipeline_context=ctx,
            context_chunks=gen_out.context_chunks,
            pipeline_audit=audit,
            canary_word=gen_out.canary_word,
            model_downgrade=model_downgrade or gen_out.model_downgrade,
        )

    @staticmethod
    def _content_scan_state(ctx: PipelineContext) -> str:
        """"ran" once RankerStage executed for this request, else "not_run".

        Derived from the audit trail rather than written as a literal, so the
        reported scan state cannot drift from what the pipeline actually did.
        (RAG-19)
        """
        return "ran" if "ranker" in ctx.executed_stages else "not_run"

    @staticmethod
    def _bundle_targets_stage(compiled_policies: Any, stage: str) -> bool:
        """True when the org's compiled bundle holds an ENABLED rule aimed at *stage*.

        RAG-31: activation must follow the operator's own selection. A rule with
        an EMPTY ``pipeline_stage`` means "all stages" and deliberately does NOT
        count here — it would switch on the generator (and its Redis grounding
        writes) for every org that ever wrote a generic rule, which is exactly
        the by-default enforcement the operator did not ask for. Only an
        explicit stage target activates the stage.
        """
        if not compiled_policies:
            return False
        try:
            for entry in compiled_policies:
                if not isinstance(entry, dict):
                    continue
                for rule in entry.get("rules") or []:
                    if not isinstance(rule, dict):
                        continue
                    if rule.get("enabled") is False:
                        continue
                    if str(rule.get("pipeline_stage") or "").strip().lower() == stage:
                        return True
        except Exception:  # noqa: BLE001 — a malformed bundle must not break retrieval
            LOG.debug("RAG-31 stage-activation scan failed", exc_info=True)
        return False

    @staticmethod
    def _policy_requires_ranker(policy: dict[str, Any]) -> bool:
        """True when the policy declares document-content controls the ranker enforces.

        These advertised guardrails (sensitive-document blocking, anomaly
        distance thresholds, required context scanning, per-field redaction)
        only run inside RankerStage. If a policy asks for any of them we must
        run the ranker for the request, even when the global ranker flag is off.
        """
        if not policy:
            return False
        if policy.get("block_sensitive_documents"):
            return True
        if policy.get("require_context_scan"):
            return True
        threshold = policy.get("anomaly_distance_threshold")
        if threshold is not None:
            try:
                if float(threshold) > 0:
                    return True
            except (TypeError, ValueError):
                pass
        if policy.get("sensitive_fields"):
            return True
        return False

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
        # Global sink absent OR the caller's org disabled telemetry/audit logging
        # (telemetry_enabled=False) → suppress. Without the ctx gate, per-stage
        # rag_pipeline events (carrying verdict detail, collection, namespace)
        # leaked to the audit sink for orgs that turned audit logging OFF, while
        # the chat path (_emit_telemetry) correctly dropped them — a config→
        # runtime asymmetry. (#16)
        if self._telemetry is None or not ctx.telemetry_enabled:
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

    @classmethod
    def _build_result(cls, ctx: PipelineContext, total_retrieved: int, blocked: bool = False) -> PipelineResult:
        last_stage = ctx.stages[-1] if ctx.stages else None
        # A terminating verdict returns before the remaining stages run. Record
        # them, so their absence from the audit cannot be read as "these
        # controls evaluated and passed". (RAG-19)
        for name in CANONICAL_STAGES:
            if name not in ctx.executed_stages:
                ctx.mark_stage_skipped(name, "not_reached")
        audit = ctx.to_audit_dict()
        return PipelineResult(
            action="block" if blocked else (last_stage.verdict.action if last_stage else "allow"),
            total_retrieved=total_retrieved,
            filtered_count=total_retrieved,
            scan_verdict={
                "action": last_stage.verdict.action if last_stage else "block",
                # StageVerdict carries threat_type and confidence, but this
                # boundary dropped both — so every downstream consumer (the
                # vector routes and the dashboard they feed) fell back to a
                # generic "rag_threat" at risk 0. Computed, then discarded one
                # line before it was needed.
                "threat_type": last_stage.verdict.threat_type if last_stage else "",
                "confidence": last_stage.verdict.confidence if last_stage else 0.0,
                # No flagged/anomalous keys here by design: emitting empty lists
                # for a request that terminated before the ranker would assert a
                # clean document scan that never happened. (RAG-19)
                "document_content_scan": cls._content_scan_state(ctx),
                "detail": last_stage.verdict.detail if last_stage else "",
            },
            pipeline_context=ctx,
            pipeline_audit=audit,
        )
