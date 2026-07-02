"""
Context Guard: document-level threat scanner for the Gateway Data Plane.

Scans retrieved RAG documents for indirect prompt injection, hidden
instructions (steganographic attacks), toxicity, and PII/secrets
before they are returned to the client or fed to an LLM.

Also provides embedding anomaly detection using distance threshold
and statistical outlier analysis.

Uses ThreadPoolExecutor (same pattern as gateway/scanner.py) to avoid
blocking the async event loop.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

try:
    from .patterns import compile_pattern, detect_pii, detect_secrets, detect_credential_exposure
except ImportError:
    from patterns import compile_pattern, detect_pii, detect_secrets, detect_credential_exposure

LOG = logging.getLogger("gateway.context_guard")

DEFAULT_THREAD_POOL_SIZE = 4

# M-19 (truncation-order invariant): the ONLY truncation in this module is
# the evidence snippet recorded in verdicts (``match.group(0)[:SNIPPET_MAX_CHARS]``).
# It is applied strictly AFTER the guard decision: every regex / PII / secret
# scan runs over the FULL document text, the verdict (block/flag/allow) is
# decided from that full-text result, and only then is the *reported* match
# trimmed so telemetry payloads stay bounded and never replay whole documents.
# Do NOT "optimize" by slicing the document before scanning — a threat planted
# beyond the slice boundary would silently bypass the guard
# (see tests/test_context_guard.py::TestNoEarlyTruncation).
SNIPPET_MAX_CHARS = 100


@dataclass
class ContextScanVerdict:
    """Structured result of document-level scanning."""

    action: str = "allow"
    threat_type: str = ""
    confidence: float = 0.0
    detail: str = ""
    flagged_documents: list[int] = field(default_factory=list)
    matched_patterns: list[str] = field(default_factory=list)


INDIRECT_INJECTION_PATTERNS: list[str] = [
    # RAG-1b + RAG-INJECTION-SYNONYM: one verb alternation (ignore/disregard/forget/
    # override) against the full object+qualifier set. RAG-1b broadened only "ignore"
    # and missed the synonyms ("disregard all previous instructions" sailed through
    # ingest when tier-2 was off); folding the verbs into a single pattern closes the
    # synonym bypass. Still requires at least one qualifier, so benign "ignore
    # instructions" is not over-matched.
    r"(?:ignore|disregard|forget|override)\s+(?:all|every|any|the|previous|prior|above|preceding|earlier)(?:\s+(?:previous|prior|above|preceding|earlier))?\s+(?:instructions|context|rules|prompts|messages|directives)",
    r"disregard\s+(the|all)\s+(above|preceding|context)",
    r"you\s+must\s+(now|instead|actually)",
    r"new\s+(instructions|directive|role)\s*:",
    r"system\s+(override|prompt)\s*:",
    r"<\s*/?\s*(?:system|instruction|context|admin)\s*>",
    r"\[\s*(?:SYSTEM|ADMIN|OVERRIDE)\s*\]",
    r"BEGIN\s+(?:HIDDEN|SECRET|REAL)\s+INSTRUCTIONS",
    r"(?:IMPORTANT|CRITICAL|URGENT)\s*:\s*ignore",
    r"act\s+as\s+(?:if|though)\s+you",
    r"forget\s+(?:everything|all|previous)",
    # C4-INJ-INGEST-SYNONYMS: ChatML/Llama control tokens + persona-reassignment +
    # "pay no attention" phrasings that the verb-set above and the chat tier-2 catch
    # but the ingest regex tier missed.
    r"<\s*\|?\s*im_(?:start|end)\s*\|?\s*>",
    r"<\s*\|?\s*(?:system|user|assistant)\s*\|?\s*>",
    r"pay\s+no\s+attention\s+to\s+(?:the\s+)?(?:previous|prior|above|preceding|earlier)",
    r"from\s+now\s+on,?\s+you\s+(?:are|will\s+be|act\s+as)",
    r"new\s+persona\s*:",
    # (removed an over-broad "you are now a/an/in" rule — it false-positived on benign
    #  prose like "you are now a premium member". The specific persona-reassignment
    #  phrasings above + ChatML tokens cover the real injection signal.)
]

HIDDEN_INSTRUCTION_PATTERNS: list[str] = [
    r"[\u200b\u200c\u200d\ufeff]{3,}",
    r"[\x00\x01\x02\x03\x04\x05\x06\x07\x08]{2,}",
    r"<!--\s*(?:instruction|ignore|override|system|admin|inject).*?-->",
    r"\{%\s*(?:exec|eval|import|system)\s*.*?%\}",
    r"<script[^>]*>.*?</script>",
    r"<iframe[^>]*>.*?</iframe>",
]

DOCUMENT_TOXICITY_PATTERNS: list[str] = [
    r"(?:execute|run|eval)\s+(?:this|the\s+following)\s+(?:code|script|command)",
    r"(?:download|fetch|curl|wget)\s+(?:from\s+)?https?://",
    r"(?:rm\s+-rf|format\s+c:|del\s+/[sfq])",
    r"(?:sudo|chmod\s+777|chown\s+root)",
    r"(?:DROP\s+TABLE|DELETE\s+FROM|TRUNCATE\s+TABLE)",
]


class ContextGuard:
    """Document-level threat scanner for RAG retrieval results."""

    def __init__(self, thread_pool_size: int = DEFAULT_THREAD_POOL_SIZE) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=thread_pool_size,
            thread_name_prefix="context_guard",
        )
        LOG.info(
            "ContextGuard initialized (thread_pool_size=%d, injection_patterns=%d, "
            "hidden_patterns=%d, toxicity_patterns=%d)",
            thread_pool_size,
            len(INDIRECT_INJECTION_PATTERNS),
            len(HIDDEN_INSTRUCTION_PATTERNS),
            len(DOCUMENT_TOXICITY_PATTERNS),
        )

    async def scan_documents(
        self,
        documents: list[dict[str, Any]],
        query_text: str,
    ) -> ContextScanVerdict:
        """Asynchronously scan a list of retrieved documents for threats."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor,
            self._scan_documents_sync,
            documents,
            query_text,
        )

    async def scan_single_document(self, document_text: str) -> ContextScanVerdict:
        """Asynchronously scan a single document text for threats."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor,
            self._scan_single_document_sync,
            document_text,
        )

    def detect_embedding_anomaly(
        self,
        distances: list[float],
        threshold: float,
    ) -> list[int]:
        """
        Detect anomalous results using distance threshold and statistical
        outlier analysis (2-sigma deviation from mean).

        Returns indices of documents flagged as anomalous.
        """
        if not distances:
            return []

        flagged: list[int] = []

        for i, dist in enumerate(distances):
            if dist > threshold:
                flagged.append(i)

        if len(distances) >= 3:
            try:
                import numpy as np

                arr = np.array(distances, dtype=float)
                mean_dist = float(np.mean(arr))
                std_dist = float(np.std(arr))

                if std_dist > 0:
                    for i, dist in enumerate(distances):
                        if i not in flagged and dist > mean_dist + 2 * std_dist:
                            flagged.append(i)
            except ImportError:
                mean_dist = sum(distances) / len(distances)
                variance = sum((d - mean_dist) ** 2 for d in distances) / len(distances)
                std_dist = variance ** 0.5
                if std_dist > 0:
                    for i, dist in enumerate(distances):
                        if i not in flagged and dist > mean_dist + 2 * std_dist:
                            flagged.append(i)

        if flagged:
            LOG.info(
                "Embedding anomaly detected: %d/%d documents flagged (threshold=%.3f)",
                len(flagged),
                len(distances),
                threshold,
            )

        return sorted(set(flagged))

    def _scan_documents_sync(
        self,
        documents: list[dict[str, Any]],
        query_text: str,
    ) -> ContextScanVerdict:
        """Synchronous batch document scanning."""
        if not documents:
            return ContextScanVerdict()

        all_flagged: list[int] = []
        all_matched: list[str] = []
        worst_threat_type = ""
        worst_confidence = 0.0
        worst_detail = ""

        for idx, doc in enumerate(documents):
            content = doc.get("content", "")
            if not content:
                continue

            verdict = self._scan_single_document_sync(content)
            if verdict.action in ("block", "flag"):
                all_flagged.append(idx)
                all_matched.extend(verdict.matched_patterns)
                if verdict.confidence > worst_confidence:
                    worst_confidence = verdict.confidence
                    worst_threat_type = verdict.threat_type
                    worst_detail = verdict.detail

        if not all_flagged:
            return ContextScanVerdict()

        action = "block" if worst_confidence >= 0.9 else "flag"
        return ContextScanVerdict(
            action=action,
            threat_type=worst_threat_type,
            confidence=worst_confidence,
            detail=worst_detail,
            flagged_documents=all_flagged,
            matched_patterns=all_matched,
        )

    def _scan_single_document_sync(self, text: str) -> ContextScanVerdict:
        """Synchronous scan of a single document.

        Scans the FULL ``text`` — the ``[:SNIPPET_MAX_CHARS]`` slices below
        truncate only the evidence snippet reported in the verdict, never the
        text being scanned (see module-level truncation-order invariant).
        """
        if not text:
            return ContextScanVerdict()

        for pattern_str in INDIRECT_INJECTION_PATTERNS:
            compiled = compile_pattern(pattern_str)
            match = compiled.search(text)
            if match:
                return ContextScanVerdict(
                    action="block",
                    threat_type="indirect_injection",
                    confidence=0.95,
                    detail=f"Indirect prompt injection in document: {match.group(0)[:SNIPPET_MAX_CHARS]}",
                    matched_patterns=[match.group(0)[:SNIPPET_MAX_CHARS]],
                )

        for pattern_str in HIDDEN_INSTRUCTION_PATTERNS:
            compiled = compile_pattern(pattern_str)
            match = compiled.search(text)
            if match:
                return ContextScanVerdict(
                    action="block",
                    threat_type="hidden_instruction",
                    confidence=0.9,
                    detail=f"Hidden instruction detected in document",
                    matched_patterns=[pattern_str],
                )

        # G9 PRECEDENCE FIX: all BLOCK-severity checks (live credentials/secrets) run
        # BEFORE the FLAG-severity checks (toxicity, PII). Previously the toxicity `flag`
        # short-circuited before the credential `block`, so a document carrying BOTH a
        # toxicity pattern AND a live credential was only FLAGGED — the credential was
        # then written to the vector store at rest (a leak). Block always outranks flag.
        #
        # RAG-C5 / C4-CRED-INGEST: BLOCK live credentials/secrets at ingest so they are
        # never stored raw (the per-org typed redaction defaults OFF). Union
        # detect_credential_exposure (CREDENTIAL_EXPOSURE_PATTERNS) + detect_secrets so
        # the ingest and output credential sets are unified; also catch credentials that
        # only detect_pii surfaces (api_key_openai / aws_access_key / aws_secret_access_key).
        # Human PII (email/phone/ssn/card — legitimate in documents) stays a flag below.
        cred_found = {**(detect_secrets(text) or {}), **(detect_credential_exposure(text) or {})}
        if cred_found:
            return ContextScanVerdict(
                action="block",
                threat_type="secret",
                confidence=0.9,
                detail=f"Secret/credential in document: {', '.join(cred_found.keys())}",
                matched_patterns=list(cred_found.keys()),
            )

        pii_found = detect_pii(text)
        _CRED_TOKENS = ("key", "token", "secret", "aws", "api", "credential", "password")
        _cred = [k for k in (pii_found or {}) if any(t in k.lower() for t in _CRED_TOKENS)]
        if _cred:
            return ContextScanVerdict(
                action="block",
                threat_type="secret",
                confidence=0.9,
                detail=f"Secret/credential in document: {', '.join(_cred)}",
                matched_patterns=_cred,
            )

        # FLAG-severity checks below (only reached when no credential BLOCK fired).
        for pattern_str in DOCUMENT_TOXICITY_PATTERNS:
            compiled = compile_pattern(pattern_str)
            match = compiled.search(text)
            if match:
                return ContextScanVerdict(
                    action="flag",
                    threat_type="toxicity",
                    confidence=0.8,
                    detail=f"Toxic content in document: {match.group(0)[:SNIPPET_MAX_CHARS]}",
                    matched_patterns=[match.group(0)[:SNIPPET_MAX_CHARS]],
                )

        if pii_found:
            return ContextScanVerdict(
                action="flag",
                threat_type="pii",
                confidence=0.85,
                detail=f"PII detected in document: {', '.join(pii_found.keys())}",
                matched_patterns=list(pii_found.keys()),
            )

        return ContextScanVerdict()
