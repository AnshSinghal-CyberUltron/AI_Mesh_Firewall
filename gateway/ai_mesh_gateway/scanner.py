"""
Gateway-local Input/Output Scanner.

Lightweight regex-based threat detection for the data plane.
Mirrors patterns from backend/security_engines/pattern_matcher.py.
Runs in ThreadPoolExecutor to avoid blocking the async event loop.

"""

import asyncio
import difflib
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

try:
    from .bedrock_scanner import BedrockScanner
    from .patterns import (
        compile_pattern,
        detect_pii,
        detect_secrets,
        redact_all,
        PII_PATTERNS,
        SECRET_PATTERNS,
    )
except ImportError:
    from bedrock_scanner import BedrockScanner
    from patterns import (
        compile_pattern,
        detect_pii,
        detect_secrets,
        redact_all,
        PII_PATTERNS,
        SECRET_PATTERNS,
    )

LOG = logging.getLogger("gateway.scanner")

MAX_PROMPT_LENGTH = 10_000
REPETITION_THRESHOLD = 0.30
DEFAULT_THREAD_POOL_SIZE = 4

TOXICITY_INDICATORS: list[re.Pattern] = [
    re.compile(r"\b(?:kill|murder|attack|destroy|eliminate|exterminate)\b", re.IGNORECASE),
    re.compile(r"\b(?:hate|hatred|racist|sexist|bigot|slur)\b", re.IGNORECASE),
    re.compile(r"\b(?:stupid|idiot|moron|dumb|worthless|pathetic)\b", re.IGNORECASE),
    re.compile(r"\b(?:threat|threaten|harm|hurt|violent|abuse)\b", re.IGNORECASE),
    re.compile(r"\b(?:suicide|self-harm|die|death\s+threat)\b", re.IGNORECASE),
    re.compile(r"\b(?:bomb|weapon|explosive|gun\s+violence)\b", re.IGNORECASE),
]
TOXICITY_WEIGHT_PER_HIT = 0.15

@dataclass
class ScanVerdict:
    "carries structured results (action, threat_type, confidence, detail, matched_patterns, tier) for downstream decision-making"
    action: str = "allow"
    threat_type: str = ""
    confidence: float = 0.0
    detail: str = ""
    matched_patterns: list[str] = field(default_factory=list)
    tier: str = ""
    reason_code: str = ""

ATTACK_PATTERNS: dict[str, list[str]] = {
    "prompt_injection": [
        r"ignore\s+(previous|all\s+previous|earlier|above|all)\s+instructions",
        r"disregard\s+(previous|all|above|your)\s+instructions",
        r"forget\s+(previous|everything|your\s+instructions)",
        r"new\s+instructions:",
        r"system\s*prompt\s*:",
        r"you\s+are\s+now",
        r"act\s+as\s+if",
        r"ignore\s+the\s+above",
        r"override\s+system\s+prompt",
        r"instead[,.]?\s+do\s+this",
        r"however[,.]?\s+your\s+real\s+task\s+is",
        r"your\s+new\s+role\s+is",
        r"your\s+actual\s+instructions\s+are",
        r"reveal\s+(your|the)\s+(system\s+)?prompt",
        r"show\s+(me\s+)?(your|the)\s+(system\s+)?prompt",
        r"what\s+(are|is)\s+your\s+(system\s+)?prompt",
        r"repeat\s+(your|the)\s+(initial|system|original)\s+(prompt|instructions)",
        r"print\s+(your|the)\s+(system\s+)?prompt",
        r"output\s+(your|the)\s+(system\s+)?prompt",
    ],
    "jailbreak": [
        r"dan\s+mode",
        r"developer\s+mode",
        r"jailbreak",
        r"strive\s+to\s+avoid\s+norms",
        r"truly\s+submissive\s+ai",
        r"\b(?:dan|stan|dude)\b.*(?:policy|virtual\s+machine|do\s+anything\s+now)",
        r"unrestricted\s+mode",
        r"no\s+limitations",
        r"i\s+(?:am|have)\s+(?:now\s+)?unrestricted",
        r"(?:i|you)\s+can\s+do\s+anything\s+now",
        r"bypass\s+(?:safety|content|security)\s+filters?",
        r"(?:without|no)\s+(?:any\s+)?(?:safety|content)\s+filters?",
        r"pretend\s+(?:you\s+are|to\s+be)\s+(?:a|an)\s+(?:ai|assistant)\s+(?:without|with\s+no)",
        r"in\s+this\s+(?:hypothetical|fictional)\s+scenario",
        r"(?:enable|activate|switch\s+to)\s+(?:unrestricted|unfiltered|uncensored)\s+mode",
        r"(?:remove|disable|turn\s+off)\s+(?:all\s+)?(?:your\s+)?(?:safety|content|ethical)\s+(?:filters|guardrails|restrictions)",
        r"do\s+anything\s+mode",
    ],
    "data_leakage": [
        r"(?:what|tell\s+me|give\s+me|show\s+me|reveal|display|print|list|dump|export)\s+.*(?:ssn|social\s+security|credit\s+card|password|secret(?:s)?|api\s+key(?:s)?)",
        r"(?:exfiltrate|extract|steal|leak|expose)\s+.*(?:data|information|credentials|secrets|keys)",
        r"(?:send|upload|transmit|forward)\s+.*(?:data|information|records)\s+to",
        r"(?:access|retrieve|download|dump)\s+.*(?:database|records|user\s+data|customer)",
    ],
    "goal_hijacking": [
        r"your\s+(?:new|real|actual|true)\s+(?:goal|purpose|objective|task)\s+is",
        r"forget\s+(?:your\s+)?(?:previous\s+)?purpose",
        r"(?:help\s+me|assist\s+me\s+(?:in|to))\s+(?:hack|attack|breach|compromise|exploit)",
        r"your\s+(?:primary|main)\s+(?:objective|goal)\s+(?:is\s+now|has\s+changed)",
    ],
    "tool_overreach": [
        r"(?:use|call|invoke|execute|run)\s+(?:the\s+)?(?:delete|remove|drop|truncate|destroy)\w*\s+(?:tool|function|command)",
        r"(?:delete|remove|destroy|wipe|erase)\s+(?:all\s+)?(?:user|customer|production|database)\s+(?:records|data|entries|tables)",
        r"(?:execute|run)\s+(?:system|shell|bash|cmd)\s+(?:command|code)",
        r"(?:access|read|write|modify)\s+(?:production|internal|admin|root)\s+(?:database|system|server)",
    ],
    "sql_injection": [
        r"'\s*OR\s+'.*'='",
        r"'\s*;.*DROP\s+TABLE",
        r"UNION\s+SELECT",
    ],
    "command_injection": [
        r";\s*rm\s+-rf",
        r"&&\s*curl",
        r"\|\s*bash",
        r"`[^`]+`",
        r"\$\([^\)]+\)",
    ],
    "path_traversal": [
        r"\.\./\.\./",
        r"\.\.\\\.\.\\",
    ],
    "vector_injection": [
        r"(?:collection|namespace|index)\s*[=:]\s*[\w]*\.\.",
        r"(?:\$where|\$regex|\$gt|\$lt|\$ne|\$nin|\$in)\b",
        r"metadata\[.*?\]\s*(?:=|!=|>=|<=|>|<)",
        r"(?:drop|delete|truncate)\s+(?:collection|index|partition)",
        r"(?:embedding|vector)\s*=\s*\[",
    ],
}

RAG_POISONING_PATTERNS: list[str] = [
    r"ignore\s+the\s+(context|documents|retriev)",
    r"the\s+documents?\s+say",
    r"according\s+to\s+the\s+following",
    r"override\s+(context|system)",
    r"<\s*/?\s*(?:system|context|instruction)\s*>",
    r"disregard\s+(?:the\s+)?(?:retrieved|provided)\s+(?:context|documents|information)",
    r"(?:the\s+)?(?:real|actual|true)\s+(?:answer|information)\s+is",
    r"(?:insert|inject)\s+(?:into|in)\s+(?:the\s+)?(?:context|knowledge\s+base)",
]

_LEET_MAP: dict[str, str] = {
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s",
    "7": "t", "@": "a", "$": "s", "!": "i",
}

_DEOBFUSCATION_VOCAB: frozenset[str] = frozenset({
    "ignore", "previous", "all", "instructions", "disregard", "above", "your",
    "forget", "everything", "new", "system", "prompt", "you", "are", "now",
    "act", "as", "if", "the", "override", "instead", "do", "this", "however",
    "real", "task", "role", "actual", "reveal", "show", "me", "what",
    "repeat", "initial", "original", "print", "output", "my",
    "dan", "developer", "mode", "jailbreak", "unrestricted", "no", "limitations",
    "can", "anything", "bypass", "safety", "content", "security", "filters",
    "without", "any", "pretend", "be", "an", "assistant", "in",
    "hypothetical", "fictional", "scenario", "enable", "activate", "switch",
    "to", "unfiltered", "uncensored", "remove", "disable", "turn", "off",
    "ethical", "guardrails", "restrictions",
    "goal", "purpose", "objective", "help", "hack", "attack", "breach",
    "compromise", "exploit", "primary", "main", "has", "changed",
    "exfiltrate", "extract", "steal", "leak", "expose", "data",
    "information", "credentials", "secrets", "keys", "send", "upload",
    "database", "user", "customer", "delete", "records",
    "execute", "command", "run", "access",
})

_CONCAT_WORD_MIN_LENGTH: int = 8
_FUZZY_SEGMENT_THRESHOLD: float = 0.80
_MAX_VOCAB_WORD_LENGTH: int = max(len(w) for w in _DEOBFUSCATION_VOCAB)


def _normalize_leet(text: str) -> str:
    """Replace common l33tspeak character substitutions with alphabetic equivalents."""
    return "".join(_LEET_MAP.get(c, c) for c in text)


def _segment_token(
    token: str,
    vocab: frozenset[str] = _DEOBFUSCATION_VOCAB,
    fuzzy_threshold: float = _FUZZY_SEGMENT_THRESHOLD,
) -> list[str]:
    """
    DP-based word segmentation for a single concatenated token.

    Tries exact dictionary matches first (score 1.0 per word), then falls back
    to fuzzy matching (SequenceMatcher ratio >= fuzzy_threshold) for misspelled
    segments. Returns the segmented word list if 2+ words are found, otherwise
    returns the token unchanged.
    """
    lower = token.lower()
    n = len(lower)
    if n < _CONCAT_WORD_MIN_LENGTH:
        return [token]

    dp: list[tuple[list[str], float] | None] = [None] * (n + 1)
    dp[0] = ([], 0.0)

    for i in range(1, n + 1):
        for j in range(max(0, i - _MAX_VOCAB_WORD_LENGTH), i):
            if dp[j] is None:
                continue
            segment = lower[j:i]
            seg_len = len(segment)
            if seg_len < 2:
                continue

            if segment in vocab:
                score = dp[j][1] + float(seg_len)
                candidate = dp[j][0] + [segment]
                if dp[i] is None or score > dp[i][1]:
                    dp[i] = (candidate, score)
            elif seg_len >= 3:
                best_match: str | None = None
                best_sim: float = 0.0
                for word in vocab:
                    if abs(len(word) - seg_len) > 2:
                        continue
                    sim = difflib.SequenceMatcher(None, segment, word).ratio()
                    if sim >= fuzzy_threshold and sim > best_sim:
                        best_match = word
                        best_sim = sim
                if best_match is not None:
                    score = dp[j][1] + float(seg_len) * best_sim
                    candidate = dp[j][0] + [best_match]
                    if dp[i] is None or score > dp[i][1]:
                        dp[i] = (candidate, score)

    if dp[n] is not None and len(dp[n][0]) > 1:
        return dp[n][0]
    return [token]


FUZZY_WORD_SIMILARITY_THRESHOLD = 0.75

BEDROCK_BLOCK_ACTIONS: frozenset[str] = frozenset({
    "block",
    "block_immediately",
    "block_and_alert",
    "deny",
    "reject",
})
BEDROCK_REDACT_ACTIONS: frozenset[str] = frozenset({
    "redact",
    "mask",
    "sanitize",
})
BEDROCK_MONITOR_ACTIONS: frozenset[str] = frozenset({
    "monitor",
    "warn",
    "flag",
    "review",
})

FUZZY_ANCHOR_PHRASES: dict[str, list[list[str]]] = {
    "prompt_injection": [
        ["ignore", "previous", "instructions"],
        ["ignore", "all", "instructions"],
        ["disregard", "previous", "instructions"],
        ["disregard", "your", "instructions"],
        ["forget", "previous", "instructions"],
        ["override", "system", "prompt"],
        ["reveal", "system", "prompt"],
        ["show", "system", "prompt"],
        ["show", "me", "your", "system", "prompt"],
        ["repeat", "system", "prompt"],
        ["output", "system", "prompt"],
        ["print", "system", "prompt"],
        ["ignore", "above", "instructions"],
        ["your", "actual", "instructions"],
        ["your", "new", "role"],
    ],
    "jailbreak": [
        ["developer", "mode"],
        ["unrestricted", "mode"],
        ["bypass", "safety", "filters"],
        ["remove", "safety", "filters"],
        ["disable", "safety", "guardrails"],
        ["without", "safety", "filters"],
        ["hypothetical", "scenario"],
        ["do", "anything", "mode"],
    ],
    "goal_hijacking": [
        ["your", "new", "goal"],
        ["your", "real", "goal"],
        ["forget", "your", "purpose"],
        ["help", "me", "hack"],
        ["help", "me", "exploit"],
        ["exfiltrate", "customer", "data"],
    ],
    "data_leakage": [
        ["exfiltrate", "data"],
        ["extract", "credentials"],
        ["steal", "data"],
        ["leak", "information"],
    ],
    "tool_overreach": [
        ["delete", "database"],
        ["remove", "user", "records"],
        ["execute", "system", "command"],
        ["drop", "production", "database"],
    ],
}

class InputScanner:
    """Gateway-local scanner for prompt/response content."""

    def __init__(
        self,
        thread_pool_size: int = DEFAULT_THREAD_POOL_SIZE,
        config: dict | None = None,
    ) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=thread_pool_size,
            thread_name_prefix="scanner",
        )
        self._config = config or {}
        # Tier-2 (Bedrock) feature flag can be enabled via env var ENABLE_TIER2
        self.tier2_enabled = os.getenv("ENABLE_TIER2", "true").lower() in ("1", "true", "yes")
        self._bedrock_scanner: BedrockScanner | None = BedrockScanner() if self.tier2_enabled else None
        LOG.info(
            "InputScanner initialized (thread_pool_size=%d, attack_categories=%d, pii_patterns=%d)",
            thread_pool_size,
            len(ATTACK_PATTERNS),
            len(PII_PATTERNS),
        )

    async def scan_prompt(self, text: str, is_rag: bool = False) -> ScanVerdict:
        """Asynchronously scan the prompt for threats and return a structured verdict."""
        loop = asyncio.get_event_loop()

        return await loop.run_in_executor(
            self._executor,
            self._scan_prompt_sync,
            text,
            is_rag,
        )
    async def scan_output(self, text: str) -> ScanVerdict:
        """Asynchronously scan the LLM output for PII/Secrets and return a structured verdict."""
        loop = asyncio.get_event_loop()

        return await loop.run_in_executor(
            self._executor,
            self._scan_output_sync,
            text,
        )

    def _scan_prompt_sync(self, text: str, is_rag: bool) -> ScanVerdict:
        """Synchronous prompt scanning logic, run in a thread to avoid blocking."""
        if not text:
            return ScanVerdict(
                action="allow",
                threat_type="none",
                confidence=0.0,
                detail="Empty prompt",
            )
        if len(text) > MAX_PROMPT_LENGTH:
            LOG.warning("Prompt exceeds max length (%d > %d)", len(text), MAX_PROMPT_LENGTH)
            return ScanVerdict(
                action="block",
                threat_type="dos",
                confidence=1.0,
                detail=f"Prompt length {len(text)} exceeds maximum {MAX_PROMPT_LENGTH}",
                tier="tier_1",
            )
        if self._is_repetitive(text):
            return ScanVerdict(
                action="block",
                threat_type="dos",
                confidence=0.9,
                detail="Excessive repetition detected (potential DoS)",
                tier="tier_1",
            )
        
        for category, patterns in ATTACK_PATTERNS.items():
            matched = []
            for pattern_str in patterns:
                compiled = compile_pattern(pattern_str)
                match = compiled.search(text)
                if match:
                    matched.append(match.group(0))

            if matched:
                return ScanVerdict(
                    action="block",
                    threat_type=category,
                    confidence=1.0,
                    detail=f"Matched {category} pattern(s)",
                    matched_patterns=matched,
                    tier="tier_1",
                )
        if is_rag:
            for pattern_str in RAG_POISONING_PATTERNS:
                compiled = compile_pattern(pattern_str)
                match = compiled.search(text)
                if match:
                    return ScanVerdict(
                        action="block",
                        threat_type="rag_poisoning",
                        confidence=0.9,
                        detail=f"RAG context poisoning attempt: {match.group(0)}",
                        matched_patterns=[match.group(0)],
                        tier="tier_1",
                    )

        deobfuscated = self._deobfuscate_text(text)
        if deobfuscated != text.lower():
            LOG.debug("Deobfuscation produced: '%s'", deobfuscated[:200])
            for category, patterns in ATTACK_PATTERNS.items():
                matched = []
                for pattern_str in patterns:
                    compiled = compile_pattern(pattern_str)
                    match = compiled.search(deobfuscated)
                    if match:
                        matched.append(match.group(0))
                if matched:
                    LOG.warning(
                        "Tier-0.5 deobfuscation detected %s: patterns=%s",
                        category, matched,
                    )
                    return ScanVerdict(
                        action="block",
                        threat_type=category,
                        confidence=0.95,
                        detail=f"Obfuscated {category} detected after deobfuscation",
                        matched_patterns=matched,
                        tier="tier_0_5",
                    )
            if is_rag:
                for pattern_str in RAG_POISONING_PATTERNS:
                    compiled = compile_pattern(pattern_str)
                    match = compiled.search(deobfuscated)
                    if match:
                        LOG.warning(
                            "Tier-0.5 deobfuscation detected rag_poisoning: %s",
                            match.group(0),
                        )
                        return ScanVerdict(
                            action="block",
                            threat_type="rag_poisoning",
                            confidence=0.90,
                            detail=f"Obfuscated RAG poisoning detected: {match.group(0)}",
                            matched_patterns=[match.group(0)],
                            tier="tier_0_5",
                        )

        fuzzy_verdict = self._fuzzy_scan(text)
        if fuzzy_verdict is not None:
            return fuzzy_verdict

        pii_matched = detect_pii(text)
        if pii_matched:
            return ScanVerdict(
                    action="redact",
                threat_type="pii",
                confidence=0.85,
                detail=f"PII detected in prompt: {', '.join(pii_matched.keys())}",
                matched_patterns=list(pii_matched.keys()),
                tier="tier_1",
            )

        secret_matched = detect_secrets(text)
        if secret_matched:
            return ScanVerdict(
                    action="redact",
                threat_type="secret",
                confidence=0.9,
                detail=f"Secret/credential detected: {', '.join(secret_matched.keys())}",
                matched_patterns=list(secret_matched.keys()),
                tier="tier_1",
            )

        toxicity_verdict = self._check_toxicity(text)
        if toxicity_verdict is not None:
            return toxicity_verdict

        return ScanVerdict()

    def _scan_output_sync(self, text: str) -> ScanVerdict:
        """Sync output scanning logic for PII/Secrets, run in a thread."""
        if not text:
            return ScanVerdict(
                action="allow",
                threat_type="none",
                confidence=0.0,
                detail="Empty output",
            )
        
        pii_matched = detect_pii(text)
        if pii_matched:
            return ScanVerdict(
                action="flag",
                threat_type="pii",
                confidence=0.85,
                detail=f"PII detected in output: {', '.join(pii_matched.keys())}",
                matched_patterns=list(pii_matched.keys()),
            )

        secret_matched = detect_secrets(text)
        if secret_matched:
            return ScanVerdict(
                action="flag",
                threat_type="secret",
                confidence=0.9,
                detail=f"Secret/credential in output: {', '.join(secret_matched.keys())}",
                matched_patterns=list(secret_matched.keys()),
            )

        return ScanVerdict()
    

    def redact_pii(self, text: str) -> str:
        return redact_all(text)

    def _is_repetitive(self, text: str) -> bool:
        """Detect excessive repetition as a simple heuristic for DoS attempts."""
        words = text.split()
        if len(words) < 10:
            return False
        word_counts: dict[str, int] = {}
        for word in words:
            lower = word.lower()
            word_counts[lower] = word_counts.get(lower, 0) + 1
        max_count = max(word_counts.values())
        return (max_count / len(words)) > REPETITION_THRESHOLD

    def _check_toxicity(self, text: str) -> ScanVerdict | None:
        """
        Heuristic toxicity scoring based on indicator pattern matches.
        Compares the computed score against the configurable toxicity_threshold
        from the gateway firewall config.
        """
        threshold = self._config.get("toxicity_threshold", 0.70)
        matched_indicators: list[str] = []
        for pattern in TOXICITY_INDICATORS:
            hits = pattern.findall(text)
            if hits:
                matched_indicators.extend(hits)
        if not matched_indicators:
            return None
        score = min(len(matched_indicators) * TOXICITY_WEIGHT_PER_HIT, 1.0)
        if score >= threshold:
            return ScanVerdict(
                action="block",
                threat_type="toxicity",
                confidence=round(score, 2),
                detail=f"Toxic content detected (score={score:.2f}, threshold={threshold:.2f})",
                matched_patterns=matched_indicators[:10],
                tier="tier_1",
            )
        return None

    def _deobfuscate_text(self, text: str) -> str:
        """
        Normalize obfuscated text by applying l33tspeak substitution and
        DP-based word segmentation on concatenated tokens.

        Returns the normalized text with spaces between segmented words.
        Only tokens longer than _CONCAT_WORD_MIN_LENGTH are segmented.
        """
        normalized = _normalize_leet(text.lower())
        tokens = re.findall(r"[a-zA-Z]+", normalized)
        if not tokens:
            return normalized

        result_tokens: list[str] = []
        for token in tokens:
            if len(token) >= _CONCAT_WORD_MIN_LENGTH:
                segments = _segment_token(token)
                result_tokens.extend(segments)
            else:
                result_tokens.append(token)

        return " ".join(result_tokens)

    def _fuzzy_scan(self, text: str) -> ScanVerdict | None:
        """
        Tier-1.5 fuzzy phrase matching to catch typo-based evasion.

        Uses difflib.SequenceMatcher for approximate string matching.
        Checks if known attack phrases appear as ordered subsequences
        in the input with per-word similarity >= FUZZY_WORD_SIMILARITY_THRESHOLD.
        """
        input_words = re.findall(r"[a-zA-Z]+", text.lower())
        if not input_words:
            return None

        for category, phrase_lists in FUZZY_ANCHOR_PHRASES.items():
            for anchor_phrase in phrase_lists:
                matched_phrase, avg_similarity = self._fuzzy_subsequence_match(
                    input_words, anchor_phrase
                )
                if matched_phrase:
                    readable = " ".join(anchor_phrase)
                    LOG.warning(
                        "Fuzzy match: category=%s, phrase='%s', similarity=%.3f",
                        category, readable, avg_similarity,
                    )
                    return ScanVerdict(
                        action="block",
                        threat_type=category,
                        confidence=round(avg_similarity, 3),
                        detail=f"Fuzzy match for '{readable}' (similarity={avg_similarity:.2f})",
                        matched_patterns=[readable],
                        tier="tier_1_5",
                    )
        return None

    @staticmethod
    def _fuzzy_subsequence_match(
        input_words: list[str],
        phrase_words: list[str],
        threshold: float = FUZZY_WORD_SIMILARITY_THRESHOLD,
    ) -> tuple[bool, float]:
        """
        Check if phrase_words appear as a fuzzy ordered subsequence
        within input_words. Returns (matched: bool, avg_similarity: float).
        """
        phrase_idx = 0
        matched_similarities: list[float] = []

        for word in input_words:
            if phrase_idx >= len(phrase_words):
                break
            similarity = difflib.SequenceMatcher(
                None, word, phrase_words[phrase_idx]
            ).ratio()
            if similarity >= threshold:
                matched_similarities.append(similarity)
                phrase_idx += 1

        if phrase_idx >= len(phrase_words) and matched_similarities:
            avg = sum(matched_similarities) / len(matched_similarities)
            return (True, avg)
        return (False, 0.0)

    @staticmethod
    def _normalize_bedrock_action(raw_action: str | None) -> str:
        value = str(raw_action or "").strip().lower()
        if value in BEDROCK_BLOCK_ACTIONS:
            return "block"
        if value in BEDROCK_REDACT_ACTIONS:
            return "redact"
        if value in BEDROCK_MONITOR_ACTIONS:
            return "monitor"
        return "allow"

    @staticmethod
    def _normalize_score(raw_score: Any) -> float:
        if isinstance(raw_score, bool) or raw_score is None:
            return 0.0
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            return 0.0
        if score <= 0.0:
            return 0.0
        if score > 1.0:
            # Handle 0-100 style score payloads from model outputs.
            score = score / 100.0
        return max(0.0, min(score, 1.0))

    @classmethod
    def _is_tier2_degraded(cls, meta: dict[str, Any], llm_guard: dict[str, Any]) -> bool:
        reason = str(meta.get("decision_reason", "")).lower()
        return bool(
            meta.get("parse_failed")
            or meta.get("error")
            or llm_guard.get("degraded")
            or reason.startswith("degraded")
            or reason in {"parse_failed", "client_error", "empty_response"}
        )

    async def scan_prompt_with_tier2(self, text: str, is_rag: bool = False) -> ScanVerdict:
        """
        Run Tier-1 regex/deterministic checks first. If no blocking verdict,
        and Tier-2 is enabled, call the Bedrock scanner (async via executor)
        and combine results to produce a final ScanVerdict.

        Deobfuscated text is passed to Bedrock alongside the original so the
        model can see both raw and segmented versions.
        """
        tier1 = await self.scan_prompt(text, is_rag)
        if tier1.action == "block":
            return tier1

        if not self.tier2_enabled or not self._bedrock_scanner:
            return tier1

        deobfuscated = self._deobfuscate_text(text)
        bedrock_input = deobfuscated if deobfuscated != text.lower() else text
        original_context = text if bedrock_input != text else None

        loop = asyncio.get_event_loop()
        bedrock_normalized = await loop.run_in_executor(
            self._executor, self._bedrock_scan_sync, bedrock_input, original_context,
        )

        meta = bedrock_normalized.get("meta", {})
        reason_code = str(meta.get("decision_reason", "")).strip().lower()
        recommended = self._normalize_bedrock_action(meta.get("recommended_action"))
        llm_guard = bedrock_normalized.get("llm_guard", {})
        score = self._normalize_score(llm_guard.get("score", 0.0))

        raw_findings = meta.get("raw_findings") or []
        bedrock_categories: list[str] = []
        bedrock_evidence: list[str] = []
        max_confidence = 0.0
        for finding in raw_findings:
            if isinstance(finding, dict):
                cat = finding.get("category", "")
                if cat:
                    bedrock_categories.append(cat)
                ev = finding.get("evidence", "")
                if ev:
                    bedrock_evidence.append(ev)
                conf = self._normalize_score(finding.get("confidence", 0.0))
                if conf > max_confidence:
                    max_confidence = conf

        if recommended == "block":
            return ScanVerdict(
                action="block",
                threat_type=bedrock_categories[0] if bedrock_categories else "bedrock",
                confidence=max_confidence or 1.0,
                detail=f"Bedrock ML detected threat: {', '.join(bedrock_evidence[:2]) or 'recommended block'}",
                matched_patterns=bedrock_evidence[:5] or [],
                tier="tier_2",
                reason_code=reason_code or "model_recommended_block",
            )
        if recommended == "redact":
            return ScanVerdict(
                action="flag",
                threat_type=bedrock_categories[0] if bedrock_categories else "bedrock_redact",
                confidence=max_confidence or 0.9,
                detail=f"Bedrock suggested redaction: {', '.join(bedrock_evidence[:2]) or 'redact'}",
                matched_patterns=bedrock_evidence[:5] or [],
                tier="tier_2",
                reason_code=reason_code or "model_recommended_redact",
            )
        if self._is_tier2_degraded(meta, llm_guard):
            degraded_reason = reason_code or "bedrock_degraded"
            degraded_detail = (
                "Bedrock scanner degraded; unable to confidently validate prompt"
            )
            if meta.get("error"):
                degraded_detail = "Bedrock scanner error; unable to confidently validate prompt"
            elif meta.get("parse_failed"):
                degraded_detail = "Bedrock response unparseable; unable to confidently validate prompt"
            return ScanVerdict(
                action="flag",
                threat_type="bedrock_degraded",
                confidence=max(score, 0.5),
                detail=degraded_detail,
                tier="tier_2",
                reason_code=degraded_reason,
            )
        if recommended == "monitor":
            return ScanVerdict(
                action="flag",
                threat_type=bedrock_categories[0] if bedrock_categories else "bedrock",
                confidence=max_confidence or float(score),
                detail=f"Bedrock advisory: {', '.join(bedrock_evidence[:2]) or 'monitor'}",
                matched_patterns=bedrock_evidence[:5] or [],
                tier="tier_2",
                reason_code=reason_code or "model_recommended_monitor",
            )

        BEDROCK_BLOCK_THRESHOLD = 0.70
        BEDROCK_FLAG_THRESHOLD = 0.40

        if score >= BEDROCK_BLOCK_THRESHOLD:
            return ScanVerdict(
                action="block",
                threat_type=bedrock_categories[0] if bedrock_categories else "bedrock_score",
                confidence=score,
                detail=f"High bedrock risk score: {', '.join(bedrock_evidence[:2]) or 'score-based block'}",
                matched_patterns=bedrock_evidence[:5] or [],
                tier="tier_2",
                reason_code=reason_code or "score_threshold_block",
            )

        if score >= BEDROCK_FLAG_THRESHOLD:
            return ScanVerdict(
                action="flag",
                threat_type=bedrock_categories[0] if bedrock_categories else "bedrock_score",
                confidence=score,
                detail=f"Moderate bedrock risk score: {', '.join(bedrock_evidence[:2]) or 'score-based flag'}",
                matched_patterns=bedrock_evidence[:5] or [],
                tier="tier_2",
                reason_code=reason_code or "score_threshold_flag",
            )

        if bedrock_categories and recommended == "allow":
            return ScanVerdict(
                action="flag",
                threat_type=bedrock_categories[0],
                confidence=max_confidence or 0.5,
                detail="Bedrock found threats but recommended allow -- escalated to flag",
                matched_patterns=bedrock_evidence[:5] or [],
                tier="tier_2",
                reason_code=reason_code or "findings_with_allow",
            )

        return ScanVerdict(
            action="allow",
            threat_type="none",
            confidence=score,
            detail="Tier-2 passed",
            tier="tier_2",
            reason_code=reason_code or "tier2_pass",
        )

    def _bedrock_scan_sync(self, analyzed_text: str, original_text: str | None = None) -> dict[str, Any]:
        """Synchronous helper to call the Bedrock scanner from a thread.

        Parameters
        ----------
        analyzed_text : str
            The text to send as the primary analysis payload (may be deobfuscated).
        original_text : str, optional
            The raw user input before deobfuscation, passed as context so the
            model can see the obfuscation pattern.
        """
        assert self._bedrock_scanner is not None
        return self._bedrock_scanner.scan(analyzed_text, context=original_text)


@dataclass
class IntentClassification:
    """Structured intent classification result with risk scoring."""

    category: str = "general"
    risk_score: float = 0.0
    confidence: float = 0.0
    secondary_categories: list[str] = field(default_factory=list)


_INTENT_CATEGORIES: dict[str, dict[str, Any]] = {
    "code_generation": {
        "keywords": ["write code", "implement", "function", "class", "def ", "script", "program", "coding", "developer", "api", "algorithm"],
        "risk_score": 0.2,
    },
    "data_analysis": {
        "keywords": ["analyze", "statistics", "dataset", "csv", "chart", "graph", "plot", "correlation", "regression", "data"],
        "risk_score": 0.15,
    },
    "information_retrieval": {
        "keywords": ["summarize", "explain", "what is", "how does", "describe", "tell me about", "define", "meaning"],
        "risk_score": 0.05,
    },
    "creative_writing": {
        "keywords": ["story", "poem", "creative", "fiction", "write a", "compose", "narrative", "essay"],
        "risk_score": 0.05,
    },
    "system_admin": {
        "keywords": ["sudo", "admin", "root", "config", "server", "deploy", "docker", "kubernetes", "infrastructure"],
        "risk_score": 0.5,
    },
    "security_test": {
        "keywords": ["hack", "exploit", "vulnerability", "penetration", "bypass", "injection", "xss", "csrf"],
        "risk_score": 0.8,
    },
    "medical_advice": {
        "keywords": ["diagnosis", "symptom", "medication", "dosage", "treatment", "medical", "patient", "prescription", "clinical"],
        "risk_score": 0.7,
    },
    "legal_advice": {
        "keywords": ["lawsuit", "legal", "attorney", "court", "regulation", "compliance", "liability", "contract", "law"],
        "risk_score": 0.6,
    },
    "financial_advice": {
        "keywords": ["investment", "stock", "portfolio", "trading", "financial", "tax", "accounting", "revenue", "profit"],
        "risk_score": 0.6,
    },
    "pii_related": {
        "keywords": ["social security", "ssn", "date of birth", "address", "phone number", "credit card", "passport", "driver license"],
        "risk_score": 0.9,
    },
    "system_compromise": {
        "keywords": ["reverse shell", "privilege escalation", "rootkit", "backdoor", "malware", "ransomware", "keylogger", "c2 server"],
        "risk_score": 0.95,
    },
    "content_moderation": {
        "keywords": ["moderate", "review content", "flag inappropriate", "content policy", "community guidelines"],
        "risk_score": 0.3,
    },
    "translation": {
        "keywords": ["translate", "translation", "language", "localize", "interpret"],
        "risk_score": 0.05,
    },
    "education": {
        "keywords": ["teach", "learn", "tutorial", "explain concept", "homework", "study", "course"],
        "risk_score": 0.05,
    },
    "automation": {
        "keywords": ["automate", "workflow", "scheduler", "cron", "pipeline", "batch", "orchestrate"],
        "risk_score": 0.35,
    },
}


def classify_intent_v2(text: str) -> IntentClassification:
    """Enhanced intent classification with risk scoring and confidence."""
    lower = text.lower()
    scores: dict[str, int] = {}
    for intent, config in _INTENT_CATEGORIES.items():
        score = sum(1 for kw in config["keywords"] if kw in lower)
        if score > 0:
            scores[intent] = score

    if not scores:
        return IntentClassification(category="general", risk_score=0.1, confidence=0.3)

    total_hits = sum(scores.values())
    sorted_intents = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    primary = sorted_intents[0]
    primary_category = primary[0]
    primary_confidence = min(primary[1] / max(total_hits, 1) + 0.3, 1.0)
    primary_risk = _INTENT_CATEGORIES[primary_category]["risk_score"]

    secondary = [cat for cat, _ in sorted_intents[1:3]]

    # Boost risk if multiple high-risk intents detected
    if len(sorted_intents) > 1:
        secondary_risks = [_INTENT_CATEGORIES[cat]["risk_score"] for cat, _ in sorted_intents[1:]]
        if any(r >= 0.7 for r in secondary_risks):
            primary_risk = min(primary_risk + 0.15, 1.0)

    return IntentClassification(
        category=primary_category,
        risk_score=round(primary_risk, 2),
        confidence=round(primary_confidence, 2),
        secondary_categories=secondary,
    )


def classify_intent(text: str) -> str:
    """Backward-compatible wrapper returning just the category string."""
    return classify_intent_v2(text).category