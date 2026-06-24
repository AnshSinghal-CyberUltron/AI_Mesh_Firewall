"""
Shared regex patterns for PII, secrets, credentials, output guardrails.

Single source of truth for the gateway data plane. Both ``scanner.py``
and ``context_guard.py`` import from this module to eliminate duplication.

Provides:
- Cached regex compilation via ``compile_pattern()``
- Detection helpers: ``detect_pii()``, ``detect_secrets()``
- Output guardrail patterns: hallucination, IP leakage, credential exposure
- Redaction helper: ``redact_all()``
- Compliance tagging: ``COMPLIANCE_TAG_MAP``, ``get_compliance_tags()``
"""
from __future__ import annotations

import functools
import re
from typing import Dict, List


@functools.lru_cache(maxsize=512)
def compile_pattern(pattern: str) -> re.Pattern[str]:
    """Compile and cache regex patterns for performance."""
    return re.compile(pattern, re.IGNORECASE)


PII_PATTERNS: Dict[str, str] = {
    "credit_card": r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b",
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    # SSN written with single spaces instead of dashes ("123 45 6789"). The
    # distinctive 3-2-4 single-space grouping is conservative: ordinary prose
    # rarely produces that exact shape. Each group is a fixed quantifier, so
    # this is LINEAR-time (no backtracking).
    "ssn_spaced": r"\b\d{3}\s\d{2}\s\d{4}\b",
    # SSN with NO separators ("123456789"). A bare 9-digit run is far too common
    # (order ids, routing numbers, etc.) to match unconditionally, so this is
    # CONTEXT-BOUNDED: it only fires when an explicit SSN keyword immediately
    # precedes the 9 digits. The context prefix is a fixed alternation and the
    # value is a fixed \d{9}, so it stays linear-time.
    "ssn_nosep": r"\b(?:ssn|social\s+security(?:\s+(?:number|no\.?|#))?)\b[\s:#]*\d{9}\b",
    # Bounded quantifiers (RFC-ish: local<=64, each label<=63, <=8 labels,
    # TLD 2-24 letters) keep this LINEAR-time. The previous unbounded form
    # `[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}` catastrophically
    # backtracks on long [A-Za-z0-9.-] runs lacking an '@'/TLD (ReDoS: a 500KB
    # field hung ~18 min). This is the single source of truth reused by the
    # scanner, context_guard, detect_pii/redact_all, and the RAG redactor.
    # Trailing ``(?!:[^\s/]+/)`` rejects SSH/git remote syntax (``git@github.com:org/repo``)
    # where a colon is immediately followed by a path segment — those are not
    # email addresses and must not be redacted. A real email followed by a colon
    # ("contact john@x.com: ...") is NOT rejected because the colon is followed
    # by whitespace, not a ``path/``. The lookahead has no nested quantifier, so
    # it stays linear-time.
    "email": r"\b[A-Za-z0-9._%+\-]{1,64}@(?:[A-Za-z0-9\-]{1,63}\.){1,8}[A-Za-z]{2,24}\b(?!:[^\s/]+/)",
    # US phone. A bare 10-digit run ("9876543210") is an order id / revenue
    # figure far more often than a phone number, so at least ONE separator (space,
    # dot, or hyphen) OR a literal area-code paren is now MANDATORY between digit
    # groups — the old "all separators optional" form false-positived on every
    # 10-digit integer. Two NON-OVERLAPPING alternatives, both with an optional
    # ``+1`` prefix:
    #   * parenthesised area code ("(415) 555-0142") — distinguished by ``\(...\)``
    #   * three groups joined by MANDATORY separators ("415-555-0142",
    #     "415.555.0142", "+1 415 555 0142")
    # Exactly 3-3-4 digit grouping, so version strings ("1.2.3") and dotted-quad
    # IPs ("192.168.1.1") do not fit. Every quantifier is fixed and separators are
    # mandatory (no ambiguous optional-repeat) — LINEAR-time, no ReDoS.
    "phone_us": (
        r"(?:\+1[\s.\-]?)?"
        r"(?:\(\d{3}\)[\s.\-]?\d{3}[\s.\-]?\d{4}|\b\d{3}[\s.\-]\d{3}[\s.\-]\d{4}\b)"
    ),
    # International / E.164 phone ("+44 20 7946 0958", "+919876543210"). Anchored
    # on a leading '+' (the distinctive E.164 marker) so it does not match bare
    # digit runs. Two NON-OVERLAPPING alternatives:
    #   * contiguous 10-15 digits (full E.164 with no separators), or
    #   * country code + at least two separated groups (rejects "+1 2" / "+100").
    # Every group is bounded and separators between groups are mandatory in the
    # separated branch, so there is no nested/ambiguous repeat — LINEAR-time.
    # Added `\d{1,4}[\s\-]?\d{6,12}` branch so `+<country>-<run-together digits>`
    # (e.g. +91-8088054321) is masked. The previous pattern only caught fully
    # run-together (`\d{10,15}` right after `+`) or separator-grouped numbers, so an
    # international number with the country code split off by a single dash leaked —
    # incl. through redact_all (the scrubber used on guard-model evidence/advisory).
    "phone_intl": r"\+(?:\d{10,15}|\d{1,4}[\s\-]?\d{6,12}|\d{1,3}(?:[\s\-]\d{1,4}){2,6})\b",
    # Dotted phone ("415.555.0142"). The 3.3.4 dotted grouping is distinctive;
    # fixed quantifiers keep it linear and the \b bounds avoid swallowing
    # adjacent digits. Version strings ("1.2.3") and dotted-quad IPs do not fit
    # the exact 3.3.4 digit-count shape.
    "phone_dotted": r"\b\d{3}\.\d{3}\.\d{4}\b",
    # Contextual bare 10-digit US phone. Tier-2 detects these semantically, but
    # phone_us intentionally skips separatorless runs (order IDs, revenue figures).
    # Only match when explicit phone/contact context immediately precedes the digits
    # (e.g. "my phone number is 8929554991") so enforcement redaction and the
    # upstream LLM never see raw PII the guard model already flagged.
    "phone_us_bare_contextual": (
        r"\b(?:phone|mobile|cell|tel(?:ephone)?)\s*(?:number|no\.?|#)?\s*(?:is|:)\s*"
        r"\d{10}\b"
    ),
    # N-CRED FIX: the original r"\bsk-[a-zA-Z0-9]{32,}\b" required an UNBROKEN
    # alphanumeric run, so it MISSED every modern hyphenated key format —
    # OpenAI project/service keys (sk-proj-…, sk-svcacct-…, sk-admin-…) and
    # OpenRouter keys (sk-or-v1-…). Those leaked through the OUTPUT credential
    # detector as a soft "flag" instead of a hard redact/block under a block
    # policy. Match the known modern prefixes (high-entropy tail) AND keep the
    # classic 32-char form. Prefix-anchored + a 16+ char tail keeps false
    # positives on benign "sk-…" prose negligible.
    "api_key_openai": r"\bsk-(?:proj|svcacct|admin|or-v1|or|live|test)-[a-zA-Z0-9_-]{16,}\b|\bsk-[a-zA-Z0-9]{32,}\b",
    "aws_access_key": r"\bAKIA[0-9A-Z]{16}\b",
    # AWS SECRET access key — the high-value credential. It has no fixed prefix
    # (40 chars of [A-Za-z0-9/+]), so match it in context of its variable name to
    # avoid false positives on arbitrary base64/hash blobs. Without this the
    # output guard masked only the AKIA id and egressed the secret in cleartext.
    "aws_secret_access_key": r"(?i)\baws[_-]?secret[_-]?access[_-]?key\b\s*[:=]\s*[\"']?[A-Za-z0-9/+=]{16,}",
    "github_token": r"\bghp_[a-zA-Z0-9]{36}\b",
    "private_key_header": r"-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----",
}

# Instructional / interrogative prose words that frequently follow a bare
# "password:" / "secret:" in benign educational or UX copy ("password: forgotten?",
# "secret: should never be shared"). A real credential value never *starts* with
# one of these, so a negative look-ahead lets us reject the prose variant without
# losing genuine secret detection.
_CREDENTIAL_PROSE_WORDS = (
    r"forgotten|forgot|instructions?|required|require|should|shouldn|never|must|"
    r"copy|least|reset|change|changed|changing|manager|management|protected|"
    r"field|here|your|yours|the|this|that|none|empty|blank|hidden|unknown|"
    r"please|click|enter|type|example|placeholder|redacted|masked|hashed"
)
# A captured value that resembles a real secret rather than prose: it must
# contain at least one digit OR one symbol (non-alphanumeric) — ordinary
# all-letter prose words are rejected — and must not *begin* with an
# instructional prose word.
_CREDENTIAL_VALUE = (
    r"(?!(?:" + _CREDENTIAL_PROSE_WORDS + r")\b)"
    r"(?=[^\s\"']*[\d\W])"
    r"[^\s\"']{8,}"
)
_TOKEN_VALUE = (
    r"(?!(?:" + _CREDENTIAL_PROSE_WORDS + r")\b)"
    r"(?=[a-zA-Z0-9_\-\.]*\d)"
    r"[a-zA-Z0-9_\-\.]{8,}"
)

SECRET_PATTERNS: Dict[str, str] = {
    # Require an actual assignment operator (':' or '=') between the keyword and
    # the value — NOT bare whitespace. Otherwise benign prose like "password
    # manager", "password-protected", "secret garden" or "token economy" is
    # falsely flagged/redacted as a leaked credential.
    #
    # Additionally require the captured VALUE to look like a real credential
    # (>= 8 chars with at least one digit/symbol, and not starting with an
    # instructional prose word) so educational copy such as
    # "password: should never be shared." is not mangled or hard-blocked.
    "password_assignment": r'password["\s]*[:=][\s"\']*' + _CREDENTIAL_VALUE,
    "secret_assignment": r'secret["\s]*[:=][\s"\']*' + _CREDENTIAL_VALUE,
    "token_assignment": r'token["\s]*[:=][\s"\']*' + _TOKEN_VALUE,
    # RAG-C5-CRED-COVERAGE: standalone credential FORMATS the assignment patterns
    # above miss. detect_secrets had no Slack token / JWT / Bearer inventory, so
    # these credential forms were stored unblocked at RAG ingest (and unredacted in
    # chat input). bearer_token already existed in CREDENTIAL_EXPOSURE_PATTERNS (the
    # output guard) — fold the class into the ingest secret inventory too.
    "slack_token": r'\bxox[baprs]-[0-9A-Za-z-]{10,}\b',
    "jwt": r'\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b',
    "bearer_token": r'Bearer\s+[A-Za-z0-9_\-\.]{20,}',
}

PHI_PATTERNS: Dict[str, str] = {
    "medical_license": r"\b(?:NPI|DEA)\s*#?\s*\d{7,10}\b",
    "medical_record": r"\b(?:MRN|medical\s+record)\s*#?\s*\d{6,10}\b",
    "insurance_id": r"\b(?:insurance\s+(?:id|number|#)|policy\s*#)\s*[:\s]*[A-Z0-9]{5,15}\b",
}

PCI_PATTERNS: Dict[str, str] = {
    # IBAN. The original matched only the CONTIGUOUS form
    # ("GB82WEST12345698765432") and missed the SPACED printed form
    # ("GB82 WEST 1234 5698 7654 32") that banks actually display. Second
    # alternative adds the canonical "blocks of 4 separated by single spaces"
    # grouping (2-7 four-char blocks + an optional 1-4 char tail). Interior
    # groups are fixed at 4 chars with a MANDATORY single-space anchor each
    # iteration, so the match cannot bleed into following prose (a 9-letter word
    # like "confirmed" is not a 4-char group) and there is no ambiguous
    # optional-repeat — LINEAR-time, no ReDoS.
    "iban_code": (
        r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}[A-Z0-9]{0,16}\b"
        r"|\b[A-Z]{2}\d{2}(?:\s[A-Z0-9]{4}){2,7}(?:\s[A-Z0-9]{1,4})?\b"
    ),
    "us_bank_number": r"\b(?:account|routing)\s*#?\s*[:\s]*\d{8,17}\b",
    "crypto_address": r"\b(?:0x[a-fA-F0-9]{40}|[13][a-km-zA-HJ-NP-Z1-9]{25,34}|bc1[a-z0-9]{39,59})\b",
}

HALLUCINATION_PATTERNS: Dict[str, str] = {
    # Narrowed: bare "but/however/might" in safety refusals caused false positives.
    "uncertainty_hedge": (
        r"\b(?:I think|I believe|probably|possibly|"
        r"I'm not (?:sure|certain)|as far as I know)\b"
    ),
    "fabricated_citation": (
        r"(?:according to|as stated (?:in|by)|per the (?:study|report|paper|article))\s+"
        r"(?:(?:Dr\.\s+)?[A-Z][a-z]+ (?:et al\.?|and colleagues)|\[\d+\])"
    ),
    "confidence_disclaimer": (
        r"(?:I (?:cannot|can't) verify (?:this|that|the)|"
        r"I do not have (?:access to|information about)|"
        r"this (?:may|might) not be (?:accurate|correct|up to date))"
    ),
    "contradictory_statement": (
        r"contrary to (?:what I (?:just )?said|the above|this)|"
        r"on the other hand,?\s+(?:this|that)\s+(?:is|was)\s+(?:not|incorrect|wrong)"
    ),
    "fabricated_statistic": (
        r"(?:approximately|roughly|about|nearly)\s+\d+(?:\.\d+)?%?\s+"
        r"(?:of|percent|per cent)"
    ),
    "nonexistent_entity": (
        r"(?:the\s+(?:University|Institute|Organization|Foundation|Agency)"
        r"\s+of\s+[A-Z][a-z]+\s+[A-Z][a-z]+)"
    ),
    "temporal_impossibility": (
        r"(?:as of\s+\d{4}|in\s+(?:19|20)\d{2})\s+.*?"
        r"(?:will|is going to|plans to|expects to)"
    ),
    "source_attribution_gap": (
        r"(?:research (?:shows|indicates|suggests|proves)|"
        r"studies (?:show|indicate|suggest|prove)|"
        r"experts (?:say|agree|believe))\b"
    ),
}

IP_LEAKAGE_PATTERNS: Dict[str, str] = {
    "internal_ipv4": (
        r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
        r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|"
        r"192\.168\.\d{1,3}\.\d{1,3})\b"
    ),
    "internal_hostname": r"\b(?:[a-z][a-z0-9-]+\.(?:internal|local|corp|intra|private|lan))\b",
    # Skip canonical *public* doc/install paths (nginx/apache config dirs, the
    # /usr/{share,bin,sbin,lib,local} tree, and any /opt/<app> install root) so
    # standard documentation examples don't pollute the §1.7 flagged-output
    # telemetry. Private/sensitive paths (/home/<user>, /etc/passwd, /var/...)
    # still match.
    "file_path_unix": (
        r"(?:/"
        r"(?!(?:etc/(?:nginx|apache2)|usr/(?:share|bin|sbin|lib|local)|"
        r"opt/[a-zA-Z0-9_.-]+)(?:[/]|$))"
        r"(?:home|var|etc|opt|usr|tmp|srv)/[a-zA-Z0-9_./-]{3,})"
    ),
    # Skip C:\Users\Public and C:\Program Files (standard public locations);
    # keep per-user, Windows, AppData and temp paths.
    "file_path_windows": (
        r"(?:[A-Z]:\\"
        r"(?!(?:Users\\Public|Program Files)(?:\\|$))"
        r"(?:Users|Windows|Program Files|temp|AppData)\\[a-zA-Z0-9_.\\ -]{3,})"
    ),
    "internal_url": (
        r"https?://(?:(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
        r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|"
        r"192\.168\.\d{1,3}\.\d{1,3})|"
        r"[a-z][a-z0-9-]+\.(?:internal|local|corp))[:/]"
    ),
}

CREDENTIAL_EXPOSURE_PATTERNS: Dict[str, str] = {
    "bearer_token": r"Bearer\s+[A-Za-z0-9_\-\.]{20,}",
    "basic_auth": r"Basic\s+[A-Za-z0-9+/=]{16,}",
    # The value must resemble an actual secret (>= 8 chars, contains a digit or
    # symbol, not an instructional prose word) so benign copy like
    # "password: forgotten? click reset" is not hard-blocked. See _CREDENTIAL_VALUE.
    "exposed_password": r"(?:password|passwd|pwd)\s*[:=]\s*['\"]?" + _CREDENTIAL_VALUE,
    "connection_string": r"(?:mongodb|mysql|postgres(?:ql)?|redis|amqp)://[^\s]{10,}",
    "private_key_block": r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----",
    # C4-CRED-INGEST-*: modern provider credential formats the assignment/url patterns
    # above miss. Folded into the MASTER inventory so the RAG-ingest path (which now
    # runs detect_credential_exposure) and the output guard share one credential set.
    "github_fine_grained_pat": r"\bgithub_pat_[A-Za-z0-9_]{22,}\b",
    "stripe_key": r"\bsk_(?:live|test)_[A-Za-z0-9]{16,}\b",
    "azure_storage_key": r"AccountKey=[A-Za-z0-9+/=]{40,}",
    "twilio_api_key": r"\bSK[0-9a-fA-F]{32}\b",
    "gcp_service_account_key": r'"private_key"\s*:\s*"-----BEGIN PRIVATE KEY-----',
    "slack_token": r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b",
    "jwt": r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b",
}

COMPLIANCE_TAG_MAP: Dict[str, List[str]] = {
    "credit_card": ["PCI-DSS"],
    "ssn": ["PII", "HIPAA"],
    "ssn_spaced": ["PII", "HIPAA"],
    "ssn_nosep": ["PII", "HIPAA"],
    "email": ["PII", "GDPR"],
    "phone_us": ["PII", "GDPR"],
    "phone_intl": ["PII", "GDPR"],
    "phone_dotted": ["PII", "GDPR"],
    "phone_us_bare_contextual": ["PII", "GDPR"],
    "api_key_openai": ["SECRET"],
    "aws_access_key": ["SECRET"],
    "aws_secret_access_key": ["SECRET"],
    "github_token": ["SECRET"],
    "private_key_header": ["SECRET"],
    "password_assignment": ["SECRET"],
    "secret_assignment": ["SECRET"],
    "token_assignment": ["SECRET"],
    "medical_license": ["PHI", "HIPAA"],
    "medical_record": ["PHI", "HIPAA"],
    "insurance_id": ["PHI", "HIPAA"],
    "iban_code": ["PCI-DSS"],
    "us_bank_number": ["PCI-DSS"],
    "crypto_address": ["PCI-DSS"],
    "internal_ipv4": ["INFRA"],
    "internal_hostname": ["INFRA"],
    "file_path_unix": ["INFRA"],
    "file_path_windows": ["INFRA"],
    "internal_url": ["INFRA"],
    "bearer_token": ["SECRET", "SOC2"],
    "basic_auth": ["SECRET", "SOC2"],
    "exposed_password": ["SECRET", "SOC2"],
    "connection_string": ["SECRET", "SOC2"],
    "private_key_block": ["SECRET", "SOC2"],
}


# Category labels (e.g. "Social Security Numbers") are not literal PII.
_PII_CATEGORY_LABEL_RE = re.compile(
    r"\b(?:social security numbers?|email addresses?|phone numbers?|"
    r"credit card numbers?|personally identifiable information)\b",
    re.IGNORECASE,
)

_SAFETY_REFUSAL_SIGNALS = (
    re.compile(r"(?:don't|do not)\s+share", re.IGNORECASE),
    re.compile(r"security advice", re.IGNORECASE),
    re.compile(r"(?:I don't|I do not)\s+(?:process|store|retain)\s+.*personal", re.IGNORECASE),
    re.compile(r"(?:cannot|can't)\s+(?:verify|store|process)", re.IGNORECASE),
    re.compile(r"not a (?:secure|verified)\s+(?:system|platform)", re.IGNORECASE),
    re.compile(r"aadhaar|aadhar", re.IGNORECASE),
)


def is_safety_refusal_output(text: str) -> bool:
    """True when output is benign security/privacy refusal advice (not factual hallucination)."""
    if not text or len(text) < 40:
        return False
    hits = sum(1 for pattern in _SAFETY_REFUSAL_SIGNALS if pattern.search(text))
    return hits >= 2


def detect_pii(text: str) -> Dict[str, str]:
    """Detect PII, PHI, and PCI patterns in text. Returns dict of type -> matched value."""
    found: Dict[str, str] = {}
    for pii_type, pattern_str in PII_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        match = compiled.search(text)
        if match:
            found[pii_type] = match.group(0)
    for phi_type, pattern_str in PHI_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        match = compiled.search(text)
        if match:
            found[phi_type] = match.group(0)
    for pci_type, pattern_str in PCI_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        match = compiled.search(text)
        if match:
            found[pci_type] = match.group(0)
    return _filter_pii_label_false_positives(text, found)


def _filter_pii_label_false_positives(text: str, found: Dict[str, str]) -> Dict[str, str]:
    """Drop matches that are only category-label discussion without literal values."""
    if not found:
        return found
    # If the only "hits" are near category headings in a refusal list, require literals.
    label_only_context = bool(_PII_CATEGORY_LABEL_RE.search(text))
    if not label_only_context:
        return found
    literal_types = {"ssn", "credit_card", "email", "phone_us", "iban_code", "us_bank_number"}
    filtered = dict(found)
    for pii_type in list(filtered.keys()):
        if pii_type not in literal_types:
            continue
        span = filtered[pii_type]
        # Keep literal SSN/CC/email/phone shapes; drop if span is just a label word.
        if pii_type == "ssn" and not re.search(r"\d{3}-\d{2}-\d{4}", span):
            del filtered[pii_type]
        elif pii_type == "credit_card" and not re.search(r"\d{4}", span):
            del filtered[pii_type]
        elif pii_type == "email" and "@" not in span:
            del filtered[pii_type]
        elif pii_type == "phone_us" and not re.search(r"\d{3}", span):
            del filtered[pii_type]
    return filtered


def detect_secrets(text: str) -> Dict[str, str]:
    """Detect secret patterns in text. Returns dict of type -> matched value."""
    found: Dict[str, str] = {}
    for secret_type, pattern_str in SECRET_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        match = compiled.search(text)
        if match:
            found[secret_type] = match.group(0)
    return found


# ── Smart partial-masking helpers ──────────────────────────────────────────────

def _mask_email(m: re.Match) -> str:
    """john@company.com → j***@c***.com"""
    full = m.group(0)
    try:
        local, domain_full = full.split("@", 1)
        domain_parts = domain_full.rsplit(".", 1)
        tld = domain_parts[1] if len(domain_parts) > 1 else ""
        domain_name = domain_parts[0]
        masked_local = (local[0] + "***") if local else "***"
        masked_domain = (domain_name[0] + "***") if domain_name else "***"
        return f"{masked_local}@{masked_domain}.{tld}"
    except Exception:
        return "[EMAIL]"


def _mask_credit_card(m: re.Match) -> str:
    """4111-1111-1111-1111 → ****-****-****-1111"""
    digits = re.sub(r"[\s\-]", "", m.group(0))
    return f"****-****-****-{digits[-4:]}"


def _mask_ssn(m: re.Match) -> str:
    """123-45-6789 → ***-**-6789"""
    raw = m.group(0)
    return f"***-**-{raw[-4:]}"


def _mask_phone(m: re.Match) -> str:
    """555-867-5309 / +44 20 7946 0958 / 415.555.0142 → ***-***-<last4>"""
    digits = re.sub(r"[^\d]", "", m.group(0))
    return f"***-***-{digits[-4:]}"


def _mask_ssn_digits(m: re.Match) -> str:
    """123 45 6789 → ***-**-6789 (separators-only SSN; keep last 4 digits)."""
    digits = re.sub(r"\D", "", m.group(0))
    return f"***-**-{digits[-4:]}"


def _mask_ssn_nosep(m: re.Match) -> str:
    """'ssn 123456789' → 'ssn ***-**-6789' — preserve the keyword context,
    mask only the trailing 9-digit value so surrounding prose is untouched."""
    return re.sub(
        r"\d{9}\b",
        lambda d: "***-**-" + d.group(0)[-4:],
        m.group(0),
    )


def _mask_api_key(m: re.Match) -> str:
    """sk-abc...xyz → sk-****xyz"""
    s = m.group(0)
    dash_idx = s.find("-")
    prefix = s[: dash_idx + 1] if dash_idx != -1 else s[:3]
    return f"{prefix}****{s[-4:]}" if len(s) > len(prefix) + 8 else f"{prefix}****"


def _mask_aws_key(m: re.Match) -> str:
    """AKIA1234ABCD5678WXYZ → AKIA****WXYZ"""
    s = m.group(0)
    return f"AKIA****{s[-4:]}"


def _mask_aws_secret(m: re.Match) -> str:
    """AWS_SECRET_ACCESS_KEY=wJal...KEY → AWS_SECRET_ACCESS_KEY=*** (keep the
    variable name + separator, mask the secret value)."""
    raw = m.group(0)
    sep = re.search(r"[:=]\s*[\"']?", raw)
    return (raw[: sep.end()] + "***") if sep else "***"


def _mask_github_token(m: re.Match) -> str:
    """ghp_abc...xyz → ghp_****xyz"""
    s = m.group(0)
    return f"ghp_****{s[-4:]}"


def _mask_secret_assignment(m: re.Match) -> str:
    """password="secret" → password="*** (keep key + separator, mask value)"""
    raw = m.group(0)
    sep_match = re.search(r'["\s:=]+', raw)
    if sep_match:
        return raw[: sep_match.end()] + "***"
    return raw[:4] + "***"



def _mask_phone_bare_contextual(m: re.Match) -> str:
    """Mask only the trailing 10-digit run in a contextual phone phrase."""
    s = m.group(0)
    digits_match = re.search(r"\d{10}\b", s)
    if not digits_match:
        return s
    digits = digits_match.group(0)
    masked = f"***-***-{digits[-4:]}"
    return s[: digits_match.start()] + masked + s[digits_match.end() :]


_PII_MASKERS = {
    "email": _mask_email,
    "credit_card": _mask_credit_card,
    "ssn": _mask_ssn,
    "ssn_spaced": _mask_ssn_digits,
    "ssn_nosep": _mask_ssn_nosep,
    "phone_us": _mask_phone,
    "phone_intl": _mask_phone,
    "phone_dotted": _mask_phone,
    "phone_us_bare_contextual": _mask_phone_bare_contextual,
    "api_key_openai": _mask_api_key,
    "aws_access_key": _mask_aws_key,
    "aws_secret_access_key": _mask_aws_secret,
    "github_token": _mask_github_token,
    "private_key_header": lambda m: "[PRIVATE_KEY]",
}

_SECRET_MASKERS = {
    "password_assignment": _mask_secret_assignment,
    "secret_assignment": _mask_secret_assignment,
    "token_assignment": _mask_secret_assignment,
}

_CREDENTIAL_MASKERS = {
    "bearer_token": lambda m: "Bearer [BEARER_TOKEN_REDACTED]",
    "basic_auth": lambda m: "Basic [BASIC_AUTH_REDACTED]",
    "exposed_password": lambda m: "password=[PASSWORD_REDACTED]",
    "connection_string": lambda m: "[CONNECTION_STRING_REDACTED]",
    "private_key_block": lambda m: "[PRIVATE_KEY_REDACTED]",
}


def redact_evidence_digit_spans(text: str, evidence_sources: List[str]) -> str:
    """Mask digit runs echoed in Tier-2 guard evidence when regex patterns miss them."""
    if not text or not evidence_sources:
        return text
    result = text
    spans: set[str] = set()
    for src in evidence_sources:
        if not src:
            continue
        for match in re.finditer(r"\b\d{8,15}\b", str(src)):
            spans.add(match.group(0))
    for digits in sorted(spans, key=len, reverse=True):
        if digits in result:
            result = result.replace(digits, f"***-***-{digits[-4:]}")
    return result


def redact_all(text: str) -> str:
    """Redact PII with smart partial masking; PHI/PCI use placeholder tags."""
    result = text
    for pii_type, pattern_str in PII_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        masker = _PII_MASKERS.get(pii_type)
        if masker:
            result = compiled.sub(masker, result)
        else:
            result = compiled.sub(f"[{pii_type.upper()}_REDACTED]", result)
    for phi_type, pattern_str in PHI_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        result = compiled.sub(f"[{phi_type.upper()}_REDACTED]", result)
    for pci_type, pattern_str in PCI_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        result = compiled.sub(f"[{pci_type.upper()}_REDACTED]", result)
    for secret_type, pattern_str in SECRET_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        masker = _SECRET_MASKERS.get(secret_type)
        if masker:
            result = compiled.sub(masker, result)
        else:
            result = compiled.sub(f"[{secret_type.upper()}_REDACTED]", result)
    for cred_type, pattern_str in CREDENTIAL_EXPOSURE_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        masker = _CREDENTIAL_MASKERS.get(cred_type)
        if masker:
            result = compiled.sub(masker, result)
        else:
            result = compiled.sub(f"[{cred_type.upper()}_REDACTED]", result)
    return result


def get_compliance_tags(pattern_keys: List[str]) -> List[str]:
    """
    Given a list of matched pattern keys (e.g. ['ssn', 'credit_card']),
    return deduplicated compliance framework tags.
    """
    tags: set[str] = set()
    for key in pattern_keys:
        mapped = COMPLIANCE_TAG_MAP.get(key)
        if mapped:
            tags.update(mapped)
    return sorted(tags)


def detect_hallucination_markers(text: str) -> Dict[str, str]:
    """Detect hallucination risk markers in text."""
    if is_safety_refusal_output(text):
        return {}
    found: Dict[str, str] = {}
    for marker_type, pattern_str in HALLUCINATION_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        match = compiled.search(text)
        if match:
            found[marker_type] = match.group(0)
    return found


# R2: canonical example / default-gateway / network addresses that appear in
# documentation, tutorials, and teaching material. Flagging these as
# "infrastructure leakage" is a false positive (the textbook home-router
# example 192.168.0.1 is not a real internal-infra disclosure). Mirrors the
# file_path doc-path carve-out above. An ARBITRARY internal IP (e.g.
# 192.168.50.123) still flags — only the well-known example/gateway addresses
# are exempt.
_IP_LEAKAGE_EXAMPLE_ADDRS: frozenset = frozenset({
    "192.168.0.1", "192.168.1.1", "192.168.0.0", "192.168.1.0",
    "10.0.0.1", "10.0.0.0", "172.16.0.1", "172.16.0.0",
})


def _ip_url_host(value: str) -> str:
    """Strip scheme + trailing punctuation from an internal_url match to get the host."""
    host = re.sub(r"^https?://", "", value)
    return host.rstrip(":/").split("/")[0].split(":")[0]


def detect_ip_leakage(text: str) -> Dict[str, str]:
    """Detect internal IP/infrastructure leakage patterns in text.

    R2: skips canonical example/default-gateway addresses so a benign textbook
    example doesn't destroy the response. Uses ``finditer`` (not ``search``) so
    that a REAL internal IP appearing alongside an example address is still
    detected — only an output whose sole match is an example address is exempt.
    """
    found: Dict[str, str] = {}
    for leak_type, pattern_str in IP_LEAKAGE_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        for match in compiled.finditer(text):
            value = match.group(0)
            if leak_type == "internal_ipv4" and value in _IP_LEAKAGE_EXAMPLE_ADDRS:
                continue
            if leak_type == "internal_url" and _ip_url_host(value) in _IP_LEAKAGE_EXAMPLE_ADDRS:
                continue
            found[leak_type] = value
            break
    return found


def detect_credential_exposure(text: str) -> Dict[str, str]:
    """Detect credential exposure patterns in text."""
    found: Dict[str, str] = {}
    for cred_type, pattern_str in CREDENTIAL_EXPOSURE_PATTERNS.items():
        compiled = compile_pattern(pattern_str)
        match = compiled.search(text)
        if match:
            found[cred_type] = match.group(0)
    return found
