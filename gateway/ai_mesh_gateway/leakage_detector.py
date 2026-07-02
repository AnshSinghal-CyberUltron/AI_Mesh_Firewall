"""
Semantic Leakage Detector for the Gateway Data Plane.

Detects information leakage by:
1. Comparing output n-grams against known confidential document fingerprints
2. Tracking cross-request information extraction patterns via Redis
3. Threshold-based alerting for similarity breaches
"""

import hashlib
import logging
import re
from dataclasses import dataclass, field

LOG = logging.getLogger("gateway.leakage_detector")


@dataclass
class LeakageVerdict:
    """Result of semantic leakage analysis."""

    action: str = "allow"
    leakage_score: float = 0.0
    detail: str = ""
    matched_fingerprints: list[str] = field(default_factory=list)
    cross_request_risk: float = 0.0


class SemanticLeakageDetector:
    """Detects confidential information leakage in LLM outputs."""

    def __init__(
        self,
        redis_client=None,
        similarity_threshold: float = 0.7,
        cross_request_window: int = 300,
        cross_request_threshold: int = 5,
    ):
        self._redis = redis_client
        self._similarity_threshold = similarity_threshold
        self._cross_request_window = cross_request_window
        self._cross_request_threshold = cross_request_threshold
        self._confidential_fingerprints: dict[str, set[str]] = {}

    def register_confidential_content(self, doc_id: str, content: str) -> None:
        """Register content that should not appear in outputs."""
        tokens = self._tokenize(content)
        fingerprints: set[str] = set()
        for n in (3, 4, 5):
            for i in range(len(tokens) - n + 1):
                ngram = " ".join(tokens[i : i + n])
                fingerprints.add(ngram)
        self._confidential_fingerprints[doc_id] = fingerprints
        LOG.debug(
            "Registered %d fingerprints for document %s",
            len(fingerprints),
            doc_id,
        )

    def check_leakage(
        self,
        output_text: str,
        project_id: str = "",
        key_hash: str = "",
    ) -> LeakageVerdict:
        """Check output text for semantic similarity to confidential content."""
        if not output_text or not self._confidential_fingerprints:
            return LeakageVerdict()

        output_tokens = self._tokenize(output_text)
        output_ngrams: set[str] = set()
        for n in (3, 4, 5):
            for i in range(len(output_tokens) - n + 1):
                ngram = " ".join(output_tokens[i : i + n])
                output_ngrams.add(ngram)

        if not output_ngrams:
            return LeakageVerdict()

        max_similarity = 0.0
        matched_docs: list[str] = []
        for doc_id, fingerprints in self._confidential_fingerprints.items():
            if not fingerprints:
                continue
            overlap = output_ngrams & fingerprints
            similarity = len(overlap) / min(len(output_ngrams), len(fingerprints))
            if similarity > max_similarity:
                max_similarity = similarity
            if similarity >= self._similarity_threshold:
                matched_docs.append(doc_id)

        if max_similarity >= self._similarity_threshold:
            return LeakageVerdict(
                action="flag",
                leakage_score=round(max_similarity, 3),
                detail=f"Output matches {len(matched_docs)} confidential document(s) (similarity={max_similarity:.3f})",
                matched_fingerprints=matched_docs,
            )

        return LeakageVerdict(leakage_score=round(max_similarity, 3))

    async def track_cross_request(
        self,
        output_text: str,
        key_hash: str,
    ) -> float:
        """Track information extraction across requests. Returns risk 0.0-1.0."""
        if self._redis is None:
            return 0.0

        try:
            fragments = self._extract_sensitive_fragments(output_text)
            if not fragments:
                return 0.0

            redis_key = f"leakage:cross:{key_hash}"
            frag_hashes = [
                hashlib.sha256(fragment.encode()).hexdigest()[:16]
                for fragment in fragments
            ]
            # CHG-0084: atomic SADD(s)+EXPIRE. Previously the per-fragment sadd(s) and the
            # expire were SEPARATE awaited round-trips, so a coroutine cancellation (client
            # disconnect under load) or a transient error between the last sadd and the
            # expire ORPHANED the leakage:cross:{key} SET with NO TTL — an unbounded Redis
            # memory leak under soak (same class as CHG-0062's rate-limit fix). One
            # MULTI/EXEC sets the members + window TTL atomically (also 1 round-trip, not
            # N+1). Sliding-window semantics preserved (EXPIRE re-set each call).
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.sadd(redis_key, *frag_hashes)
                pipe.expire(redis_key, self._cross_request_window)
                await pipe.execute()

            unique_count = await self._redis.scard(redis_key)
            risk = min(unique_count / self._cross_request_threshold, 1.0)

            if risk >= 0.8:
                LOG.warning(
                    "Cross-request leakage risk HIGH: key=%s, fragments=%d, risk=%.2f",
                    key_hash[:12],
                    unique_count,
                    risk,
                )

            return round(risk, 3)
        except Exception as exc:
            LOG.debug("Cross-request tracking failed: %s", exc)
            return 0.0

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Simple whitespace + punctuation tokenizer."""
        return re.findall(r"\b\w+\b", text.lower())

    @staticmethod
    def _extract_sensitive_fragments(text: str) -> list[str]:
        """Extract fragments that look like they could be sensitive data."""
        fragments: list[str] = []
        fragments.extend(re.findall(r"\b\d{6,}\b", text))
        fragments.extend(
            re.findall(
                r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", text
            )
        )
        fragments.extend(re.findall(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b", text))
        return fragments
