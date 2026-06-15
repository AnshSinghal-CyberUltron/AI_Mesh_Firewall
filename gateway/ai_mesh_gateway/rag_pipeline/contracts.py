"""Typed contracts for every stage boundary in the RAG firewall pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StageVerdict:
    """Independent policy decision from a single pipeline stage."""

    action: str = "allow"  # allow | block | flag | redact | rewrite | model_downgrade
    threat_type: str = ""
    confidence: float = 0.0
    detail: str = ""
    matched_patterns: list[str] = field(default_factory=list)
    rewritten_text: str = ""  # Non-empty when action is "rewrite"
    downgrade_model: str = ""  # Target model for model_downgrade action


@dataclass
class DocumentManifest:
    """Tracks document identity across pipeline stages for chain-of-custody."""

    doc_id: str = ""
    content_hash: str = ""  # SHA-256 of document content
    source_stage: str = ""  # Stage that first introduced this document
    approved_by: list[str] = field(default_factory=list)  # Stages that approved it


# ──────────────────────── Query Stage ────────────────────────


@dataclass
class QueryStageInput:
    query_text: str
    collection_name: str
    project_id: str
    vector_db_type: str
    n_results: int
    where_filter: dict[str, Any] | None
    namespace: str
    policy: dict[str, Any]
    key_hash: str


@dataclass
class QueryStageOutput:
    verdict: StageVerdict
    sanitized_query: str
    rewritten_query: str = ""  # The rewritten query (empty if no rewrite)
    original_query: str = ""  # Preserved original for audit trail
    injection_flags: list[str] = field(default_factory=list)
    scan_tier: str = "validation"
    latency_ms: float = 0.0
    # New detection layer results
    llm_judge_verdict: dict[str, Any] = field(default_factory=dict)
    embedding_vault_verdict: dict[str, Any] = field(default_factory=dict)
    intent_verdict: dict[str, Any] = field(default_factory=dict)


# ──────────────────────── Retriever Stage ────────────────────────


@dataclass
class RetrieverStageInput:
    query_text: str
    collection_name: str
    project_id: str
    vector_db_type: str
    n_results: int
    where_filter: dict[str, Any] | None
    namespace: str
    policy: dict[str, Any]
    escalation_level: int
    key_hash: str
    # Request-scoped vector client resolved from the caller's per-org provider
    # config (VectorProviderConfig). When set, the retriever uses it instead of
    # the pipeline's static, env-built client dict — this is what lets an org's
    # own Pinecone/Milvus credentials drive retrieval without leaking a client
    # into the shared, cross-tenant dict.
    vector_client: Any = None


@dataclass
class RetrieverStageOutput:
    verdict: StageVerdict
    documents: list[dict[str, Any]] = field(default_factory=list)
    total_retrieved: int = 0
    retrieval_latency_ms: float = 0.0
    circuit_breaker_state: str = "closed"
    rate_limit_remaining: int = -1
    document_manifest: list[DocumentManifest] = field(default_factory=list)


# ──────────────────────── Ranker Stage ────────────────────────


@dataclass
class RankerStageInput:
    documents: list[dict[str, Any]]
    query_text: str
    policy: dict[str, Any]
    escalation_level: int
    # M-04: request actor ({user_id, agent_id, roles}) for actor-scoped policies.
    actor: dict[str, Any] | None = None


@dataclass
class RankerStageOutput:
    verdict: StageVerdict
    ranked_documents: list[dict[str, Any]] = field(default_factory=list)
    anomalous_indices: list[int] = field(default_factory=list)
    flagged_indices: list[int] = field(default_factory=list)
    trust_scores: dict[int, float] = field(default_factory=dict)
    documents_removed: int = 0
    approved_manifest: list[DocumentManifest] = field(default_factory=list)


# ──────────────────────── Generator Stage ────────────────────────


@dataclass
class GeneratorStageInput:
    documents: list[dict[str, Any]]
    query_text: str
    project_id: str
    policy: dict[str, Any]
    escalation_level: int
    key_hash: str
    approved_manifest: list[DocumentManifest] = field(default_factory=list)


@dataclass
class GeneratorStageOutput:
    verdict: StageVerdict
    safe_documents: list[dict[str, Any]] = field(default_factory=list)
    context_chunks: list[str] = field(default_factory=list)
    context_binding_id: str = ""
    leakage_registrations: int = 0
    verified_manifest: list[DocumentManifest] = field(default_factory=list)
    context_integrity_verified: bool = False
    canary_word: str = ""  # Canary token injected into context
    model_downgrade: str = ""  # Downgrade model hint from pipeline


# ──────────────────────── Pipeline Result ────────────────────────


@dataclass
class PipelineResult:
    """Final result of the complete RAG firewall pipeline."""

    action: str = "allow"
    documents: list[dict[str, Any]] = field(default_factory=list)
    total_retrieved: int = 0
    filtered_count: int = 0
    scan_verdict: dict[str, Any] = field(default_factory=dict)
    context_binding_id: str = ""
    pipeline_context: Any = None  # PipelineContext
    context_chunks: list[str] = field(default_factory=list)
    pipeline_audit: dict[str, Any] = field(default_factory=dict)
    canary_word: str = ""  # For post-LLM leakage verification
    model_downgrade: str = ""  # Suggested model downgrade
