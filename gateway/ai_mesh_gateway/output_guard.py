"""
Output Guard -- generator-level output guardrails for the gateway.

Orchestrates all output inspection checks:
1. PII/secret detection (delegated to InputScanner)
2. Hallucination risk scoring (pattern + grounding + contradiction)
3. IP/infrastructure leakage
4. Credential exposure
5. Semantic leakage detection (optional, via SemanticLeakageDetector)

Returns the highest-severity OutputVerdict across all checks.
Action precedence: block(4) > redact(3) > rewrite(2) > flag(1) > allow(0).
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

ACTION_PRIORITY = {"allow": 0, "flag": 1, "rewrite": 2, "redact": 3, "block": 4}

# Per-detector output actions an operator may configure. Anything outside this
# set falls back to the detector's default action.
_VALID_OUTPUT_ACTIONS = frozenset({"block", "redact", "rewrite", "flag", "allow"})

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
        self._grounding_guard = None  # type: ignore[assignment]

    def set_leakage_detector(self, detector) -> None:
        """Attach an optional SemanticLeakageDetector."""
        self._leakage_detector = detector

    def set_grounding_guard(self, guard) -> None:
        """Attach an optional :class:`rag_pipeline.grounding_guard.GroundingGuard`.

        When attached, the ``hallucination_grounding_mode`` org-config field
        (per migration 0022) selects the algorithm used to compute the
        ``grounding_score`` component of the hallucination risk:

        * ``"lexical"`` (default) — existing Jaccard token-overlap, no
          embeddings used. Preserves legacy behavior.
        * ``"semantic"`` — max Bedrock Titan v2 cosine similarity (fail-open
          to lexical if the embedder is unavailable / circuit OPEN).
        * ``"hybrid"`` — mean of lexical and semantic (fail-open to lexical
          on embedder failure).
        """
        self._grounding_guard = guard

    async def inspect(
        self,
        text: str,
        context_chunks: list[str] | None = None,
        *,
        org_config: dict | None = None,
        org_slug: str = "",
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

        Args:
            org_config: Per-tenant config (used to look up
                ``hallucination_grounding_mode`` and
                ``hallucination_grounding_threshold`` from migration 0022).
                Falls back to the gateway-level ``self._config`` if omitted.
            org_slug: Tenant identifier passed to the semantic grounding
                backend for embedder cache isolation and telemetry.
        """
        if not text:
            return OutputVerdict()

        verdicts: list[OutputVerdict] = []

        # Per-detector control reads from the per-tenant org_config first, then
        # falls back to the gateway-level config. Each detector has an independent
        # enable toggle and a configurable action (block/redact/rewrite/flag/allow).
        cfg = org_config or self._config

        def _enabled(key: str, default: bool = True) -> bool:
            return bool(cfg.get(key, default))

        def _action(key: str, default: str) -> str:
            value = str(cfg.get(key, default) or default).lower()
            return value if value in _VALID_OUTPUT_ACTIONS else default

        if _enabled("output_pii_enabled", True):
            pii_action = _action("output_pii_action", "redact")
            if pii_action != "allow":
                pii_verdict = await self._check_pii_secrets(text, pii_action)
                if pii_verdict.action != "allow":
                    verdicts.append(pii_verdict)

        if _enabled("output_credential_enabled", True):
            cred_action = _action(
                "output_credential_action",
                "block" if self._config.get("output_block_on_credential", True) else "redact",
            )
            if cred_action != "allow":
                cred_verdict = self._check_credential_exposure(text, cred_action)
                if cred_verdict.action != "allow":
                    verdicts.append(cred_verdict)

        if _enabled("output_ip_leakage_enabled", True):
            ip_action = _action(
                "output_ip_leakage_action",
                "block" if self._config.get("output_block_on_ip_leakage", False) else "flag",
            )
            if ip_action != "allow":
                ip_verdict = self._check_ip_leakage(text, ip_action)
                if ip_verdict.action != "allow":
                    verdicts.append(ip_verdict)

        if cfg.get("hallucination_flag_enabled", self._config.get("hallucination_flag_enabled", True)):
            hall_action = _action("output_hallucination_action", "flag")
            if hall_action != "allow":
                hall_verdict = await self._check_hallucination_markers(
                    text,
                    context_chunks,
                    action=hall_action,
                    org_config=org_config,
                    org_slug=org_slug,
                )
                if hall_verdict.action != "allow":
                    verdicts.append(hall_verdict)

        if self._leakage_detector is not None:
            leakage_verdict = self._check_semantic_leakage(text)
            if leakage_verdict.action != "allow":
                verdicts.append(leakage_verdict)

        # ── Tier-2: ZeroShield guard model (ML) on the OUTPUT ───────────────
        # Mirrors INPUT scanning: the static detectors above are tier-1; the
        # ZeroShield guard model then scans the model output (same guard model +
        # breaker/cache as input). Gated by output_tier2_enabled (default on)
        # AND the org tri-state tier2_enabled. FAIL-OPEN: a guard-model outage
        # must never block an already-generated response — scan_output_with_tier2
        # degrades to the static verdict and any error here is swallowed.
        if _enabled("output_tier2_enabled", True) and self._scanner is not None:
            try:
                t2 = await self._scanner.scan_output_with_tier2(
                    text,
                    org_tier2_override=cfg.get("tier2_enabled"),
                    org_slug=org_slug,
                )
                if t2 is not None and t2.action in ("block", "redact", "flag"):
                    t2_patterns = list(getattr(t2, "matched_patterns", []) or [])
                    verdicts.append(OutputVerdict(
                        action=t2.action,
                        threat_type=t2.threat_type or "guard_model",
                        confidence=float(getattr(t2, "confidence", 0.0) or 0.0),
                        detail=t2.detail or "ZeroShield guard model (tier-2) flagged output",
                        matched_patterns=t2_patterns,
                        compliance_tags=get_compliance_tags(t2_patterns),
                    ))
            except Exception:  # noqa: BLE001 - output tier-2 must never break delivery
                LOG.debug("Output tier-2 guard-model scan failed; failing open", exc_info=True)

        if not verdicts:
            return OutputVerdict()

        return self._select_highest_severity(verdicts)

    async def _check_pii_secrets(self, text: str, action: str = "redact") -> OutputVerdict:
        """Delegate PII/secret detection to the existing scanner."""
        verdict = await self._scanner.scan_output(text)
        if verdict.threat_type in ("pii", "secret") and verdict.matched_patterns:
            pattern_keys = verdict.matched_patterns
            return OutputVerdict(
                action=action,
                threat_type=verdict.threat_type,
                confidence=verdict.confidence,
                detail=f"PII/secret detected in output: {', '.join(pattern_keys)}",
                matched_patterns=pattern_keys,
                compliance_tags=get_compliance_tags(pattern_keys),
            )
        return OutputVerdict()

    def _check_credential_exposure(self, text: str, action: str = "block") -> OutputVerdict:
        """Check for exposed credentials (bearer tokens, connection strings, etc.)."""
        found = detect_credential_exposure(text)
        if not found:
            return OutputVerdict()

        pattern_keys = list(found.keys())
        return OutputVerdict(
            action=action,
            threat_type="credential",
            confidence=0.95,
            detail=f"Credential exposure detected: {', '.join(pattern_keys)}",
            matched_patterns=pattern_keys,
            compliance_tags=get_compliance_tags(pattern_keys),
        )

    def _check_ip_leakage(self, text: str, action: str = "flag") -> OutputVerdict:
        """Check for internal IP addresses, hostnames, and file paths."""
        found = detect_ip_leakage(text)
        if not found:
            return OutputVerdict()

        pattern_keys = list(found.keys())
        return OutputVerdict(
            action=action,
            threat_type="ip_leakage",
            confidence=0.8,
            detail=f"Infrastructure leakage detected: {', '.join(pattern_keys)}",
            matched_patterns=pattern_keys,
            compliance_tags=get_compliance_tags(pattern_keys),
        )

    async def _check_hallucination_markers(
        self,
        text: str,
        context_chunks: list[str] | None = None,
        *,
        action: str = "flag",
        org_config: dict | None = None,
        org_slug: str = "",
    ) -> OutputVerdict:
        """Enhanced hallucination check with numeric scoring."""
        score = await self.score_hallucination(
            text,
            context_chunks,
            org_config=org_config,
            org_slug=org_slug,
        )

        # Per-tenant threshold from migration 0022 overrides the default.
        cfg = org_config or self._config
        threshold = float(cfg.get("hallucination_grounding_threshold", 0.2))

        if score.risk_score < threshold:
            return OutputVerdict()

        return OutputVerdict(
            action=action,
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

    async def score_hallucination(
        self,
        output_text: str,
        context_chunks: list[str] | None = None,
        *,
        org_config: dict | None = None,
        org_slug: str = "",
    ) -> HallucinationScore:
        """
        Compute a numeric hallucination risk score.

        Components:
        1. Pattern score: count of hallucination marker matches, normalized
        2. Grounding score: if context_chunks provided, measure overlap.
           Algorithm is selected by the per-tenant
           ``hallucination_grounding_mode`` field (migration 0022):
           ``"lexical"`` (default), ``"semantic"``, or ``"hybrid"``. The
           semantic backend (Bedrock Titan v2) is used only when a
           :class:`GroundingGuard` has been attached via
           :meth:`set_grounding_guard`; otherwise we fall back to lexical.
        3. Contradiction score: detect self-contradictions within the output
        """
        if not output_text:
            return HallucinationScore()

        found = detect_hallucination_markers(output_text)
        pattern_count = len(found)
        pattern_score = min(pattern_count * 0.15, 1.0)

        cfg = org_config or self._config
        mode = str(cfg.get("hallucination_grounding_mode", "lexical") or "lexical").lower()

        grounding_score = 1.0
        if context_chunks:
            grounding_score = await self._compute_grounding_score_with_mode(
                output_text,
                context_chunks,
                mode=mode,
                org_slug=org_slug,
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
                f"grounding={grounding_score:.2f} [{mode}], "
                f"contradiction={contradiction_score:.2f})"
            ),
        )

    async def _compute_grounding_score_with_mode(
        self,
        output_text: str,
        context_chunks: list[str],
        *,
        mode: str,
        org_slug: str,
    ) -> float:
        """Algorithm dispatcher for ``hallucination_grounding_mode``.

        Returns a float in ``[0.0, 1.0]`` where higher == better-grounded
        (matches the legacy lexical-Jaccard contract used by
        ``score_hallucination``).

        Fail-open behavior: any semantic-path failure (no guard attached,
        circuit OPEN, embedder error) silently falls back to the lexical
        result so the gateway never blocks on grounding-pipeline outages.
        """
        lexical = self._compute_grounding_score(output_text, context_chunks)

        # Fast paths
        if mode == "lexical" or self._grounding_guard is None:
            return lexical
        if mode not in {"semantic", "hybrid"}:
            return lexical

        try:
            semantic = await self._grounding_guard.score(
                answer_text=output_text,
                context_chunks=list(context_chunks),
                org_slug=org_slug,
                assume_redacted=True,  # caller MUST have already redacted
            )
        except Exception:  # noqa: BLE001 — fail-OPEN to lexical
            LOG.exception(
                "OutputGuard: semantic grounding failed; falling back to lexical"
            )
            return lexical

        if semantic is None:
            return lexical

        if mode == "semantic":
            return float(semantic)
        # hybrid
        return float((lexical + semantic) / 2.0)

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
