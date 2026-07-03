"""
Gateway-local Input/Output Scanner.

Lightweight regex-based threat detection for the data plane.
Mirrors patterns from backend/security_engines/pattern_matcher.py.
Runs in ThreadPoolExecutor to avoid blocking the async event loop.

"""

import asyncio
import base64
import binascii
import difflib
import hashlib
import logging
import os
import random
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

try:
    from .bedrock_scanner import BedrockScanner
    from .bedrock_tier2_breaker import BREAKER, Tier2UnavailableStrict
    from .config import _env_float, _env_int
    from .telemetry_ops import (
        EVENT_CLASS_TIER2_DEGRADED_PASS,
        emit_operational_event,
    )
    from .patterns import (
        compile_pattern,
        detect_pii,
        detect_secrets,
        detect_credential_exposure,
        detect_ip_leakage,
        redact_all,
        strip_interleaved_emphasis,
        PII_PATTERNS,
        SECRET_PATTERNS,
    )
except ImportError:
    from bedrock_scanner import BedrockScanner
    from bedrock_tier2_breaker import BREAKER, Tier2UnavailableStrict  # type: ignore[no-redef]
    from config import _env_float, _env_int  # type: ignore[no-redef]
    from telemetry_ops import (  # type: ignore[no-redef]
        EVENT_CLASS_TIER2_DEGRADED_PASS,
        emit_operational_event,
    )
    from patterns import (
        compile_pattern,
        detect_pii,
        detect_secrets,
        detect_credential_exposure,
        detect_ip_leakage,
        redact_all,
        strip_interleaved_emphasis,
        PII_PATTERNS,
        SECRET_PATTERNS,
    )

LOG = logging.getLogger("gateway.scanner")

MAX_PROMPT_LENGTH = 10_000
REPETITION_THRESHOLD = 0.30
# Any single whitespace-free token longer than this is treated as a DoS attempt:
# it has no legitimate use and would otherwise drive the word-segmenter's fuzzy
# fallback (SequenceMatcher per vocab word per DP cell) into multi-second CPU.
_MAX_REPETITIVE_TOKEN_LENGTH = 200
# Scanner worker pool size. Sized via env so it can be raised per-instance
# (e.g. to the worker's vCPU count) to avoid head-of-line blocking when blocking
# scan work (Tier-2/vault) runs in the executor.
# M-09: _env_int so a typo'd value can't crash the gateway at import time.
DEFAULT_THREAD_POOL_SIZE = _env_int(
    "GATEWAY_SCANNER_THREAD_POOL_SIZE", 8, min_value=1, max_value=256)

TOXICITY_INDICATORS: list[re.Pattern] = [
    re.compile(r"\b(?:kill|murder|attack|destroy|eliminate|exterminate)\b", re.IGNORECASE),
    re.compile(r"\b(?:hate|hatred|racist|sexist|bigot|slur)\b", re.IGNORECASE),
    re.compile(r"\b(?:stupid|idiot|moron|dumb|worthless|pathetic)\b", re.IGNORECASE),
    re.compile(r"\b(?:threat|threaten|harm|hurt|violent|abuse)\b", re.IGNORECASE),
    re.compile(r"\b(?:suicide|self-harm|die|death\s+threat)\b", re.IGNORECASE),
    re.compile(r"\b(?:bomb|weapon|explosive|gun\s+violence)\b", re.IGNORECASE),
]
TOXICITY_WEIGHT_PER_HIT = 0.15

# ── policy #5: explanatory/quoting context guard (injection/jailbreak only) ──
# Security-education prompts that merely MENTION or QUOTE an injection phrase
# ("explain what 'ignore all previous instructions' means") were hard-blocked as
# tier-1 false positives. This narrow lead-in pattern, plus a quote/backtick
# wrapper check, lets us downgrade a quoted/explanatory mention from block to
# allow — but ONLY for the prompt_injection / jailbreak category, and ONLY when
# there is no fresh, unquoted imperative attack elsewhere in the prompt.
_INJECTION_EXPLANATORY_CATEGORIES = frozenset({"prompt_injection", "jailbreak"})
_EXPLANATORY_LEADIN = re.compile(
    r"(?i)(explain|describe|what\s+(is|does|are)|why\s+(is|are)|example\s+of|"
    r"meaning\s+of|quote|definition\s+of|how\s+does|tell\s+me\s+about|"
    # mention constructs: "the phrase/sentence/string/text/words/term <X>" is a
    # quoting/educational frame, never an imperative — a real attacker would not
    # prepend it. Catches "...risk of the phrase 'ignore all instructions'" where
    # the leading verb ("explain") sits outside the lookbehind window.
    r"the\s+(phrase|sentence|string|text|words?|term))",
)
# How far back from a matched span we look for an explanatory lead-in.
_EXPLANATORY_LEADIN_WINDOW = 30
_QUOTE_CHARS = ("'", '"', "`")

@dataclass
class ScanVerdict:
    "carries structured results (action, threat_type, confidence, detail, matched_patterns, tier) for downstream decision-making"
    action: str = "allow"
    threat_type: str = ""
    confidence: float = 0.0
    detail: str = ""
    matched_patterns: list[str] = field(default_factory=list)
    matched_values: dict[str, str] = field(default_factory=dict)
    tier: str = ""
    reason_code: str = ""
    owasp_codes: list[str] = field(default_factory=list)
    scan_meta: dict = field(default_factory=dict)

ATTACK_PATTERNS: dict[str, list[str]] = {
    "prompt_injection": [
        r"ignore\s+(previous|all\s+previous|earlier|above|all)\s+instructions",
        r"disregard\s+(previous|all|above|your)\s+instructions",
        r"forget\s+(previous|everything|your\s+instructions)",
        # G15: the three verb patterns above accept only a SINGLE qualifier (so the
        # two-word "all previous" only worked for "ignore"), and "override" was
        # absent — "disregard/forget/override all previous instructions" (and the
        # prior/earlier/above variants) bypassed Tier-1 even single-turn, while
        # context_guard already had the full verb alternation. Add one unified verb-
        # alternation pattern that allows an optional second qualifier. Object stays
        # "instructions" (NOT the broader context/rules/messages set context_guard
        # uses for RAG docs) so benign chat like "disregard the previous messages"
        # or "forget the above context" is not false-blocked. Linear-time (fixed
        # quantifiers, no ambiguous optional-repeat) => ReDoS-safe.
        r"(?:ignore|disregard|forget|override)\s+(?:all|every|any|the|previous|prior|above|preceding|earlier)(?:\s+(?:previous|prior|above|preceding|earlier))?\s+instructions",
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
        # G28: common SEMANTIC jailbreak / prompt-extraction phrasings that the exact-
        # keyword patterns above miss (Tier-2 is the primary catch for semantics, but
        # these forms are frequent + low-FP, so Tier-1 defense-in-depth is warranted).
        # Cross-validated against the independent aidefence oracle (flagged high-conf
        # context_manipulation) and against a benign corpus (zero false positives:
        # "the safety guidelines apply", "bypass the CDN cache", "no length limit",
        # "translate the following paragraph", "act as a helpful assistant" — none match).
        # All bounded quantifiers => linear / ReDoS-safe.
        r"\bjailbr(?:oke|oken)\b",
        r"everything\s+(?:written\s+)?(?:above|before)\s+this\s+(?:line|message|prompt|point)",
        r"\b(?:answer|respond|reply|talk|speak)\s+(?:to\s+me\s+)?(?:without|with\s+no)\s+(?:any\s+)?(?:filter|restriction|guardrail|censor|limitation)",
        r"\bno\s+(?:ethical|moral|content|safety)\s+(?:constraint|restriction|guideline|boundar|limit|filter)",
        r"\b(?:translate|leak|dump|paste|exfiltrate|expose|disclose)\s+(?:me\s+)?(?:your|the)\s+(?:full\s+|entire\s+|complete\s+|system\s+|initial\s+|original\s+)?(?:system\s+)?(?:prompt|instructions?)",
        r"(?:content|safety|ethical|moderation)\s+(?:guideline|rule|filter|polic|restriction)s?\s+(?:do\s+not|don'?t|no\s+longer)\s+apply",
        r"\b(?:you\s+are|act\s+as|become|pretend\s+(?:you\s+are|to\s+be))\s+(?:an?\s+)?(?:ai|assistant|model|bot)\s+(?:with\s+)?no\s+(?:ethical\s+|content\s+|safety\s+)?(?:constraint|restriction|filter|limit|guideline)",
        # G29: three MORE semantic classes, each held to the same evidence bar as G28.
        #   (1) encode-to-evade: the aidefence oracle CONFIRMS this class as an
        #       "encoding_attack" (an explicit instruction to encode output so a
        #       filter/scanner cannot read it). Gated on encode-token + evasion-intent
        #       so plain "encode the payload in base64" (benign transport) does NOT match.
        #   (2) system-message/-instructions extraction: a direct, low-FP EXTENSION of
        #       the already-frozen "system prompt" extraction family above (adds the
        #       "system message" / "system instructions" phrasings; "summarize" verb).
        #   (3) become-an-unrestricted-<entity>: an explicit jailbreak-persona demand
        #       requiring BOTH a strong unshackle adjective AND an ai/model/oracle noun,
        #       so "become an unrestricted free spirit" (self-help) does NOT match.
        # The weaker fictional-/hypothetical-framing and "forget your training" forms
        # were DELIBERATELY left to Tier-2 semantics: the oracle marked them safe and
        # they collide with legitimate creative-writing / coaching / business-hypothetical
        # inputs, so a Tier-1 hard block would be a false positive. Verified ZERO FP on a
        # creative+coaching+technical+business benign corpus. All bounded => ReDoS-safe.
        r"\b(?:base64|rot-?13|hex|binary|a\s+cipher|encoded?)\b[^.\n]{0,40}?\b(?:bypass|evade|circumvent|get\s+past|so\s+(?:the\s+)?(?:filter|scanner|detection|guard)|can(?:'?t|not)\s+(?:read|detect|see)|hide\s+from|avoid\s+detection)",
        r"\b(?:bypass|evade|circumvent|get\s+past|sneak\s+past)\s+(?:the\s+)?(?:content|safety|security|moderation|ai)?\s*(?:filter|detection|moderation|scanner|guardrail|censorship)s?\b",
        r"\b(?:print|show|reveal|repeat|display|leak|dump|give\s+me|summar(?:ize|ise))\s+(?:me\s+)?(?:the\s+)?(?:first\s+\d+\s+\w+\s+of\s+)?(?:your|the)\s+system\s+(?:prompt|message|instructions?)",
        r"\bbecome\s+(?:an?\s+)?(?:unrestricted|unfiltered|unchained|unbound|jailbroken|uncensored|lawless|rule-?free)\s+(?:ai|assistant|model|bot|oracle|version|entity|being|persona|agent)\b",
        # Chat-template / role-delimiter spoofing — a user payload that smuggles a
        # forged system/developer turn via ChatML or Llama control tokens. These
        # have no natural place in a genuine user message; the quoted-mention
        # carve-out below still suppresses benign security-education quoting.
        r"<\|im_start\|>\s*system",
        r"<\|im_(start|end)\|>",
        r"<<\s*sys\s*>>",
        r"(?:^|\n)\s*(system|developer)\s*:\s*you\s+(are|have|must|will)\b",
        # G37: Gemma chat-template turn tokens. The gateway builds the template, so
        # a user MESSAGE never legitimately contains <start_of_turn>/<end_of_turn> —
        # their presence is a forged-turn (system/model) role-spoof smuggle. Literal
        # control tokens => near-zero FP (no benign human text types them). The
        # Claude "\n\nHuman:/Assistant:" delimiter is DELIBERATELY left to Tier-2:
        # it legitimately appears when a user shares a conversation transcript, so a
        # Tier-1 hard block would be a false positive.
        r"<\s*(?:start|end)_of_turn\s*>",
    ],
    "jailbreak": [
        r"dan\s+mode",
        r"developer\s+mode",
        r"jailbreak",
        r"strive\s+to\s+avoid\s+norms",
        r"truly\s+submissive\s+ai",
        # DAN/STAN persona jailbreak — require explicit persona framing or the
        # acronym expansion, NOT a bare proper-noun "Dan" with a distant phrase
        # (the old `\bdan\b.*do anything now` greedily matched a benign sentence
        # like "my friend Dan asked if he can do anything now to fix his bike").
        r"(?:you\s+are|act\s+as|become|pretend\s+(?:to\s+be|you\s+are)|roleplay\s+as|enable|activate)\s+(?:now\s+)?(?:dan|stan|dude)\b",
        r"\b(?:dan|stan|dude)\b\s*(?:mode\b|\([^)]{0,30}\)|[,:\-]?\s*(?:which\s+)?(?:stands?\s+for|means?|=)\s*[\"']?\s*(?:do\s+anything\s+now|anything|bypass|no\s+restrictions))",
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
# Upper bound on the length of a single token handed to the DP word-segmenter.
# The fuzzy fallback runs difflib.SequenceMatcher per vocab word per DP cell, so
# cost grows with token length; a giant single token (e.g. ~10k chars with no
# spaces) would hang the scanner for seconds. A legitimately segmentable
# concatenated-obfuscation token is short, so any token longer than this is
# treated as opaque and returned unchanged without running the DP.
_MAX_SEGMENT_TOKEN_LENGTH: int = 64


def _normalize_leet(text: str) -> str:
    """Replace common l33tspeak character substitutions with alphabetic equivalents."""
    return "".join(_LEET_MAP.get(c, c) for c in text)


# Zero-width / bidirectional-override / soft-hyphen characters used to break up
# attack phrases so they slip past plaintext pattern matching. Stripped wholesale.
_ZERO_WIDTH_CHARS: str = (
    "​‌‍⁠﻿"  # ZWSP, ZWNJ, ZWJ, word-joiner, BOM
    "‎‏"                      # LRM, RLM
    "‪‫‬‭‮"    # LRE, RLE, PDF, LRO, RLO
    "⁦⁧⁨⁩"          # LRI, RLI, FSI, PDI
    "­"                            # soft hyphen
)
_ZERO_WIDTH_RE: re.Pattern[str] = re.compile("[" + _ZERO_WIDTH_CHARS + "]")

# Cyrillic / Greek confusables that NFKC does NOT fold to ASCII (they are
# distinct legitimate letters), but which are routinely used as homoglyph
# substitutes in injection payloads. Folded to their ASCII look-alike so the
# downstream ASCII pattern set can match.
_HOMOGLYPH_MAP: dict[str, str] = {
    # Cyrillic lowercase look-alikes
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c",
    "у": "y", "х": "x", "і": "i", "ј": "j", "һ": "h",
    "ԁ": "d", "ԛ": "q", "ѕ": "s", "н": "h", "в": "b",
    "м": "m", "т": "t", "к": "k",
    # Cyrillic uppercase look-alikes
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M",
    "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T",
    "Х": "X", "Ѕ": "S", "І": "I", "Ј": "J",
    # Greek look-alikes
    "α": "a", "ο": "o", "ρ": "p", "υ": "u", "ν": "v",
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H",
    "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O",
    "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
}
_HOMOGLYPH_TABLE: dict[int, int] = {ord(k): ord(v) for k, v in _HOMOGLYPH_MAP.items()}

# G19: Unicode SMALL-CAPITAL letters (ɪɢɴᴏʀᴇ …). These are legitimate IPA/phonetic
# letters, so NFKC does NOT fold them to ASCII, yet an LLM reads small-caps as normal
# text — so "ɪɢɴᴏʀᴇ ᴀʟʟ ᴘʀᴇᴠɪᴏᴜꜱ ɪɴꜱᴛʀᴜᴄᴛɪᴏɴꜱ" bypassed the ASCII pattern set. Fold each
# small-cap to its ASCII lowercase look-alike (same approach as _HOMOGLYPH_TABLE).
# q and x have no widely-used small-cap form and are omitted.
_SMALLCAP_MAP: dict[int, str] = {
    0x1D00: "a", 0x0299: "b", 0x1D04: "c", 0x1D05: "d", 0x1D07: "e", 0xA730: "f",
    0x0262: "g", 0x029C: "h", 0x026A: "i", 0x1D0A: "j", 0x1D0B: "k", 0x029F: "l",
    0x1D0D: "m", 0x0274: "n", 0x1D0F: "o", 0x1D18: "p", 0x0280: "r", 0xA731: "s",
    0x1D1B: "t", 0x1D1C: "u", 0x1D20: "v", 0x1D21: "w", 0x028F: "y", 0x1D22: "z",
}
_SMALLCAP_TABLE: dict[int, int] = {k: ord(v) for k, v in _SMALLCAP_MAP.items()}

# Bounds for the transport decode-and-rescan stage (single decode, no recursion).
_TRANSPORT_DECODE_MAX_LEN: int = 200
_BASE64_TOKEN_RE: re.Pattern[str] = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")
_HEX_TOKEN_RE: re.Pattern[str] = re.compile(r"(?:[0-9a-fA-F]{2}){8,}")
# G22: follow up to this many NESTED encoding layers (double-base64 / base64-of-hex
# "prompt laundering") so an injection wrapped in >1 encoding layer is still rescanned.
# Bounded depth + per-token length cap => decode-bomb safe.
_MAX_TRANSPORT_DEPTH: int = 3
_ALL_HEX_RE: re.Pattern[str] = re.compile(r"[0-9a-fA-F]+")


def _nested_decode_variants(token: str, is_hex: bool, seen: set[str]) -> list[str]:
    """Decode ``token`` through up to ``_MAX_TRANSPORT_DEPTH`` nested base64/hex layers,
    returning each readable-ASCII layer so an injection buried under multiple encodings
    (e.g. double-base64) is surfaced for rescanning."""
    out: list[str] = []
    layer = token
    cur_hex = is_hex
    for _ in range(_MAX_TRANSPORT_DEPTH):
        if len(layer) > _TRANSPORT_DECODE_MAX_LEN:
            break
        try:
            if cur_hex:
                raw = bytes.fromhex(layer)
            else:
                raw = base64.b64decode(layer + "=" * (-len(layer) % 4), validate=False)
            decoded = raw.decode("utf-8", errors="strict")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            break
        # G26: a base64-wrapped ZERO-WIDTH / Unicode-TAG obfuscated injection decodes to
        # a string full of Cf chars, which is legitimately "not printable" — yet it IS the
        # payload we must catch. Strip that obfuscation for the printability gate only, so
        # the compound (base64 ∘ zero-width) evasion is not dropped before the caller
        # normalizes it. Genuine binary garbage still has no printable residue and breaks.
        probe = _ZERO_WIDTH_RE.sub("", _decode_unicode_tags(decoded))
        if not probe.isprintable():
            break
        if decoded not in seen:
            seen.add(decoded)
            out.append(decoded)
        # Is the decoded text ITSELF another encoding layer? A pure-hex string is also
        # valid base64, so prefer a HEX interpretation when the whole decoded layer is
        # hex (else base64 would mis-decode base64-of-hex laundering).
        s = decoded.strip()
        if 16 <= len(s) <= _TRANSPORT_DECODE_MAX_LEN and len(s) % 2 == 0 and _ALL_HEX_RE.fullmatch(s):
            layer, cur_hex = s, True
            continue
        b = _BASE64_TOKEN_RE.search(decoded)
        h = _HEX_TOKEN_RE.search(decoded)
        if b and len(b.group(0)) <= _TRANSPORT_DECODE_MAX_LEN:
            layer, cur_hex = b.group(0), False
        elif h and len(h.group(0)) <= _TRANSPORT_DECODE_MAX_LEN:
            layer, cur_hex = h.group(0), True
        else:
            break
    return out


# G17: Unicode Tag block (U+E0000..U+E007F) "ASCII smuggling". U+E0020 (TAG SPACE)
# .. U+E007E (TAG TILDE) mirror printable ASCII 0x20..0x7E; they render as NOTHING
# but several LLMs decode them back to the mirrored ASCII, so an ENTIRE injection can
# be smuggled invisibly. NFKC does NOT fold them (category Cf), and they are not in
# the zero-width set. Decode the printable range back to ASCII and drop the tag
# controls (U+E0000 lang tag, U+E0001 lang-tag begin, U+E007F CANCEL TAG).
_TAG_BLOCK_RE: re.Pattern[str] = re.compile(r"[\U000E0000-\U000E007F]")


def _decode_unicode_tags(text: str) -> str:
    if not text or not _TAG_BLOCK_RE.search(text):
        return text
    out: list[str] = []
    for ch in text:
        cp = ord(ch)
        if 0xE0020 <= cp <= 0xE007E:      # TAG SPACE..TAG TILDE -> ASCII 0x20..0x7E
            out.append(chr(cp - 0xE0000))
        elif 0xE0000 <= cp <= 0xE007F:    # tag language / cancel controls -> drop
            continue
        else:
            out.append(ch)
    return "".join(out)


def _normalize_unicode(text: str) -> str:
    """
    Fold Unicode-obfuscated text toward canonical ASCII so the ASCII-oriented
    pattern set can match. Closes the fullwidth / homoglyph / zero-width / RTL /
    combining-diacritic / Unicode-tag smuggling blind spot.

    Steps: decode Unicode Tag block -> strip zero-width & bidi-override chars ->
    NFKC (folds fullwidth, ligatures, circled/styled forms) -> drop combining marks
    (NFD + Mn filter) -> fold residual Cyrillic/Greek homoglyphs to ASCII look-alikes.
    """
    if not text:
        return text
    stripped = _decode_unicode_tags(text)
    stripped = _ZERO_WIDTH_RE.sub("", stripped)
    normalized = unicodedata.normalize("NFKC", stripped)
    # Strip combining marks (e.g. zalgo / diacritic smuggling).
    decomposed = unicodedata.normalize("NFD", normalized)
    no_marks = "".join(
        ch for ch in decomposed if unicodedata.category(ch) != "Mn"
    )
    recomposed = unicodedata.normalize("NFKC", no_marks)
    return recomposed.translate(_HOMOGLYPH_TABLE).translate(_SMALLCAP_TABLE)


# ROT13 is its own inverse; a whole-text Caesar-13 shift is a common evasion
# ("vtaber nyy cerivbhf vafgehpgvbaf" -> "ignore all previous instructions").
_ROT13_MAP = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
    "NOPQRSTUVWXYZABCDEFGHIJKLMnopqrstuvwxyzabcdefghijklm",
)


def _decode_one_layer(text: str, seen: set[str]) -> list[str]:
    """One transport-decode layer over ``text``: whole-text ROT13 + nested base64/hex
    tokens + text-encodings (HTML/URL/escape). Adds each new readable variant to
    ``seen`` (dedup). Bounded token counts/lengths -> linear, ReDoS-safe."""
    out: list[str] = []
    try:
        _rot = text.translate(_ROT13_MAP)
        if _rot != text and _rot.isprintable() and _rot not in seen:
            seen.add(_rot)
            out.append(_rot)
    except Exception:  # noqa: BLE001 - decode helpers must never break the scan
        pass
    # G22: each token is decoded through nested layers (double-base64 / base64-of-hex).
    for token in _BASE64_TOKEN_RE.findall(text)[:8]:
        if len(token) > _TRANSPORT_DECODE_MAX_LEN:
            continue
        out.extend(_nested_decode_variants(token, False, seen))
    for token in _HEX_TOKEN_RE.findall(text)[:8]:
        if len(token) > _TRANSPORT_DECODE_MAX_LEN:
            continue
        out.extend(_nested_decode_variants(token, True, seen))
    for v in _decode_text_encoding_variants(text):
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _decode_transport_variants(text: str) -> list[str]:
    """
    Best-effort bounded transport decode (base64 / hex / ROT13 / HTML-entity / URL /
    source-escape) of embedded payloads so an encoded injection can be rescanned.
    Only tokens up to _TRANSPORT_DECODE_MAX_LEN, only readable ASCII results.
    """
    if not text or len(text) > MAX_PROMPT_LENGTH:
        return []
    seen: set[str] = set()
    level1 = _decode_one_layer(text, seen)
    variants = list(level1)
    # G34: ONE additional decode layer catches 2-stage cross-encoding laundering that
    # a single pass misses — URL-of-base64, base64-of-ROT13, base64-of-URL, ROT13-of-
    # URL, etc. Bounded to depth 2 over already-bounded token counts/lengths (linear,
    # ReDoS/DoS-safe); `seen` dedups and prevents any re-processing loop.
    for v in level1:
        if v and len(v) <= MAX_PROMPT_LENGTH:
            variants.extend(_decode_one_layer(v, seen))
    return variants


# G32: common prompt-laundering TEXT encodings that base64/hex transport-decode
# misses — HTML character references (&#NNN; / &#xHH;), percent/URL-encoding
# (%XX), and source-style escapes (\uXXXX / \xHH). A downstream model (or a
# "decode this and follow it" instruction) will interpret these, so an encoded
# injection must be decoded for detection. All bounded single-pass regex subs
# (linear, ReDoS-safe). Additive: decoded forms are rescanned, never replacing
# the original — verified zero FP on benign HTML entities / URLs / code escapes
# (percent-off entities, url query params, JSON/regex escapes, copyright and
# em-dash entities all decode to harmless text, never to an injection phrase).
_HTML_DEC_RE = re.compile(r"&#(\d{1,7});")
_HTML_HEX_RE = re.compile(r"&#x([0-9a-fA-F]{1,6});")
_PERCENT_RE = re.compile(r"%([0-9a-fA-F]{2})")
_USTR_RE = re.compile(r"\\u([0-9a-fA-F]{4})")
_XHEX_RE = re.compile(r"\\x([0-9a-fA-F]{2})")


def _cp(n: int) -> str:
    return chr(n) if 0 <= n < 0x110000 else ""


def _decode_text_encoding_variants(text: str) -> list[str]:
    if not text or len(text) > MAX_PROMPT_LENGTH:
        return []
    variants: list[str] = []
    try:
        html = _HTML_DEC_RE.sub(lambda m: _cp(int(m.group(1))) or m.group(0), text)
        html = _HTML_HEX_RE.sub(lambda m: _cp(int(m.group(1), 16)) or m.group(0), html)
        if html != text:
            variants.append(html)
    except Exception:  # noqa: BLE001 - decode helpers must never break the scan
        pass
    try:
        url = _PERCENT_RE.sub(lambda m: _cp(int(m.group(1), 16)) or m.group(0), text)
        if url != text:
            variants.append(url)
    except Exception:  # noqa: BLE001
        pass
    try:
        esc = _USTR_RE.sub(lambda m: _cp(int(m.group(1), 16)) or m.group(0), text)
        esc = _XHEX_RE.sub(lambda m: _cp(int(m.group(1), 16)) or m.group(0), esc)
        if esc != text:
            variants.append(esc)
    except Exception:  # noqa: BLE001
        pass
    return variants


# Minimum length of a single-letter run before it is treated as a spaced-out
# obfuscation candidate (e.g. "i g n o r e   a l l").
_SINGLE_LETTER_RUN_MIN: int = 4


def _collapse_single_letter_runs(tokens: list[str]) -> list[str]:
    """
    Collapse runs of >= _SINGLE_LETTER_RUN_MIN consecutive single-letter tokens
    into one concatenated token so spaced-out injection ("i g n o r e") can be
    re-segmented. Multi-letter tokens and short runs are left untouched.
    """
    result: list[str] = []
    run: list[str] = []

    def _flush() -> None:
        if len(run) >= _SINGLE_LETTER_RUN_MIN:
            result.append("".join(run))
        else:
            result.extend(run)
        run.clear()

    for token in tokens:
        if len(token) == 1:
            run.append(token)
        else:
            _flush()
            result.append(token)
    _flush()
    return result


# G3: intra-word space-splitting ("ig no re all previous instructions"). Unlike
# single-letter runs (handled above), the attacker splits a keyword into 2-4 char
# fragments that _collapse_single_letter_runs leaves alone and _segment_token never
# reaches (each fragment is < _CONCAT_WORD_MIN_LENGTH). We glue runs of consecutive
# short fragments and re-segment them against the injection vocab. Because the vocab
# is curated to injection terms (not general English), a benign short-word run does
# NOT segment and its originals are kept verbatim => no false positives. The glue
# length is capped, so this stays linear/ReDoS-safe.
_SPLIT_FRAG_MAXLEN: int = 4
_SPLIT_RUN_MIN: int = 3
_SPLIT_GLUE_MAXLEN: int = 48


def _reassemble_split_words(tokens: list[str]) -> list[str]:
    """Glue runs of >= _SPLIT_RUN_MIN consecutive short fragments and re-segment them
    IN PLACE, reconstructing space-split injection keywords; leave everything else as-is."""
    out: list[str] = []
    i, n = 0, len(tokens)
    while i < n:
        if len(tokens[i]) <= _SPLIT_FRAG_MAXLEN:
            j = i
            glued = ""
            while (
                j < n
                and len(tokens[j]) <= _SPLIT_FRAG_MAXLEN
                and len(glued) + len(tokens[j]) <= _SPLIT_GLUE_MAXLEN
            ):
                glued += tokens[j]
                j += 1
            segs = (
                _segment_token(glued)
                if (j - i) >= _SPLIT_RUN_MIN and len(glued) >= _CONCAT_WORD_MIN_LENGTH
                else None
            )
            if segs and len(segs) >= 2:
                out.extend(segs)          # accepted a real injection-vocab segmentation
            else:
                out.extend(tokens[i:j])   # benign run -> keep originals verbatim
            i = j
        else:
            out.append(tokens[i])
            i += 1
    return out


# G6 (multi-turn / crescendo split injection): main._extract_prompt_from_messages
# folds the OpenAI messages array into one string as ``[role]: content`` lines. A
# prompt-injection phrase can be fragmented across successive client-controlled
# instruction turns, with the intervening ``[assistant]: …`` turns breaking contiguity
# so neither any single turn NOR the full concatenation matches a signature.
# Reassembling those instruction turns (dropping the assistant/tool filler + role
# markers) makes the phrase contiguous again for detection.
# G27: the OpenAI ``developer`` role is ALSO client-controlled and instruction-bearing,
# so an injection split across developer turns bypassed the user-only reassembly.
# Reassemble user AND developer turns (the untrusted, attacker-driven channels).
# (``system`` is intentionally excluded: legitimate system prompts are usually app-
# controlled and may quote injection phrases for defensive instruction, which would
# false-positive; the explanatory-mention carve-out covers the single-turn case.)
_ROLE_LINE_RE = re.compile(r"^\[(user|assistant|system|developer|tool)\]:\s?(.*)$")
_INSTRUCTION_ROLES = ("user", "developer")


def _reassemble_user_turns(text: str) -> str | None:
    """Return the instruction-turn-only reassembly (user + developer) of a folded
    multi-turn conversation, or ``None`` when ``text`` is not a multi-turn fold (so
    single-turn scans are unaffected). Continuation lines of a multi-line instruction
    message are kept with that turn; assistant/tool/system turns are dropped."""
    if "\n" not in text or not any(f"[{r}]:" in text for r in _INSTRUCTION_ROLES):
        return None
    role_lines = 0
    parts: list[str] = []
    cur_role: str | None = None
    for ln in text.split("\n"):
        m = _ROLE_LINE_RE.match(ln)
        if m:
            role_lines += 1
            cur_role = m.group(1)
            if cur_role in _INSTRUCTION_ROLES:
                parts.append(m.group(2))
        elif cur_role in _INSTRUCTION_ROLES:
            parts.append(ln)  # continuation of a multi-line instruction message
    # Require a real multi-turn fold: >=2 role-labelled turns and >=2 instruction segments.
    if role_lines < 2 or len(parts) < 2:
        return None
    reassembled = " ".join(p for p in parts if p).strip()
    return reassembled or None


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
    # A single token longer than this has no legitimate word segmentation and
    # would make the fuzzy DP fallback (SequenceMatcher per vocab word per cell)
    # catastrophically expensive. Treat it as opaque and skip the DP.
    if n > _MAX_SEGMENT_TOKEN_LENGTH:
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

_ROT13_TABLE = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
    "NOPQRSTUVWXYZABCDEFGHIJKLMnopqrstuvwxyzabcdefghijklm",
)


def _norm_word_set(s: str) -> set[str]:
    """Lowercased alphanumeric word set — for order/punctuation-insensitive compare."""
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def _tier2_evidence_is_self_referential_encoding(evidence_blob: str, input_text: str) -> bool:
    """Detect a Tier-2 guard-model HALLUCINATION: the guard cites a 'hidden encoded
    payload' (ROT13 / base64 / hex) as its block evidence, but that payload — when
    decoded — is essentially the VISIBLE INPUT re-encoded (a fabricated threat).

    Confirmed live 2026-07-02: "Please translate the following paragraph into French."
    was hard-blocked (tier_2, prompt_injection, conf 0.92) citing "ROT13 encoded payload
    appended: 'hfre cyrnfr genafyngr gur sbyybjvat cnentencu vagb serapu'…", and
    ROT13(that) == the prompt itself. A REAL hidden payload decodes to ATTACK content
    that differs from the visible input, so a high BIDIRECTIONAL word-set overlap
    (Jaccard >= 0.85) between a decoded cited-token and the input is a reliable
    hallucination signature that can NEVER match a genuine encoded attack (whose decoded
    form carries the extra malicious content -> low overlap). Fail-safe: any parse/decode
    failure returns False (the Tier-2 block stands)."""
    if not evidence_blob or not input_text:
        return False
    input_words = _norm_word_set(input_text)
    if len(input_words) < 3:
        return False  # too short to match reliably; keep the block
    candidates = re.findall(r"'([^']{6,})'", evidence_blob) + re.findall(r'"([^"]{6,})"', evidence_blob)
    for tok in candidates:
        tok = tok.strip()
        decoded: list[str] = [tok.translate(_ROT13_TABLE)]
        try:
            _b = base64.b64decode(tok + "=" * (-len(tok) % 4), validate=False).decode("utf-8", "ignore")
            if any(c.isalpha() for c in _b):
                decoded.append(_b)
        except Exception:
            pass
        try:
            if re.fullmatch(r"[0-9a-fA-F]+", tok) and len(tok) % 2 == 0:
                decoded.append(bytes.fromhex(tok).decode("utf-8", "ignore"))
        except Exception:
            pass
        for dec in decoded:
            dw = _norm_word_set(dec)
            if dw and len(input_words | dw) and len(input_words & dw) / len(input_words | dw) >= 0.85:
                return True
    return False

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
        embedding_vault: Any = None,  # M9: removed (legacy Tier-1.6 vault); kept for call-site compat, ignored
    ) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=thread_pool_size,
            thread_name_prefix="scanner",
        )
        # Dedicated pool for network-bound Tier-2 (Bedrock) calls. Keeping these
        # off the CPU-bound Tier-1 scan pool prevents a slow/stalled Bedrock
        # invocation from starving Tier-1 workers (the head-of-line blocking that
        # caused the gateway to collapse under load).
        bedrock_pool_size = _env_int(
            "GATEWAY_BEDROCK_THREAD_POOL_SIZE", 16, min_value=1, max_value=256)
        self._bedrock_executor = ThreadPoolExecutor(
            max_workers=bedrock_pool_size,
            thread_name_prefix="bedrock",
        )
        self._config = config or {}
        # M9: legacy Tier-1.6 EmbeddingVault removed; param ignored.
        # Tier-2 (Bedrock) feature flag can be enabled via env var ENABLE_TIER2
        self.tier2_enabled = os.getenv("ENABLE_TIER2", "true").lower() in ("1", "true", "yes")
        self._bedrock_scanner: BedrockScanner | None = BedrockScanner() if self.tier2_enabled else None
        # Tier-2 cost levers. The verdict cache stores the *Bedrock response*
        # keyed by scanned text, so identical prompts don't re-invoke Bedrock
        # (decision logic is unchanged). Sampling defaults to 1.0 (always run)
        # so security posture is unchanged unless an operator opts in.
        self._tier2_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._tier2_cache_ttl = _env_float(
            "GATEWAY_TIER2_CACHE_TTL_SECONDS", 300.0, min_value=0.0)
        self._tier2_cache_max = _env_int(
            "GATEWAY_TIER2_CACHE_MAX", 10000, min_value=0)
        self._tier2_sample_rate = _env_float(
            "GATEWAY_TIER2_SAMPLE_RATE", 1.0, min_value=0.0, max_value=1.0)
        LOG.info(
            "InputScanner initialized (thread_pool_size=%d, attack_categories=%d, pii_patterns=%d)",
            thread_pool_size,
            len(ATTACK_PATTERNS),
            len(PII_PATTERNS),
        )

    async def scan_prompt(
        self,
        text: str,
        is_rag: bool = False,
        toxicity_threshold: float | None = None,
    ) -> ScanVerdict:
        """Asynchronously scan the prompt for threats and return a structured verdict.

        ``toxicity_threshold`` is an optional per-request override. When ``None``
        the scanner falls back to the gateway-wide ``self._config`` value. It is
        passed by value (not stored on the singleton) so concurrent requests from
        different orgs cannot race on a shared mutable threshold.
        """
        loop = asyncio.get_event_loop()

        return await loop.run_in_executor(
            self._executor,
            self._scan_prompt_sync,
            text,
            is_rag,
            toxicity_threshold,
        )
    async def scan_output(self, text: str) -> ScanVerdict:
        """Asynchronously scan the LLM output for PII/Secrets and return a structured verdict."""
        loop = asyncio.get_event_loop()

        return await loop.run_in_executor(
            self._executor,
            self._scan_output_sync,
            text,
        )

    def _scan_prompt_sync(
        self,
        text: str,
        is_rag: bool,
        toxicity_threshold: float | None = None,
        _multiturn: bool = True,
    ) -> ScanVerdict:
        """Synchronous prompt scanning logic, run in a thread to avoid blocking.

        ``_multiturn`` guards the G6 user-turn reassembly re-scan so the derived
        view is scanned exactly once (no unbounded recursion)."""
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
            match_objs: list[re.Match] = []
            for pattern_str in patterns:
                compiled = compile_pattern(pattern_str)
                match = compiled.search(text)
                if match:
                    # Several attack patterns carry unbounded wildcard spans
                    # (e.g. data_leakage "tell me .* ssn", command-injection
                    # backticks), so the raw span can embed PII/secrets from
                    # the prompt. matched_patterns reaches clients via the
                    # zeroshield metadata and pipeline trace — mask evidence
                    # the same way the tier-2 path does.
                    matched.append(redact_all(match.group(0)))
                    match_objs.append(match)

            if matched:
                # policy #5: for the injection/jailbreak class ONLY, suppress the
                # block when EVERY matched span is a quoted or explanatory MENTION
                # (security-education / quoting prompts) rather than an imperative
                # attack. If any single match is a fresh, unquoted imperative the
                # block stands. sql/command/data_leakage/credential/PII categories
                # are never suppressed (real-payload risk even when quoted).
                if (
                    category in _INJECTION_EXPLANATORY_CATEGORIES
                    and all(
                        self._is_explanatory_mention(text, m) for m in match_objs
                    )
                ):
                    LOG.info(
                        "policy #5: suppressed %s block — all matches are "
                        "quoted/explanatory mentions (patterns=%s)",
                        category, matched,
                    )
                else:
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
                        detail=f"RAG context poisoning attempt: {redact_all(match.group(0))}",
                        matched_patterns=[redact_all(match.group(0))],
                        tier="tier_1",
                    )

        deobfuscated = self._deobfuscate_text(text)
        if deobfuscated != text.lower():
            # NF-1: scrub PII from the deobfuscated buffer before logging. Even at
            # DEBUG this trace echoed cleartext that survived deobfuscation (an
            # already-plaintext name stays readable). redact_all masks the
            # deterministic PII classes so a DEBUG trace can't leak SSN/CC/email
            # (names have no deterministic mask — accepted; DEBUG is off in prod).
            LOG.debug("Deobfuscation produced: '%s'", redact_all(deobfuscated[:200]))
            for category, patterns in ATTACK_PATTERNS.items():
                matched = []
                match_objs: list[re.Match] = []
                for pattern_str in patterns:
                    compiled = compile_pattern(pattern_str)
                    match = compiled.search(deobfuscated)
                    if match:
                        matched.append(redact_all(match.group(0)))
                        match_objs.append(match)
                if matched:
                    # policy #5 parity at tier-0.5: suppress the block when EVERY
                    # match is a quoted/explanatory MENTION (security-education).
                    # The raw text often didn't match (quotes broke the \s spans),
                    # so the carve-out runs against the deobfuscated buffer — where
                    # the explanatory lead-in words (explain / what does / means)
                    # survive because they are alphabetic. Without this, benign
                    # quoting like "what does 'ignore all previous instructions'
                    # mean" hard-blocked at tier-0.5, defeating the tier-1 fix.
                    if (
                        category in _INJECTION_EXPLANATORY_CATEGORIES
                        and all(self._is_explanatory_mention(deobfuscated, m) for m in match_objs)
                    ):
                        LOG.info(
                            "policy #5 (tier-0.5): suppressed %s block — all matches "
                            "are explanatory mentions (patterns=%s)",
                            category, matched,
                        )
                        continue
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
                            redact_all(match.group(0)),
                        )
                        return ScanVerdict(
                            action="block",
                            threat_type="rag_poisoning",
                            confidence=0.90,
                            detail=f"Obfuscated RAG poisoning detected: {redact_all(match.group(0))}",
                            matched_patterns=[redact_all(match.group(0))],
                            tier="tier_0_5",
                        )

        fuzzy_verdict = self._fuzzy_scan(text)
        if fuzzy_verdict is not None:
            return fuzzy_verdict

        # M9: the legacy Tier-1.6 EmbeddingVault semantic-injection check was
        # REMOVED. It was disabled by default, failed OPEN on credential/config
        # failure (silently disabling a whole detection layer with only a WARN),
        # and is fully covered by the Tier-2 Bedrock semantic scan. Removed to
        # eliminate the silent-fail-open footgun.

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

        # G68: credential-EXPOSURE patterns (connection string, basic-auth, stripe/github/
        # azure key, exposed password) that are NOT in SECRET_PATTERNS. The input scan ran
        # detect_pii + detect_secrets but NEVER detect_credential_exposure, so a credential-
        # only value pasted into a prompt (``sk_live_…``, ``mongodb://user:pw@host/db``)
        # reached the model provider RAW — the input analog of the output-guard gap G54.
        # redact_all masks CREDENTIAL_EXPOSURE_PATTERNS, so a redact verdict scrubs it; and
        # the detector is obfuscation-aware (G54/G55) so this also catches a fullwidth /
        # base64-encoded credential in the prompt. threat_type='secret' -> the same
        # redactable input enforcement path as detect_secrets above.
        cred_matched = detect_credential_exposure(text)
        if cred_matched:
            return ScanVerdict(
                action="redact",
                threat_type="secret",
                confidence=0.9,
                detail=f"Credential detected in prompt: {', '.join(cred_matched.keys())}",
                matched_patterns=list(cred_matched.keys()),
                tier="tier_1",
            )

        # G33: obfuscated PII/secret exfil via text-encodings (HTML char refs,
        # URL/percent-encoding, \\u / \\x escapes). detect_pii/detect_secrets above
        # already fold base64/hex transport; these text-encodings are checked here
        # on the decoded variants (same decoder G32 wired into the injection path).
        # The RAW PII/secret is absent from the egress bytes (it is encoded), but a
        # model trivially decodes it, so an encoded PII/secret in a prompt is a
        # laundering/exfil attempt -> block (blocking sidesteps masking an encoded
        # span). Reaching here means the plaintext carried no PII/secret, so this
        # only fires on genuinely-hidden payloads; verified zero FP on benign
        # entities/URLs/escapes (they decode to harmless text, not PII patterns).
        for _variant in _decode_text_encoding_variants(text):
            _v_pii = detect_pii(_variant)
            _v_secret = detect_secrets(_variant)
            if _v_pii or _v_secret:
                _kinds = list(_v_pii.keys()) + list(_v_secret.keys())
                return ScanVerdict(
                    action="block",
                    threat_type="obfuscated_pii" if _v_pii else "obfuscated_secret",
                    confidence=0.9,
                    detail=(
                        "Encoded PII/secret exfil attempt via text-encoding: "
                        + ", ".join(_kinds)
                    ),
                    matched_patterns=_kinds,
                    tier="tier_1",
                )

        # G53: obfuscated PII/secret hidden by INLINE markdown emphasis / render-invisible
        # HTML (``1**2**3-45-6789`` / ``12<!-- -->3-45-6789``) — the raw bytes dodge the
        # regexes but a model reading the markdown source can reconstruct the value, just
        # as G33 blocks text-encoded PII. Symmetric to the OUTPUT-side G44/G51 neutralizer;
        # entity-split is already covered above (G33 decodes entities). Only fires when
        # stripping the render-invisible markers REVEALS PII/secret the plaintext lacked,
        # so benign markdown (**bold**, snake_case, `code`) is unaffected -> block.
        _md_stripped = strip_interleaved_emphasis(text)
        if _md_stripped != text:
            _s_pii = detect_pii(_md_stripped)
            _s_secret = detect_secrets(_md_stripped)
            # G53: also a bearer/api-key CREDENTIAL hidden by emphasis (sk_live_**..**);
            # internal-IP leakage is an OUTPUT concern (a user-supplied IP is not exfil).
            _s_cred = detect_credential_exposure(_md_stripped)
            if _s_pii or _s_secret or _s_cred:
                _skinds = list(_s_pii.keys()) + list(_s_secret.keys()) + list(_s_cred.keys())
                return ScanVerdict(
                    action="block",
                    threat_type="obfuscated_pii" if _s_pii else "obfuscated_secret",
                    confidence=0.9,
                    detail=(
                        "Markdown/HTML-obfuscated PII/secret exfil attempt: "
                        + ", ".join(_skinds)
                    ),
                    matched_patterns=_skinds,
                    tier="tier_1",
                )

        toxicity_verdict = self._check_toxicity(text, toxicity_threshold)
        if toxicity_verdict is not None:
            return toxicity_verdict

        # G6: multi-turn / crescendo split injection. Everything above passed, so if
        # `text` is a folded conversation, reassemble the USER turns only (the
        # attacker-driven channel) and re-scan that contiguous view through the full
        # pipeline. Honor ONLY a genuine attack block (never a length/repetition
        # `dos` artifact of concatenation, and never a downgrade). Recursion is
        # guarded by `_multiturn` so the reassembled view is scanned exactly once.
        if _multiturn:
            reassembled = _reassemble_user_turns(text)
            if reassembled and reassembled != text:
                mt = self._scan_prompt_sync(
                    reassembled, is_rag, toxicity_threshold, _multiturn=False
                )
                if mt.action == "block" and mt.threat_type != "dos":
                    LOG.warning(
                        "Multi-turn split %s detected across user turns (patterns=%s)",
                        mt.threat_type, mt.matched_patterns,
                    )
                    return ScanVerdict(
                        action="block",
                        threat_type=mt.threat_type or "prompt_injection",
                        confidence=mt.confidence,
                        detail=(
                            f"Multi-turn split {mt.threat_type or 'injection'} across "
                            f"user turns: {mt.detail}"
                        ),
                        matched_patterns=mt.matched_patterns,
                        tier="tier_1_multiturn",
                    )

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
                matched_values=dict(pii_matched),
            )

        secret_matched = detect_secrets(text)
        if secret_matched:
            return ScanVerdict(
                action="flag",
                threat_type="secret",
                confidence=0.9,
                detail=f"Secret/credential in output: {', '.join(secret_matched.keys())}",
                matched_patterns=list(secret_matched.keys()),
                matched_values=dict(secret_matched),
            )

        # G35: encoded PII/secret in model output (HTML-entity / URL / source escapes).
        # A manipulated model can emit PII as &#..; / %.. so the RAW value is absent
        # from egress bytes, yet a browser/markdown renderer decodes it back to the
        # PII. Flag it (same shape as plain output PII) so the egress sanitizer's
        # neutralize_encoded_pii masks the encoded run. Only fires when the DECODED
        # form has PII/secret the plaintext lacked (benign encoded output unaffected).
        for _variant in _decode_text_encoding_variants(text):
            _v_pii = detect_pii(_variant)
            _v_secret = detect_secrets(_variant)
            if _v_pii or _v_secret:
                _k = list(_v_pii.keys()) + list(_v_secret.keys())
                return ScanVerdict(
                    action="flag",
                    threat_type="pii" if _v_pii else "secret",
                    confidence=0.85,
                    detail=f"Encoded PII/secret in output: {', '.join(_k)}",
                    matched_patterns=_k,
                )

        # G44: PII/secret hidden by INLINE markdown emphasis interleaved among its chars
        # (``1**2**3-45-6789`` -> SSN, ``john`@`example.com`` -> email). The raw bytes
        # dodge the regexes but a markdown client renders the value. Flag (threat_type
        # pii/secret + matched_patterns) so the output guard elevates to redact and the
        # egress sanitizer's neutralize_markdown_split_pii masks it. Only fires when
        # stripping REVEALS PII the plaintext lacked, so benign markdown is unaffected.
        _md_stripped = strip_interleaved_emphasis(text)
        if _md_stripped != text:
            _m_pii = detect_pii(_md_stripped)
            _m_secret = detect_secrets(_md_stripped)
            # G50: also the credential + internal-IP detectors (obfuscated bearer/api
            # key or ``10.**0**.0.5`` internal IP). Flagged as pii/secret so the guard
            # elevates to redact and neutralize_markdown_split_pii masks the run.
            _m_cred = detect_credential_exposure(_md_stripped)
            _m_ip = detect_ip_leakage(_md_stripped)
            if _m_pii or _m_secret or _m_cred or _m_ip:
                _mk = (list(_m_pii.keys()) + list(_m_secret.keys())
                       + list(_m_cred.keys()) + list(_m_ip.keys()))
                return ScanVerdict(
                    action="flag",
                    threat_type="pii" if _m_pii else "secret",
                    confidence=0.85,
                    detail=f"Markdown-split PII/secret/credential/IP in output: {', '.join(_mk)}",
                    matched_patterns=_mk,
                )

        return ScanVerdict()


    def redact_pii(self, text: str, verdict: ScanVerdict | None = None) -> str:
        result = redact_all(text)
        if verdict is None:
            return result
        sources: list[str] = [str(p) for p in (verdict.matched_patterns or [])]
        scan_meta = verdict.scan_meta if isinstance(getattr(verdict, "scan_meta", None), dict) else {}
        for finding in scan_meta.get("findings") or []:
            if isinstance(finding, dict) and finding.get("evidence"):
                sources.append(str(finding["evidence"]))
        try:
            from patterns import redact_evidence_digit_spans
        except ImportError:
            from .patterns import redact_evidence_digit_spans
        return redact_evidence_digit_spans(result, sources)

    @staticmethod
    def _is_explanatory_mention(text: str, match: re.Match) -> bool:
        """Return True when an injection/jailbreak match is a quoted or explanatory
        MENTION rather than an imperative attack.

        policy #5 (CONSERVATIVE): suppresses ONLY the prompt_injection / jailbreak
        class — never sql/command injection, credentials, or PII. A single
        injection match qualifies as a benign mention when:

          (a) the matched span is wrapped in quotes (' ' / " " / backticks), OR
          (b) it is immediately preceded (within ~30 chars) by an explanatory
              lead-in ("explain", "what is", "example of", "meaning of", …).

        Callers must additionally confirm that EVERY injection match in the prompt
        is such a mention before downgrading — this only classifies one span.
        """
        start, end = match.start(), match.end()

        # (a) quote/backtick wrapper: a quote char appears on both sides of the
        # matched span (looking left within the lead-in window, and immediately
        # to the right past any trailing punctuation).
        left = text[max(0, start - _EXPLANATORY_LEADIN_WINDOW):start]
        right = text[end:end + 5]
        for q in _QUOTE_CHARS:
            if q in left and q in right:
                return True

        # (b) explanatory lead-in immediately preceding the matched span.
        lead_window = text[max(0, start - _EXPLANATORY_LEADIN_WINDOW):start]
        if _EXPLANATORY_LEADIN.search(lead_window):
            return True

        return False

    def _is_repetitive(self, text: str) -> bool:
        """Detect excessive repetition as a simple heuristic for DoS attempts."""
        words = text.split()
        # A single oversized whitespace-free token would otherwise slip past the
        # word-frequency heuristic below (len(words) < 10) while still driving the
        # downstream word-segmenter into its expensive fuzzy fallback. Flag it as
        # a DoS attempt without running that fallback.
        if any(len(word) > _MAX_REPETITIVE_TOKEN_LENGTH for word in words):
            return True
        if len(words) < 10:
            return False
        word_counts: dict[str, int] = {}
        for word in words:
            # G31: single-character tokens (e.g. the individual digits of a
            # space-separated SSN / phone / year list — "years 2 0 2 4 2 0 2 5",
            # "call 5 5 5 1 2 3 4 5 6 7") are NOT a repetition-DoS vector: they are
            # cheap to process and single digits legitimately repeat (only 0-9
            # exist). A genuine repetition flood repeats MULTI-char words/phrases
            # (still counted) or ships one oversized token (caught above), so
            # excluding len<=1 tokens removes a class of false blocks on benign,
            # non-PII digit sequences (independent aidefence oracle: hasPII=false)
            # without weakening real DoS detection.
            if len(word) <= 1:
                continue
            lower = word.lower()
            word_counts[lower] = word_counts.get(lower, 0) + 1
        if not word_counts:
            return False
        max_count = max(word_counts.values())
        return (max_count / len(words)) > REPETITION_THRESHOLD

    def _check_toxicity(
        self, text: str, toxicity_threshold: float | None = None
    ) -> ScanVerdict | None:
        """
        Heuristic toxicity scoring based on indicator pattern matches.
        Compares the computed score against the configurable toxicity_threshold.
        A per-request ``toxicity_threshold`` override (passed by the caller from
        the org-scoped config) takes precedence over the gateway-wide
        ``self._config`` default.
        """
        threshold = (
            toxicity_threshold
            if toxicity_threshold is not None
            else self._config.get("toxicity_threshold", 0.70)
        )
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

        Unicode smuggling (fullwidth, homoglyph, zero-width, RTL-override,
        combining diacritic) is folded to ASCII first, and bounded base64/hex
        transport-decoded variants are appended so the Tier-0.5 rescan sees
        them.
        """
        unified = _normalize_unicode(text)
        normalized = _normalize_leet(unified.lower())
        tokens = _reassemble_split_words(
            _collapse_single_letter_runs(re.findall(r"[a-zA-Z]+", normalized))
        )

        result_tokens: list[str] = []
        for token in tokens:
            if len(token) >= _CONCAT_WORD_MIN_LENGTH:
                segments = _segment_token(token)
                result_tokens.extend(segments)
            else:
                result_tokens.append(token)

        # Append bounded transport-decoded payloads (base64/hex) so a wrapped
        # injection is rescanned by the caller against the full pattern set.
        for variant in _decode_transport_variants(unified):
            decoded_norm = _normalize_leet(_normalize_unicode(variant).lower())
            for token in re.findall(r"[a-zA-Z]+", decoded_norm):
                if len(token) >= _CONCAT_WORD_MIN_LENGTH:
                    result_tokens.extend(_segment_token(token))
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
        normalized = _normalize_leet(_normalize_unicode(text).lower())
        input_words = re.findall(r"[a-zA-Z]+", normalized)
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
        first_match_idx = -1
        last_match_idx = -1

        for i, word in enumerate(input_words):
            if phrase_idx >= len(phrase_words):
                break
            similarity = difflib.SequenceMatcher(
                None, word, phrase_words[phrase_idx]
            ).ratio()
            if similarity >= threshold:
                if first_match_idx < 0:
                    first_match_idx = i
                last_match_idx = i
                matched_similarities.append(similarity)
                phrase_idx += 1

        if phrase_idx >= len(phrase_words) and matched_similarities:
            # Bound the matched SPAN. The match was an ordered subsequence with
            # UNLIMITED gaps, so a benign input with the anchor tokens scattered
            # far apart (e.g. "ignore" in one sentence, "previous instructions" 40
            # words later) matched at similarity 1.00 — a false positive that
            # blocked legitimate prompts. A real typo/word-split injection keeps
            # the anchor words CLOSE together; require the span (first→last matched
            # word) to stay within phrase_len + a small gap budget so insertion /
            # splitting evasion ("ignore the previous set of instructions") still
            # matches while scattered tokens do not. Purely additive — only
            # REJECTS loose matches, never weakens a tight one (no detection loss).
            span = last_match_idx - first_match_idx + 1
            max_span = len(phrase_words) * 2 + 4
            if span > max_span:
                return (False, 0.0)
            # Require a TYPO signal for GAPPED matches. The fuzzy tier-1.5 exists to
            # catch typo/word-split evasion ("ignor prevous instuctions"); a tier-1
            # regex already catches genuine EXACT injections (contiguous and common
            # word-split forms). So when all matched tokens are EXACT *and* the
            # match is gapped, it's benign words that merely appear in anchor order
            # ("...want to ignore. What previous instructions...") — a false
            # positive. Defer those to tier-1. A contiguous exact anchor, or any
            # approximate (0.75<=sim<1.0) match, still fires.
            has_approx = any(s < 0.999 for s in matched_similarities)
            is_contiguous = span == len(phrase_words)
            if not has_approx and not is_contiguous:
                return (False, 0.0)
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

    def _store_tier2_cache(self, key: str, value: dict[str, Any]) -> None:
        """Insert a Bedrock response into the bounded TTL verdict cache.

        Evicts the oldest entry when the cache is at capacity. The cache is
        per-process (per gunicorn worker); correctness does not depend on it
        being shared, since it only short-circuits an idempotent scan.
        """
        if self._tier2_cache_max <= 0:
            return
        if len(self._tier2_cache) >= self._tier2_cache_max:
            try:
                oldest = min(self._tier2_cache.items(), key=lambda kv: kv[1][0])[0]
                self._tier2_cache.pop(oldest, None)
            except ValueError:
                pass
        self._tier2_cache[key] = (time.monotonic(), value)

    async def scan_prompt_with_tier2(
        self,
        text: str,
        is_rag: bool = False,
        org_tier2_override: bool | None = None,
        org_slug: str = "",
        org_tier2_strict: bool = True,
        toxicity_threshold: float | None = None,
        request_id: str = "",
    ) -> ScanVerdict:
        """
        Run Tier-1 regex/deterministic checks first. If no blocking verdict,
        and Tier-2 is enabled, call the Bedrock scanner (async via executor)
        and combine results to produce a final ScanVerdict.

        Deobfuscated text is passed to Bedrock alongside the original so the
        model can see both raw and segmented versions.

        ``org_tier2_override`` (Phase 0 D-G2-v3) is a tri-state per-org switch:
            * ``None``  — no per-org opinion; use gateway-wide default
              (``self.tier2_enabled`` from ENABLE_TIER2 env).
            * ``True``  — force-enable Tier-2 for this org (requires a
              ``_bedrock_scanner`` instance to actually be configured).
            * ``False`` — force-disable Tier-2 for this org.
        Callers MUST pass via identity (``org_config.get("tier2_enabled")``)
        so that ``None`` is preserved and not coerced to ``False``.

        ``org_slug`` + ``org_tier2_strict`` (Phase 0 D-G3-v3) feed the
        per-(org, scanning_model_id) Bedrock circuit breaker. When the
        breaker is OPEN:
            * strict=True  -> ``Tier2UnavailableStrict`` is raised; the
              gateway HTTP layer translates it to HTTP 451 with a
              ``tier2_unavailable_strict`` degraded envelope.
            * strict=False -> Tier-2 is skipped; the Tier-1 verdict is
              returned and a ``tier2_degraded_pass`` event is emitted.
        """
        tier1 = await self.scan_prompt(text, is_rag, toxicity_threshold=toxicity_threshold)
        if tier1.action == "block":
            return tier1

        # Per-org override beats the gateway-wide default. Identity check
        # (``is None``) distinguishes "no override" from "explicit False".
        if org_tier2_override is False:
            return tier1
        if org_tier2_override is None and not self.tier2_enabled:
            return tier1
        if self._bedrock_scanner is None:
            return tier1

        # ---- G3 circuit breaker pre-check ----
        scanner_model_id = getattr(self._bedrock_scanner, "model", "") or ""
        allow_call = BREAKER.allow(
            org_slug=org_slug,
            model_id=scanner_model_id,
            strict=org_tier2_strict,
        )
        if not allow_call:
            # OPEN + non-strict: pass through with Tier-1 verdict and emit
            # an operational event so ops can see the degradation.
            try:
                loop_for_emit = asyncio.get_event_loop()
                if loop_for_emit.is_running():
                    loop_for_emit.create_task(
                        emit_operational_event(
                            EVENT_CLASS_TIER2_DEGRADED_PASS,
                            org_slug=org_slug or None,
                            severity="warning",
                            metadata={
                                "model_id": scanner_model_id,
                                "reason": "breaker_open_non_strict",
                            },
                        )
                    )
            except Exception:
                pass
            return tier1

        deobfuscated = self._deobfuscate_text(text)
        bedrock_input = deobfuscated if deobfuscated != text.lower() else text
        original_context = text if bedrock_input != text else None

        # ---- Tier-2 verdict cache (cost lever #1) ----
        # Cache the Bedrock *response* (not the final decision) keyed by the
        # exact scanned text so identical prompts skip the Bedrock round-trip.
        # All downstream decision/normalization logic still runs on the cached
        # payload, so security behaviour is identical to a fresh call.
        cache_key = None
        bedrock_normalized = None
        if self._tier2_cache_ttl > 0:
            # M-06: scope the verdict cache to the org so org A's cached verdict
            # can't be reused for org B (cross-tenant collision in the shared
            # _tier2_cache). Adding org_slug self-invalidates old entries (safe).
            cache_key = hashlib.sha256(
                f"in\x00{org_slug or ''}\x00{bedrock_input}\x00{original_context or ''}".encode("utf-8", "ignore")
            ).hexdigest()
            entry = self._tier2_cache.get(cache_key)
            if entry is not None:
                ts, value = entry
                if (time.monotonic() - ts) <= self._tier2_cache_ttl:
                    bedrock_normalized = value
                else:
                    self._tier2_cache.pop(cache_key, None)

        # ---- Tier-2 sampling (opt-in cost lever) ----
        # When GATEWAY_TIER2_SAMPLE_RATE < 1.0, skip Bedrock for a fraction of
        # prompts that Tier-1 found completely clean (action == "allow"). Never
        # sample-skip a prompt Tier-1 flagged, and never skip on a cache hit.
        if (
            bedrock_normalized is None
            and self._tier2_sample_rate < 1.0
            and tier1.action == "allow"
            and random.random() > self._tier2_sample_rate
        ):
            return tier1

        loop = asyncio.get_event_loop()
        if bedrock_normalized is None:
            try:
                bedrock_normalized = await loop.run_in_executor(
                    self._bedrock_executor, self._bedrock_scan_sync, bedrock_input, original_context, request_id,
                )
            except Exception:
                # Hard failure during Bedrock invocation counts toward the
                # breaker. Re-raise so existing error-handling paths run.
                BREAKER.record_result(org_slug, scanner_model_id, failure=True)
                raise
            # Only cache successful, non-degraded responses.
            if cache_key is not None:
                _cmeta = bedrock_normalized.get("meta", {})
                if not _cmeta.get("error") and not _cmeta.get("parse_failed"):
                    self._store_tier2_cache(cache_key, bedrock_normalized)

        meta = bedrock_normalized.get("meta", {})
        reason_code = str(meta.get("decision_reason", "")).strip().lower()
        recommended = self._normalize_bedrock_action(meta.get("recommended_action"))
        llm_guard = bedrock_normalized.get("llm_guard", {})
        score = self._normalize_score(llm_guard.get("score", 0.0))

        # Feed the G3 breaker. A "failure" is a soft-degraded Bedrock
        # response (HTTP error, parse failure, missing LLM-guard payload).
        _bedrock_failed = bool(
            meta.get("error")
            or meta.get("parse_failed")
            or (not meta.get("error") and not meta.get("parse_failed") and not llm_guard)
        )
        BREAKER.record_result(org_slug, scanner_model_id, failure=_bedrock_failed)

        raw_findings = meta.get("raw_findings") or []
        bedrock_categories: list[str] = []
        bedrock_evidence: list[str] = []
        bedrock_owasp: list[str] = []
        max_confidence = 0.0
        for finding in raw_findings:
            if isinstance(finding, dict):
                cat = finding.get("category", "")
                if cat:
                    bedrock_categories.append(cat)
                rid = str(finding.get("rule_id") or finding.get("owasp_code") or "").strip().upper()
                if rid and rid[:3] in ("LLM", "MCP", "AGE") and len(rid) >= 5:
                    if rid not in bedrock_owasp:
                        bedrock_owasp.append(rid)
                ev = finding.get("evidence", "")
                if ev:
                    # The guard model's free-text evidence may carry RAW PII
                    # (it only masks inconsistently). Run it through the
                    # deterministic redactor so every reported pattern is masked,
                    # and de-dup so overlapping findings don't repeat (these
                    # strings are surfaced to the client in matched_patterns).
                    ev_masked = redact_all(str(ev))
                    if ev_masked not in bedrock_evidence:
                        bedrock_evidence.append(ev_masked)
                conf = self._normalize_score(finding.get("confidence", 0.0))
                if conf > max_confidence:
                    max_confidence = conf

        # ── Tier-1 deterministic redact is AUTHORITATIVE; Tier-2 may only ESCALATE
        # it to a block, never DOWNGRADE it. A deterministic Tier-1 PII/secret
        # ``redact`` verdict was being DISCARDED here whenever Tier-2 ran and
        # returned anything other than a block (allow / monitor / flag / risk-score
        # / parse-failed-degraded): every branch below returns a fresh Tier-2
        # verdict, so the combined result lost Tier-1's ``redact`` and main.py never
        # applied the redaction — forwarding RAW PII upstream. Caught by a
        # real-fleet mitmproxy capture: a 4-PII prompt overflowed the guard model's
        # JSON findings (max_tokens) -> parse-fail -> degraded fail-open -> raw
        # SSN/email/phone/card egressed to OpenRouter while Tier-1 had said redact.
        # Preserve the Tier-1 redact unless Tier-2 genuinely escalates to a block
        # (e.g. PII prompt that ALSO carries a high-confidence injection).
        if tier1.action == "redact":
            _t2_escalates_to_block = recommended == "block" or score >= 0.70
            if not _t2_escalates_to_block:
                if not isinstance(getattr(tier1, "scan_meta", None), dict):
                    tier1.scan_meta = {}
                tier1.scan_meta["tier2_advisory"] = {
                    "recommended_action": recommended,
                    "score": score,
                    "degraded": bool(meta.get("parse_failed") or meta.get("error")),
                }
                return tier1

        def _bedrock_verdict(**kwargs: Any) -> ScanVerdict:
            verdict = ScanVerdict(**kwargs)
            findings: list[dict[str, Any]] = []
            for finding in raw_findings or []:
                if not isinstance(finding, dict):
                    continue
                findings.append(
                    {
                        "category": finding.get("category") or "",
                        "confidence": self._normalize_score(finding.get("confidence", 0.0)),
                        "evidence": redact_all(str(finding.get("evidence") or "")),
                        "rule_id": finding.get("rule_id") or finding.get("owasp_code") or "",
                    }
                )
            verdict.scan_meta = {
                "scanner": "zeroshield_guard_model",
                "recommended_action": recommended,
                "decision_reason": str(meta.get("decision_reason") or ""),
                "llm_guard_score": score,
                "findings": findings[:8],
            }
            return verdict

        if recommended == "block":
            # Guard-model hallucination guard: when Tier-1 found nothing (allow) and the
            # guard's block evidence is a "hidden encoded payload" that decodes to the
            # VISIBLE INPUT itself (a fabricated self-referential encoding — see
            # _tier2_evidence_is_self_referential_encoding), the block is unsupported.
            # Downgrade to a monitor 'flag' rather than hard-blocking a benign prompt.
            # Provably cannot suppress a real encoded attack (whose decoded payload differs
            # from the visible input). Only fires on tier1=allow so a Tier-1 verdict is
            # never weakened.
            if tier1.action == "allow":
                _ev_blob = " ".join(bedrock_evidence) + " " + str(meta.get("decision_reason") or "")
                if _tier2_evidence_is_self_referential_encoding(_ev_blob, text):
                    return _bedrock_verdict(
                        action="flag",
                        threat_type=bedrock_categories[0] if bedrock_categories else "policy_violation",
                        confidence=min(max_confidence or 0.5, 0.5),
                        detail=(
                            "ZeroShield Tier-2 block suppressed: guard-model cited a self-"
                            "referential encoded payload (decodes to the visible input) — "
                            "hallucinated hidden payload; downgraded to monitor"
                        ),
                        matched_patterns=[],
                        tier="tier_2",
                        reason_code="tier2_self_referential_encoding_hallucination",
                        owasp_codes=bedrock_owasp,
                    )
            return _bedrock_verdict(
                action="block",
                threat_type=bedrock_categories[0] if bedrock_categories else "policy_violation",
                confidence=max_confidence or 1.0,
                detail=f"ZeroShield Tier-2 detected threat: {', '.join(bedrock_evidence[:2]) or 'recommended block'}",
                matched_patterns=bedrock_evidence[:5] or [],
                tier="tier_2",
                reason_code=reason_code or "model_recommended_block",
                owasp_codes=bedrock_owasp,
            )
        if recommended == "redact":
            return _bedrock_verdict(
                action="flag",
                threat_type=bedrock_categories[0] if bedrock_categories else "sensitive_content",
                confidence=max_confidence or 0.9,
                detail=f"ZeroShield Tier-2 suggested redaction: {', '.join(bedrock_evidence[:2]) or 'redact'}",
                matched_patterns=bedrock_evidence[:5] or [],
                tier="tier_2",
                reason_code=reason_code or "model_recommended_redact",
                owasp_codes=bedrock_owasp,
            )
        if self._is_tier2_degraded(meta, llm_guard):
            degraded_reason = reason_code or "bedrock_degraded"
            degraded_detail = (
                "ZeroShield Tier-2 degraded; unable to confidently validate prompt"
            )
            if meta.get("error"):
                degraded_detail = "ZeroShield Tier-2 error; unable to confidently validate prompt"
            elif meta.get("parse_failed"):
                degraded_detail = "ZeroShield Tier-2 response unparseable; unable to confidently validate prompt"
            # Fail-closed option for the INPUT path: a degraded/unparseable Tier-2
            # response is reached precisely by prompts that EVADE Tier-1 static
            # signatures, so a fail-open 'flag' forwards the (possibly evasive)
            # attack to the LLM with no enforcement. When tier2_input_fail_closed
            # is enabled (env GATEWAY_TIER2_INPUT_FAIL_CLOSED=true, or per-org
            # override), block instead. Default OFF preserves availability. Output
            # scanning (scan_output_with_tier2) intentionally stays fail-open.
            _fail_closed = bool(self._config.get("tier2_input_fail_closed", False))
            if _fail_closed:
                return _bedrock_verdict(
                    action="block",
                    threat_type="scanner_degraded",
                    confidence=max(score, 0.5),
                    detail=degraded_detail + " (fail-closed: blocked by policy)",
                    tier="tier_2",
                    reason_code=degraded_reason + "_failclosed",
                )
            return _bedrock_verdict(
                action="flag",
                threat_type="scanner_degraded",
                confidence=max(score, 0.5),
                detail=degraded_detail,
                tier="tier_2",
                reason_code=degraded_reason,
            )
        if recommended == "monitor":
            return _bedrock_verdict(
                action="flag",
                threat_type=bedrock_categories[0] if bedrock_categories else "policy_violation",
                confidence=max_confidence or float(score),
                detail=f"ZeroShield Tier-2 advisory: {', '.join(bedrock_evidence[:2]) or 'monitor'}",
                matched_patterns=bedrock_evidence[:5] or [],
                tier="tier_2",
                reason_code=reason_code or "model_recommended_monitor",
            )

        BEDROCK_BLOCK_THRESHOLD = 0.70
        BEDROCK_FLAG_THRESHOLD = 0.40

        if score >= BEDROCK_BLOCK_THRESHOLD:
            return _bedrock_verdict(
                action="block",
                threat_type=bedrock_categories[0] if bedrock_categories else "risk_score",
                confidence=score,
                detail=f"High ZeroShield Tier-2 risk score: {', '.join(bedrock_evidence[:2]) or 'score-based block'}",
                matched_patterns=bedrock_evidence[:5] or [],
                tier="tier_2",
                reason_code=reason_code or "score_threshold_block",
            )

        if score >= BEDROCK_FLAG_THRESHOLD:
            return _bedrock_verdict(
                action="flag",
                threat_type=bedrock_categories[0] if bedrock_categories else "risk_score",
                confidence=score,
                detail=f"Moderate ZeroShield Tier-2 risk score: {', '.join(bedrock_evidence[:2]) or 'score-based flag'}",
                matched_patterns=bedrock_evidence[:5] or [],
                tier="tier_2",
                reason_code=reason_code or "score_threshold_flag",
            )

        if bedrock_categories and recommended == "allow":
            return _bedrock_verdict(
                action="flag",
                threat_type=bedrock_categories[0],
                confidence=max_confidence or 0.5,
                detail="ZeroShield Tier-2 found threats but recommended allow -- escalated to flag",
                matched_patterns=bedrock_evidence[:5] or [],
                tier="tier_2",
                reason_code=reason_code or "findings_with_allow",
            )

        return _bedrock_verdict(
            action="allow",
            # M2: confidence here is consumed downstream as the request RISK score
            # (main.py request_risk_score -> ×100 security_risk_score -> "critical"
            # at >=80). The old `1.0 - score` INVERTED it: a clean prompt (score 0)
            # got confidence 1.0 -> risk 100 -> every benign request flagged
            # critical (63% of traffic, 99.5% on module 1.5). Report the actual
            # (low) threat score instead — matches the tier-1 allow paths (0.0) and
            # the threat paths (high). Clean -> ~0.
            threat_type="clean",
            confidence=round(max(0.0, score), 4),
            detail="ZeroShield Model (Tier-2) completed — no threats detected.",
            tier="tier_2",
            reason_code=reason_code or "tier2_pass",
        )

    async def scan_output_with_tier2(
        self,
        text: str,
        *,
        org_tier2_override: bool | None = None,
        org_slug: str = "",
        request_id: str = "",
    ) -> ScanVerdict:
        """Output counterpart of scan_prompt_with_tier2: run the STATIC output
        scan (tier-1) FIRST, then the ZeroShield guard model (tier-2, Bedrock)
        on the model OUTPUT. The SAME guard model + breaker/cache/sampling infra
        as the input path is reused.

        ALWAYS fail-open: a guard-model outage (breaker open, Bedrock error,
        parse failure) must never block or hold an already-generated completion
        — it degrades to the static tier-1 verdict. ``org_tier2_override`` is the
        same tri-state per-org switch as input (pass ``org_config.get("tier2_enabled")``
        so ``None`` is preserved).
        """
        tier1 = await self.scan_output(text)
        if org_tier2_override is False:
            return tier1
        if org_tier2_override is None and not self.tier2_enabled:
            return tier1
        if self._bedrock_scanner is None or not text:
            return tier1

        scanner_model_id = getattr(self._bedrock_scanner, "model", "") or ""
        # Fail-open breaker for output (strict=False -> never raise; OPEN -> tier1).
        if not BREAKER.allow(org_slug=org_slug, model_id=scanner_model_id, strict=False):
            return tier1

        cache_key = None
        bedrock_normalized = None
        if self._tier2_cache_ttl > 0:
            # M-06: scope the verdict cache to the org so org A's cached output
            # verdict can't be reused for org B (cross-tenant collision). Adding
            # org_slug to the key self-invalidates any pre-existing entries (safe).
            cache_key = hashlib.sha256(
                f"out\x00{org_slug or ''}\x00{text}".encode("utf-8", "ignore")
            ).hexdigest()
            entry = self._tier2_cache.get(cache_key)
            if entry is not None:
                ts, value = entry
                if (time.monotonic() - ts) <= self._tier2_cache_ttl:
                    bedrock_normalized = value
                else:
                    self._tier2_cache.pop(cache_key, None)

        if (
            bedrock_normalized is None
            and self._tier2_sample_rate < 1.0
            and tier1.action == "allow"
            and random.random() > self._tier2_sample_rate
        ):
            return tier1

        if bedrock_normalized is None:
            loop = asyncio.get_event_loop()
            try:
                bedrock_normalized = await loop.run_in_executor(
                    self._bedrock_executor, self._bedrock_scan_sync, text, None, request_id,
                )
            except Exception:
                BREAKER.record_result(org_slug, scanner_model_id, failure=True)
                return tier1  # fail-open
            if cache_key is not None:
                _cm = bedrock_normalized.get("meta", {})
                if not _cm.get("error") and not _cm.get("parse_failed"):
                    self._store_tier2_cache(cache_key, bedrock_normalized)

        meta = bedrock_normalized.get("meta", {})
        recommended = self._normalize_bedrock_action(meta.get("recommended_action"))
        llm_guard = bedrock_normalized.get("llm_guard", {})
        score = self._normalize_score(llm_guard.get("score", 0.0))
        _failed = bool(meta.get("error") or meta.get("parse_failed") or not llm_guard)
        BREAKER.record_result(org_slug, scanner_model_id, failure=_failed)
        if _failed:
            return tier1  # degraded output scan -> fail-open to static verdict

        raw_findings = meta.get("raw_findings") or []
        cats: list[str] = []
        evidence: list[str] = []
        owasp: list[str] = []
        max_conf = 0.0
        for f in raw_findings:
            if not isinstance(f, dict):
                continue
            if f.get("category"):
                cats.append(f["category"])
            if f.get("evidence"):
                # M-01: the guard model's free-text evidence may carry RAW PII
                # (it masks inconsistently). Mirror the INPUT path: run every
                # evidence string through the deterministic redactor and de-dup
                # (exact, order-preserving) before it reaches the client via
                # matched_patterns / detail / scan_meta findings.
                ev_masked = redact_all(str(f["evidence"]))
                if ev_masked not in evidence:
                    evidence.append(ev_masked)
            rid = str(f.get("rule_id") or f.get("owasp_code") or "").strip().upper()
            if rid and rid[:3] in ("LLM", "MCP", "AGE") and len(rid) >= 5 and rid not in owasp:
                owasp.append(rid)
            c = self._normalize_score(f.get("confidence", 0.0))
            if c > max_conf:
                max_conf = c
        reason_code = str(meta.get("decision_reason", "")).strip().lower()

        def _verdict(action: str, threat: str, conf: float, detail: str, rc: str) -> ScanVerdict:
            v = ScanVerdict(
                action=action, threat_type=threat, confidence=conf, detail=detail,
                matched_patterns=evidence[:5], tier="tier_2",
                reason_code=rc, owasp_codes=owasp,
            )
            v.scan_meta = {
                "scanner": "zeroshield_guard_model",
                "recommended_action": recommended,
                "decision_reason": str(meta.get("decision_reason") or ""),
                "llm_guard_score": score,
                "findings": [
                    {
                        "category": ff.get("category") or "",
                        "confidence": self._normalize_score(ff.get("confidence", 0.0)),
                        # M-01: mask evidence here too (mirror input-path findings).
                        "evidence": redact_all(str(ff.get("evidence") or "")),
                        "rule_id": ff.get("rule_id") or ff.get("owasp_code") or "",
                    }
                    for ff in raw_findings if isinstance(ff, dict)
                ][:8],
            }
            return v

        if recommended == "block":
            return _verdict("block", cats[0] if cats else "output_finding", max_conf or 1.0,
                            f"ZeroShield Model flagged output: {', '.join(evidence[:2]) or 'recommended block'}",
                            reason_code or "model_recommended_block")
        if recommended in ("redact", "monitor"):
            return _verdict("flag", cats[0] if cats else "output_finding", max_conf or float(score),
                            f"ZeroShield Model output advisory: {', '.join(evidence[:2]) or recommended}",
                            reason_code or f"model_recommended_{recommended}")
        if score >= 0.70:
            return _verdict("block", cats[0] if cats else "output_finding", score,
                            "ZeroShield Model: high output risk score", reason_code or "score_threshold_block")
        if score >= 0.40:
            return _verdict("flag", cats[0] if cats else "output_finding", score,
                            "ZeroShield Model: moderate output risk score", reason_code or "score_threshold_flag")
        if cats:
            return _verdict("flag", cats[0], max_conf or 0.5,
                            "ZeroShield Model found output findings; escalated to flag",
                            reason_code or "findings_with_allow")
        # Clean tier-2: preserve any static tier-1 flag, else allow.
        if tier1.action != "allow":
            return tier1
        return _verdict("allow", "clean", round(max(0.0, 1.0 - score), 4),
                        "ZeroShield Model (Tier-2) output scan — no threats detected.",
                        reason_code or "tier2_pass")

    def _bedrock_scan_sync(self, analyzed_text: str, original_text: str | None = None, request_id: str = "") -> dict[str, Any]:
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
        return self._bedrock_scanner.scan(analyzed_text, context=original_text, request_id=request_id or None)


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
    # M-18: confidence = primary's share of all keyword hits (in (0, 1]) plus a
    # +0.3 additive boost, clamped to 1.0. Semantics are correct/monotonic: a
    # primary intent that dominates the hits yields >= confidence than one that
    # ties with others. The +0.3 is an intentional floor so a clear-but-not-
    # dominant primary (e.g. share 0.2) still reports moderate confidence (0.5),
    # not a near-zero value. Not an inversion — left as-is.
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