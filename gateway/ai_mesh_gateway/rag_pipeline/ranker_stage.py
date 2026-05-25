"""Ranker stage: trust scoring, anomaly detection, content scanning, and policy-based filtering."""
from __future__ import annotations

import logging
import time
from typing import Any, TYPE_CHECKING

from .contracts import DocumentManifest, RankerStageInput, RankerStageOutput, StageVerdict
from .escalation import get_escalation_config

if TYPE_CHECKING:
    from context_guard import ContextGuard

LOG = logging.getLogger("gateway.rag_pipeline.ranker_stage")


def compute_trust_score(document: dict, policy: dict) -> float:
    """Compute a trust score [0.0-1.0] for a retrieved document.

    Canonical implementation (moved from rag_orchestrator.py).
    Score degrades as distance approaches anomaly threshold.
    Bonuses for verified_source (+0.1) and system-created (+0.05).
    """
    score = 1.0
    distance = document.get("distance", 0.0)
    threshold = policy.get("anomaly_distance_threshold") or 0.85
    if threshold > 0 and distance > threshold * 0.7:
        score -= (distance - threshold * 0.7) / (threshold * 0.3) * 0.4
    metadata = document.get("metadata", {})
    if metadata.get("verified_source"):
        score += 0.1
    if metadata.get("created_by") == "system":
        score += 0.05
    return max(0.0, min(1.0, round(score, 3)))


class RankerStage:
    """Wraps ContextGuard for trust scoring, anomaly detection, and content scanning.

    Responsibilities:
    - Embedding anomaly detection (escalation-aware threshold)
    - Trust score computation and re-ranking
    - Context scanning: injection, toxicity, PII, secrets
    - Sensitive document filtering
    - Escalation-aware enforcement tightening
    """

    def __init__(self, context_guard: "ContextGuard | None", config: dict, compiled_policies: list[dict] | None = None) -> None:
        self._guard = context_guard
        self._config = config
        self._compiled_policies = compiled_policies or []

    def update_policies(self, compiled_policies: list[dict]) -> None:
        """Hot-update compiled policies without recreating the stage."""
        self._compiled_policies = compiled_policies

    async def execute(self, inp: RankerStageInput) -> RankerStageOutput:
        start = time.perf_counter()
        documents = list(inp.documents)
        initial_count = len(documents)
        escalation = get_escalation_config(inp.escalation_level)
        anomalous_indices: list[int] = []
        flagged_indices: list[int] = []
        trust_scores: dict[int, float] = {}

        if not documents:
            return RankerStageOutput(
                verdict=StageVerdict(action="allow"),
            )

        if self._guard is None:
            return RankerStageOutput(
                verdict=StageVerdict(action="allow"),
                ranked_documents=documents,
            )

        # ── 1. Embedding anomaly detection (escalation-adjusted threshold) ──
        anomalies_enabled = self._config.get("rag_anomaly_detection_enabled", True)
        threshold_override = inp.policy.get("anomaly_distance_threshold")
        distances = [doc.get("distance", 0.0) for doc in documents]
        anomaly_removed_all = False

        if anomalies_enabled and distances:
            if threshold_override is None:
                # No explicit policy threshold: keep filtering conservative and rely on
                # statistical outliers only (2-sigma path in detect_embedding_anomaly).
                adjusted_threshold = float("inf")
            else:
                adjusted_threshold = float(threshold_override) * escalation.anomaly_threshold_multiplier

            anomalous_indices = self._guard.detect_embedding_anomaly(distances, adjusted_threshold)

            if anomalous_indices:
                anomalous_set = set(anomalous_indices)
                filtered_documents = [doc for i, doc in enumerate(documents) if i not in anomalous_set]
                if filtered_documents:
                    documents = filtered_documents
                else:
                    # Do not hard-block purely on anomaly heuristics when every document is
                    # flagged. Preserve retrieval continuity and surface a downstream flag.
                    anomaly_removed_all = True

        # ── 2. Trust scoring and re-ranking ──
        for i, doc in enumerate(documents):
            ts = compute_trust_score(doc, inp.policy)
            doc["_trust_score"] = ts
            trust_scores[i] = ts
        documents.sort(key=lambda d: d.get("_trust_score", 0.0), reverse=True)

        # Drop docs below escalation-adjusted trust minimum
        if escalation.trust_score_minimum > 0:
            documents = [
                d for d in documents
                if d.get("_trust_score", 0) >= escalation.trust_score_minimum
            ]

        # ── 3. Content scanning (injection, toxicity, PII) ──
        require_scan = inp.policy.get("require_context_scan", True) or escalation.force_context_scan
        if documents and require_scan and self._config.get("rag_context_scan_enabled", True):
            scan_result = await self._guard.scan_documents(documents, inp.query_text)

            scan_action = getattr(scan_result, "action", "allow")
            scan_confidence = getattr(scan_result, "confidence", 0.0)
            try:
                scan_confidence = float(scan_confidence)
            except (TypeError, ValueError):
                scan_confidence = 0.0
            scan_threat = getattr(scan_result, "threat_type", "")
            scan_detail = getattr(scan_result, "detail", "")
            scan_patterns = getattr(scan_result, "matched_patterns", [])
            scan_flagged = getattr(scan_result, "flagged_documents", [])

            if scan_action == "block":
                flagged_list = list(scan_flagged) if scan_flagged else []
                # If specific documents are flagged, remove them rather than blocking
                if flagged_list:
                    flagged_set = set(flagged_list)
                    flagged_indices = flagged_list
                    documents = [doc for i, doc in enumerate(documents) if i not in flagged_set]
                else:
                    # No specific docs flagged = entire batch rejected
                    return RankerStageOutput(
                        verdict=StageVerdict(
                            action="block",
                            threat_type=str(scan_threat) if scan_threat else "context_scan",
                            confidence=scan_confidence,
                            detail=str(scan_detail),
                            matched_patterns=list(scan_patterns) if scan_patterns else [],
                        ),
                        anomalous_indices=anomalous_indices,
                        flagged_indices=flagged_list,
                        trust_scores=trust_scores,
                        documents_removed=initial_count,
                    )
            elif scan_flagged:
                flagged_set = set(scan_flagged)
                flagged_indices = list(scan_flagged)
                documents = [doc for i, doc in enumerate(documents) if i not in flagged_set]

        # ── 4. Sensitive document filtering ──
        require_sensitive = inp.policy.get("block_sensitive_documents", True) or escalation.force_sensitive_scan
        if documents and require_sensitive and hasattr(self._guard, "scan_single_document"):
            clean: list[dict[str, Any]] = []
            for doc in documents:
                content = doc.get("content", "")
                if content:
                    try:
                        sv = await self._guard.scan_single_document(content)
                    except TypeError:
                        # scan_single_document may not be a coroutine in some contexts
                        sv = self._guard.scan_single_document(content)
                    sv_threat = getattr(sv, "threat_type", "")
                    sv_action = getattr(sv, "action", "allow")
                    if sv_threat in ("pii", "secret") and sv_action in ("flag", "block"):
                        continue
                clean.append(doc)
            documents = clean

        # ── 5. Stage-specific policy-based filtering ──
        policy_rules_consulted: list[str] = []
        rejected_doc_ids: list[str] = []
        if documents and self._compiled_policies:
            try:
                from policy_engine import evaluate_for_stage
            except ImportError:
                from gateway.policy_engine import evaluate_for_stage

            surviving: list[dict[str, Any]] = []
            for doc in documents:
                content = doc.get("content", "")
                result = evaluate_for_stage(
                    prompt=content,
                    response_text="",
                    compiled_policies=self._compiled_policies,
                    stage="ranker",
                )
                policy_rules_consulted.extend(result.matched_rule_names)
                if result.action == "block":
                    rejected_doc_ids.append(doc.get("_doc_id", ""))
                    LOG.debug("Ranker policy blocked doc %s: %s", doc.get("_doc_id", ""), result.message)
                else:
                    surviving.append(doc)
            documents = surviving

        # ── 6. Build approved manifest for chain-of-custody ──
        approved_manifest: list[DocumentManifest] = []
        approved_doc_ids: list[str] = []
        for doc in documents:
            doc_id = doc.get("_doc_id", "")
            content_hash = doc.get("_content_hash", "")
            if doc_id:
                approved_doc_ids.append(doc_id)
                approved_manifest.append(DocumentManifest(
                    doc_id=doc_id,
                    content_hash=content_hash,
                    source_stage="retriever",
                    approved_by=["retriever", "ranker"],
                ))

        removed = initial_count - len(documents)
        if anomaly_removed_all:
            removed = 0

        verdict_action = "allow"
        if escalation.block_on_any_flag and (anomalous_indices or flagged_indices):
            verdict_action = "block" if not documents else "flag"
        elif anomaly_removed_all:
            verdict_action = "flag"

        return RankerStageOutput(
            verdict=StageVerdict(
                action=verdict_action,
                threat_type="anomaly" if anomalous_indices else "",
                confidence=0.4 if anomaly_removed_all else 0.0,
                detail="All retrieved documents matched anomaly heuristics; returning flagged results without drop" if anomaly_removed_all else "",
            ),
            ranked_documents=documents,
            anomalous_indices=anomalous_indices,
            flagged_indices=flagged_indices,
            trust_scores=trust_scores,
            documents_removed=removed,
            approved_manifest=approved_manifest,
        )
