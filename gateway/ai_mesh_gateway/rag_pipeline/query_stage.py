"""Query stage: validate and scan the incoming query before retrieval."""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, TYPE_CHECKING

from .contracts import QueryStageInput, QueryStageOutput, StageVerdict

if TYPE_CHECKING:
    from scanner import InputScanner
    from llm_judge import LLMJudge
    from embedding_vault import EmbeddingVault
    from intent_classifier import IntentClassifier

LOG = logging.getLogger("gateway.rag_pipeline.query_stage")


class QueryStage:
    """Wraps InputScanner, LLM Judge, Embedding Vault, and Intent Classifier.

    Responsibilities:
    - Query length validation
    - Tier1/1.5/2 scanning for injection (regex + optional Bedrock)
    - LLM-as-Judge: asks a dedicated model to classify injection attempts
    - Embedding Vault: cosine similarity against known attack patterns
    - Intent Classification: determines if query intent is suspicious
    - Query rewrite: strip injection patterns while preserving legitimate query
    - Below-threshold flags for downstream escalation
    - Model downgrade recommendation for medium-risk queries
    """

    def __init__(
        self,
        scanner: "InputScanner | None",
        config: dict,
        llm_judge: "LLMJudge | None" = None,
        embedding_vault: "EmbeddingVault | None" = None,
        intent_classifier: "IntentClassifier | None" = None,
    ) -> None:
        self._scanner = scanner
        self._config = config
        self._llm_judge = llm_judge
        self._embedding_vault = embedding_vault
        self._intent_classifier = intent_classifier

    async def execute(self, inp: QueryStageInput) -> QueryStageOutput:
        start = time.perf_counter()
        injection_flags: list[str] = []
        llm_judge_result: dict[str, Any] = {}
        vault_result: dict[str, Any] = {}
        intent_result: dict[str, Any] = {}

        # ── Basic validation ──
        if not inp.query_text.strip():
            return QueryStageOutput(
                verdict=StageVerdict(
                    action="block",
                    threat_type="empty_query",
                    confidence=1.0,
                    detail="Query text is empty",
                ),
                sanitized_query=inp.query_text,
                original_query=inp.query_text,
                scan_tier="validation",
                latency_ms=(time.perf_counter() - start) * 1000,
            )

        max_len = inp.policy.get(
            "max_query_length",
            self._config.get("rag_max_query_length", 2000),
        )
        if len(inp.query_text) > max_len:
            return QueryStageOutput(
                verdict=StageVerdict(
                    action="block",
                    threat_type="query_too_long",
                    confidence=1.0,
                    detail=f"Query length {len(inp.query_text)} exceeds max {max_len}",
                ),
                sanitized_query=inp.query_text,
                original_query=inp.query_text,
                scan_tier="validation",
                latency_ms=(time.perf_counter() - start) * 1000,
            )

        # ── Intent Classification (fast, pattern-based) ──
        if self._intent_classifier is not None:
            try:
                intent_v = self._intent_classifier.classify(inp.query_text)
                intent_result = {
                    "intent": intent_v.intent,
                    "confidence": intent_v.confidence,
                    "risk_factors": intent_v.risk_factors,
                    "is_suspicious": intent_v.is_suspicious,
                }
                if intent_v.is_suspicious:
                    injection_flags.append(f"intent:{intent_v.intent}")
            except Exception as e:
                LOG.debug("Intent classification failed: %s", e)

        # ── Embedding Vault check (cosine similarity against known attacks) ──
        if self._embedding_vault is not None:
            try:
                vault_v = await self._embedding_vault.check(inp.query_text)
                vault_result = {
                    "is_match": vault_v.is_match,
                    "confidence": vault_v.confidence,
                    "closest_distance": vault_v.closest_distance,
                    "vault_size": vault_v.vault_size,
                    "matches": [
                        {"attack_id": m.attack_id, "attack_type": m.attack_type, "distance": m.distance}
                        for m in vault_v.matches
                    ],
                    "latency_ms": vault_v.latency_ms,
                }
                if vault_v.is_match and vault_v.confidence >= 0.85:
                    # High-confidence vault match → block
                    return QueryStageOutput(
                        verdict=StageVerdict(
                            action="block",
                            threat_type="embedding_vault_match",
                            confidence=vault_v.confidence,
                            detail=f"Query matches {len(vault_v.matches)} known attack pattern(s) "
                                   f"(distance={vault_v.closest_distance:.4f})",
                        ),
                        sanitized_query=inp.query_text,
                        original_query=inp.query_text,
                        injection_flags=["embedding_vault_match"],
                        scan_tier="embedding_vault",
                        latency_ms=(time.perf_counter() - start) * 1000,
                        embedding_vault_verdict=vault_result,
                        intent_verdict=intent_result,
                    )
                elif vault_v.is_match:
                    injection_flags.append("embedding_vault_match")
            except Exception as e:
                LOG.debug("Embedding vault check failed: %s", e)

        # ── Scanner: Tier1 regex + Tier1.5 fuzzy + optional Tier2 Bedrock ──
        scan_tier = "none"
        if self._scanner is not None:
            verdict = await self._scanner.scan_prompt(inp.query_text, is_rag=True)
            scan_tier = getattr(verdict, "tier", "tier_1") or "tier_1"
            threshold = self._config.get("prompt_injection_threshold", 0.80)
            rewrite_threshold = self._config.get("prompt_rewrite_threshold", 0.50)
            downgrade_threshold = self._config.get("prompt_downgrade_threshold", 0.40)

            if verdict.action == "block" and verdict.confidence >= threshold:
                # Hard block: confidence exceeds block threshold
                # Self-harden: add to embedding vault
                if self._embedding_vault is not None:
                    try:
                        await self._embedding_vault.add_attack(
                            inp.query_text,
                            attack_type=verdict.threat_type or "detected",
                        )
                    except Exception:
                        pass

                return QueryStageOutput(
                    verdict=StageVerdict(
                        action="block",
                        threat_type=verdict.threat_type,
                        confidence=verdict.confidence,
                        detail=verdict.detail,
                        matched_patterns=verdict.matched_patterns,
                    ),
                    sanitized_query=inp.query_text,
                    original_query=inp.query_text,
                    injection_flags=[verdict.threat_type],
                    scan_tier=scan_tier,
                    latency_ms=(time.perf_counter() - start) * 1000,
                    embedding_vault_verdict=vault_result,
                    intent_verdict=intent_result,
                )

            # Rewrite path: confidence between rewrite and block thresholds
            if (
                verdict.action in ("block", "flag")
                and rewrite_threshold <= verdict.confidence < threshold
                and verdict.matched_patterns
            ):
                rewritten = self._attempt_rewrite(inp.query_text, verdict.matched_patterns)
                if rewritten and rewritten != inp.query_text:
                    LOG.info(
                        "Query rewritten: removed %d injection patterns (confidence=%.2f)",
                        len(verdict.matched_patterns),
                        verdict.confidence,
                    )
                    return QueryStageOutput(
                        verdict=StageVerdict(
                            action="rewrite",
                            threat_type=verdict.threat_type,
                            confidence=verdict.confidence,
                            detail=f"Query rewritten: removed {len(verdict.matched_patterns)} injection patterns",
                            matched_patterns=verdict.matched_patterns,
                            rewritten_text=rewritten,
                        ),
                        sanitized_query=rewritten,
                        rewritten_query=rewritten,
                        original_query=inp.query_text,
                        injection_flags=[verdict.threat_type],
                        scan_tier=scan_tier,
                        latency_ms=(time.perf_counter() - start) * 1000,
                        embedding_vault_verdict=vault_result,
                        intent_verdict=intent_result,
                    )

            # Model downgrade path: confidence between downgrade and rewrite thresholds
            if (
                verdict.action in ("block", "flag")
                and downgrade_threshold <= verdict.confidence < rewrite_threshold
            ):
                downgrade_model = self._config.get("rag_downgrade_model", os.getenv("BEDROCK_MODEL", "openai.gpt-oss-120b-1:0"))
                LOG.info(
                    "Model downgrade recommended: confidence=%.2f → model=%s",
                    verdict.confidence, downgrade_model,
                )
                return QueryStageOutput(
                    verdict=StageVerdict(
                        action="model_downgrade",
                        threat_type=verdict.threat_type,
                        confidence=verdict.confidence,
                        detail=f"Medium-risk query: downgrade to {downgrade_model}",
                        downgrade_model=downgrade_model,
                    ),
                    sanitized_query=inp.query_text,
                    original_query=inp.query_text,
                    injection_flags=[verdict.threat_type],
                    scan_tier=scan_tier,
                    latency_ms=(time.perf_counter() - start) * 1000,
                    embedding_vault_verdict=vault_result,
                    intent_verdict=intent_result,
                )

            # Sub-threshold detections → flag for downstream escalation
            if verdict.action in ("block", "flag") and verdict.confidence > 0:
                injection_flags.append(verdict.threat_type)

        # ── LLM-as-Judge (async, higher latency, higher accuracy) ──
        if self._llm_judge is not None and self._llm_judge.enabled:
            try:
                judge_v = await self._llm_judge.judge(inp.query_text)
                llm_judge_result = {
                    "is_injection": judge_v.is_injection,
                    "confidence": judge_v.confidence,
                    "attack_type": judge_v.attack_type,
                    "reasoning": judge_v.reasoning,
                    "judge_model": judge_v.judge_model,
                    "latency_ms": judge_v.latency_ms,
                }
                if judge_v.is_injection and judge_v.confidence >= 0.85:
                    # High-confidence LLM judge detection → block
                    # Self-harden: add to vault
                    if self._embedding_vault is not None:
                        try:
                            await self._embedding_vault.add_attack(
                                inp.query_text,
                                attack_type=judge_v.attack_type or "llm_detected",
                            )
                        except Exception:
                            pass

                    return QueryStageOutput(
                        verdict=StageVerdict(
                            action="block",
                            threat_type=f"llm_judge:{judge_v.attack_type}",
                            confidence=judge_v.confidence,
                            detail=f"LLM Judge detected injection: {judge_v.reasoning[:200]}",
                        ),
                        sanitized_query=inp.query_text,
                        original_query=inp.query_text,
                        injection_flags=["llm_judge_detection"],
                        scan_tier="llm_judge",
                        latency_ms=(time.perf_counter() - start) * 1000,
                        llm_judge_verdict=llm_judge_result,
                        embedding_vault_verdict=vault_result,
                        intent_verdict=intent_result,
                    )
                elif judge_v.is_injection:
                    injection_flags.append(f"llm_judge:{judge_v.attack_type}")
            except Exception as e:
                LOG.debug("LLM Judge failed: %s", e)

        return QueryStageOutput(
            verdict=StageVerdict(
                action="flag" if injection_flags else "allow",
                threat_type=injection_flags[0] if injection_flags else "",
                confidence=0.0,
            ),
            sanitized_query=inp.query_text,
            original_query=inp.query_text,
            injection_flags=injection_flags,
            scan_tier=scan_tier,
            latency_ms=(time.perf_counter() - start) * 1000,
            llm_judge_verdict=llm_judge_result,
            embedding_vault_verdict=vault_result,
            intent_verdict=intent_result,
        )

    @staticmethod
    def _attempt_rewrite(query: str, patterns: list[str]) -> str:
        """Strip injection patterns while preserving legitimate query content.

        Returns the cleaned query if meaningful content remains (>20% of
        original length), otherwise returns empty string so the caller
        can fall through to another action.
        """
        rewritten = query
        for pattern in patterns:
            try:
                rewritten = re.sub(pattern, "", rewritten, flags=re.IGNORECASE).strip()
            except re.error:
                # Pattern may not be a valid regex — try literal removal
                rewritten = rewritten.replace(pattern, "").strip()
        # Collapse multiple spaces
        rewritten = re.sub(r"\s{2,}", " ", rewritten).strip()
        # Only return if meaningful content remains
        if len(rewritten) > len(query) * 0.2 and rewritten.strip():
            return rewritten.strip()
        return ""
