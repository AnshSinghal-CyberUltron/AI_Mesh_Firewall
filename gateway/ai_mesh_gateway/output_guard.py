"""
Output Guard -- generator-level output guardrails for the gateway.

Orchestrates all output inspection checks:
1. PII/secret detection (delegated to InputScanner)
2. Hallucination risk scoring (pattern + grounding + contradiction)
3. IP/infrastructure leakage
4. Credential exposure
5. Semantic leakage detection (optional, via SemanticLeakageDetector)

Returns the highest-severity OutputVerdict across all checks.
Action precedence: block(3) > redact(2) > flag(1) > allow(0).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scanner import InputScanner

try:
    from .patterns import (
        detect_credential_exposure,
        detect_hallucination_markers,
        detect_ip_leakage,
        get_compliance_tags,
    )
except ImportError:
    from patterns import (
        detect_credential_exposure,
        detect_hallucination_markers,
        detect_ip_leakage,
        get_compliance_tags,
    )

LOG = logging.getLogger("gateway.output_guard")

ACTION_PRIORITY = {"allow": 0, "flag": 1, "redact": 2, "block": 3}

_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "it", "this", "that", "and", "or",
    "but", "not", "no", "if", "so", "as",
})

_CONTRADICTION_PATTERNS = [
    re.compile(r"\b(?:however|but|on the other hand|conversely|contrary to)\b", re.IGNORECASE),
    re.compile(r"\b(?:actually|in fact|to be precise|to correct)\b", re.IGNORECASE),
    re.compile(r"\b(?:not true|incorrect|wrong|mistaken|inaccurate)\b", re.IGNORECASE),
]


@dataclass
class OutputVerdict:
    """Result of output inspection."""

    action: str = "allow"
    threat_type: str = ""
    confidence: float = 0.0
    detail: str = ""
    matched_patterns: list[str] = field(default_factory=list)
    compliance_tags: list[str] = field(default_factory=list)


@dataclass
class HallucinationScore:
    """Numeric hallucination risk assessment."""

    risk_score: float = 0.0
    pattern_score: float = 0.0
    grounding_score: float = 0.0
    contradiction_score: float = 0.0
    matched_markers: list[str] = field(default_factory=list)
    detail: str = ""


class OutputGuard:
    """
    Orchestrates all output inspection checks and returns the
    highest-severity verdict.

    Configuration keys (from gateway config dict):
    - output_guard_enabled: master toggle
    - output_block_on_credential: block on credential exposure (default True)
    - output_block_on_ip_leakage: block on IP leakage (default False, flag only)
    - hallucination_flag_enabled: flag hallucination markers (default True)
    """

    def __init__(self, scanner: "InputScanner", config: dict | None = None) -> None:
        self._scanner = scanner
        self._config = config or {}
        self._leakage_detector = None

    def set_leakage_detector(self, detector) -> None:
        """Attach an optional SemanticLeakageDetector."""
        self._leakage_detector = detector

    async def inspect(
        self,
        text: str,
        context_chunks: list[str] | None = None,
    ) -> OutputVerdict:
        """
        Run all output checks. Returns the combined highest-severity verdict.

        Checks are run in order:
        1. PII/secret (via scanner) -> redact
        2. Credential exposure -> block or redact
        3. IP leakage -> block or flag
        4. Hallucination scoring -> flag
        5. Semantic leakage -> flag (if detector attached)

        The worst (highest-priority) action wins.
        """
        if not text:
            return OutputVerdict()

        verdicts: list[OutputVerdict] = []

        pii_verdict = await self._check_pii_secrets(text)
        if pii_verdict.action != "allow":
            verdicts.append(pii_verdict)

        cred_verdict = self._check_credential_exposure(text)
        if cred_verdict.action != "allow":
            verdicts.append(cred_verdict)

        ip_verdict = self._check_ip_leakage(text)
        if ip_verdict.action != "allow":
            verdicts.append(ip_verdict)

        if self._config.get("hallucination_flag_enabled", True):
            hall_verdict = self._check_hallucination_markers(text, context_chunks)
            if hall_verdict.action != "allow":
                verdicts.append(hall_verdict)

        if self._leakage_detector is not None:
            leakage_verdict = self._check_semantic_leakage(text)
            if leakage_verdict.action != "allow":
                verdicts.append(leakage_verdict)

        if not verdicts:
            return OutputVerdict()

        return self._select_highest_severity(verdicts)

    async def _check_pii_secrets(self, text: str) -> OutputVerdict:
        """Delegate PII/secret detection to the existing scanner."""
        verdict = await self._scanner.scan_output(text)
        if verdict.threat_type in ("pii", "secret") and verdict.matched_patterns:
            pattern_keys = verdict.matched_patterns
            return OutputVerdict(
                action="redact",
                threat_type=verdict.threat_type,
                confidence=verdict.confidence,
                detail=f"PII/secret detected in output: {', '.join(pattern_keys)}",
                matched_patterns=pattern_keys,
                compliance_tags=get_compliance_tags(pattern_keys),
            )
        return OutputVerdict()

    def _check_credential_exposure(self, text: str) -> OutputVerdict:
        """Check for exposed credentials (bearer tokens, connection strings, etc.)."""
        found = detect_credential_exposure(text)
        if not found:
            return OutputVerdict()

        pattern_keys = list(found.keys())
        action = "block" if self._config.get("output_block_on_credential", True) else "redact"
        return OutputVerdict(
            action=action,
            threat_type="credential",
            confidence=0.95,
            detail=f"Credential exposure detected: {', '.join(pattern_keys)}",
            matched_patterns=pattern_keys,
            compliance_tags=get_compliance_tags(pattern_keys),
        )

    def _check_ip_leakage(self, text: str) -> OutputVerdict:
        """Check for internal IP addresses, hostnames, and file paths."""
        found = detect_ip_leakage(text)
        if not found:
            return OutputVerdict()

        pattern_keys = list(found.keys())
        action = "block" if self._config.get("output_block_on_ip_leakage", False) else "flag"
        return OutputVerdict(
            action=action,
            threat_type="ip_leakage",
            confidence=0.8,
            detail=f"Infrastructure leakage detected: {', '.join(pattern_keys)}",
            matched_patterns=pattern_keys,
            compliance_tags=get_compliance_tags(pattern_keys),
        )

    def _check_hallucination_markers(
        self,
        text: str,
        context_chunks: list[str] | None = None,
    ) -> OutputVerdict:
        """Enhanced hallucination check with numeric scoring."""
        score = self.score_hallucination(text, context_chunks)

        if score.risk_score < 0.2:
            return OutputVerdict()

        return OutputVerdict(
            action="flag",
            threat_type="hallucination",
            confidence=score.risk_score,
            detail=score.detail,
            matched_patterns=score.matched_markers,
            compliance_tags=[],
        )

    def _check_semantic_leakage(self, text: str) -> OutputVerdict:
        """Check output for semantic similarity to confidential content."""
        verdict = self._leakage_detector.check_leakage(text)
        if verdict.action != "allow":
            return OutputVerdict(
                action="flag",
                threat_type="semantic_leakage",
                confidence=verdict.leakage_score,
                detail=verdict.detail,
                matched_patterns=verdict.matched_fingerprints,
                compliance_tags=["DLP"],
            )
        return OutputVerdict()

    def score_hallucination(
        self,
        output_text: str,
        context_chunks: list[str] | None = None,
    ) -> HallucinationScore:
        """
        Compute a numeric hallucination risk score.

        Components:
        1. Pattern score: count of hallucination marker matches, normalized
        2. Grounding score: if context_chunks provided, measure lexical overlap
        3. Contradiction score: detect self-contradictions within the output
        """
        if not output_text:
            return HallucinationScore()

        found = detect_hallucination_markers(output_text)
        pattern_count = len(found)
        pattern_score = min(pattern_count * 0.15, 1.0)

        grounding_score = 1.0
        if context_chunks:
            grounding_score = self._compute_grounding_score(
                output_text, context_chunks
            )

        contradiction_score = self._detect_contradictions(output_text)

        if context_chunks:
            risk = (
                pattern_score * 0.25
                + (1.0 - grounding_score) * 0.50
                + contradiction_score * 0.25
            )
        else:
            risk = pattern_score * 0.60 + contradiction_score * 0.40

        return HallucinationScore(
            risk_score=round(min(risk, 1.0), 3),
            pattern_score=round(pattern_score, 3),
            grounding_score=round(grounding_score, 3),
            contradiction_score=round(contradiction_score, 3),
            matched_markers=list(found.keys()),
            detail=(
                f"hallucination_risk={risk:.3f} "
                f"(pattern={pattern_score:.2f}, "
                f"grounding={grounding_score:.2f}, "
                f"contradiction={contradiction_score:.2f})"
            ),
        )

    @staticmethod
    def _compute_grounding_score(output: str, context_chunks: list[str]) -> float:
        """Compute lexical grounding score using token-level overlap."""
        output_tokens = set(output.lower().split())
        if not output_tokens:
            return 1.0

        context_tokens: set[str] = set()
        for chunk in context_chunks:
            context_tokens.update(chunk.lower().split())

        if not context_tokens:
            return 0.5

        output_meaningful = output_tokens - _STOPWORDS
        if not output_meaningful:
            return 1.0

        overlap = output_meaningful & context_tokens
        return len(overlap) / len(output_meaningful)

    @staticmethod
    def _detect_contradictions(text: str) -> float:
        """Detect self-contradictions within text. Returns 0.0-1.0."""
        sentences = re.split(r"[.!?]+", text)
        sentences = [s.strip().lower() for s in sentences if len(s.strip()) > 10]

        if len(sentences) < 2:
            return 0.0

        contradiction_signals = 0
        for sentence in sentences:
            for pattern in _CONTRADICTION_PATTERNS:
                if pattern.search(sentence):
                    contradiction_signals += 1
                    break

        return min(contradiction_signals * 0.2, 1.0)

    @staticmethod
    def _select_highest_severity(verdicts: list[OutputVerdict]) -> OutputVerdict:
        """Select the verdict with the highest-priority action."""
        return max(verdicts, key=lambda v: ACTION_PRIORITY.get(v.action, 0))
