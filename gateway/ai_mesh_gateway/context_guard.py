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
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

try:
    from .patterns import (
        compile_pattern, detect_pii, detect_secrets, detect_credential_exposure,
        canonicalize_for_detection, _iter_transport_decodes_canon,
    )
except ImportError:
    from patterns import (
        compile_pattern, detect_pii, detect_secrets, detect_credential_exposure,
        canonicalize_for_detection, _iter_transport_decodes_canon,
    )

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

# G12 (ReDoS / DoS cap on document scanning): a retrieved RAG document is
# attacker-influenced (the indirect-injection channel). The per-document scan
# runs the full injection/hidden/toxicity regex catalogue PLUS the PII/secret
# detectors over the ENTIRE text — the M-19 truncation-order invariant above
# forbids slicing the text before the decision, so scan cost is linear in the
# attacker-controlled document length with NO upper bound. Measured: a ~21 MB
# document pins a scan thread for ~14 s, and the pool has only
# DEFAULT_THREAD_POOL_SIZE workers, so a handful of oversized documents starve
# RAG scanning entirely (denial of service). policy_engine already bounds its
# match path (``_search_with_budget``); context_guard previously had no equivalent.
#
# Fix: two FAIL-CLOSED bounds. Neither truncates-then-allows, so neither creates
# an evasion — an unscannable document is BLOCKED (refused), never silently
# ingested with a threat hiding past a cut boundary:
#   1. ``_MAX_DOC_SCAN_LEN`` — a document longer than this is refused outright,
#      bounding worst-case CPU per scan. Legitimate retrieval chunks are orders
#      of magnitude smaller (upstream MAX_PROMPT_LENGTH is 10k); a multi-MB single
#      "document" is anomalous, and refusing it is safe (block ≠ evasion).
#   2. ``_DOC_SCAN_TIMEOUT_S`` — a wall-clock net around the whole scan, run in a
#      daemon thread (mirroring policy_engine._run_with_timeout). If the scan
#      overruns (pathological backtracking under the size ceiling, or a future bad
#      catalogue pattern) the calling worker is freed and the document is BLOCKED.
_MAX_DOC_SCAN_LEN = 1_000_000
_DOC_SCAN_TIMEOUT_S = 3.0


def _run_with_timeout(fn, timeout):
    """Run ``fn()`` in a daemon thread; return its result, or ``None`` on
    timeout/exception. Frees the caller after ``timeout`` seconds even if a
    backtracking regex is still running (the worker is a daemon). Mirrors
    ``policy_engine._run_with_timeout`` so context_guard shares the same
    ReDoS-containment contract."""
    box: dict[str, Any] = {}

    def _target() -> None:
        try:
            box["result"] = fn()
        except Exception:  # noqa: BLE001 — fail-closed: caught error -> None -> block
            box["error"] = True

    worker = threading.Thread(target=_target, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive() or box.get("error"):
        return None
    return box.get("result")


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

    @staticmethod
    def _distance_tail_outliers(distances: list[float]) -> tuple[list[int], list[int]]:
        """Return ``(upper_tail, lower_tail)`` 2-sigma outlier indices.

        Single place where mean/std are computed, so the numpy path and the
        pure-Python ImportError fallback can never disagree about either tail
        (they previously duplicated the upper-tail loop verbatim). Uses the
        POPULATION std (``np.std`` default ddof=0), preserving the historical
        upper-tail behaviour exactly.

        NOTE (shared by BOTH tails): with the population std the largest possible
        |z| for n samples is ``(n - 1) / sqrt(n)``, which only reaches 2.0 at
        n >= 6. A 2-sigma test therefore cannot fire on a result set of 5 or
        fewer documents — a pre-existing property of the upper tail that the
        lower tail inherits, deliberately, so both tails stay calibrated the same.
        """
        if len(distances) < 3:
            return [], []

        try:
            import numpy as np

            arr = np.array(distances, dtype=float)
            mean_dist = float(np.mean(arr))
            std_dist = float(np.std(arr))
        except ImportError:
            mean_dist = sum(distances) / len(distances)
            variance = sum((d - mean_dist) ** 2 for d in distances) / len(distances)
            std_dist = variance ** 0.5

        if std_dist <= 0:
            return [], []

        hi = mean_dist + 2 * std_dist
        lo = mean_dist - 2 * std_dist
        upper = [i for i, dist in enumerate(distances) if dist > hi]
        lower = [i for i, dist in enumerate(distances) if dist < lo]
        return upper, lower

    def detect_embedding_anomaly(
        self,
        distances: list[float],
        threshold: float,
    ) -> list[int]:
        """
        Detect anomalous results using distance threshold and statistical
        outlier analysis (2-sigma deviation from mean).

        UPPER tail only: a distance far ABOVE the corpus mean is an off-topic /
        injected vector. The LOWER tail (a document matching far more tightly
        than the rest of the corpus) is a different threat with a different
        response and is reported separately by ``detect_near_duplicate_anomaly``
        — see the RAG-05a note there for why the two must never be merged.

        Returns indices of documents flagged as anomalous.
        """
        if not distances:
            return []

        flagged: list[int] = []

        for i, dist in enumerate(distances):
            if dist > threshold:
                flagged.append(i)

        upper, _lower = self._distance_tail_outliers(distances)
        for i in upper:
            if i not in flagged:
                flagged.append(i)

        if flagged:
            LOG.info(
                "Embedding anomaly detected: %d/%d documents flagged (threshold=%.3f)",
                len(flagged),
                len(distances),
                threshold,
            )

        return sorted(set(flagged))

    def detect_near_duplicate_anomaly(self, distances: list[float]) -> list[int]:
        """RAG-05a: detect LOWER-tail distance outliers (near-duplicate poisoning).

        ``detect_embedding_anomaly`` is strictly one-tailed, so a "cloned
        authority" attack — seed a near-copy of a legitimate document that
        contradicts it — was not merely missed: the clone's ~0 distance made it
        the single BEST match, it took rank 1, kept a full 1.0 trust score
        (``compute_trust_score`` only ever penalises HIGH distance) and was
        served verbatim. A document that matches the query dramatically more
        tightly than the genuine corpus does is exactly that signature.

        Reported SEPARATELY from ``detect_embedding_anomaly`` on purpose, and the
        caller must keep the two lists distinct:
          * ranker_stage turns "every document anomalous" into a hard BLOCK, and
          * an exact-match query legitimately produces a low-distance outlier,
        so folding the lower tail into the anomaly list would fail CLOSED on
        valid traffic. The lower tail is advisory: flag + trust demotion only,
        never a drop and never a block.

        Takes no threshold — the test is purely relative to this result set (an
        absolute "too close" bound would fire on every exact match).

        Returns indices of documents flagged as near-duplicate outliers.
        """
        if not distances:
            return []

        _upper, lower = self._distance_tail_outliers(distances)

        if lower:
            LOG.info(
                "Near-duplicate anomaly detected (RAG-05a): %d/%d documents are "
                "lower-tail distance outliers (indices=%s)",
                len(lower),
                len(distances),
                lower,
            )

        return lower

    # RAG-06 / RAG-05a (NOT FIXED — recorded so it is not re-attempted blindly):
    # a pairwise CONTENT near-duplicate detector was implemented here and REMOVED
    # after live calibration. Token containment (|A n B| / min) saturates at 1.00
    # for BOTH the malicious clone and a legitimate longer document covering the
    # same facts:
    #     an1 "refund policy returns 30 days"  (the victim, 5 tokens)
    #       vs a1  "Refund policy: returns accepted within 30 days with a receipt."
    #              -> 1.00  (LEGITIMATE — false positive)
    #       vs ndpoison "...returns 30 days: ACTUALLY refunds are DENIED..."
    #              -> 1.00  (the attack)
    # Thresholds 0.80/0.90/0.95/1.00 all produce the identical flag set, so this
    # is not a tuning problem. A symmetric measure (difflib ratio) fails the other
    # way: the clone is ~4x longer than its victim, so the length gap dominates
    # and the pair scores LOW. Distance-tail statistics cannot work either — see
    # detect_near_duplicate_anomaly, whose lower 2-sigma bound goes NEGATIVE on a
    # realistic spread because the outlier being hunted inflates sigma.
    # The separating feature is SEMANTIC CONTRADICTION ("this document asserts the
    # opposite of that one"), which needs an NLI/LLM judgement over retrieved
    # pairs, not a lexical heuristic. Shipping the lexical version would demote
    # legitimate documents, which the FROZEN operator model forbids.

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
        """Synchronous scan of a single document under the G12 DoS/ReDoS bounds.

        Fail-closed guard rails (see ``_MAX_DOC_SCAN_LEN`` / ``_DOC_SCAN_TIMEOUT_S``):
        an oversized or unscannable document is BLOCKED, never truncated-then-allowed
        (which would let a threat hide past the cut). The real scan happens in
        ``_scan_single_document_impl`` over the FULL text.
        """
        if not text:
            return ContextScanVerdict()

        # (1) size ceiling — refuse (never truncate-then-scan-then-allow) an
        # oversized document so a huge blob cannot pin a scan worker.
        if len(text) > _MAX_DOC_SCAN_LEN:
            LOG.warning(
                "context_guard: document %d chars exceeds scan ceiling %d; blocked (possible DoS)",
                len(text), _MAX_DOC_SCAN_LEN,
            )
            return ContextScanVerdict(
                action="block",
                threat_type="scan_budget_exceeded",
                confidence=0.9,
                detail=(
                    f"Document refused: length {len(text)} exceeds scan ceiling "
                    f"{_MAX_DOC_SCAN_LEN} (fail-closed, possible DoS payload)."
                ),
                matched_patterns=["scan_budget_exceeded"],
            )

        # (2) wall-clock net — run the real scan under a budget so a backtracking
        # pattern cannot pin the worker. On overrun, fail closed (block) + free it.
        verdict = _run_with_timeout(
            lambda: self._scan_single_document_impl(text), _DOC_SCAN_TIMEOUT_S
        )
        if verdict is None:
            LOG.warning(
                "context_guard: document scan exceeded %.1fs budget; blocked (possible ReDoS)",
                _DOC_SCAN_TIMEOUT_S,
            )
            return ContextScanVerdict(
                action="block",
                threat_type="scan_budget_exceeded",
                confidence=0.9,
                detail=(
                    f"Document refused: scan exceeded {_DOC_SCAN_TIMEOUT_S:.1f}s budget "
                    "(fail-closed, possible ReDoS payload)."
                ),
                matched_patterns=["scan_budget_exceeded"],
            )
        return verdict

    def _scan_single_document_impl(self, text: str) -> ContextScanVerdict:
        """Synchronous scan of a single document (full-text detection core).

        Scans the FULL ``text`` — the ``[:SNIPPET_MAX_CHARS]`` slices below
        truncate only the evidence snippet reported in the verdict, never the
        text being scanned (see module-level truncation-order invariant). The
        DoS/ReDoS bounds live in ``_scan_single_document_sync`` which wraps this.
        """
        if not text:
            return ContextScanVerdict()

        # G21 (obfuscation parity with the chat scanner): a retrieved RAG document is
        # attacker-influenced; an indirect injection can be smuggled with unicode
        # tags / small-caps / homoglyph / zero-width / fullwidth so the RAW pattern
        # match misses it while the LLM still reads it. Also match the canonical form
        # (patterns.canonicalize_for_detection folds all of those to ASCII). The raw
        # match is tried FIRST so plain-text evidence snippets are unchanged; the
        # canonical form is only computed/searched when it actually differs (no-op on
        # plain ASCII => existing behaviour and the frozen cases are untouched).
        # NOTE: HIDDEN_INSTRUCTION_PATTERNS deliberately stay RAW-only below — they
        # DETECT obfuscation structure (zero-width runs, control chars) that
        # canonicalization removes.
        canonical = canonicalize_for_detection(text)
        canonical = canonical if canonical != text else None

        for pattern_str in INDIRECT_INJECTION_PATTERNS:
            compiled = compile_pattern(pattern_str)
            match = compiled.search(text)
            if not match and canonical is not None:
                match = compiled.search(canonical)
            if match:
                return ContextScanVerdict(
                    action="block",
                    threat_type="indirect_injection",
                    confidence=0.95,
                    detail=f"Indirect prompt injection in document: {match.group(0)[:SNIPPET_MAX_CHARS]}",
                    matched_patterns=[match.group(0)[:SNIPPET_MAX_CHARS]],
                )

        # G-DOC-TRANSPORT: a retrieved document can smuggle an indirect injection
        # through a base64/hex/base32/base85 transport layer ("please base64-decode
        # and follow: <blob>") that the raw + canonical (unicode-folded) search
        # above cannot see, yet an LLM reading the context decodes and obeys it.
        # The chat-side InputScanner already deobfuscates transport; the document
        # scanner did not, so this class bypassed the RAG doc-poisoning gate.
        # Re-scan the SAME shared, budget-/depth-/printable-gated decoder the chat
        # scanner uses (patterns._iter_transport_decodes_canon) — it inherits that
        # path's FP safety, so benign base64/hex in docs (data URIs, hashes, keys)
        # decodes to high-entropy bytes that match no injection pattern.
        canon_for_decode = canonical if canonical is not None else text
        for decoded in _iter_transport_decodes_canon(text, canon_for_decode):
            if not decoded:
                continue
            decoded_canon = canonicalize_for_detection(decoded)
            for pattern_str in INDIRECT_INJECTION_PATTERNS:
                compiled = compile_pattern(pattern_str)
                match = compiled.search(decoded) or (
                    compiled.search(decoded_canon) if decoded_canon != decoded else None
                )
                if match:
                    return ContextScanVerdict(
                        action="block",
                        threat_type="indirect_injection",
                        confidence=0.95,
                        detail=(
                            "Indirect prompt injection in transport-encoded document payload: "
                            f"{match.group(0)[:SNIPPET_MAX_CHARS]}"
                        ),
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
            if not match and canonical is not None:   # G21: obfuscation-resistant
                match = compiled.search(canonical)
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
