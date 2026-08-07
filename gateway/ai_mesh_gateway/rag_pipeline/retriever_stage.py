"""Retriever stage: execute vector DB query with circuit breaker and rate limiting."""
from __future__ import annotations

import asyncio
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

        # ── RAG-30: operator policy rules scoped to the RETRIEVER stage ──
        # The control plane lets an operator target a rule at pipeline_stage
        # "retriever" and the UI offers it, but only ranker_stage ever called
        # evaluate_for_stage — so such a rule saved, compiled, shipped to Redis
        # and displayed as Enabled while evaluating ZERO times. Rules with an
        # empty pipeline_stage already match every stage and are unaffected.
        if getattr(inp, "compiled_policies", None):
            try:
                try:
                    from policy_engine import evaluate_for_stage
                except ImportError:
                    from gateway.policy_engine import evaluate_for_stage
                _pol = evaluate_for_stage(
                    prompt=inp.query_text,
                    response_text="",
                    compiled_policies=inp.compiled_policies,
                    stage="retriever",
                    actor=getattr(inp, "actor", None),
                )
                if _pol.action == "block":
                    LOG.info("RAG retriever blocked by operator policy rule (RAG-30): %s", _pol.message)
                    return RetrieverStageOutput(
                        verdict=StageVerdict(
                            action="block",
                            threat_type="policy_violation",
                            confidence=1.0,
                            detail=_pol.message or "Blocked by policy",
                        ),
                    )
            except Exception:  # noqa: BLE001 — advisory layer, never break retrieval
                LOG.warning("RAG-30 retriever-stage policy evaluation failed", exc_info=True)

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
        # BYOK: each org has its OWN provider client + credentials, so key the
        # breaker per-org (project_id). Otherwise one tenant's provider outage /
        # bad-credentials failures open the SHARED ``vectordb:{type}`` breaker and
        # block every other tenant on the same provider type — even those whose
        # own provider is healthy (a cross-tenant availability coupling; the rate
        # limiter above is already org-scoped). The gateway-env client (no
        # per-request override) is genuinely shared, so it keeps a global key.
        if inp.vector_client is not None and inp.project_id:
            cb_key = f"vectordb:{inp.vector_db_type}:{inp.project_id}"
        else:
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
        # Prefer the request-scoped per-org client (resolved from the caller's
        # VectorProviderConfig) over the static, env-built dict. This is how an
        # organisation's own vector DB credentials drive retrieval.
        client = inp.vector_client or self._clients.get(inp.vector_db_type)
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
            # RAG-03 (2026-08-03): tenant isolation is UNCONDITIONAL. This previously read
            # ``inp.project_id if self._config.get("vector_db_isolation", True) else None`` —
            # with the toggle off, project_id became None and every affected tenant's reads
            # and writes collapsed into ONE shared physical namespace ``None__{collection}``
            # (vector_client._build_collection_name), silently mixing tenants. There is no
            # legitimate reason to address a vector store without a tenant key, so the
            # off-switch is gone; a missing project_id now fails CLOSED below.
            project_id = inp.project_id
            if not project_id:
                return RetrieverStageOutput(
                    verdict=StageVerdict(
                        action="block",
                        threat_type="tenant_isolation_missing",
                        confidence=1.0,
                        detail="No tenant namespace resolved for this request; refusing to query the vector store.",
                    ),
                    circuit_breaker_state=cb_state,
                )
            # Bound the vector query so a hanging/slow provider (network failure,
            # unresponsive server) cannot block the request indefinitely — the SDK
            # calls run in a ThreadPoolExecutor with no timeout, and Chroma's
            # HttpClient / Pinecone index set none. On timeout we raise below, the
            # circuit breaker records the error (repeated timeouts OPEN it -> fast
            # fail subsequent queries, capping thread accumulation), and the caller
            # gets a clean block instead of a hung connection. (VECTOR PROVIDER:
            # timeouts / network failures)
            _q_timeout = float(self._config.get("rag_query_timeout_s", 30.0) or 30.0)
            documents = await asyncio.wait_for(
                client.query(
                    collection_name=inp.collection_name,
                    query_text=inp.query_text,
                    n_results=inp.n_results,
                    where=inp.where_filter,
                    namespace=inp.namespace,
                    project_id=project_id,
                ),
                timeout=_q_timeout,
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
                    # E3: do NOT leak the raw provider exception (Pinecone internals /
                    # query echo) to the client; the full exc is logged above.
                    detail="Vector retrieval failed.",
                ),
                retrieval_latency_ms=(time.perf_counter() - start) * 1000,
                circuit_breaker_state=cb_state,
            )

        # ── 4b. Optional per-org provider-hosted reranking ──
        # When the org configured a reranker_model on its vector provider (e.g.
        # Pinecone-hosted bge-reranker-v2-m3), reorder the retrieved documents by
        # semantic relevance to the query BEFORE threshold filtering + guardrail
        # scoring. Fail-open: rerank() returns the original order on any error.
        if documents and hasattr(client, "rerank") and getattr(client, "_reranker_model", ""):
            try:
                # Bound the rerank (fail-open) — a hanging provider-hosted reranker
                # must not block the request after retrieval already succeeded. On
                # timeout we keep the retrieval order, same as any rerank error. (#26)
                _rr_timeout = float(self._config.get("rag_rerank_timeout_s", 15.0) or 15.0)
                documents = await asyncio.wait_for(
                    client.rerank(inp.query_text, documents, top_n=inp.n_results),
                    timeout=_rr_timeout,
                )
            except Exception as exc:  # noqa: BLE001
                LOG.warning("Reranker step failed (keeping retrieval order): %s", exc)

        # ── 5. Relevance threshold filtering ──
        # Compare a normalized SIMILARITY (higher = more relevant) against the
        # threshold. Clients emit `score` (cosine similarity) and/or `distance`
        # (1 - similarity). Previously this read only `score`/`similarity` and
        # defaulted to 1.0, so when a client emitted only `distance` every doc
        # passed — a silent no-op filter that never dropped degenerate matches.
        # Derive the similarity from whichever key is present. (H6)
        def _relevance(doc: dict) -> float:
            if doc.get("score") is not None:
                return float(doc["score"])
            if doc.get("similarity") is not None:
                return float(doc["similarity"])
            if doc.get("distance") is not None:
                return max(0.0, min(1.0, 1.0 - float(doc["distance"])))
            return 1.0  # no relevance signal available → don't drop

        # Default 0.0 = OFF. The previous default (0.75) was never actually
        # applied — the filter read a key the clients don't emit and fell back to
        # 1.0, so EVERY doc passed. Activating it now WITH that stale 0.75 default
        # would wrongly drop legitimate results (e5 cosine for good matches often
        # sits below 0.75). So default OFF and let operators opt in to a calibrated
        # threshold; the filter is now functional when they do. Degenerate matches
        # from embedding failure are already prevented upstream (fail-closed embed).
        # Per-request policy (carrying the per-org FirewallConfig value the RAG
        # handler merges in) overrides the static startup default, so an org's
        # configured relevance threshold actually applies. Previously the retriever
        # read ONLY the static config and silently ignored the per-org frontend
        # setting — a config→runtime mismatch (same class as the query-stage
        # injection thresholds). Float-coerced; falls back to static config.
        try:
            relevance_threshold = float(inp.policy.get(
                "rag_relevance_threshold",
                self._config.get("rag_relevance_threshold", 0.0),
            ))
        except (TypeError, ValueError):
            relevance_threshold = float(self._config.get("rag_relevance_threshold", 0.0) or 0.0)
        if documents and relevance_threshold > 0:
            documents = [doc for doc in documents if _relevance(doc) >= relevance_threshold]

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
