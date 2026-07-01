"""Vector-safe typed-placeholder redaction for RAG ingestion / queries.

Unlike ``patterns.redact_all`` (which partial-masks: ``j***@c***.com``,
``***-**-6789``), this module replaces each sensitive value with a *bare
semantic-category placeholder* — ``[EMAIL]``, ``[SSN]``, ``[CREDIT_CARD]`` —
so the redacted text keeps its sentence structure and embeds to a
semantically meaningful vector. ``***`` masking destroys cosine similarity;
typed placeholders preserve it.

Single source of truth: this module reuses the canonical regex catalogues in
``patterns.py``. It does NOT define its own regexes.

Overlap handling is leak-safe: overlapping matches (e.g. ``secret=sk-abc...``
where a secret-assignment span contains an api-key span) are merged into one
span before replacement, so no raw bytes of a matched value can survive.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

try:  # gateway modules are imported both as a package and as top-level
    from .patterns import (
        PII_PATTERNS,
        SECRET_PATTERNS,
        PHI_PATTERNS,
        PCI_PATTERNS,
        CREDENTIAL_EXPOSURE_PATTERNS,
        compile_pattern,
        _BARE_PHONE_10_SPLIT,
    )
except ImportError:  # pragma: no cover - top-level import path
    from patterns import (  # type: ignore[no-redef]
        PII_PATTERNS,
        SECRET_PATTERNS,
        PHI_PATTERNS,
        PCI_PATTERNS,
        CREDENTIAL_EXPOSURE_PATTERNS,
        compile_pattern,
        _BARE_PHONE_10_SPLIT,
    )


# The contextual bare-phone pattern matches a CUE + (possibly long) gap + the
# trailing phone value (e.g. "contact <email>, call back on 8929554991"). Its
# match SPAN therefore includes the cue/gap, which may contain another nested
# PII match (the email). ``patterns.redact_all`` masks ONLY the trailing digit
# run for this type (``_mask_phone_bare_contextual``), preserving the prefix; the
# typed redactor must use the SAME narrow span so the phone placeholder does not
# overlap-swallow the email in the merge step — keeping both engines' redaction
# byte-identical in structure (B4 path-unification). Compiled once.
_BARE_PHONE_VALUE_RE = compile_pattern(_BARE_PHONE_10_SPLIT + r"\b")


def _narrow_bare_contextual_phone(match) -> Tuple[int, int]:
    """Return the (start, end) of just the trailing phone value inside a
    ``phone_us_bare_contextual`` match, so the redaction span excludes the cue
    prefix (which may nest another PII match). Falls back to the full span if the
    value sub-pattern unexpectedly does not re-match."""
    value = _BARE_PHONE_VALUE_RE.search(match.group(0))
    if value is None:  # pragma: no cover - defensive; pattern guarantees a value
        return match.start(), match.end()
    base = match.start()
    return base + value.start(), base + value.end()


# Bare, semantic-category placeholders (same type -> same token, so a doc with
# two emails embeds as "... [EMAIL] ... [EMAIL] ..." — stable + meaningful).
TYPE_PLACEHOLDERS: Dict[str, str] = {
    # PII
    "email": "[EMAIL]",
    "ssn": "[SSN]",
    "credit_card": "[CREDIT_CARD]",
    "phone_us": "[PHONE]",
    "phone_us_bare_contextual": "[PHONE]",
    "phone_intl": "[PHONE]",
    "phone_dotted": "[PHONE]",
    "api_key_openai": "[API_KEY]",
    "aws_access_key": "[AWS_KEY]",
    "github_token": "[GITHUB_TOKEN]",
    "private_key_header": "[PRIVATE_KEY]",
    # secrets (assignment-style)
    "password_assignment": "[PASSWORD]",
    "secret_assignment": "[SECRET]",
    "token_assignment": "[TOKEN]",
    # PHI
    "medical_license": "[MEDICAL_LICENSE]",
    "medical_record": "[MEDICAL_RECORD]",
    "insurance_id": "[INSURANCE_ID]",
    # PCI / financial
    "iban_code": "[IBAN]",
    "us_bank_number": "[BANK_ACCOUNT]",
    "crypto_address": "[CRYPTO_ADDRESS]",
    # credential exposure
    "bearer_token": "[BEARER_TOKEN]",
    "basic_auth": "[BASIC_AUTH]",
    "exposed_password": "[PASSWORD]",
    "connection_string": "[CONNECTION_STRING]",
    "private_key_block": "[PRIVATE_KEY]",
}

# Catalogues scanned for ingestion/query redaction. IP-leakage and
# hallucination patterns are intentionally excluded — internal hostnames /
# file paths can be legitimate document content, and over-redacting them would
# harm retrieval. PII + secrets + PHI + PCI + credential exposure are redacted.
_REDACTION_CATALOGUES: Tuple[Dict[str, str], ...] = (
    PII_PATTERNS,
    SECRET_PATTERNS,
    PHI_PATTERNS,
    PCI_PATTERNS,
    CREDENTIAL_EXPOSURE_PATTERNS,
)


def _placeholder_for(ptype: str) -> str:
    return TYPE_PLACEHOLDERS.get(ptype, f"[{ptype.upper()}]")


@dataclass
class RedactionResult:
    """Outcome of a typed-placeholder redaction pass.

    ``spans`` and ``counts`` are keyed by the final placeholder type and use
    offsets into the ORIGINAL text (for audit/telemetry — never store the raw
    matched values).
    """

    text: str
    spans: Dict[str, List[Tuple[int, int]]] = field(default_factory=dict)
    counts: Dict[str, int] = field(default_factory=dict)

    @property
    def redacted(self) -> bool:
        return bool(self.counts)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def detect_and_redact_typed(text: str) -> RedactionResult:
    """Redact all detected sensitive spans with bare typed placeholders.

    Deterministic: the same input always yields the same output (so identical
    documents embed identically).
    """
    if text is None:
        # Contract: callers must coerce to str before redaction. Returning
        # RedactionResult(text=None) would silently propagate a None into
        # downstream ``.split()`` / ``len()`` and crash far from the cause;
        # fail loud here instead.
        raise TypeError(
            "detect_and_redact_typed() requires a str, got None."
        )
    if not text:
        return RedactionResult(text=text)

    # 1) Collect every match across catalogues: (start, end, type).
    raw: List[Tuple[int, int, str]] = []
    for catalogue in _REDACTION_CATALOGUES:
        for ptype, pattern in catalogue.items():
            for m in compile_pattern(pattern).finditer(text):
                if ptype == "phone_us_bare_contextual":
                    # Narrow to the trailing phone value only (mirrors
                    # patterns._mask_phone_bare_contextual) so the cue/gap — which
                    # may contain a nested email/SSN match — is NOT swallowed into
                    # the [PHONE] span during overlap-merge.
                    start, end = _narrow_bare_contextual_phone(m)
                elif m.end() > m.start():
                    start, end = m.start(), m.end()
                else:
                    continue  # skip zero-length matches
                if end > start:
                    raw.append((start, end, ptype))
    if not raw:
        return RedactionResult(text=text)

    # 2) Merge overlapping spans (leak-safe). Dominant type = the longest
    #    contributing match (most specific value wins its placeholder).
    raw.sort(key=lambda t: (t[0], t[1]))
    merged: List[List[object]] = []  # [start, end, type]
    for start, end, ptype in raw:
        if merged and start < merged[-1][1]:  # overlaps previous merged span
            prev = merged[-1]
            prev_len = int(prev[1]) - int(prev[0])  # type: ignore[arg-type]
            if (end - start) > prev_len:
                prev[2] = ptype  # longer match dictates the placeholder
            prev[1] = max(int(prev[1]), end)  # type: ignore[arg-type]
        else:
            merged.append([start, end, ptype])

    # 3) Build the output in a SINGLE O(n) forward pass over the (already
    #    merged, non-overlapping) spans. The previous per-match
    #    ``result = result[:s] + ph + result[e:]`` rebuilt the entire string on
    #    every match -> O(n*m), which on a large high-PII-density document
    #    (~2MB of SSNs/credit-cards) took ~20s and is a latent CPU-exhaustion
    #    DoS on the synchronous RAG ingest path. Joining segments is O(n).
    spans: Dict[str, List[Tuple[int, int]]] = {}
    counts: Dict[str, int] = {}
    parts: List[str] = []
    cursor = 0
    for start, end, ptype in sorted(merged, key=lambda m: int(m[0])):  # type: ignore[arg-type]
        s, e, t = int(start), int(end), str(ptype)
        parts.append(text[cursor:s])
        parts.append(_placeholder_for(t))
        cursor = e
        spans.setdefault(t, []).append((s, e))
        counts[t] = counts.get(t, 0) + 1
    parts.append(text[cursor:])
    result = "".join(parts)

    return RedactionResult(text=result, spans=spans, counts=counts)


def redact_for_embedding(text: str) -> str:
    """Return only the redacted text (vector-safe typed placeholders)."""
    return detect_and_redact_typed(text).text
